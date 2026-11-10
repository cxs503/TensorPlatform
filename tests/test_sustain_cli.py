"""Regression suite: CLI wiring of the sustainment matrix (exit codes + JSON)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tools.sustainment", *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=timeout)


def _json_of(stdout: str) -> dict:
    start = stdout.index("{")
    return json.loads(stdout[start:])


def test_lineage_cli_build_and_verify(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / "d").mkdir(parents=True)
    (root / "d" / "a.json").write_text('{"n": 1}')
    (root / "d" / "a.png").write_text("PNG")
    out = tmp_path / "lineage.json"
    lock = tmp_path / "lock.json"
    proc = _cli("lineage", "build", "--root", str(root), "--out", str(out), "--lock", str(lock))
    assert proc.returncode == 0, proc.stderr[-1500:]
    payload = _json_of(proc.stdout)
    assert payload["summary"]["nodes"] == 2 and payload["summary"]["is_dag"] is True
    assert out.exists() and lock.exists()

    proc = _cli("lineage", "verify", "--root", str(root), "--lock", str(lock))
    assert proc.returncode == 0, proc.stdout[-800:]
    assert _json_of(proc.stdout)["ok"] is True

    (root / "d" / "a.json").write_text('{"n": 2}')
    proc = _cli("lineage", "verify", "--root", str(root), "--lock", str(lock))
    assert proc.returncode == 1
    assert _json_of(proc.stdout)["drift"]["counts"]["modified"] == 1


def test_backup_cli_fixture_drill(tmp_path: Path):
    src = tmp_path / "db" / "platform.db"
    out = tmp_path / "drill.json"
    ledger = tmp_path / "ledger.json"
    proc = _cli("backup", "--source", str(src), "--fixture", "5",
                "--workdir", str(tmp_path / "work"), "--ledger", str(ledger),
                "--out", str(out))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-800:]
    payload = json.loads(out.read_text())
    assert payload["verdict"] == "PASS"
    assert payload["checks"]["triple_cross_check"]["tables_matched"] == 5
    assert len(payload["negative_controls"]) == 3
    assert ledger.exists() and len(json.loads(ledger.read_text())["records"]) == 1


def test_backup_cli_missing_table_exits_1(tmp_path: Path):
    src = tmp_path / "db" / "platform.db"
    proc = _cli("backup", "--source", str(src), "--fixture", "3",
                "--tables", "core_01,absent_table", "--workdir", str(tmp_path / "work"))
    assert proc.returncode == 1
    payload = _json_of(proc.stdout)
    assert payload["verdict"] == "FAIL"


def test_evidence_cli_exit_codes(tmp_path: Path):
    (tmp_path / "f.png").write_bytes(b"\x89PNG\r\n\x1a\npayload")
    import hashlib
    sha = hashlib.sha256((tmp_path / "f.png").read_bytes()).hexdigest()
    (tmp_path / "figs.json").write_text(json.dumps(
        {"figures": [{"name": "f.png", "path": "f.png", "sha256": sha}]}))
    (tmp_path / "crit.json").write_text(json.dumps({"criteria": [
        {"id": "K1", "op": "<=", "actual": 1, "threshold": 2, "verdict": "PASS",
         "evidence": ["figs.json"]}]}))
    good = tmp_path / "good.md"
    good.write_text("① a ② b ③ c ④ ![f](f.png) ⑤ e ⑥ K1 ok\n")
    proc = _cli("evidence", "--report", str(good), "--figures", str(tmp_path / "figs.json"),
                "--criteria", str(tmp_path / "crit.json"))
    assert proc.returncode == 0, proc.stdout[-1500:]

    bad = tmp_path / "bad.md"
    bad.write_text("no structure, and an untraced 42.5 figure\n")
    proc = _cli("evidence", "--report", str(bad), "--figures", str(tmp_path / "figs.json"),
                "--criteria", str(tmp_path / "crit.json"))
    assert proc.returncode == 1


def test_gc_cli_disk_pressure_gate_reports_levels(tmp_path: Path):
    proc = _cli("gc", "--repo", str(REPO_ROOT), "--limit", "3", "--no-size",
                "--disk-path", str(tmp_path), "--warn-pct", "0", "--force-pct", "0")
    assert proc.returncode == 0, proc.stderr[-1500:]
    payload = _json_of(proc.stdout)
    assert payload["gate_verdict"]["level"] == "force"
    assert payload["gate_verdict"]["allow_new_worktree"] is False
    assert payload["summary"]["worktrees"] >= 1


def test_matrix_cli_single_pass(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / "d").mkdir(parents=True)
    (root / "d" / "x.json").write_text('{"k": 1}')
    (root / "d" / "x.md").write_text("see x.json\n")
    out_dir = tmp_path / "sustain"
    proc = _cli("matrix", "--out-dir", str(out_dir), "--root", str(root),
                "--backup-source", str(tmp_path / "db" / "platform.db"),
                "--backup-fixture", "5", "--repo", str(REPO_ROOT), "--gc-limit", "2",
                "--no-size")
    assert proc.returncode == 0, proc.stdout[-2500:] + proc.stderr[-1000:]
    matrix = json.loads((out_dir / "matrix.json").read_text())
    comps = matrix["components"]
    assert comps["lineage"]["ok"] is True and comps["lineage"]["nodes"] == 2
    assert comps["backup"]["ok"] is True and comps["backup"]["negative_controls_detected"] == 3
    assert comps["gc"]["ok"] is True
    assert comps["evidence"]["ok"] is None            # no report supplied -> honest null
    for name in ("lineage.json", "lineage_lock.json", "lineage_dag.png",
                 "backup_drill.json", "backup_ledger.json", "worktree_gc.json"):
        assert (out_dir / name).exists(), name