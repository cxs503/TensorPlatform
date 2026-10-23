"""Real PostgreSQL integration fixtures; no shared or production database access."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

import pgserver
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
RUNTIME = tempfile.TemporaryDirectory(prefix="factory-project-tests-")
runtime = Path(RUNTIME.name)
postgres = pgserver.get_server(runtime / "pg", cleanup_mode="stop")
os.environ["DATABASE_URL"] = postgres.get_uri().replace("postgresql://", "postgresql+psycopg2://", 1)
os.environ["JWT_SECRET_KEY"] = secrets.token_hex(32)
os.environ["REFRESH_SECRET_KEY"] = secrets.token_hex(32)
profile = json.loads((ROOT / "config/factory.example.json").read_text())
profile["state_root"] = str(runtime / "agent")
profile["projects"]["factory-project"]["path"] = str(ROOT)
profile_file = runtime / "profile.json"
profile_file.write_text(json.dumps(profile))
os.environ["FACTORY_PROFILE"] = str(profile_file)

from database.config import engine, SessionLocal
from services.factory_bootstrap import bootstrap_development_database
from sqlalchemy import text
from utils.jwt_utils import create_token_pair, get_password_hash
from fastapi.testclient import TestClient
from factory_app import app

bootstrap_development_database(engine)
PASSWORD = secrets.token_urlsafe(24)
HASH = get_password_hash(PASSWORD)


@pytest.fixture()
def factory():
    with engine.begin() as db:
        db.execute(text("TRUNCATE organizations RESTART IDENTITY CASCADE"))
        db.execute(text("INSERT INTO organizations (id,name) VALUES (1,'TensorPlatform'),(2,'Other')"))
        for ident, name, role, org in ((1, "admin", "ADMIN", 1), (2, "engineer", "ENGINEER", 1),
                                      (3, "agent", "AGENT", 1), (4, "agent2", "AGENT", 1),
                                      (5, "viewer", "VIEWER", 1), (6, "other_agent", "AGENT", 2),
                                      (7, "other_admin", "ADMIN", 2)):
            db.execute(text("""INSERT INTO users
                (id,username,email,hashed_password,role,organization_id)
                VALUES (:id,:name,:email,:password,:role,:org)"""),
                       {"id": ident, "name": name, "email": name+"@factory.local",
                        "password": HASH, "role": role, "org": org})
    with TestClient(app) as client:
        def headers(ident):
            return {"Authorization": "Bearer " + create_token_pair({"user_id": ident})["access_token"]}
        yield {"client": client, "headers": headers, "password": PASSWORD,
               "runtime": runtime, "sessions": SessionLocal, "engine": engine}


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    postgres.cleanup()
    RUNTIME.cleanup()
