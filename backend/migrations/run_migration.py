"""Versioned PostgreSQL migration runner for the foundation platform."""

from __future__ import annotations

import argparse
import hashlib
import logging
from pathlib import Path

from sqlalchemy import text

from database.config import engine


logger = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent
MIGRATION_GLOB = "[0-9][0-9][0-9]_*.sql"


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob(MIGRATION_GLOB), key=lambda path: path.name)


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_version_table(connection) -> None:
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(255) PRIMARY KEY,
            checksum CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))


def run_pending_migrations(*, baseline: bool = False) -> list[str]:
    """Apply every unrecorded numbered SQL migration in one transaction.

    PostgreSQL executes each complete SQL file without splitting on semicolons,
    so DO blocks and functions remain valid. A transaction-scoped advisory lock
    prevents two backend replicas from migrating concurrently.

    ``baseline=True`` only records the current files. It is intentionally a CLI
    operation for adopting the runner on a database whose schema was previously
    managed manually; application startup never enables it automatically.
    """

    files = _migration_files()
    applied: list[str] = []
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('foundation_schema_migrations'))")
        )
        _ensure_version_table(connection)
        existing = {
            row[0]: row[1]
            for row in connection.execute(
                text("SELECT version, checksum FROM schema_migrations")
            ).fetchall()
        }

        for path in files:
            version = path.name
            checksum = _checksum(path)
            recorded_checksum = existing.get(version)
            if recorded_checksum:
                if recorded_checksum != checksum:
                    raise RuntimeError(
                        f"Applied migration checksum changed: {version}. "
                        "Create a new numbered migration instead of editing history."
                    )
                continue

            if not baseline:
                logger.info("Applying database migration %s", version)
                connection.exec_driver_sql(path.read_text(encoding="utf-8"))

            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, checksum) "
                    "VALUES (:version, :checksum)"
                ),
                {"version": version, "checksum": checksum},
            )
            applied.append(version)

    action = "baselined" if baseline else "applied"
    logger.info("Database migrations %s: %s", action, applied or "none")
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Run foundation database migrations")
    parser.add_argument(
        "--baseline-existing",
        action="store_true",
        help="record numbered migrations without executing them",
    )
    args = parser.parse_args()
    applied = run_pending_migrations(baseline=args.baseline_existing)
    print("\n".join(applied) if applied else "No pending migrations")


if __name__ == "__main__":
    main()
