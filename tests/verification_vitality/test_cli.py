"""CLI 冒烟：四个子命令都要在**合成仓**上跑通（不依赖真仓库的规模与状态）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(args, cwd):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONPATH": str(ROOT / "src"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-m", "verification_vitality", *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=180, env=env)
    return proc


def test_cli_patrol_cross_upgrade_roundtrip(healthy_repo, tmp_path):
    out = tmp_path / "patrol.json"
    proc = _run(["patrol", "--repo", str(healthy_repo), "--test-dirs", "tests",
                 "--registry", str(healthy_repo / "config/capabilities.json"),
                 "--out", str(out)], cwd=healthy_repo)
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(out.read_text())
    assert report["summary"]["dead"] == 0

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"capabilities": [
        {"id": "synthetic.base", "claimed_level": "WITHHELD", "evidence": []}]}),
        encoding="utf-8")
    cross_out = tmp_path / "cross.json"
    proc = _run(["cross", "--report", str(out), "--ledger", str(ledger),
                 "--out", str(cross_out)], cwd=healthy_repo)
    assert proc.returncode == 0, proc.stderr[-800:]
    cross = json.loads(cross_out.read_text())
    assert cross["blocked"] is False
    assert cross["summary"]["n_capabilities"] == 1

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.jsonl"
    up_out = tmp_path / "upgrade.json"
    proc = _run(["upgrade", "--ledger", str(ledger), "--report", str(out),
                 "--repo", str(healthy_repo), "--state", str(state),
                 "--journal", str(journal), "--out", str(up_out)], cwd=healthy_repo)
    assert proc.returncode == 0, proc.stderr[-800:]
    upg = json.loads(up_out.read_text())
    assert upg["summary"]["UPGRADE"] == 0, "无度量证据时必须 HOLD，CLI 默认 dry-run"
    assert not state.exists(), "dry-run 不得写状态"


def test_cli_rollback_reports_untracked(tmp_path):
    state = tmp_path / "state.json"
    proc = _run(["rollback", "--state", str(state), "--capability", "synthetic.base"],
                cwd=tmp_path)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["status"] == "NOT_TRACKED"