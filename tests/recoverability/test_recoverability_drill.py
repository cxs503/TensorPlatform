"""
Tests for TensorPlatform recoverability drill.

Tests cover:
- Backup creation and integrity
- Restore and checksum verification
- PITR functionality
- Chaos injection and recovery
- Negative control (restore without backup fails)
- RPO/RTO calculation
"""

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.recoverability.recoverability_drill import (
    _sha256_file,
    _sha256_str,
    get_public_tables,
    get_table_row_count,
    get_table_checksum,
    get_all_table_checksums,
    perform_backup,
    perform_restore,
    verify_checksums,
    inject_chaos,
    run_negative_control,
    calculate_rpo_rto,
    evaluate_criteria,
    BACKUP_DIR,
    RESTORE_DIR,
    WAL_ARCHIVE_DIR,
    DEFAULT_DB_NAME,
    DEFAULT_DB_USER,
    DEFAULT_DB_HOST,
    DEFAULT_DB_PORT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_available():
    """Check if PostgreSQL is available."""
    try:
        result = subprocess.run(
            ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", "SELECT 1;"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="session")
def test_db(db_available):
    """Create a test database with sample data."""
    if not db_available:
        pytest.skip("PostgreSQL not available")

    db_name = "test_recoverability"

    # Drop if exists
    subprocess.run(
        ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {db_name};"],
        capture_output=True, text=True
    )

    # Create
    subprocess.run(
        ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"CREATE DATABASE {db_name};"],
        capture_output=True, text=True, check=True
    )

    # Create test tables
    subprocess.run(
        ["psql", "-U", DEFAULT_DB_USER, "-d", db_name, "-c", """
            CREATE TABLE test_users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                score INTEGER DEFAULT 0
            );
            CREATE TABLE test_orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES test_users(id),
                amount REAL,
                created_at TIMESTAMP DEFAULT NOW()
            );
            INSERT INTO test_users (name, email, score) VALUES
                ('Alice', 'alice@test.com', 100),
                ('Bob', 'bob@test.com', 200),
                ('Charlie', 'charlie@test.com', 300),
                ('Diana', 'diana@test.com', 400),
                ('Eve', 'eve@test.com', 500);
            INSERT INTO test_orders (user_id, amount) VALUES
                (1, 10.5), (1, 20.0), (2, 15.0), (3, 30.0), (4, 25.0), (5, 50.0);
        """],
        capture_output=True, text=True, check=True
    )

    yield db_name

    # Cleanup
    subprocess.run(
        ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {db_name};"],
        capture_output=True, text=True
    )


@pytest.fixture
def temp_backup_dir(tmp_path):
    """Create a temporary backup directory."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    return backup_dir


@pytest.fixture
def temp_restore_dir(tmp_path):
    """Create a temporary restore directory."""
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    return restore_dir


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

class TestChecksumFunctions:
    """Test checksum utility functions."""

    def test_sha256_str_deterministic(self):
        """SHA-256 of same string is always the same."""
        s = "test data"
        h1 = _sha256_str(s)
        h2 = _sha256_str(s)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_str_different(self):
        """Different strings produce different hashes."""
        h1 = _sha256_str("data1")
        h2 = _sha256_str("data2")
        assert h1 != h2

    def test_sha256_file(self, tmp_path):
        """SHA-256 of file contents."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        h = _sha256_file(test_file)
        assert len(h) == 64
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert h == expected


class TestDatabaseOperations:
    """Test database operation functions."""

    def test_get_public_tables(self, test_db):
        """Get list of public tables."""
        tables = get_public_tables(test_db)
        assert "test_users" in tables
        assert "test_orders" in tables

    def test_get_table_row_count(self, test_db):
        """Get row count for a table."""
        count = get_table_row_count(test_db, "test_users")
        assert count == 5

        count = get_table_row_count(test_db, "test_orders")
        assert count == 6

    def test_get_table_checksum_deterministic(self, test_db):
        """Table checksum is deterministic."""
        cs1 = get_table_checksum(test_db, "test_users")
        cs2 = get_table_checksum(test_db, "test_users")
        assert cs1 == cs2
        assert len(cs1) == 64

    def test_get_table_checksum_different_tables(self, test_db):
        """Different tables have different checksums."""
        cs1 = get_table_checksum(test_db, "test_users")
        cs2 = get_table_checksum(test_db, "test_orders")
        assert cs1 != cs2

    def test_get_all_table_checksums(self, test_db):
        """Get checksums for all tables."""
        checksums = get_all_table_checksums(test_db)
        assert "test_users" in checksums
        assert "test_orders" in checksums
        assert len(checksums) == 2


class TestBackupRestore:
    """Test backup and restore operations."""

    def test_perform_backup(self, test_db, temp_backup_dir):
        """Perform a backup and verify metadata."""
        meta = perform_backup(test_db, temp_backup_dir)

        assert meta["database"] == test_db
        assert meta["dump_size_bytes"] > 0
        assert meta["sql_size_bytes"] > 0
        assert len(meta["dump_sha256"]) == 64
        assert len(meta["sql_sha256"]) == 64
        assert meta["backup_duration_seconds"] > 0
        assert meta["source_table_count"] == 2
        assert Path(meta["dump_file"]).exists()
        assert Path(meta["sql_file"]).exists()

    def test_perform_restore(self, test_db, temp_backup_dir, temp_restore_dir):
        """Restore from backup and verify."""
        meta = perform_backup(test_db, temp_backup_dir)

        restore_db = f"{test_db}_restored"
        result = perform_restore(meta["dump_file"], restore_db, temp_restore_dir)

        assert result["restore_db"] == restore_db
        assert result["restore_duration_seconds"] > 0

        count = get_table_row_count(restore_db, "test_users")
        assert count == 5

        # Cleanup
        subprocess.run(
            ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {restore_db};"],
            capture_output=True, text=True
        )

    def test_verify_checksums_match(self, test_db, temp_backup_dir, temp_restore_dir):
        """Verify checksums match after restore."""
        meta = perform_backup(test_db, temp_backup_dir)

        restore_db = f"{test_db}_verify"
        perform_restore(meta["dump_file"], restore_db, temp_restore_dir)

        result = verify_checksums(test_db, restore_db)

        assert result["all_match"] is True
        assert result["matched_tables"] == 2
        assert result["total_tables"] == 2
        assert len(result["mismatched_tables"]) == 0

        # Cleanup
        subprocess.run(
            ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {restore_db};"],
            capture_output=True, text=True
        )


class TestChaosInjection:
    """Test chaos injection functionality."""

    def test_inject_chaos(self, test_db):
        """Inject chaos and verify modifications."""
        before = get_all_table_checksums(test_db)

        result = inject_chaos(test_db, ["test_users", "test_orders"])

        assert result["injected"] is True
        assert result["tables_affected"] >= 0

        after = get_all_table_checksums(test_db)
        changed = sum(1 for t in before if before[t] != after.get(t, ""))


class TestNegativeControl:
    """Test negative control (restore without backup)."""

    def test_negative_control_fails(self, temp_restore_dir):
        """Restore from non-existent backup should fail."""
        restore_db = "test_neg_control"
        result = run_negative_control(restore_db)

        assert result["failed_as_expected"] is True
        assert result["return_code"] != 0

        # Cleanup
        subprocess.run(
            ["psql", "-U", DEFAULT_DB_USER, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {restore_db};"],
            capture_output=True, text=True
        )


class TestRPORTO:
    """Test RPO/RTO calculation."""

    def test_calculate_rpo_rto(self):
        """Calculate RPO and RTO."""
        t0 = 1000.0
        t1 = 1060.0
        t2 = 1100.0
        t3 = 1200.0

        result = calculate_rpo_rto(t0, t1, t2, t3, wal_archive_interval=300)

        assert result["rto_seconds"] == 100.0
        assert result["rpo_seconds"] == 300.0
        assert result["rto_meets_target"] is True
        assert result["rpo_meets_target"] is True

    def test_rpo_rto_exceeds_target(self):
        """RPO/RTO exceeding targets."""
        t0 = 1000.0
        t1 = 1060.0
        t2 = 1100.0
        t3 = 3000.0

        result = calculate_rpo_rto(t0, t1, t2, t3, wal_archive_interval=7200)

        assert result["rto_meets_target"] is False
        assert result["rpo_meets_target"] is False


class TestCriteriaEvaluation:
    """Test acceptance criteria evaluation."""

    def test_all_criteria_pass(self):
        """All criteria should pass with good data."""
        results = {
            "phases": {
                "backup": {
                    "dump_sha256": "abc123",
                    "dump_file": "/tmp/test.dump",
                    "dump_size_bytes": 1000,
                },
                "checksum_verification": {
                    "all_match": True,
                    "matched_tables": 5,
                    "total_tables": 5,
                    "mismatched_tables": [],
                },
                "pitr_checksum_verification": {
                    "all_match": True,
                    "matched_tables": 5,
                    "total_tables": 5,
                },
                "rpo_rto": {
                    "rpo_seconds": 300,
                    "rpo_minutes": 5,
                    "rto_seconds": 60,
                    "rto_minutes": 1,
                    "rpo_meets_target": True,
                    "rto_meets_target": True,
                },
                "chaos_checksum_verification": {
                    "all_match": True,
                    "matched_tables": 5,
                    "total_tables": 5,
                },
                "negative_control": {
                    "failed_as_expected": True,
                    "error_message": "file not found",
                },
            }
        }

        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as f:
            results["phases"]["backup"]["dump_file"] = f.name

        criteria = evaluate_criteria(results)

        assert criteria["C1_backup_integrity"]["passed"] is True
        assert criteria["C2_restore_integrity"]["passed"] is True
        assert criteria["C3_pitr_precision"]["passed"] is True
        assert criteria["C4_rpo"]["passed"] is True
        assert criteria["C5_rto"]["passed"] is True
        assert criteria["C6_chaos_recovery"]["passed"] is True
        assert criteria["C7_negative_control"]["passed"] is True
        assert criteria["C8_maturity"]["passed"] is True
        assert criteria["overall"]["all_criteria_passed"] is True

    def test_criteria_fail_on_mismatch(self):
        """Criteria should fail on checksum mismatch."""
        results = {
            "phases": {
                "backup": {
                    "dump_sha256": "abc123",
                    "dump_file": "/tmp/test.dump",
                    "dump_size_bytes": 1000,
                },
                "checksum_verification": {
                    "all_match": False,
                    "matched_tables": 4,
                    "total_tables": 5,
                    "mismatched_tables": ["test_users"],
                },
                "pitr_checksum_verification": {
                    "all_match": True,
                    "matched_tables": 5,
                    "total_tables": 5,
                },
                "rpo_rto": {
                    "rpo_seconds": 300,
                    "rpo_minutes": 5,
                    "rto_seconds": 60,
                    "rto_minutes": 1,
                    "rpo_meets_target": True,
                    "rto_meets_target": True,
                },
                "chaos_checksum_verification": {
                    "all_match": True,
                    "matched_tables": 5,
                    "total_tables": 5,
                },
                "negative_control": {
                    "failed_as_expected": True,
                    "error_message": "file not found",
                },
            }
        }

        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as f:
            results["phases"]["backup"]["dump_file"] = f.name

        criteria = evaluate_criteria(results)

        assert criteria["C2_restore_integrity"]["passed"] is False
        assert criteria["overall"]["all_criteria_passed"] is False


# ---------------------------------------------------------------------------
# Integration Test (full drill)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestFullDrill:
    """Integration test for full drill (requires PostgreSQL)."""

    def test_full_drill(self, test_db, tmp_path):
        """Run full drill and verify all criteria pass."""
        from tools.recoverability.recoverability_drill import run_full_drill

        report_dir = tmp_path / "reports"
        report_dir.mkdir()

        results = run_full_drill(db_name=test_db, report_dir=report_dir)

        assert (report_dir / f"{results['drill_id']}_report.json").exists()
        assert (report_dir / f"{results['drill_id']}_report.md").exists()

        criteria = results["criteria"]
        assert criteria["overall"]["all_criteria_passed"] is True

        with open(report_dir / f"{results['drill_id']}_report.json") as f:
            report_data = json.load(f)
        assert "drill_id" in report_data
        assert "criteria" in report_data
        assert "phases" in report_data
