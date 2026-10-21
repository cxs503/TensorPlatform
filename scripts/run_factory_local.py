#!/usr/bin/env python3
"""Start the isolated developer edition on loopback with a private PostgreSQL."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import subprocess

import pgserver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    credentials = runtime / "local-accounts.json"
    if credentials.exists():
        settings = json.loads(credentials.read_text())
    else:
        settings = {"JWT_SECRET_KEY": secrets.token_hex(32), "REFRESH_SECRET_KEY": secrets.token_hex(32),
                    "accounts": {name: secrets.token_urlsafe(24) for name in ("admin", "engineer", "agent", "viewer")}}
        with os.fdopen(os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump(settings, f)
    postgres = pgserver.get_server(runtime / "postgres", cleanup_mode="stop")
    os.environ["DATABASE_URL"] = postgres.get_uri().replace("postgresql://", "postgresql+psycopg2://", 1)
    os.environ["JWT_SECRET_KEY"] = settings["JWT_SECRET_KEY"]
    os.environ["REFRESH_SECRET_KEY"] = settings["REFRESH_SECRET_KEY"]
    os.environ.setdefault("FACTORY_PROFILE", str(ROOT / "config/factory.example.json"))
    os.environ["ENVIRONMENT"] = "development"
    os.environ["FACTORY_REVISION"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    from database.config import engine
    from services.factory_bootstrap import bootstrap_development_database, provision_account
    from sqlalchemy import text
    with engine.connect() as db:
        exists = db.execute(text("SELECT to_regclass('public.users')")).scalar()
    if not exists:
        bootstrap_development_database(engine)
        with engine.begin() as db:
            org = db.execute(text("INSERT INTO organizations(name) VALUES ('目标TensorPlatform resident development') RETURNING id")).scalar()
            for username, password in settings["accounts"].items():
                provision_account(db, organization_id=org, username=username,
                                  password=password, role=username.upper())
    # Only this private database; this launcher never reads production DATABASE_URL.
    with engine.begin() as db:
        for name in ['091_factory_residents.sql','092_factory_strategy.sql','093_factory_discussion.sql']:
            db.exec_driver_sql((ROOT / 'backend/migrations'/name).read_text(),
                               execution_options={'no_parameters': True})
    print(f"Local accounts: {credentials} (owner-readable only)", flush=True)
    print("This database is isolated from the main TensorPlatform service.", flush=True)
    import uvicorn
    try:
        uvicorn.run("factory_app:app", host="127.0.0.1", port=args.port)
    finally:
        engine.dispose()
        postgres.cleanup()


if __name__ == "__main__":
    main()
