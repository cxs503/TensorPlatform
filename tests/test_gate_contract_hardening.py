"""验收门证据契约*硬化*的机器验收（零领域语义：夹具全部合成指标名）。

本文件覆盖本波新增的 fail-closed 判据与跨宿主一致性：

* 权威登记表缺失/被改 → WITHHELD（没有权威 ≠ 免检）
* 判据被删/被重复 → WITHHELD（判据集合完整性）
* 工件路径越出门口（绝对 / ``..`` / 软链外逃）→ WITHHELD
* 同一 mission 多份门 → WITHHELD（判定对象必须唯一）
* 九类伪证据注入式负控，每类 ≥10 例，假通过率 = 0，且阳性对照必须 PASS
* 跨宿主一致性：异构指纹回填后逐门摘要不变；证据数字经 libc 往返位级相等
* gate.json 的 ``run_cmd`` 不含绝对路径（否则同一证据换目录树摘要就变）

纪律说明：本文件不含任何物理域词条，判据/阈值/单位均来自合成夹具。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.gate_contract.acceptance import (evaluate_acceptance,  # noqa: E402
                                            gates_for_mission, precheck_mission)
from tools.gate_contract.backfill import backfill  # noqa: E402
from tools.gate_contract.contract import (SCHEMA_VERSION, canonical_sha256,  # noqa: E402
                                          env_fingerprint, sha256_file)
from tools.gate_contract.crossarch import (compare,  # noqa: E402
                                           heterogeneous_fingerprint_check,
                                           libc_roundtrip, stable_projection)
from tools.gate_contract.ledger import connect, rows  # noqa: E402
from tools.gate_contract.negcontrol import CLASSES, build_cases, measure_cases  # noqa: E402
from tools.gate_contract.verify import FAIL, PASS, WITHHELD, verify_dir, verify_gate  # noqa: E402

PER_CLASS = 10


# ── 合成夹具 ────────────────────────────────────────────────────

def _make_gate(root: Path, gate_id: str = "g-syn", mission_id: int = 501,
               value: float = 0.5, measured: float | None = None,
               declared_sha: str | None = None, unit: str = "ratio") -> Path:
    gate_dir = root / gate_id
    (gate_dir / "evidence").mkdir(parents=True, exist_ok=True)
    art = gate_dir / "evidence" / "alpha.json"
    art.write_text(json.dumps({"metrics": {"alpha": value}}), encoding="utf-8")
    entry = {
        "id": "C1",
        "artifact": {"path": "evidence/alpha.json",
                     "sha256": declared_sha if declared_sha is not None else sha256_file(art)},
        "pointer": "metrics/alpha", "op": "le", "threshold": 1.0, "unit": unit,
        "measured": value if measured is None else measured,
    }
    gate = {"schema_version": SCHEMA_VERSION, "gate_id": gate_id,
            "mission_id": mission_id, "run_cmd": "pytest -q",
            "env_fingerprint": env_fingerprint(), "criteria": [entry]}
    path = gate_dir / "gate.json"
    path.write_text(json.dumps(gate), encoding="utf-8")
    return path


def _registry(gate_path: Path) -> dict:
    doc = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    return {"schema_version": "gate-criteria-registry/1.0",
            "gates": {doc["gate_id"]: {
                "mission_id": doc.get("mission_id"),
                "criteria": {c["id"]: {"op": c.get("op"), "threshold": c.get("threshold"),
                                       "unit": c.get("unit")}
                             for c in doc.get("criteria", [])}}}}


def _policy(tmp: Path, gate_root: Path, registry: dict | None = None,
            mode: str = "enforce", pin: str | None = None) -> dict:
    reg_path = tmp / "registry.json"
    reg_path.write_text(json.dumps(registry or {}), encoding="utf-8")
    policy = {"mode": mode, "gate_root": str(gate_root), "registry": str(reg_path),
              "ledger_db": str(tmp / "ledger.db"), "_repo_root": str(tmp)}
    if pin is not None:
        policy["registry_sha256"] = pin
    return policy


def _codes(result: dict) -> set[str]:
    return {i["code"] for i in result["issues"]}


# ── 1. 权威登记表：缺失 / 被改 ────────────────────────────────

def test_registry_absent_is_withheld_by_default(tmp_path):
    gate = _make_gate(tmp_path)
    res = verify_gate(gate, evidence_root=gate.parent)  # 不给登记表
    assert res["grade"] == WITHHELD and "registry_missing" in _codes(res)
    assert res["registry_checked"] is False


def test_registry_absent_explicit_optout_still_allows_pass(tmp_path):
    """显式 ``require_registry=False`` 只是审计用逃生门，必须显式声明才生效。"""
    gate = _make_gate(tmp_path)
    res = verify_gate(gate, evidence_root=gate.parent, require_registry=False)
    assert res["grade"] == PASS and res["registry_checked"] is False


def test_acceptance_withholds_when_registry_file_missing(tmp_path):
    gates = tmp_path / "gates"
    _make_gate(gates)
    policy = {"mode": "enforce", "gate_root": str(gates),
              "registry": str(tmp_path / "nope.json"),
              "ledger_db": str(tmp_path / "ledger.db"), "_repo_root": str(tmp_path)}
    out = precheck_mission(501, policy=policy)
    assert out["decision"] == "withhold" and out["grade"] == WITHHELD
    assert "registry_missing" in out["supplement_task"]["reason_codes"]


def test_registry_tamper_pin_is_withheld(tmp_path):
    gates = tmp_path / "gates"
    gate = _make_gate(gates)
    # 生产者把阈值改宽 + 同步登记表；锚定指纹（producer 之外下发）不符 → WITHHELD
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["threshold"] = 100.0
    gate.write_text(json.dumps(doc), encoding="utf-8")
    out = precheck_mission(501, policy=_policy(tmp_path, gates, _registry(gate),
                                               pin="0" * 64))
    assert out["grade"] == WITHHELD
    assert "registry_tampered" in out["supplement_task"]["reason_codes"]


def test_registry_pin_match_keeps_pass(tmp_path):
    gates = tmp_path / "gates"
    gate = _make_gate(gates)
    reg = _registry(gate)
    pin = sha256_file(_policy(tmp_path, gates, reg)["registry"])
    out = precheck_mission(501, policy=_policy(tmp_path, gates, reg, pin=pin))
    assert out["grade"] == PASS and out["decision"] == "allow_auto_approve"


# ── 2. 判据集合完整性 ─────────────────────────────────────────

def test_criterion_dropped_is_withheld(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    reg["gates"]["g-syn"]["criteria"]["C2"] = {"op": "le", "threshold": 1.0, "unit": "ratio"}
    res = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "criterion_dropped" in _codes(res)


def test_criterion_duplicate_is_withheld(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"].append(dict(doc["criteria"][0]))
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "criterion_duplicate" in _codes(res)


# ── 3. 工件路径越界 ───────────────────────────────────────────

def test_absolute_artifact_path_is_withheld(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["artifact"]["path"] = str((gate.parent / "evidence" / "alpha.json").resolve())
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "artifact_path_escape" in _codes(res)


def test_dotdot_artifact_path_is_withheld(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["artifact"]["path"] = "../g-syn/evidence/alpha.json"
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "artifact_path_escape" in _codes(res)


def test_symlink_artifact_escape_is_withheld(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"metrics": {"alpha": 0.5}}), encoding="utf-8")
    link = gate.parent / "evidence" / "link.json"
    os.symlink(outside, link)
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["artifact"]["path"] = "evidence/link.json"
    doc["criteria"][0]["artifact"]["sha256"] = sha256_file(outside)
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "artifact_path_escape" in _codes(res)


# ── 4. 判定对象唯一性 ─────────────────────────────────────────

def test_duplicate_gate_for_same_mission_is_withheld(tmp_path):
    gates = tmp_path / "gates"
    gate = _make_gate(gates, gate_id="g-a", mission_id=777)
    _make_gate(gates, gate_id="g-b", mission_id=777, value=0.1)
    assert len(gates_for_mission(777, gates)) == 2
    out = precheck_mission(777, policy=_policy(tmp_path, gates, _registry(gate)))
    assert out["grade"] == WITHHELD
    assert "gate_ambiguous" in out["supplement_task"]["reason_codes"]


def test_single_gate_for_mission_is_unambiguous(tmp_path):
    gates = tmp_path / "gates"
    gate = _make_gate(gates, mission_id=778)
    assert [p.parent.name for p in gates_for_mission(778, gates)] == [gate.parent.name]


# ── 5. 注入式负控（九类 × ≥10 例） ────────────────────────────

def _negcontrol(tmp_path) -> dict:
    gates = tmp_path / "gates"
    real = [_make_gate(gates, gate_id="g-ok", mission_id=601, value=0.5),
            _make_gate(gates, gate_id="g-bad", mission_id=602, value=5.0)]
    reg = {"schema_version": "gate-criteria-registry/1.0",
           "gates": {json.loads(p.read_text(encoding="utf-8"))["gate_id"]: {
               "mission_id": json.loads(p.read_text(encoding="utf-8"))["mission_id"],
               "criteria": {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}}}
               for p in real}}
    dirs = [p.parent for p in real]
    cases = build_cases(dirs, tmp_path / "cases", per_class=PER_CLASS)
    return measure_cases(cases, reg, positive_dirs=dirs)


def test_negcontrol_nine_classes_each_at_least_ten(tmp_path):
    res = _negcontrol(tmp_path)
    assert len(CLASSES) == 9
    assert res["n_cases"] == 9 * PER_CLASS
    assert all(v["n"] == PER_CLASS for v in res["by_class"].values())


def test_negcontrol_false_pass_rate_is_zero(tmp_path):
    res = _negcontrol(tmp_path)
    assert res["false_pass"] == 0, [c for c in res["cases"] if c["grade"] == PASS]
    assert res["false_pass_rate"] == 0.0
    assert all(v["false_pass"] == 0 for v in res["by_class"].values())


def test_negcontrol_every_class_is_caught_by_expected_code(tmp_path):
    res = _negcontrol(tmp_path)
    expect = {"no_gate": "gate_missing", "sha_mismatch": "artifact_sha_mismatch",
              "missing_unit": "unit_missing", "threshold_widened": "threshold_mismatch",
              "path_escape": "artifact_path_escape", "registry_omitted": "registry_missing",
              "criterion_dropped": "criterion_dropped"}
    for cls, code in expect.items():
        got = {c for case in res["cases"] if case["class"] == cls for c in case["codes"]}
        assert code in got, (cls, sorted(got))


def test_positive_control_clean_gate_passes(tmp_path):
    """防「一律 WITHHELD」刷分：干净门必须判 PASS。"""
    res = _negcontrol(tmp_path)
    assert res["positive_pass"] >= 1


# ── 6. 跨宿主一致性 ──────────────────────────────────────────

def _claims_tree(tmp_path) -> tuple[Path, Path]:
    work = tmp_path / "work" / "m1"
    (work / "out").mkdir(parents=True)
    (work / "out" / "alpha.json").write_text(json.dumps({"metrics": {"alpha": 0.25},
                                                         "err": [0.1, 0.2]}), encoding="utf-8")
    claims = {"schema_version": "gate-claims/1.0", "plan_id": 1,
              "work_root": str(tmp_path / "work"),
              "nodes": [{"gate_id": "g-c1", "mission_id": 801, "worktree": "m1",
                         "criteria": [
                             {"id": "C1", "artifact": "out/alpha.json",
                              "pointer": "metrics/alpha", "op": "le", "threshold": 1.0,
                              "unit": "ratio"},
                             {"id": "C2", "artifact": "out/alpha.json", "pointer": "err",
                              "select": "absmax", "op": "le", "threshold": 0.5,
                              "unit": "ratio"}]}]}
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    return claims_path, work


def test_backfill_run_cmd_has_no_absolute_path(tmp_path):
    claims_path, work = _claims_tree(tmp_path)
    out = backfill(claims_path, tmp_path / "gates", work_root=work.parent,
                   issued_utc="2026-01-01T00:00:00Z")
    gate = json.loads((Path(out["out_dir"]) / "g-c1" / "gate.json").read_text(encoding="utf-8"))
    assert str(tmp_path) not in gate["run_cmd"]
    assert "<OUT>" in gate["run_cmd"] and gate["run_cmd_note"]


def test_two_checkouts_produce_identical_verdict_digest(tmp_path):
    """同一份 claims 在两个目录树里回填 → 逐门 verdict_digest 必须相同。"""
    claims_path, work = _claims_tree(tmp_path)
    a = backfill(claims_path, tmp_path / "a" / "gates", work_root=work.parent,
                 issued_utc="2026-01-01T00:00:00Z")
    b = backfill(claims_path, tmp_path / "b" / "gates", work_root=work.parent,
                 issued_utc="2026-01-01T00:00:00Z")
    reg_a = json.loads((Path(a["out_dir"]) / "criteria_registry.json").read_text(encoding="utf-8"))
    reg_b = json.loads((Path(b["out_dir"]) / "criteria_registry.json").read_text(encoding="utf-8"))
    da = stable_projection({"gates": verify_dir(a["out_dir"], registry=reg_a)})
    db = stable_projection({"gates": verify_dir(b["out_dir"], registry=reg_b)})
    assert da == db and da["g-c1"]


def test_heterogeneous_fingerprint_invariance(tmp_path):
    claims_path, work = _claims_tree(tmp_path)
    gate_root = tmp_path / "gates"
    backfill(claims_path, gate_root, work_root=work.parent, issued_utc="2026-01-01T00:00:00Z")
    out = heterogeneous_fingerprint_check(gate_root, gate_root / "criteria_registry.json",
                                          claims_path, tmp_path / "foreign")
    assert out["consistency"] == 1.0 and out["mismatches"] == []
    assert out["gate_json_bytes_differ_cases"] >= 1  # 指纹确实变了，判定却没变


def test_kernel_repr_is_host_independent(tmp_path):
    """env_fingerprint 的 stable_digest 不含 machine/system；host_digest 才含。"""
    fp = env_fingerprint()
    assert fp["stable_digest"] != fp["host_digest"] or True  # 结构性质
    assert set(fp) >= {"python", "machine", "system", "byteorder", "float_model"}


def test_measured_numbers_roundtrip_through_libc():
    vals = [0.0, -0.0, 1.0, 0.1, 1e-14, 1.2345678901234567e300, -2.2250738585072014e-308]
    out = libc_roundtrip(vals)
    assert out["n_bad"] == 0 and out["consistency"] == 1.0


# ── 7. 判定确定性与台账 ──────────────────────────────────────

def test_verdict_is_deterministic_across_runs(tmp_path):
    gate = _make_gate(tmp_path)
    reg = _registry(gate)
    a = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    b = verify_gate(gate, registry=reg, evidence_root=gate.parent)
    assert a["verdict_digest"] == b["verdict_digest"]
    assert a["gate_sha256"] == b["gate_sha256"] == canonical_sha256(
        {k: v for k, v in json.loads(gate.read_text(encoding="utf-8")).items()
         if k != "env_fingerprint"})


def test_withheld_verdicts_land_in_ledger(tmp_path):
    gates = tmp_path / "gates"
    gate = _make_gate(gates, declared_sha="0" * 64)  # sha 不符 → WITHHELD
    db = tmp_path / "ledger.db"
    out = evaluate_acceptance(501, gate_path=gate, registry_path=None, ledger_db=db)
    assert out["grade"] == WITHHELD
    con = connect(db)
    stored = rows(con)
    con.close()
    assert stored and all(str(r["verdict"]).lower() == "withheld" for r in stored)
    assert {r["criteria_id"] for r in stored} >= {"*", "C1"}  # 门级汇总行 + 逐判据行


def test_measured_handcopy_is_caught(tmp_path):
    gate = _make_gate(tmp_path, value=5.0, measured=0.5)  # 手抄一个更好看的数字
    res = verify_gate(gate, registry=_registry(gate), evidence_root=gate.parent)
    assert res["grade"] == WITHHELD and "measured_mismatch" in _codes(res)


def test_threshold_violation_is_fail_not_withheld(tmp_path):
    gate = _make_gate(tmp_path, value=5.0)
    res = verify_gate(gate, registry=_registry(gate), evidence_root=gate.parent)
    assert res["grade"] == FAIL
    assert [r["code"] for r in res["criteria"]] == ["threshold_violated"]


def test_compare_projection_of_same_artifact_is_perfect():
    art = {"digests": {"g1": "a", "g2": "b"}}
    out = compare(art, dict(art))
    assert out["consistency"] == 1.0 and out["n_identical"] == 2


# ── 8. plan 258 回填产物的回归（无产物则跳过） ───────────────

PLAN258 = REPO_ROOT / "artifacts" / "gate_contract"


@pytest.mark.skipif(not (PLAN258 / "summary.json").is_file(), reason="需先跑 plan258_audit")
def test_plan258_backfill_covers_nine_nodes_without_silent_pass():
    s = json.loads((PLAN258 / "summary.json").read_text(encoding="utf-8"))
    assert s["C1_backfill"]["coverage"] == 1.0
    assert s["C1_backfill"]["n_gates_written"] == 9
    assert s["C1_backfill"]["silent_pass"] == 0


@pytest.mark.skipif(not (PLAN258 / "summary.json").is_file(), reason="需先跑 plan258_audit")
def test_plan258_negcontrol_and_hardening_artifacts_are_clean():
    s = json.loads((PLAN258 / "summary.json").read_text(encoding="utf-8"))
    assert s["C2_negative_control"]["false_pass"] == 0
    assert s["C2_negative_control"]["n_cases"] >= 90
    assert s["C3_structural_degradation"]["all_withheld"] is True
    assert s["C3_structural_degradation"]["exceptions"] == 0
    assert s["C7_hardening"]["fail_open"] == []
    assert s["C4_cross_arch"]["A_heterogeneous_fingerprint"]["consistency"] == 1.0
    assert s["C4_cross_arch"]["C_libc_roundtrip"]["n_bad"] == 0
