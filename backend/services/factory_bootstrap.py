"""Provision ONLY a new, isolated development database; never upgrade TensorPlatform production."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import text


def bootstrap_development_database(engine) -> None:
    with engine.begin() as db:
        if db.execute(text("SELECT to_regclass('public.users')")).scalar():
            raise RuntimeError("Refusing bootstrap: users table already exists; use reviewed migrations")
        db.execute(text("""
            CREATE TABLE organizations (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE users (
                id BIGSERIAL PRIMARY KEY, username VARCHAR(120) UNIQUE NOT NULL,
                email TEXT, full_name TEXT, hashed_password TEXT NOT NULL,
                role TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
                organization_id BIGINT NOT NULL REFERENCES organizations(id),
                features JSONB NOT NULL DEFAULT '[]'::jsonb
            );
        """))
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        # Schema-only reuse: omit the upstream scientific scenario seed query.
        base = (migrations / "084_tensor_eco_console.sql").read_text()
        base = base[:base.index("-- ============================================================")]
        db.exec_driver_sql(base, execution_options={"no_parameters": True})
        for name in ("086_tensor_dev_missions.sql", "087_mission_runner_origin_gc_indexes.sql",
                     "088_mission_model.sql", "089_mission_tree_codispatch.sql",
                     "090_factory_claim_principal.sql", "091_factory_residents.sql", "092_factory_strategy.sql", "093_factory_discussion.sql"):
            db.exec_driver_sql((migrations / name).read_text(), execution_options={"no_parameters": True})


def provision_account(db, *, organization_id: int, username: str, password: str, role: str) -> int:
    from utils.jwt_utils import get_password_hash

    if role not in ("ADMIN", "MANAGER", "ENGINEER", "AGENT", "VIEWER"):
        raise ValueError("unsupported factory role")
    if len(password) < 12:
        raise ValueError("account password must contain at least 12 characters")
    return db.execute(text("""
        INSERT INTO users (username, email, hashed_password, role, organization_id)
        VALUES (:username, :email, :password, :role, :org) RETURNING id
    """), {"username": username, "email": username + "@factory.local",
            "password": get_password_hash(password), "role": role,
            "org": organization_id}).scalar()
