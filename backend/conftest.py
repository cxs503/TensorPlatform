"""
pytest configuration for Open Digital Foundation
"""
import os
import tempfile
from pathlib import Path

import pytest

# Set test environment
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "localhost")


@pytest.fixture(autouse=True)
def patch_app_paths(monkeypatch):
    """Redirect /app/ paths to temp dir, avoiding PermissionError in tests"""
    tmp = Path(tempfile.mkdtemp(prefix="foundation_test_"))
    (tmp / "uploads").mkdir(exist_ok=True)
    (tmp / "uploads" / "models").mkdir(exist_ok=True)
    (tmp / "uploads" / "datasets").mkdir(exist_ok=True)
    (tmp / "uploads" / "mlruns").mkdir(exist_ok=True)

    _original_mkdir = Path.mkdir

    def _safe_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        path_str = str(self)
        if path_str.startswith("/app/"):
            alt = tmp / path_str[5:]
            if parents:
                alt.parent.mkdir(parents=True, exist_ok=True)
            alt.mkdir(mode=mode, exist_ok=exist_ok)
            return
        try:
            _original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
        except (PermissionError, FileNotFoundError):
            if exist_ok:
                return

    monkeypatch.setattr(Path, "mkdir", _safe_mkdir)
    yield


@pytest.fixture
def db_session():
    """In-memory SQLite session for tests"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
