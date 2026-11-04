"""PR-7b: red ratchet log-mode regression (mutation probes included).

The ratchet gate is itself code, and a gate that cannot fail is decoration.
These tests probe every failure mode of ``check-from-log`` /
``measure-from-log`` (tools/ops/red_ratchet.py):

  * new red vs baseline        -> rc 1   (the gate's actual job)
  * baseline edited fatter     -> rc 1   (adopt-by-edit, the ratchet's bypass)
  * empty / crashed log        -> rc 2   (0-red here would be a green lie)
  * measure growth             -> refuse (re-baseline must be audited + reasoned)
  * degradation never weakens the new-red check (growth guard unavailable
    -> warn+skip, fresh-red detection unaffected)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.ops import red_ratchet as R  # noqa: E402

SUITE = "backend/tests"
RED_A = "backend/tests/test_a.py::test_x"
RED_B = "backend/tests/test_b.py::test_y"

# 完整 pytest -q 日志样本（终端横幅不可少——见 parse_log 的空日志守卫）
CLEAN_LOG = (
    "2026-11-04T12:00:00.12Z FAILED tests/test_a.py::test_x - AssertionError: boom\n"
    "FAILED tests/test_b.py::test_y - AssertionError: bam\n"
    "PASSED tests/test_ok.py::test_z\n"
    "=== 2 failed, 1 passed in 3.5s ===\n"
)
NEW_RED_LOG = CLEAN_LOG + (
    "ERROR tests/test_c.py - NameError: 'ResistanceRequest' not defined\n"
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _baseline(path: Path, nodes: list[str]) -> Path:
    path.write_text(json.dumps(
        {"total_red": len(nodes), "suites": {SUITE: sorted(nodes)}}), encoding="utf-8")
    return path


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return _baseline(tmp_path / "base.json", [RED_A, RED_B])


@pytest.fixture
def clean_log(tmp_path: Path) -> Path:
    return _write(tmp_path / "clean.log", CLEAN_LOG)


class TestCheckFromLog:
    def test_baseline_only_reds_pass(self, base, clean_log, capsys):
        assert R.check_from_log([SUITE], [str(clean_log)], base) == 0
        assert "0 NEW RED" not in capsys.readouterr().out

    def test_new_red_fails_the_gate(self, base, tmp_path, capsys):
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        assert R.check_from_log([SUITE], [str(log)], base) == 1
        out = capsys.readouterr().out
        assert "NEW RED" in out and "test_c.py" in out

    def test_ci_timestamp_prefix_tolerated(self, base, clean_log):
        """The tee'd log may carry GitHub-service timestamp prefixes."""
        assert R.check_from_log([SUITE], [str(clean_log)], base) == 0

    def test_node_id_normalized_to_repo_root(self):
        """CI runs pytest from backend/ (``tests/...``); baseline nodes are
        repo-root relative. A red logged as ``tests/...`` must hit a
        ``backend/tests/...`` baseline entry, not look 'new'."""
        assert R._normalize({"tests/test_a.py::test_x"}, SUITE) == {RED_A}

    def test_fixed_reds_reported_not_fatal(self, base, tmp_path, capsys):
        log = _write(tmp_path / "fixed.log", CLEAN_LOG.replace(
            "FAILED tests/test_b.py::test_y - AssertionError: bam\n", ""))
        assert R.check_from_log([SUITE], [str(log)], base) == 0
        assert "1 fixed since baseline" in capsys.readouterr().out

    def test_missing_baseline_is_config_error(self, tmp_path, clean_log):
        assert R.check_from_log([SUITE], [str(clean_log)],
                                tmp_path / "absent.json") == 2

    def test_missing_log_is_config_error(self, base, tmp_path):
        assert R.check_from_log([SUITE], [str(tmp_path / "absent.log")], base) == 2

    def test_empty_log_is_not_a_green_state(self, base, tmp_path, capsys):
        """A crashed run with no pytest banner parses as 0 red = everything
        fixed; the gate must refuse to judge a run that never happened."""
        log = _write(tmp_path / "empty.log", "Traceback (most recent call last):\n")
        assert R.check_from_log([SUITE], [str(log)], base) == 2
        assert "terminal summary" in capsys.readouterr().out

    def test_no_tests_ran_is_not_a_green_state(self, base, tmp_path):
        log = _write(tmp_path / "dead.log",
                     "=== no tests ran in 2.1s ===\n")
        assert R.check_from_log([SUITE], [str(log)], base) == 2


class TestCompletionEvidence:
    """Completion proof: the quiet stats line (no '=' border) and/or the
    captured pytest exit code.  PR #597 stage 1 hit both in production: CI
    runs ``pytest -q`` (quiet tail has no '=' border) AND the Actions runner
    dropped the FINAL stdout line of the step — exactly that stats line —
    from the captured log while the tee'd file carried it."""

    def test_quiet_stats_line_is_a_completed_run(self, base, tmp_path):
        log = _write(tmp_path / "quiet.log",
                     "FAILED tests/test_a.py::test_x - AssertionError: boom\n"
                     "FAILED tests/test_b.py::test_y - AssertionError: bam\n"
                     "2 failed, 12 passed in 3.5s\n")
        assert R.check_from_log([SUITE], [str(log)], base) == 0

    def test_rc_one_authoritative_when_stats_line_dropped(
            self, base, tmp_path):
        log = _write(tmp_path / "dropped.log",
                     "FAILED tests/test_a.py::test_x - AssertionError: boom\n"
                     "FAILED tests/test_b.py::test_y - AssertionError: bam\n")
        rc = _write(tmp_path / "pytest.rc", "1\n")
        assert R.check_from_log([SUITE], [str(log)], base, str(rc)) == 0

    def test_rc_one_without_entries_is_truncated(self, base, tmp_path, capsys):
        log = _write(tmp_path / "empty.log", "garbage output\n")
        rc = _write(tmp_path / "pytest.rc", "1\n")
        assert R.check_from_log([SUITE], [str(log)], base, str(rc)) == 2
        assert "truncated" in capsys.readouterr().out

    def test_rc_two_is_a_config_error_even_with_banner(
            self, base, clean_log, tmp_path, capsys):
        rc = _write(tmp_path / "pytest.rc", "2\n")
        assert R.check_from_log([SUITE], [str(clean_log)], base, str(rc)) == 2
        assert "not a completed run" in capsys.readouterr().out

    def test_missing_rc_file_falls_back_to_banner(self, base, clean_log,
                                                  tmp_path):
        assert R.check_from_log([SUITE], [str(clean_log)], base,
                                str(tmp_path / "absent.rc")) == 0

    def test_rc_file_non_integer_ignored(self, base, clean_log, tmp_path):
        rc = _write(tmp_path / "pytest.rc", "killed?\n")
        assert R.check_from_log([SUITE], [str(clean_log)], base, str(rc)) == 0

    def test_measure_from_log_with_rc(self, tmp_path):
        log = _write(tmp_path / "dropped.log",
                     "FAILED tests/test_a.py::test_x - AssertionError: boom\n")
        rc = _write(tmp_path / "pytest.rc", "1\n")
        out = tmp_path / "newbase.json"
        assert R.measure_from_log([SUITE], [str(log)], out, False, None,
                                  str(rc)) == 0
        assert json.loads(out.read_text())["total_red"] == 1


class TestGrowthGuard:
    """Baseline fat-by-edit is the ratchet's own bypass — the gate catches it."""

    def test_baseline_grown_vs_main_fails(self, tmp_path, clean_log,
                                          monkeypatch, capsys):
        main_base = _baseline(tmp_path / "main.json", [RED_A])
        grown = _baseline(tmp_path / "grown.json", [RED_A, RED_B])
        monkeypatch.setenv("RED_RATCHET_BASELINE_MAIN", str(main_base))
        assert R.check_from_log([SUITE], [str(clean_log)], grown) == 1
        assert "baseline GREW" in capsys.readouterr().out

    def test_baseline_shrunk_vs_main_passes(self, tmp_path, monkeypatch):
        """Shrinking the baseline (a fix landed) is the ratchet working; the
        log must then contain only the remaining baseline red."""
        main_base = _baseline(tmp_path / "main.json", [RED_A, RED_B])
        shrunk = _baseline(tmp_path / "shrunk.json", [RED_A])
        one_red_log = _write(tmp_path / "one.log", CLEAN_LOG.replace(
            "FAILED tests/test_b.py::test_y - AssertionError: bam\n", ""))
        monkeypatch.setenv("RED_RATCHET_BASELINE_MAIN", str(main_base))
        assert R.check_from_log([SUITE], [str(one_red_log)], shrunk) == 0

    def test_guard_unavailable_never_weakens_new_red_check(
            self, base, tmp_path, monkeypatch, capsys):
        """Growth guard degrades to warn+skip; the fresh-red verdict does NOT
        degrade with it (a missing main baseline must not un-declare a new red)."""
        monkeypatch.setattr(R, "_main_baseline_nodes",
                            lambda p: (None, "stub-unavailable"))
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        assert R.check_from_log([SUITE], [str(log)], base) == 1
        out = capsys.readouterr().out
        assert "NEW RED" in out and "SKIPPED" in out


class TestMeasureFromLog:
    def test_refuses_growth_without_recompute(self, base, tmp_path, capsys):
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        assert R.measure_from_log([SUITE], [str(log)], base, False, None) == 1
        assert "REFUSING" in capsys.readouterr().out
        assert json.loads(base.read_text())["total_red"] == 2  # untouched

    def test_recompute_requires_reason(self, base, tmp_path):
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        assert R.measure_from_log([SUITE], [str(log)], base, True, None) == 1

    def test_recompute_with_reason_writes_and_records(
            self, base, tmp_path, capsys):
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        rc = R.measure_from_log(
            [SUITE], [str(log)], base, True,
            "PR-7a revived 8 dead collection files; the old 344-entry "
            "baseline predates a suite that never actually ran")
        assert rc == 0
        out = capsys.readouterr().out
        assert "RECOMPUTE 2 -> 3" in out
        doc = json.loads(base.read_text())
        assert doc["total_red"] == 3
        assert doc["recomputed_reason"].startswith("PR-7a")
        assert doc["suites"][SUITE] == sorted([RED_A, RED_B,
                                               "backend/tests/test_c.py"])

    def test_shrink_writes_without_reason(self, base, tmp_path, clean_log):
        log = _write(tmp_path / "fixed.log", CLEAN_LOG.replace(
            "FAILED tests/test_b.py::test_y - AssertionError: bam\n", ""))
        assert R.measure_from_log([SUITE], [str(log)], base, False, None) == 0
        assert json.loads(base.read_text())["total_red"] == 1


class TestCliWiring:
    """argparse surface used by ci.yml (subprocess = the exact CI call)."""

    def test_check_from_log_cli(self, base, clean_log):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "ops" / "red_ratchet.py"),
             "check-from-log", "--suite", SUITE, "--log", str(clean_log),
             "--baseline", str(base)],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "backend/tests: 2 red" in proc.stdout

    def test_check_from_log_cli_new_red_fails(self, base, tmp_path):
        import subprocess
        log = _write(tmp_path / "newred.log", NEW_RED_LOG)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "ops" / "red_ratchet.py"),
             "check-from-log", "--suite", SUITE, "--log", str(log),
             "--baseline", str(base)],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
        assert proc.returncode == 1
        assert "test_c.py" in proc.stdout

    def test_check_from_log_cli_with_rc_file(self, base, tmp_path):
        """The exact ci.yml call shape: tee'd log + PIPESTATUS rc file."""
        import subprocess
        log = _write(tmp_path / "dropped.log",
                     "FAILED tests/test_a.py::test_x - AssertionError: boom\n"
                     "FAILED tests/test_b.py::test_y - AssertionError: bam\n")
        rc = _write(tmp_path / "pytest.rc", "1\n")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "ops" / "red_ratchet.py"),
             "check-from-log", "--suite", SUITE, "--log", str(log),
             "--rc-file", str(rc), "--baseline", str(base)],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "backend/tests: 2 red" in proc.stdout
