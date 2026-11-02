"""Provision ONLY a new, isolated development database; never upgrade TensorPlatform production."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
# The console base schema the factory development database is built from.
BASE_MIGRATION = "084_tensor_eco_console.sql"
# Numbered files at or below this prefix are platform-schema migrations; the
# factory development database starts from the console base instead.
BASE_PREFIX = 84
# Exceptions to the "apply every later numbered migration" rule. Only files that
# genuinely need the production platform schema belong here, with a reason;
# everything else is picked up automatically so API/model code can never depend
# on a column whose migration the test database skipped (m1625 CI red:
# tensor_dev_missions.report_id / .fail_stage existed in prod, not in tests).
PRODUCTION_ONLY: dict[str, str] = {}


def factory_migration_files() -> list[Path]:
    """Every numbered migration after the console base, in filename order.

    Same ordering rule as ``backend/migrations/run_migration.py`` (lexicographic
    on the zero-padded number), minus ``PRODUCTION_ONLY``.
    """
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"), key=lambda path: path.name)
    return [path for path in files
            if int(path.name.split("_", 1)[0]) > BASE_PREFIX
            and path.name not in PRODUCTION_ONLY]


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
        # Schema-only reuse: omit the upstream scientific scenario seed query.
        base = (MIGRATIONS_DIR / BASE_MIGRATION).read_text()
        base = base[:base.index("-- ============================================================")]
        db.exec_driver_sql(base, execution_options={"no_parameters": True})
        for path in factory_migration_files():
            db.exec_driver_sql(path.read_text(), execution_options={"no_parameters": True})
    # Soul 迁移走带版本+校验和的登记器：重复应用幂等，文件内容变动会报错而不是静默覆盖。
    from services.soul_migrations import apply_soul_migrations
    apply_soul_migrations(engine)


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
