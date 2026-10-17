import os
# backend/app/core/config.py -> 上3级是 backend/，上4级是项目根
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(CORE_DIR)
BACKEND_DIR = os.path.dirname(APP_DIR)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
MODELS_DIR = os.path.join(BACKEND_DIR, "models")
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads")
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "samples")
DB_PATH = os.path.join(PROJECT_ROOT, "snapscan.db")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
