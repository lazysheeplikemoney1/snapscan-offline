"""Isolate storage before test collection imports app.main and initializes SQLite."""
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core import config

_storage = TemporaryDirectory(prefix="snapscan-backend-tests-")
config.DB_PATH = str(Path(_storage.name) / "history.db")
config.UPLOAD_DIR = str(Path(_storage.name) / "uploads")
Path(config.UPLOAD_DIR).mkdir()


def pytest_sessionfinish(session, exitstatus):
    _storage.cleanup()
