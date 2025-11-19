# db.py (REPLACE ENTIRE FILE)
import os
import time
import json
import logging
from datetime import datetime
from mysql.connector import pooling, Error

from config import DB_HOST, DB_USER, DB_PASS, DB_NAME, DB_PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


POOL_NAME = os.getenv("DB_POOL_NAME", "mypool")
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "2")) 

_cnxpool = None

def create_pool(retries: int = 3, backoff_seconds: int = 1):
    """
    Lazily create the MySQL connection pool. Retries with exponential backoff.
    """
    global _cnxpool
    if _cnxpool is not None:
        return _cnxpool

    last_exc = None
    for attempt in range(retries):
        try:
            _cnxpool = pooling.MySQLConnectionPool(
                pool_name=POOL_NAME,
                pool_size=POOL_SIZE,
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASS,
                database=DB_NAME,
                port=DB_PORT,
                autocommit=True,
                pool_reset_session=True
            )
            # smoke test
            conn = _cnxpool.get_connection()
            conn.close()
            logger.info("DB pool created")
            return _cnxpool
        except Error as e:
            last_exc = e
            wait = backoff_seconds * (2 ** attempt)
            logger.warning(f"DB pool creation attempt {attempt+1}/{retries} failed: {e}; retrying in {wait}s")
            time.sleep(wait)

    logger.error("Could not create DB pool after retries")
    raise RuntimeError("Could not create DB pool") from last_exc

def get_conn():
    """
    Return a connection from the pool. Create the pool if needed.
    """
    global _cnxpool
    if _cnxpool is None:
        create_pool()
    try:
        return _cnxpool.get_connection()
    except Error as e:
        logger.error(f"Error getting connection from pool: {e}")
        # try once to re-create pool and retry
        _cnxpool = None
        create_pool()
        return _cnxpool.get_connection()

def get_user_by_username(username):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, username, email, password_hash FROM users WHERE username = %s AND is_active = TRUE", (username,))
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()

def get_user_by_id(user_id):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, username, email FROM users WHERE id = %s AND is_active = TRUE", (user_id,))
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()

def create_user(username, email, password_hash):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)", (username, email, password_hash))
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()

def update_user_last_login(user_id):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET last_login = %s WHERE id = %s", (datetime.utcnow(), user_id))
    finally:
        cur.close()
        conn.close()

def fetch_problem_by_slug(slug):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, title, statement, slug, description, difficulty, image_url, template_java, template_python, template_c, template_cpp, template_javascript, template_csharp, examples, constraints, time_limit, memory_limit FROM problems WHERE slug = %s AND is_public = TRUE", (slug,))
        problem = cur.fetchone()
        if problem:
            cur.execute("SELECT id, input_text, expected_output, is_hidden, execution_order FROM testcases WHERE problem_id = %s ORDER BY execution_order, id", (problem['id'],))
            testcases = cur.fetchall()
            for field in ['examples', 'constraints']:
                if problem.get(field):
                    try:
                        if isinstance(problem[field], str):
                            problem[field] = json.loads(problem[field])
                        elif isinstance(problem[field], (bytes, bytearray)):
                            problem[field] = json.loads(problem[field].decode('utf-8'))
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Could not decode JSON for problem {problem['id']} field {field}")
                        problem[field] = []
                else:
                    problem[field] = []
            problem['testcases'] = testcases
        return problem
    finally:
        cur.close()
        conn.close()

def fetch_problems_page(page, page_size, difficulty=None, search=None):
    offset = (page - 1) * page_size
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        query_base = "FROM problems WHERE is_public = TRUE"
        params = []
        if difficulty:
            query_base += " AND difficulty = %s"
            params.append(difficulty)
        if search:
            query_base += " AND (title LIKE %s OR slug LIKE %s)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term])

        count_query = "SELECT COUNT(*) as cnt " + query_base
        cur.execute(count_query, tuple(params))
        total = cur.fetchone()['cnt']

        query = "SELECT id, title, slug, difficulty " + query_base + " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([page_size, offset])
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        return rows, total
    finally:
        cur.close()
        conn.close()

def store_submission(user_id, problem_id, code, language, verdict, passed, total, execution_time=0.0, memory_used=0, error_message=None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO submissions (user_id, problem_id, code, language, verdict, passed, total, execution_time, memory_used, error_message) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (user_id, problem_id, code, language, verdict, passed, total, execution_time, memory_used, error_message))
        return cur.lastrowid
    except Error as e:
        conn.rollback()
        logger.error(f"DB Error storing submission: {e}")
        raise
    finally:
        cur.close()
        conn.close()

def store_submission_testcase(submission_id, testcase_id, status, execution_time, memory_used, output, error_message):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO submission_testcases (submission_id, testcase_id, status, execution_time, memory_used, output, error_message) VALUES (%s, %s, %s, %s, %s, %s, %s)", (submission_id, testcase_id, status, execution_time, memory_used, output, error_message))
    except Error as e:
        conn.rollback()
        logger.error(f"DB Error storing submission testcase: {e}")
        raise
    finally:
        cur.close()
        conn.close()

def get_user_submissions(user_id, page=1, page_size=20):
    offset = (page - 1) * page_size
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT s.id, s.problem_id, p.title, p.slug, p.difficulty, s.language, s.verdict, s.passed, s.total, s.execution_time, s.created_at FROM submissions s JOIN problems p ON s.problem_id = p.id WHERE s.user_id = %s ORDER BY s.created_at DESC LIMIT %s OFFSET %s", (user_id, page_size, offset))
        submissions = cur.fetchall()
        cur.execute("SELECT COUNT(*) as total FROM submissions WHERE user_id = %s", (user_id,))
        total = cur.fetchone()['total']
        return submissions, total
    finally:
        cur.close()
        conn.close()

def get_submission_detail(submission_id, user_id=None):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        query = "SELECT s.*, p.title, p.slug, u.username FROM submissions s JOIN problems p ON s.problem_id = p.id LEFT JOIN users u ON s.user_id = u.id WHERE s.id = %s"
        params = [submission_id]
        if user_id:
            query += " AND s.user_id = %s"
            params.append(user_id)
        cur.execute(query, params)
        submission = cur.fetchone()
        if submission:
            cur.execute("SELECT st.*, t.input_text, t.expected_output, t.is_hidden FROM submission_testcases st JOIN testcases t ON st.testcase_id = t.id WHERE st.submission_id = %s ORDER BY st.id", (submission_id,))
            submission['testcases'] = cur.fetchall()
        return submission
    finally:
        cur.close()
        conn.close()
