import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "switchback.proxy.rlwy.net")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "ETfOxlqXMuPmFbrWAhUCGcvhirJBMJmt")
DB_NAME = os.getenv("DB_NAME", "railway")
DB_PORT = int(os.getenv("DB_PORT", 19327))

SECRET_KEY = os.getenv("SECRET_KEY", "your_very_strong_secret_key_here_change_in_production")
JWT_SECRET = os.getenv("JWT_SECRET", "jwt_secret_key_change_in_production")
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5000").split(",")

RUN_TIMEOUT = int(os.getenv("RUN_TIMEOUT", "15"))
MEMORY_LIMIT = os.getenv("MEMORY_LIMIT", "1024m")
CPU_QUOTA = int(os.getenv("CPU_QUOTA", "-1"))

PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50000"))
MAX_TOTAL_FILES_SIZE = int(os.getenv("MAX_TOTAL_FILES_SIZE", "200000"))
SESSION_TYPE = os.getenv("SESSION_TYPE", "filesystem")

RATE_LIMIT_STORAGE_URL = os.getenv("RATE_LIMIT_STORAGE_URL", "memory://")
DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "1000 per hour")

DOCKER_NETWORK_DISABLED = False
DOCKER_READONLY_ROOTFS = False


DEPLOYMENT_ENVIRONMENTS = ["dev", "staging", "production"]
DEFAULT_DEPLOYMENT_ENV = "dev"

ENABLE_DEPLOYMENT_VALIDATION = os.getenv("ENABLE_DEPLOYMENT_VALIDATION", "True") == "True"

MIN_SECURITY_SCORE = 80