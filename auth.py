import jwt
import bcrypt
from flask import app, request, jsonify, g
from functools import wraps
from datetime import datetime, timedelta
from config import JWT_SECRET, BCRYPT_ROUNDS
from db import get_user_by_username, create_user, get_user_by_id

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(BCRYPT_ROUNDS)).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def generate_token(user_id, username):
    payload = {
        'user_id': user_id,
        'username': username,
        'exp': datetime.utcnow() + timedelta(days=7),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

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
        
        user = get_user_by_id(payload['user_id'])
        if not user:
            return jsonify({"error": "User not found"}), 401
        
        g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def optional_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
            payload = verify_token(token)
            if payload:
                user = get_user_by_id(payload['user_id'])
                if user:
                    g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    
    if not all([username, email, password]):
        return jsonify({"error": "All fields are required"}), 400
    
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    
    existing_user = get_user_by_username(username)
    if existing_user:
        return jsonify({"error": "Username already exists"}), 409
    
    hashed_password = hash_password(password)
    user_id = create_user(username, email, hashed_password)
    
    token = generate_token(user_id, username)
    return jsonify({
        "message": "User created successfully",
        "token": token,
        "user": {"id": user_id, "username": username, "email": email}
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = get_user_by_username(username)
    if not user or not verify_password(password, user['password_hash']):
        return jsonify({"error": "Invalid credentials"}), 401
    
    token = generate_token(user['id'], user['username'])
    return jsonify({
        "message": "Login successful",
        "token": token,
        "user": {"id": user['id'], "username": user['username'], "email": user['email']}
    })