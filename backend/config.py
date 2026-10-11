"""
开放数字化底座 - 配置管理
"""
import os
from pathlib import Path

# 基础路径
BACKEND_DIR = Path(os.getenv("BACKEND_DIR", str(Path(__file__).resolve().parent)))
BASE_DIR = Path(os.getenv("BASE_DIR", str(BACKEND_DIR.parent)))

# 上传配置
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BACKEND_DIR / "uploads" / "datasets")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 模型存储
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(BACKEND_DIR / "models")))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# 文件限制
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "104857600"))  # 100MB
ALLOWED_EXTENSIONS = set(
    os.getenv("ALLOWED_EXTENSIONS", ".csv,.json,.jsonl,.txt,.parquet").split(",")
)

# 数据库
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"
)

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# JWT
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
REFRESH_SECRET_KEY = os.getenv("REFRESH_SECRET_KEY")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# 安全
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_PERIOD_SECONDS = int(os.getenv("RATE_LIMIT_PERIOD_SECONDS", "60"))

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "open-foundation")
USE_MINIO = os.getenv("USE_MINIO", "false").lower() == "true"

# 环境
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT != "production"

# CORS
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8001"
).split(",")
