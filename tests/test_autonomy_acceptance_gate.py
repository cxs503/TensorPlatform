"""IN20-G21 自主续航验收门：判据机单测（脱库为主，DB 用例按可用性跳过）。

三层：
1. **纯函数**：`classify_stuck_actions` / `pad_four_verdict` / `aggregate` 的正负例；
2. **产物不变量**：`artifacts/autonomy_acceptance/autonomy_acceptance.json` 的
   判据数字必须自洽（六项全 PASS 才算 PASS；WITHHELD 不得混成 PASS），
   证据文件的 sha256 必须与现场重算一致；
3. **接线回归**：R1 补给器在真库跑不通的两处根因（缺 `capability_ledger` 表、
   `origin='backlog_refiller'` 撞 CHECK）必须有钉死的回归用例，防回退。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ART = REPO_ROOT / "artifacts" / "autonomy_acceptance"
ACCEPTANCE = ART / "autonomy_acceptance.json"

import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from tools.autonomy import acceptance as acc  # noqa: E402

PSQL = shutil.which("psql")
needs_psql = pytest.mark.skipif(PSQL is None, reason="psql 不可用（脱库环境）")


# ---------------------------------------------------------------------------
# 1. 纯函数正负例
# ---------------------------------------------------------------------------


class TestClassifyStuckActions:
    def test_unclaimable_past_grace_is_stuck(self):
        buckets = acc.classify_stuck_actions([
            {"mission_id": 1, "claimable": False, "reason": "dead_model"},
        ])
        assert buckets["stuck_backlog"] == 1
        assert buckets["grace_backlog"] == 0

    def test_grace_window_is_not_backlog(self):
        """宽限内不可领取≠积压：worker 下一轮可能就抢到。"""
        buckets = acc.classify_stuck_actions([
            {"mission_id": 2, "claimable": False, "reason": "grace(<1800s)"},
        ])
        assert buckets["stuck_backlog"] == 0
        assert buckets["grace_backlog"] == 1

    def test_claimable_is_clear(self):
        buckets = acc.classify_stuck_actions([
            {"mission_id": 3, "claimable": True, "reason": "live_model"},
        ])
        assert buckets["stuck_backlog"] == 0
        assert len(buckets["clear"]) == 1

    def test_mixed_sample_partitions(self):
        buckets = acc.classify_stuck_actions([
            {"mission_id": 1, "claimable": True, "reason": "ok"},
            {"mission_id": 2, "claimable": False, "reason": "grace(<60s)"},
            {"mission_id": 3, "claimable": False, "reason": "no_live_worker"},
            {"mission_id": 4, "claimable": False, "reason": "policy=withdraw"},
        ])
        assert buckets["stuck_backlog"] == 2
        assert buckets["grace_backlog"] == 1
        assert [a["mission_id"] for a in buckets["stuck"]] == [3, 4]


class TestPadFourVerdict:
    def _ok_inputs(self):
        missions = {mid: "succeeded" for mid in acc.PAD_MISSIONS}
        ledger = {mid: 1 for mid in acc.PAD_MISSIONS}
        return missions, ledger

    def test_all_terminal_and_booked(self):
        missions, ledger = self._ok_inputs()
        out = acc.pad_four_verdict(missions, ledger)
        assert out["ok"] is True
        assert out["terminal_and_booked"] == 4

    def test_non_terminal_mission_fails(self):
        missions, ledger = self._ok_inputs()
        missions[5053] = "running"
        out = acc.pad_four_verdict(missions, ledger)
        assert out["ok"] is False
        assert out["per_mission"]["5053"]["terminal"] is False

    def test_missing_ledger_row_fails(self):
        missions, ledger = self._ok_inputs()
        ledger.pop(5052)
        out = acc.pad_four_verdict(missions, ledger)
        assert out["ok"] is False
        assert out["per_mission"]["5052"]["ledger_rows"] == 0

    def test_suspended_counts_as_terminal(self):
        """R4 现场定义：suspended 也算终态，否则提案波永不点火。"""
        missions, ledger = self._ok_inputs()
        missions[5051] = "suspended"
        assert acc.pad_four_verdict(missions, ledger)["per_mission"]["5051"]["terminal"] is True


class TestAggregate:
    def test_all_pass(self):
        checks = {k: {"status": acc.PASS} for k in ("C1", "C2", "C3")}
        assert acc.aggregate(checks)["verdict"] == acc.PASS

    def test_withheld_is_not_pass(self):
        checks = {"C1": {"status": acc.PASS}, "C2": {"status": acc.WITHHELD}}
        out = acc.aggregate(checks)
        assert out["verdict"] == acc.WITHHELD
        assert out["withheld"] == ["C2"]

    def test_fail_dominates(self):
        checks = {"C1": {"status": acc.FAIL}, "C2": {"status": acc.WITHHELD}}
        out = acc.aggregate(checks)
        assert out["verdict"] == acc.FAIL
        assert out["failing"] == ["C1"]


# ---------------------------------------------------------------------------
# 2. 产物不变量
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def payload():
    if not ACCEPTANCE.exists():
        pytest.skip("验收产物未生成（先跑 python3 -m tools.autonomy.acceptance）")
    return json.loads(ACCEPTANCE.read_text(encoding="utf-8"))


class TestAcceptanceArtifact:
    def test_schema_and_identity(self, payload):
        assert payload["schema"] == "tp.autonomy.acceptance/v1"
        assert payload["campaign_node"] == "IN20-G21"
        assert payload["mission_id"] == 5177

    def test_six_criteria_all_pass(self, payload):
        checks = payload["checks"]
        assert set(checks) == {
            "C1_refill_stall_replay",
            "C2a_gate_release_executed",
            "C2b_merge_executed",
            "C3_propose_backlog_zero",
            "C4_exit_dashboard_baseline",
            "C5_pad_four_terminal_and_booked",
        }
        assert all(c["status"] == acc.PASS for c in checks.values())
        assert payload["aggregate"]["verdict"] == acc.PASS
        assert payload["aggregate"]["criteria_pass"] == 6

    def test_aggregate_recomputes_from_checks(self, payload):
        recomputed = acc.aggregate(payload["checks"])
        assert recomputed["verdict"] == payload["aggregate"]["verdict"]
        assert recomputed["statuses"] == payload["aggregate"]["statuses"]

    def test_c1_numbers_are_self_consistent(self, payload):
        c = payload["checks"]["C1_refill_stall_replay"]["criteria"]
        assert c["replay_pending_missions"] == 0
        assert c["observed_pending_missions"] == 0
        assert c["triggered"] is True
        assert c["missions_generated"] >= 1
        assert c["refill_audit_rows"] >= 1
        assert c["rollback_recorded"] is True
        assert c["rollback_verified_absent"] is True
        # 三源全绿：缺表/权限问题会把它写成 unavailable:*
        assert set(c["gap_source_status"].values()) == {"ok"}

    def test_c2a_numbers_are_self_consistent(self, payload):
        c = payload["checks"]["C2a_gate_release_executed"]["criteria"]
        assert c["releases_recorded"] >= 1
        assert c["release_transition"] == "blocked->pending"
        assert c["rollbacks_recorded"] >= 1
        assert c["owner_only_holds"] >= 1
        assert c["idempotent_second_run_releases"] == 0
        assert c["recompute_matches_audit"] is True

    def test_c2b_numbers_are_self_consistent(self, payload):
        c = payload["checks"]["C2b_merge_executed"]["criteria"]
        assert c["audit_chain_ok"] is True
        assert c["merges_executed"] >= 1
        assert c["merges_confirmed_in_main"] >= 1
        assert c["main_ref_sha"]

    def test_c3_backlog_zero(self, payload):
        c = payload["checks"]["C3_propose_backlog_zero"]["criteria"]
        assert c["stuck_backlog"] == 0
        assert c["guard_suite_failed"] == 0
        assert c["guard_suite_passed"] > 0

    def test_c3_apply_path_is_audited_and_rollbackable(self, payload):
        """处置能力必须留审计且可回滚：apply 摘要 ↔ JSONL ↔ 回滚三方对账。"""
        c = payload["checks"]["C3_propose_backlog_zero"]["criteria"]
        assert c["apply_actions"] >= 1
        assert c["apply_audit_rows"] >= 1
        assert c["apply_ids_match"] is True
        ap = c["apply_path"]
        assert ap["rollback_files"] >= 1
        assert ap["rollback_records"] >= c["apply_actions"]

    def test_c4_three_real_metrics(self, payload):
        c = payload["checks"]["C4_exit_dashboard_baseline"]["criteria"]
        assert c["metrics_present"] >= 3
        assert c["metrics_real"] >= 3
        assert c["chronicle_write"] == "written"

    def test_c5_four_terminal_and_booked(self, payload):
        c = payload["checks"]["C5_pad_four_terminal_and_booked"]["criteria"]
        assert c["ok"] is True
        assert c["terminal_and_booked"] == 4
        assert c["campaign_nodes_done"] == 4

    def test_evidence_hashes_match_files(self, payload):
        checked = 0
        for name, check in payload["checks"].items():
            for ev in check.get("evidence") or []:
                if not ev.get("exists"):
                    continue
                path = REPO_ROOT / ev["path"]
                assert path.exists(), f"{name}: 证据文件缺失 {ev['path']}"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                assert digest == ev["sha256"], f"{name}: 证据 {ev['path']} 已漂移（重跑门禁）"
                checked += 1
        assert checked >= 5

    def test_acceptance_doc_exists(self):
        doc = REPO_ROOT / "docs" / "autonomy" / "IN20_G21_autonomy_acceptance.md"
        assert doc.exists()
        text = doc.read_text(encoding="utf-8")
        assert "C1" in text and "C5" in text


# ---------------------------------------------------------------------------
# 3. 接线回归（R1 补给器：真库跑不通的两处根因）
# ---------------------------------------------------------------------------


class TestRefillerWiringRegression:
    def test_origin_respects_platform_check_constraint(self):
        """平台 CHECK：tensor_dev_missions.origin ∈ {owner, agent}。"""
        from backlog_refiller.mission_generator import GENERATOR_TAG, MISSION_ORIGIN

        assert MISSION_ORIGIN in {"owner", "agent"}
        assert GENERATOR_TAG == "backlog_refiller"

    def test_gap_ledger_tolerates_missing_source_table(self):
        """缺表不再拖死整轮补给：源级容错 + 状态可读。"""

        class BoomSession:
            def rollback(self):
                return None

        class Src(acc_src_base()):
            def _collect_withheld_capabilities(self):
                raise RuntimeError("relation \"capability_ledger\" does not exist")

            def _collect_campaign_gaps(self):
                return [{"source": "campaign_nodes", "ext_id": "x", "repo": "TensorPlatform",
                         "title": "t", "description": "d", "priority": 2, "metadata": {}}]

            def _collect_mission_gaps(self):
                return []

        src = Src(BoomSession())
        gaps = src.collect_gaps()
        assert len(gaps) == 1
        assert src.source_status["capability_ledger"].startswith("unavailable:")
        assert src.source_status["campaign_nodes"] == "ok"

    @needs_psql
    def test_ensure_capability_ledger_is_idempotent(self):
        dsn = acc.DEFAULT_REFILL_DSN
        try:
            engine = acc._refill_engine(dsn)
        except Exception as exc:  # pragma: no cover - 沙箱库不存在
            pytest.skip(f"沙箱库不可用: {exc}")
        from backlog_refiller.gap_ledger import ensure_capability_ledger

        try:
            with engine.connect() as conn:
                assert ensure_capability_ledger(conn) is True
                assert ensure_capability_ledger(conn) is True  # 再建一次不报错
                cols = conn.exec_driver_sql(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name='capability_ledger'").scalar()
            assert cols and cols >= 5
        finally:
            engine.dispose()


def acc_src_base():
    """拿真实的 GapLedgerSource 作基类，避免测试复制一份实现。"""
    from backlog_refiller.gap_ledger import GapLedgerSource

    return GapLedgerSource


class TestProposeApplyEvidence:
    """apply/审计/回滚三方对账：少了任一侧就判证据链断裂（不许当 PASS）。"""

    def _mk(self, tmp_path, *, audit_ids=(1, 2), rollback_records=2):
        base = tmp_path
        (base / "summary_apply_20260101T000000Z.json").write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z", "policy": "withdraw", "scanned": 2,
            "changed": [{"mission_id": 1, "verb": "withdraw"},
                        {"mission_id": 2, "verb": "withdraw"}],
        }), encoding="utf-8")
        (base / "audit-20260101.jsonl").write_text(
            "\n".join(json.dumps({"mission_id": mid, "verb": "withdraw"})
                       for mid in audit_ids) + "\n", encoding="utf-8")
        (base / "rollback-20260101T000001Z.json").write_text(json.dumps({
            "ts": "x", "records": [{"mission_id": i} for i in range(rollback_records)],
        }), encoding="utf-8")
        return base

    def test_matching_evidence_passes(self, tmp_path):
        out = acc._propose_apply_evidence(self._mk(tmp_path))
        assert out["ok"] is True
        assert out["ids_match"] is True

    def test_audit_id_mismatch_fails(self, tmp_path):
        out = acc._propose_apply_evidence(self._mk(tmp_path, audit_ids=(1,)))
        assert out["ok"] is False
        assert out["ids_match"] is False

    def test_missing_evidence_is_unavailable(self, tmp_path):
        assert acc._propose_apply_evidence(tmp_path)["available"] is False


@needs_psql
class TestGateSandboxRecompute:
    """沙箱审计 vs 纯函数复算：门禁判定必须可离线复现。"""

    def test_release_decision_recomputes(self):
        sys.path.insert(0, str(REPO_ROOT / "ops" / "taskplane"))
        import gate_auto_release as gar

        sys.path.insert(0, str(REPO_ROOT / "tools" / "autonomy"))
        nodes = {
            9001: {"id": 9001, "plan_id": 999, "name": "SBX-UP", "node_type": "task",
                   "status": "done", "dependencies": [], "metadata": {}},
            9002: {"id": 9002, "plan_id": 999, "name": "SBX-GATE-OK", "node_type": "gate",
                   "status": "blocked", "dependencies": [9001],
                   "metadata": {"gate_criteria": {
                       "artifacts": ["docs/autonomy/IN20_G21_autonomy_acceptance.md"]}}},
            9003: {"id": 9003, "plan_id": 999, "name": "SBX-GATE-SPEC-规范修订",
                   "node_type": "gate", "status": "blocked", "dependencies": [9001],
                   "metadata": {"gate_criteria": {
                       "artifacts": ["docs/autonomy/IN20_G21_autonomy_acceptance.md"]}}},
            9004: {"id": 9004, "plan_id": 999, "name": "SBX-GATE-NOART", "node_type": "gate",
                   "status": "blocked", "dependencies": [9001],
                   "metadata": {"gate_criteria": {
                       "artifacts": ["artifacts/nonexistent_probe.json"]}}},
            9005: {"id": 9005, "plan_id": 999, "name": "SBX-GATE-DEP", "node_type": "gate",
                   "status": "blocked", "dependencies": [9001, 9006],
                   "metadata": {"gate_criteria": {
                       "artifacts": ["docs/autonomy/IN20_G21_autonomy_acceptance.md"]}}},
            9006: {"id": 9006, "plan_id": 999, "name": "SBX-UP2", "node_type": "task",
                   "status": "in-flight", "dependencies": [], "metadata": {}},
        }
        roots = [str(REPO_ROOT)]
        decisions = {nid: gar.evaluate_gate(n, nodes, roots) for nid, n in nodes.items()
                     if n["node_type"] == "gate"}
        assert decisions[9002].action == gar.ACTION_RELEASE
        assert decisions[9003].action == gar.ACTION_HOLD_OWNER_ONLY
        assert decisions[9004].action == gar.ACTION_WAIT
        assert decisions[9004].reason == "artifact_missing"
        assert decisions[9005].action == gar.ACTION_WAIT
        assert decisions[9005].reason == "upstream_pending"


@needs_psql
class TestLiveLedgerConsistency:
    """C5 判据的现场复核（只读；库不可达则跳过）。"""

    def _query(self, sql: str):
        out = subprocess.run(
            [PSQL, "-X", "-q", "-tA", "-F", "|", "-h", "/var/run/postgresql",
             "-U", "postgres", "-d", "tensorplatform", "-c", sql],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            pytest.skip(f"psql 不可用: {out.stderr.strip()[:120]}")
        return [line.split("|") for line in out.stdout.strip().splitlines() if line]

    def test_pad_four_terminal_and_booked(self):
        ids = ",".join(str(i) for i in acc.PAD_MISSIONS)
        statuses = {int(r[0]): r[1] for r in
                    self._query(f"SELECT id, status FROM tensor_dev_missions WHERE id IN ({ids})")}
        ledger = {int(r[0]): int(r[1]) for r in
                  self._query("SELECT mission_id, count(*) FROM tensor_capability_deltas"
                              f" WHERE mission_id IN ({ids}) GROUP BY mission_id")}
        assert acc.pad_four_verdict(statuses, ledger)["ok"] is True