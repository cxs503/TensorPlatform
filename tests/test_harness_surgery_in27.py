"""IN27-H3 acceptance tests: coordinator harness-surgery execution authority.

Gate chain: DIAGNOSE -> CANARY -> BACKUP -> SURGERY -> VERIFY -> COMMIT|ROLLED_BACK
Red-team drill: injected faulty surgery must be blocked by the rollback gate.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autonomy.harness_surgery import (  # noqa: E402
    HarnessSurgery,
    SurgeryTicket,
    diagnose,
    make_repair_ticket,
    red_team_drill,
    run_closed_loop,
    seed_harness,
    sha256_of,
)


def _mk(tmp_path, coef=2):
    root = tmp_path / "root"
    probe = seed_harness(root, coef=coef)
    hs = HarnessSurgery(root, tmp_path / "audit.jsonl", tmp_path / "backups")
    return root, probe, hs


class TestCanaryGate:
    def test_canary_fail_rejects_without_touch(self, tmp_path):
        root, probe, hs = _mk(tmp_path)
        pre = sha256_of(probe)
        t = SurgeryTicket(
            ticket_id="T-CANARY-FAIL", target=probe.name,
            old="return x * 2", new="return x * 3",
            canary=[("canary:always_false", lambda: False)],
            verify=[("verify:noop", lambda: True)],
        )
        r = hs.execute(t)
        assert r.status == "REJECTED"
        assert sha256_of(probe) == pre, "rejected ticket must not touch target"
        stages = [e["stage"] for e in hs.audit.events()]
        assert "SURGERY" not in stages and "REJECTED" in stages

    def test_canary_pass_opens_door(self, tmp_path):
        root, probe, hs = _mk(tmp_path)
        t = make_repair_ticket(probe, "T-CANARY-PASS")
        r = hs.execute(t)
        assert r.status == "COMPLETED"
        assert [e["stage"] for e in hs.audit.events()] == [
            "CANARY", "BACKUP", "SURGERY", "VERIFY", "COMMIT"]


class TestBackupAndRollback:
    def test_backup_hash_equals_pre(self, tmp_path):
        root, probe, hs = _mk(tmp_path)
        t = make_repair_ticket(probe, "T-BACKUP")
        r = hs.execute(t)
        assert r.status == "COMPLETED"
        assert sha256_of(r.backup) == r.pre_hash

    def test_bad_surgery_auto_rollback_restores_bytes(self, tmp_path):
        root, probe, hs = _mk(tmp_path, coef=3)  # healthy baseline
        pre = sha256_of(probe)
        t = SurgeryTicket(
            ticket_id="T-BAD", target=probe.name,
            old="return x * 3", new="return x * 999",
            canary=[("canary:ok", lambda: True)],
            verify=[("verify:compute(2)==6",
                     lambda: _load(probe)(2) == 6)],
        )
        r = hs.execute(t)
        assert r.status == "ROLLED_BACK"
        assert sha256_of(probe) == pre, "rollback must restore exact bytes"
        stages = [e["stage"] for e in hs.audit.events()]
        assert stages[-1] == "ROLLED_BACK" and "VERIFY" in stages


class TestRedTeam:
    def test_injected_faulty_surgery_blocked(self, tmp_path):
        rep = red_team_drill(tmp_path / "rt", tmp_path / "rt_audit.jsonl",
                            tmp_path / "rt_backups")
        assert rep["blocked"] is True
        assert rep["verdict"] == "PASS"
        assert rep["result"]["status"] == "ROLLED_BACK"
        assert rep["pre_hash"] == rep["post_hash"]


class TestClosedLoop:
    def test_unattended_loop_full_audit_chain(self, tmp_path):
        rep = run_closed_loop(tmp_path / "cl", tmp_path / "cl_audit.jsonl",
                              tmp_path / "cl_backups", ticket_id="T-LOOP")
        assert rep["diagnosis"]["verdict"] == "FAULT"
        assert rep["result"]["status"] == "COMPLETED"
        assert rep["human_events"] == 0, "no human intervention allowed"
        stages = [e["stage"] for e in rep["audit_events"]]
        assert stages == ["CANARY", "BACKUP", "SURGERY", "VERIFY", "COMMIT"]
        assert all(e["actor"] == "coordinator" and e["human"] is False
                   for e in rep["audit_events"])
        assert rep["target_sha256_backup"] == rep["target_sha256_pre"]
        assert rep["target_sha256_post"] != rep["target_sha256_pre"]
        # repaired file now matches the reference model
        assert _load(rep["result"] and tmp_path / "cl" / "harness_probe.py")(5) == 15


def _load(p):
    from tools.autonomy.harness_surgery import _load_compute
    return _load_compute(Path(p))
