"""gate.json 证据契约的机器验收（零领域语义：夹具全部用合成指标名）。

覆盖：契约结构 → 工件存在性 → sha256 复算 → 阈值判定 → 三值降级 →
判定台账回溯 → 注入式负控假通过率 → dev_acceptance 前门 → 跨架构摘要不变 →
回填器。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.gate_contract.acceptance import (build_supplement_task,  # noqa: E402
                                            evaluate_acceptance, precheck_mission)
from tools.gate_contract.backfill import backfill  # noqa: E402
from tools.gate_contract.contract import (SCHEMA_VERSION, canonical_sha256,  # noqa: E402
                                          env_fingerprint, sha256_file)
from tools.gate_contract.ledger import append_verdict, connect, trace_back  # noqa: E402
from tools.gate_contract.negcontrol import (CLASSES, build_cases,  # noqa: E402
                                            measure_cases)
from tools.gate_contract.verify import FAIL, PASS, WITHHELD, verify_gate  # noqa: E402


# ── 合成夹具 ────────────────────────────────────────────────────

def _artifact(dirpath: Path, doc: dict, name: str = "alpha.json") -> tuple[str, str]:
    (dirpath / "evidence").mkdir(parents=True, exist_ok=True)
    path = dirpath / "evidence" / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return f"evidence/{name}", sha256_file(path)


def _gate(tmp_path: Path, *, doc: dict | None = None, value: float = 0.5,
          op: str = "le", threshold=1.0, unit: str | None = None,
          measured=None, pointer: str = "metrics/alpha",
          declared_sha: str | None = None, gate_id: str = "g-synthetic",
          mission_id: int = 7) -> Path:
    body = {"metrics": {"alpha": value},
            "series": [{"e": 0.1}, {"e": 0.4}]} if doc is None else doc
    rel, sha = _artifact(tmp_path, body)
    entry = {
        "id": "C1",
        "artifact": {"path": rel, "sha256": declared_sha if declared_sha is not None else sha},
        "pointer": pointer,
        "op": op,
        "threshold": threshold,
        "unit": "ratio" if unit is None else unit,
        "measured": value if measured is None else measured,
    }
    gate = {"schema_version": SCHEMA_VERSION, "gate_id": gate_id, "mission_id": mission_id,
            "run_cmd": "pytest -q tests/test_gate_contract.py",
            "env_fingerprint": env_fingerprint(), "criteria": [entry]}
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate), encoding="utf-8")
    return path


def _registry(**overrides) -> dict:
    crit = {"op": "le", "threshold": 1.0, "unit": "ratio"}
    crit.update(overrides)
    return {"schema_version": "gate-criteria-registry/1.0",
            "gates": {"g-synthetic": {"mission_id": 7, "criteria": {"C1": crit}}}}


# ── 复算判定 ────────────────────────────────────────────────────

def test_valid_gate_passes(tmp_path):
    res = verify_gate(_gate(tmp_path), registry=_registry())
    assert res["grade"] == PASS, res["issues"]
    assert res["criteria"][0]["status"] == "pass"
    assert res["verdict_digest"] and res["gate_sha256"]


def test_threshold_violation_fails(tmp_path):
    res = verify_gate(_gate(tmp_path, value=5.0), registry=_registry())
    assert res["grade"] == FAIL
    assert res["criteria"][0]["code"] == "threshold_violated"


def test_missing_gate_is_withheld(tmp_path):
    res = verify_gate(tmp_path / "absent.json", registry=_registry())
    assert res["grade"] == WITHHELD
    assert res["issues"][0]["code"] == "gate_missing"


def test_sha_mismatch_is_withheld(tmp_path):
    res = verify_gate(_gate(tmp_path, declared_sha="0" * 64), registry=_registry())
    assert res["grade"] == WITHHELD
    assert "artifact_sha_mismatch" in {i["code"] for i in res["issues"]}


def test_missing_unit_is_withheld(tmp_path):
    res = verify_gate(_gate(tmp_path, unit=""), registry=_registry())
    assert res["grade"] == WITHHELD
    assert "unit_missing" in {i["code"] for i in res["issues"]}


def test_pointer_unresolved_is_withheld(tmp_path):
    res = verify_gate(_gate(tmp_path, pointer="metrics/beta"), registry=_registry())
    assert res["grade"] == WITHHELD
    assert "pointer_unresolved" in {i["code"] for i in res["issues"]}


def test_hand_copied_number_is_withheld(tmp_path):
    res = verify_gate(_gate(tmp_path, value=0.5, measured=0.123), registry=_registry())
    assert res["grade"] == WITHHELD
    assert "measured_mismatch" in {i["code"] for i in res["issues"]}


def test_log_only_is_withheld(tmp_path):
    gate = _gate(tmp_path)
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"] = [{"id": "C1", "log": "looks fine", "unit": "ratio"}]
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=_registry())
    assert res["grade"] == WITHHELD


def test_widened_threshold_is_withheld(tmp_path):
    """阈值被改宽：判定必须由权威登记表复算，不信 gate.json 自述。"""
    gate = _gate(tmp_path, value=5.0, threshold=1.0)
    ok_registry = _registry(threshold=1.0)
    assert verify_gate(gate, registry=ok_registry)["grade"] == FAIL

    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["threshold"] = 100.0  # 改宽
    gate.write_text(json.dumps(doc), encoding="utf-8")
    res = verify_gate(gate, registry=ok_registry)
    assert res["grade"] == WITHHELD
    assert "threshold_mismatch" in {i["code"] for i in res["issues"]}


def test_verdict_digest_is_architecture_invariant(tmp_path, monkeypatch):
    """环境指纹变化（架构/主机）不得改变判定摘要——跨架构一致性的构造性保证。"""
    path = _gate(tmp_path)
    before = verify_gate(path, registry=_registry())
    import platform as _p
    monkeypatch.setattr(_p, "machine", lambda: "x86_64")
    monkeypatch.setattr(_p, "system", lambda: "Darwin")
    after = verify_gate(path, registry=_registry())
    assert before["verdict_digest"] == after["verdict_digest"]
    assert before["gate_sha256"] == after["gate_sha256"]


# ── 台账 ────────────────────────────────────────────────────────

def test_ledger_rows_are_sql_traceable(tmp_path):
    gate = _gate(tmp_path)
    res = verify_gate(gate, registry=_registry())
    db = tmp_path / "ledger.db"
    con = connect(db)
    append_verdict(con, res, run_cmd="pytest")
    con.commit()
    row = con.execute("SELECT mission_id, gate_id, criteria_id, verdict, artifact_sha256,"
                      " verdict_digest FROM gate_verdicts WHERE criteria_id='C1'").fetchone()
    assert row[0] == 7 and row[1] == "g-synthetic" and row[3] == "pass"
    assert row[4] == sha256_file(tmp_path / res["criteria"][0]["artifact"])
    assert row[5] == res["verdict_digest"]
    trace = trace_back(con, limit=20)
    assert trace["ratio"] == 1.0 and trace["traceable"] == trace["evidence_rows"] >= 1


# ── 负控 ────────────────────────────────────────────────────────

def test_negcontrol_false_pass_rate_is_zero(tmp_path):
    gates = []
    for i, value in enumerate((0.5, 5.0)):
        d = tmp_path / f"real_{i}"
        d.mkdir()
        (_gate(d, value=value, gate_id="g-synthetic")).rename(d / "gate.json")
        gates.append(d)
    cases = build_cases(gates, tmp_path / "cases", per_class=3)
    assert len(cases) == 3 * len(CLASSES)
    res = measure_cases(cases, _registry(), positive_dirs=gates)
    assert res["false_pass"] == 0, [c for c in res["cases"] if c["grade"] == PASS]
    assert res["n_cases"] == 3 * len(CLASSES)
    # 阳性对照：真门必须能被判 PASS（否则「一律 WITHHELD」也能骗过假通过率）
    assert res["positive_pass"] == 1 and res["positive_fail"] == 1


# ── dev_acceptance 前门 ─────────────────────────────────────────

def _registry_from_tree(root: Path) -> dict:
    """由门目录现场生成权威登记表（判据=算子/阈值/单位三元组）。"""
    reg = {"schema_version": "gate-criteria-registry/1.0", "gates": {}}
    for path in sorted(Path(root).glob("*/gate.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        reg["gates"][doc["gate_id"]] = {
            "mission_id": doc.get("mission_id"),
            "criteria": {c["id"]: {"op": c.get("op"), "threshold": c.get("threshold"),
                                   "unit": c.get("unit")} for c in doc.get("criteria", [])},
        }
    return reg


def _registry_for_gate(gate_path: Path) -> dict:
    doc = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    return {"schema_version": "gate-criteria-registry/1.0",
            "gates": {doc["gate_id"]: {
                "mission_id": doc.get("mission_id"),
                "criteria": {c["id"]: {"op": c.get("op"), "threshold": c.get("threshold"),
                                       "unit": c.get("unit")}
                             for c in doc.get("criteria", [])}}}}


def _policy(tmp_path: Path, mode: str = "enforce") -> dict:
    """策略必须带**权威登记表**：没有登记表 = 没有权威 → 恒等 WITHHELD（fail-closed）。"""
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(_registry_from_tree(tmp_path)), encoding="utf-8")
    return {"mode": mode, "gate_root": str(tmp_path), "registry": str(reg_path),
            "ledger_db": str(tmp_path / "ledger.db"), "_repo_root": str(tmp_path)}


def test_acceptance_blocks_on_withheld_and_files_supplement(tmp_path):
    gate_root = tmp_path / "gates"
    gate_root.mkdir()
    _gate(gate_root, declared_sha="0" * 64)  # sha 不符 → WITHHELD
    out = precheck_mission(7, policy=_policy(tmp_path))
    assert out["decision"] == "withhold" and out["approver_verdict"] == "escalate"
    task = out["supplement_task"]
    assert task["kind"] == "supplement_evidence" and task["mission_id"] == 7
    assert task["required"] and task["reason_codes"]


def test_acceptance_allows_pass_and_rejects_fail(tmp_path):
    gate_root = tmp_path / "gates"
    gate_root.mkdir()
    gate = _gate(gate_root, value=0.5)
    assert precheck_mission(7, policy=_policy(tmp_path))["decision"] == "allow_auto_approve"
    # 同一门内实测值越线 → 必须拒绝（证据齐但未达标）
    doc = json.loads(gate.read_text(encoding="utf-8"))
    doc["criteria"][0]["measured"] = 5.0
    gate.write_text(json.dumps(doc), encoding="utf-8")
    (_gate(gate_root, value=5.0)).replace(gate)
    out = precheck_mission(7, policy=_policy(tmp_path, mode="enforce"))
    assert out["grade"] == FAIL and out["decision"] == "reject"
    assert out["supplement_task"] is not None


def test_shadow_mode_never_blocks(tmp_path):
    gate_root = tmp_path / "gates"
    gate_root.mkdir()
    _gate(gate_root, declared_sha="0" * 64)
    out = precheck_mission(7, policy=_policy(tmp_path, mode="shadow"))
    assert out["decision"] == "allow_auto_approve"
    assert out["grade"] == WITHHELD


def test_unknown_mission_without_gate_is_withheld(tmp_path):
    out = precheck_mission(99, policy=_policy(tmp_path))
    assert out["decision"] == "withhold"
    assert out["supplement_task"]["reason_codes"] == ["gate_missing"]


def test_supplement_task_dedupe_is_stable():
    res = verify_gate(gate_doc=None, gate_json_text="{}")
    a = build_supplement_task(res, 7)
    b = build_supplement_task(res, 7)
    assert a["dedupe_key"] == b["dedupe_key"] and len(a["dedupe_key"]) == 16


def test_evaluate_acceptance_writes_ledger(tmp_path):
    gate = _gate(tmp_path)
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(_registry_for_gate(gate)), encoding="utf-8")
    db = tmp_path / "ledger.db"
    out = evaluate_acceptance(7, gate_path=gate, ledger_db=db, registry_path=reg_path)
    assert out["decision"] == "allow_auto_approve"
    con = connect(db)
    assert con.execute("SELECT COUNT(*) FROM gate_verdicts").fetchone()[0] >= 2


# ── 回填器 ──────────────────────────────────────────────────────

def test_backfill_writes_gate_and_registry(tmp_path):
    work = tmp_path / "work" / "m1"
    (work / "out").mkdir(parents=True)
    (work / "out" / "alpha.json").write_text(json.dumps({"metrics": {"alpha": 0.25}}),
                                             encoding="utf-8")
    claims = {"schema_version": "gate-claims/1.0", "plan_id": 1,
              "work_root": str(tmp_path / "work"),
              "nodes": [{"gate_id": "g-1", "mission_id": 11, "worktree": "m1",
                         "criteria": [{"id": "C1", "artifact": "out/alpha.json",
                                       "pointer": "metrics/alpha", "op": "le",
                                       "threshold": 1.0, "unit": "ratio"}]}]}
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    out = backfill(claims_path, tmp_path / "gates", issued_utc="2026-01-01T00:00:00Z")
    assert out["coverage"] == 1.0 and out["nodes"][0]["resolvable"] == 1
    registry = json.loads((tmp_path / "gates" / "criteria_registry.json").read_text())
    res = verify_gate(tmp_path / "gates" / "g-1" / "gate.json", registry=registry)
    assert res["grade"] == PASS
    gate = json.loads((tmp_path / "gates" / "g-1" / "gate.json").read_text())
    assert gate["criteria"][0]["measured"] == 0.25
    assert gate["criteria"][0]["artifact"]["sha256"] == sha256_file(
        tmp_path / "gates" / "g-1" / gate["criteria"][0]["artifact"]["path"])


def test_backfill_marks_unresolvable_instead_of_inventing_numbers(tmp_path):
    work = tmp_path / "work" / "m2"
    (work / "out").mkdir(parents=True)
    (work / "out" / "alpha.json").write_text(json.dumps({"metrics": {"alpha": 0.25}}),
                                             encoding="utf-8")
    claims = {"schema_version": "gate-claims/1.0", "plan_id": 1,
              "work_root": str(tmp_path / "work"),
              "nodes": [{"gate_id": "g-2", "mission_id": 12, "worktree": "m2",
                         "criteria": [{"id": "C1", "artifact": "out/alpha.json",
                                       "pointer": "metrics/absent", "op": "le",
                                       "threshold": 1.0, "unit": "ratio"}]}]}
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    backfill(claims_path, tmp_path / "gates")
    gate = json.loads((tmp_path / "gates" / "g-2" / "gate.json").read_text())
    assert "measured" not in gate["criteria"][0]
    assert gate["criteria"][0]["unresolved"].startswith("pointer_unresolved")
    registry = json.loads((tmp_path / "gates" / "criteria_registry.json").read_text())
    res = verify_gate(tmp_path / "gates" / "g-2" / "gate.json", registry=registry)
    assert res["grade"] == WITHHELD


# ── 零领域语义 ──────────────────────────────────────────────────

def test_new_modules_carry_no_domain_vocabulary():
    lexicon_path = REPO_ROOT / "tools" / "evidence_domain_lexicon.json"
    lexicon = json.loads(lexicon_path.read_text(encoding="utf-8"))
    terms = list(lexicon.get("ascii_terms", [])) + list(lexicon.get("cjk_terms", []))
    targets = list((REPO_ROOT / "tools" / "gate_contract").glob("*.py"))
    targets.append(REPO_ROOT / "backend" / "services" / "gate_contract_service.py")
    targets.append(Path(__file__).resolve())
    hits = []
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for term in terms:
            if term and term.lower() in text:
                hits.append((path.name, term))
    assert hits == [], hits
