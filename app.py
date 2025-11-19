from flask import Flask, render_template, request, jsonify, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, generate_csrf
import html
import re
import time
import json
import logging
from functools import wraps
from datetime import datetime, timedelta
import jwt
import bcrypt

from config import (
    ALLOWED_ORIGINS, RUN_TIMEOUT, SECRET_KEY, 
    MAX_FILE_SIZE, MAX_TOTAL_FILES_SIZE, JWT_SECRET, BCRYPT_ROUNDS,
    MEMORY_LIMIT, ENABLE_DEPLOYMENT_VALIDATION
)
from db import (
    fetch_problem_by_slug, fetch_problems_page, store_submission, 
    get_user_submissions, get_submission_detail, get_user_by_username,
    get_user_by_id, create_user, update_user_last_login,
    store_submission_testcase
)
from executor import run_in_docker, ExecutionResult, simulate_deployment_validation

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY

CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per hour", "50 per minute"],
    storage_uri="memory://"
)

def init_resources():
   
    try:
        from db import create_pool
        create_pool(retries=1)
    except Exception as e:
        app.logger.warning("DB pool not ready at startup (will retry later): %s", e)

# Initialize resources right after app creation and configuration
# This replaces the deprecated @app.before_first_request
init_resources() 

@app.route("/health")
def health():
    try:
        from db import get_conn
        conn = get_conn()
        conn.close()
        return {"status":"ok"}, 200
    except Exception as e:
        app.logger.error("Health DB error: %s", e)
        return {"status":"error", "detail": str(e)}, 503

CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

VALID_LANGUAGES = {"java", "python", "c", "cpp", "javascript", "csharp"}
VALID_FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_.-]+$')
FORBIDDEN_PATTERNS = [r"\.\.", r"^/", r"^~", r"\.pyc$", r"\.class$", r"\.exe$", r"\.dll$", r"\.so$", r"\.sh$"]
MAX_INPUT_LENGTH = 10000
MAX_CODE_LENGTH = 50000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def hash_password(password):
    try: return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(BCRYPT_ROUNDS)).decode('utf-8')
    except Exception as e: logger.error(f"Error hashing password: {e}"); return None

def verify_password(password, hashed):
    try: return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception as e: logger.error(f"Error verifying password: {e}"); return False

def generate_token(user_id, username):
    payload = { 'user_id': user_id, 'username': username, 'exp': datetime.utcnow() + timedelta(days=7), 'iat': datetime.utcnow() }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try: return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError): return None
    except Exception as e: logger.error(f"Token verification error: {e}"); return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith('Bearer '):
            return jsonify({"error": "Authentication required"}), 401
        token = token[7:]
        payload = verify_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        try: user = get_user_by_id(payload['user_id'])
        except Exception as e: logger.error(f"DB Error for user ID {payload['user_id']}: {e}"); return jsonify({"error": "Internal server error"}), 500
        if not user: return jsonify({"error": "User not found"}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def optional_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        g.current_user = None
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
            payload = verify_token(token)
            if payload:
                try: user = get_user_by_id(payload['user_id'])
                except Exception as e: logger.error(f"DB Error for user ID {payload['user_id']}: {e}"); return jsonify({"error": "Internal server error"}), 500
                if user: g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def validate_files(files):
    if not isinstance(files, dict) or len(files) == 0:
        logger.warning("Validation Error: No files provided")
        return False, "No files provided"
    if len(files) > 10:
        logger.warning("Validation Error: Too many files")
        return False, "Too many files (maximum 10)"
    total_size = 0
    for fname, content in files.items():
        if not VALID_FILENAME_PATTERN.match(fname):
            logger.warning(f"Validation Error: Invalid filename: {fname}")
            return False, f"Invalid filename: {fname}"
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, fname):
                return False, f"Forbidden filename pattern: {fname}"
        content_size = len(content.encode('utf-8'))
        if content_size > MAX_FILE_SIZE:
            logger.warning(f"Validation Error: File {fname} too large ({content_size})")
            return False, f"File {fname} too large"
        total_size += content_size
    if total_size > MAX_TOTAL_FILES_SIZE:
        logger.warning(f"Validation Error: Total files size too large ({total_size})")
        return False, "Total files size too large"
    return True, ""

def sanitize_input(text, max_length=MAX_INPUT_LENGTH):
    if not text: return ""
    text = str(text)[:max_length]
    return html.escape(text)

def rate_limit_key():
    if hasattr(g, 'current_user') and g.current_user:
        return f"user_{g.current_user['id']}"
    return get_remote_address()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/csrf-token")
def get_csrf_token():
    return jsonify({"csrf_token": generate_csrf()})

@app.route("/api/auth/register", methods=["POST"])
@limiter.limit("10 per hour")
def register():
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not all([username, email, password]):
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
        
    try:
        existing_user = get_user_by_username(username)
        if existing_user:
            return jsonify({"error": "Username already exists"}), 409
        hashed_password = hash_password(password)
        if not hashed_password: return jsonify({"error": "Registration failed"}), 500
        user_id = create_user(username, email, hashed_password)
        token = generate_token(user_id, username)
        return jsonify({"message": "User created successfully", "token": token, "user": {"id": user_id, "username": username, "email": email}}), 201
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({"error": "Registration failed"}), 500

@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("20 per hour")
def login():
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    try:
        user = get_user_by_username(username)
        if not user or not verify_password(password, user['password_hash']):
            return jsonify({"error": "Invalid credentials"}), 401
        update_user_last_login(user['id'])
        token = generate_token(user['id'], user['username'])
        return jsonify({"message": "Login successful", "token": token, "user": {"id": user['id'], "username": user['username'], "email": user['email']}})
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({"error": "Login failed"}), 500

@app.route("/api/problems")
@limiter.limit("100 per hour")
@optional_login
def public_problems():
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(50, max(1, int(request.args.get("page_size", "20"))))
        difficulty = request.args.get("difficulty")
        search = request.args.get("search", "").strip()
        items, total = fetch_problems_page(page, page_size, difficulty, search)
        return jsonify({"problems": items, "total": total, "page": page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})
    except Exception as e:
        logger.error(f"Error fetching problems: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/problem/<slug>")
@limiter.limit("100 per hour")
def public_get_problem(slug):
    try:
        problem = fetch_problem_by_slug(slug)
        if not problem:
            return jsonify({"error": "Problem not found"}), 404
        return jsonify({"problem": problem})
    except Exception as e:
        logger.error(f"Error fetching problem {slug}: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/run", methods=["POST"])
@limiter.limit("50 per hour", key_func=rate_limit_key)
@optional_login
def public_run_code():
    start_time = time.time()
    data = request.get_json(force=True, silent=True) or {}
    language = data.get("language", "java")
    files = data.get("files") or {}
    problem_slug = data.get("problem_slug")
    user_input = sanitize_input(data.get("stdin", ""))
    
    if language not in VALID_LANGUAGES: return jsonify({"error": "Unsupported language"}), 400
    is_valid, error_msg = validate_files(files)
    if not is_valid: return jsonify({"error": error_msg}), 400

    safe_files = {fname: content[:MAX_CODE_LENGTH] for fname, content in files.items()}

    try:
        problem_time_limit = RUN_TIMEOUT
        problem_memory_limit = MEMORY_LIMIT
        problem = None
        
        if problem_slug:
            problem = fetch_problem_by_slug(problem_slug)
            if problem:
                problem_time_limit = problem.get('time_limit', RUN_TIMEOUT)
                problem_memory_limit = problem.get('memory_limit', MEMORY_LIMIT)

        if user_input:
            result = run_in_docker(safe_files, language, user_input, problem_time_limit, problem_memory_limit)
            return jsonify({
                "compiled": result.compiled, 
                "output": result.output, 
                "error": result.error, 
                "execution_time": round(time.time() - start_time, 3),
                "verdict": "IE" if result.error and "Internal Error" in result.error else ("RE" if result.error else "AC")
            })
        
        if problem:
            testcases = problem.get("testcases", [])
            result = _run_tests_for_submission(safe_files, language, testcases, False, problem_time_limit, problem_memory_limit)
            result["execution_time"] = round(time.time() - start_time, 3)
            return jsonify(result)
        
        result = run_in_docker(safe_files, language, "", problem_time_limit, problem_memory_limit)
        return jsonify({
            "compiled": result.compiled, 
            "output": result.output, 
            "error": result.error, 
            "execution_time": round(time.time() - start_time, 3),
            "verdict": "IE" if result.error and "Internal Error" in result.error else ("RE" if result.error else "AC")
        })
        
    except Exception as e:
        logger.error(f"Error in public_run_code: {e}", exc_info=True)
        return jsonify({"error": "Internal Execution System Failure (IE)"}), 500

@app.route("/api/submit", methods=["POST"])
@limiter.limit("30 per hour", key_func=rate_limit_key)
@login_required
def public_submit():
    start_time = time.time()
    data = request.get_json(force=True, silent=True) or {}
    language = data.get("language", "java")
    files = data.get("files") or {}
    problem_slug = data.get("problem_slug")
    
    if language not in VALID_LANGUAGES: 
        logger.warning(f"Submission Error: Unsupported language {language}")
        return jsonify({"error": "Unsupported language"}), 400
    if not problem_slug: 
        logger.warning("Submission Error: Problem slug required")
        return jsonify({"error": "Problem slug is required"}), 400
        
    is_valid, error_msg = validate_files(files)
    if not is_valid: 
        logger.warning(f"Submission Error: {error_msg}")
        return jsonify({"error": error_msg}), 400

    try:
        problem = fetch_problem_by_slug(problem_slug)
        if not problem: 
            logger.warning(f"Submission Error: Problem {problem_slug} not found")
            return jsonify({"error": "Problem not found"}), 404

        safe_files = {fname: content[:MAX_CODE_LENGTH] for fname, content in files.items()}
        testcases = problem.get("testcases", [])
        problem_time_limit = problem.get('time_limit', RUN_TIMEOUT)
        problem_memory_limit = problem.get('memory_limit', MEMORY_LIMIT)
        
        if not testcases:
            submission_id = store_submission(g.current_user['id'], problem['id'], json.dumps(safe_files), language, "AC", 0, 0, round(time.time() - start_time, 3))
            return jsonify({"compiled": True, "passed": 0, "total": 0, "verdict": "AC", "submission_id": submission_id, "execution_time": round(time.time() - start_time, 3)})

        result = _run_tests_for_submission_with_storage(safe_files, language, testcases, g.current_user['id'], problem['id'], problem_time_limit, problem_memory_limit)
        result["execution_time"] = round(time.time() - start_time, 3)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in public_submit: {e}", exc_info=True)
        return jsonify({"error": "Internal Submission System Failure (IE)"}), 500

def _run_tests_for_submission_with_storage(safe_files, language, testcases, user_id, problem_id, time_limit, memory_limit):
    total = len(testcases)
    passed = 0
    tests = []
    submission_id = None
    
    if total == 0:
        logger.info(f"Problem {problem_id} submitted with no testcases.")
        submission_id = store_submission(user_id, problem_id, json.dumps(safe_files), language, "AC", 0, 0, 0.0)
        return {"compiled": True, "tests": tests, "passed": 0, "total": 0, "verdict": "AC", "submission_id": submission_id}

    # PHASE 1: Compilation
    compile_result = run_in_docker(safe_files, language, "", time_limit, memory_limit)
    if not compile_result.compiled:
        logger.info(f"Submission failed compilation for problem {problem_id}. Error: {compile_result.error}")
        submission_id = store_submission(user_id, problem_id, json.dumps(safe_files), language, "CE", 0, total, 0.0, 0, compile_result.error)
        return {"compiled": False, "compile_error": compile_result.error, "tests": [], "passed": 0, "total": total, "verdict": "CE", "submission_id": submission_id}

    overall_error = None
    verdict = "AC"
    total_execution_time = 0.0
    
    # PHASE 2: Test Execution
    for tc in testcases:
        test_start_time = time.time()
        inp = tc.get("input_text", "")
        expected = tc.get("expected_output", "")
        is_hidden = tc.get("is_hidden", False)
        
        result = run_in_docker(safe_files, language, inp, time_limit, memory_limit)
        test_end_time = time.time()
        test_run_time = round(test_end_time - test_start_time, 3)
        total_execution_time += test_run_time
        
        test_error = None
        test_status = "FAIL"
        
        if result.error:
            test_status = "RE"
            test_error = result.error
            if not overall_error: overall_error = test_error
        else:
            output_clean = result.output.strip().replace('\r\n', '\n')
            expected_clean = expected.strip().replace('\r\n', '\n')
            test_status = "PASS" if output_clean == expected_clean else "FAIL"
        
        if test_status == "PASS":
            passed += 1
        else:
            if verdict == "AC":
                if "Time Limit Exceeded" in result.error: verdict = "TLE"
                elif "Memory Limit Exceeded" in result.error: verdict = "MLE"
                elif test_status == "RE": verdict = "RE"
                else: verdict = "WA"
            
        tests.append({
            "id": tc.get("id"),
            "input": inp if not is_hidden else "[Hidden]",
            "expected": expected if not is_hidden else "[Hidden]",
            "output": result.output if not is_hidden else "[Hidden]",
            "status": test_status,
            "error": test_error,
            "is_hidden": is_hidden,
            "execution_time": test_run_time
        })
    
    # Determine final verdict based on tests
    if passed != total and verdict == "AC": verdict = "WA"

    # PHASE 3: Deployment Validation (Simulated Enterprise Check)
    if ENABLE_DEPLOYMENT_VALIDATION and verdict == "AC":
        deployment_success, deploy_message = simulate_deployment_validation(language)
        if not deployment_success:
            verdict = "DEP" # Deployment Check Failed
            overall_error = deploy_message
            logger.warning(f"Submission failed deployment check: {deploy_message}")

    # Store submission result
    if not submission_id:
        submission_id = store_submission(user_id, problem_id, json.dumps(safe_files), language, verdict, passed, total, round(total_execution_time, 3), 0, overall_error)
    
    for i, test in enumerate(tests):
        store_submission_testcase(submission_id, testcases[i]['id'], test['status'], test['execution_time'], 0, test.get('output', ''), test.get('error', ''))

    return {"compiled": True, "tests": tests, "passed": passed, "total": total, "verdict": verdict, "error": overall_error, "submission_id": submission_id}

def _run_tests_for_submission(safe_files, language, testcases, is_submission=True, time_limit=RUN_TIMEOUT, memory_limit=MEMORY_LIMIT):
    tests = []
    passed = 0
    total = len(testcases)
    overall_execution_time = 0.0
    
    if total == 0: return {"compiled": True, "tests": tests, "passed": 0, "total": 0, "verdict": "AC"}

    compile_result = run_in_docker(safe_files, language, "", time_limit, memory_limit)
    if not compile_result.compiled:
        return {"compiled": False, "compile_error": compile_result.error, "tests": [], "passed": 0, "total": total, "verdict": "CE"}

    overall_error = None
    for tc in testcases:
        test_start_time = time.time()
        inp = tc.get("input_text", "")
        expected = tc.get("expected_output", "")
        is_hidden = tc.get("is_hidden", False)
        
        result = run_in_docker(safe_files, language, inp, time_limit, memory_limit)
        test_run_time = round(time.time() - test_start_time, 3)
        overall_execution_time += test_run_time
        
        test_error = None
        
        if result.error:
            test_status = "RE"
            test_error = result.error
            if not overall_error: overall_error = test_error
        else:
            output_clean = result.output.strip().replace('\r\n', '\n')
            expected_clean = expected.strip().replace('\r\n', '\n')
            test_status = "PASS" if output_clean == expected_clean else "FAIL"
        
        if test_status == "PASS": passed += 1
            
        tests.append({
            "id": tc.get("id"),
            "input": inp,
            "expected": expected,
            "output": result.output,
            "status": test_status,
            "error": test_error,
            "is_hidden": is_hidden,
            "execution_time": test_run_time
        })

    if passed == total: verdict = "AC"
    elif overall_error and "Time Limit Exceeded" in str(overall_error): verdict = "TLE"
    elif overall_error and "Memory Limit Exceeded" in str(overall_error): verdict = "MLE"
    elif overall_error: verdict = "RE"
    else: verdict = "WA"

    return {"compiled": True, "tests": tests, "passed": passed, "total": total, "verdict": verdict, "error": overall_error, "execution_time": round(overall_execution_time, 3)}


@app.route("/api/submissions")
@login_required
def get_submissions():
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(50, max(1, int(request.args.get("page_size", "20"))))
        submissions, total = get_user_submissions(g.current_user['id'], page, page_size)
        return jsonify({"submissions": submissions, "total": total, "page": page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})
    except Exception as e:
        logger.error(f"Error fetching submissions: {e}")
        return jsonify({"error": "Failed to fetch submissions"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)