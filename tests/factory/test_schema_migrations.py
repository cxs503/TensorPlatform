"""Schema-drift guard for the factory test database.

m1625 CI red: ``services.factory_bootstrap.bootstrap_development_database`` applied a
hand-maintained list of migration files (084, 086..093 + soul 094), so
``090_tensor_reports`` / ``095_mission_lifecycle`` / ``096_report_gate`` never reached
the test database although the API already selected
``tensor_dev_missions.report_id`` / ``.fail_stage``:

    psycopg2.errors.UndefinedColumn: column "report_id" of relation
    "tensor_dev_missions" does not exist

These tests pin the invariant instead of the file list: every numbered migration after
the console base is applied, and every column those migrations add to the factory tables
really exists in the bootstrapped schema.
"""
from pathlib import Path
import re

from sqlalchemy import text

from services.factory_bootstrap import (
    BASE_PREFIX,
    MIGRATIONS_DIR,
    PRODUCTION_ONLY,
    factory_migration_files,
)

FACTORY_TABLES = ("tensor_dev_missions", "campaign_plans")


def _numbered_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"), key=lambda p: p.name)


def _added_columns(table: str) -> dict[str, str]:
    """{column: migration file} for ``ALTER TABLE <table> ADD COLUMN`` in migrations."""
    added: dict[str, str] = {}
    pattern = re.compile(r"ADD COLUMN IF NOT EXISTS\s+(\w+)")
    for path in _numbered_migrations():
        for statement in path.read_text().split(";"):
            if re.search(rf"ALTER TABLE\s+{table}\b", statement):
                for column in pattern.findall(statement):
                    added.setdefault(column, path.name)
    return added


def _columns(engine, table: str) -> set[str]:
    with engine.begin() as db:
        return {
            row[0]
            for row in db.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_schema = 'public' AND table_name = :t"),
                {"t": table},
            )
        }


def test_every_migration_after_the_console_base_is_applied():
    """A new numbered migration must not be silently skipped by the bootstrap."""
    applied = [path.name for path in factory_migration_files()]
    later = [path.name for path in _numbered_migrations()
             if int(path.name.split("_", 1)[0]) > BASE_PREFIX]
    skipped = [name for name in later if name not in applied]

    assert set(skipped) <= set(PRODUCTION_ONLY), f"unlisted skip: {skipped}"
    assert all(PRODUCTION_ONLY[name].strip() for name in skipped), \
        "PRODUCTION_ONLY entries must carry a reason"
    assert applied == sorted(later), "factory migrations must run in filename order"


def test_mission_columns_added_by_migrations_exist(factory):
    expected = _added_columns("tensor_dev_missions")
    # columns the control-plane / mission-lifecycle API reads today
    assert {"report_id", "report_owed", "fail_stage"} <= set(expected)
    missing = sorted(set(expected) - _columns(factory["engine"], "tensor_dev_missions"))
    assert not missing, f"tensor_dev_missions missing {missing} (declared by migrations)"


def test_campaign_plan_columns_added_by_migrations_exist(factory):
    expected = _added_columns("campaign_plans")
    assert expected, "campaign_plans migrations should add columns"
    missing = sorted(set(expected) - _columns(factory["engine"], "campaign_plans"))
    assert not missing, f"campaign_plans missing {missing} (declared by migrations)"


def test_factory_tables_exist(factory):
    for table in FACTORY_TABLES:
        assert _columns(factory["engine"], table), f"{table} was not created"
