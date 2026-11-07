"""能力台账消费者契约门 + 已交付声明复现门的验收用例（m5352）。

覆盖两单的机器可裁判据：
A) 4 消费者 sha256 相等 / 重定位不抛异常且降级 DEGRADED / 负例红灯 / schema 不符红；
B) 24 单分层抽样 + 判据解析 + 干净 venv 复跑 + 复现率 95%CI + 五类根因账本 +
   注入篡改必判不可复现且归入真实数值漂移 + 抽样清单 sha256 落盘。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402
from tools.audit import ledger_contract_gate as GATE  # noqa: E402
from tools.audit import report_gate_ledger as RGL  # noqa: E402

LEDGER = ROOT / LP.LEDGER_REL
RESOLVED = ROOT / LP.RESOLVED_REL
PIN = ROOT / LP.PIN_REL


# ── 夹具：把一份自洽的台账世界造到 tmp 根下 ──────────────────────────────
def _make_world(tmp: Path, *, mutate: bool = False, schema: str | None = None) -> Path:
    d = tmp / "artifacts" / "capability_ledger"
    d.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if mutate:
        ledger["capabilities"][0]["claimed_level"] = "NOT_SUPPORTED"  # 内容改动
    if schema is not None:
        ledger["schema_version"] = schema
    (d / "capability_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(RESOLVED, d / "capability_ledger_resolved.json")
    if not mutate:
        # pin 必须指向**当前内容**的 sha256（等价于合法变更后重 pin）
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        pin["sha256"] = LP.sha256_file(d / "capability_ledger.json")
        pin["schema_version"] = ledger["schema_version"]
        (d / "ledger_contract.json").write_text(json.dumps(pin, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        # 漂移场景：pin 仍是原台账的指纹（有人改了内容没重 pin）
        shutil.copy2(PIN, d / "ledger_contract.json")
    return tmp


# ══════════════════ A. 消费者契约门 ══════════════════
def test_four_consumers_resolve_same_sha256():
    """C1：4 消费者（工具/CLI/health/报告 gate）解析到同一 sha256。"""
    rep = LP.contract_report()
    assert rep["contract_ok"] is True, rep["violations"]
    shas = {n: c["sha256"] for n, c in rep["consumers"].items()}
    assert set(rep["consumers"]) == {"audit_capabilities", "cli", "health", "report_gate"}
    assert len(set(shas.values())) == 1
    assert shas["audit_capabilities"] == LP.sha256_file(LEDGER)
    assert all(c["error"] is None for c in rep["consumers"].values())


def test_gate_cli_pass_and_artifact_written(tmp_path):
    """CI 门在真实仓上 PASS，并把判据数字落盘。"""
    res = GATE.run()
    assert res["verdict"] == "PASS", res["failed"]
    assert res["checks"]["C1_consumer_sha256_equal"] is True
    assert res["checks"]["C2_schema_version_registered"] is True
    assert res["checks"]["C3_no_ledger_drift"] is True
    out = tmp_path / "run.json"
    assert GATE.main(["--check", "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["verdict"] == "PASS" and saved["canonical_sha256"] == res["canonical_sha256"]


def test_health_field_schema_and_degraded_never_raises():
    """健康端点字段键集固定，且降级时也不抛异常。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "capability_health_under_test", ROOT / "backend" / "api" / "capability_health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    field = mod.ledger_field()
    assert set(field) == set(mod.FIELD_KEYS)
    assert len(field["sha256"]) == 64
    assert field["capabilities"] >= 20
    assert isinstance(field["unverified"], int) and isinstance(field["overclaim"], int)


def test_relocation_drill_identical_copy_keeps_identity(tmp_path, monkeypatch):
    """重定位演练①：把台账搬走（内容不变）→ 仍解析到同一 sha256，不抛异常。"""
    d = tmp_path / "relocated"
    d.mkdir()
    shutil.copy2(LEDGER, d / "ledger.json")
    monkeypatch.setenv("TP_CAP_LEDGER", str(d / "ledger.json"))
    ref = LP.resolve_ledger()
    assert ref["sha256"] == LP.sha256_file(LEDGER)
    assert ref["relocated"] is True
    assert ref["status"] == LP.STATUS_OK, ref["reasons"]


def test_relocation_drill_empty_root_degrades_without_exception(tmp_path):
    """重定位演练②：台账根本不存在 → DEGRADED + 具名原因，绝不抛未捕获异常。"""
    ref = LP.resolve_ledger(root=tmp_path)
    assert ref["status"] == LP.STATUS_DEGRADED
    assert "NO_LEDGER" in ref["reasons"]
    view = LP.ledger_view(ref)
    assert view["capabilities"] == 0 and view["sha256"] is None
    # 工具消费者同样只降级不炸
    import tools.audit.ledger_consumer_audit_tool as TOOL
    text = TOOL.render(ref)
    assert "DEGRADED" in text


def test_negative_control_schema_mismatch_turns_red(tmp_path, monkeypatch):
    """负例：schema_version 不在登记表内 → 契约红灯（CI 退出码 1）。"""
    world = _make_world(tmp_path, schema="9.9")
    monkeypatch.setenv("TP_CAP_LEDGER", str(world / "artifacts" / "capability_ledger" / "capability_ledger.json"))
    ref = LP.resolve_ledger()
    assert ref["status"] == LP.STATUS_DEGRADED
    assert "SCHEMA_VERSION_MISMATCH" in ref["reasons"]
    rep = LP.contract_report()
    assert rep["contract_ok"] is False
    assert any("SCHEMA_VERSION_MISMATCH" in v for v in rep["violations"])
    assert GATE.run()["verdict"] == "FAIL"


def test_negative_control_drift_alarm_on_tampered_ledger(tmp_path):
    """负例：台账内容被改但没重 pin → LEDGER_DRIFT 告警 + 门红灯。"""
    world = _make_world(tmp_path, mutate=True)
    ref = LP.resolve_ledger(root=world)
    assert "LEDGER_DRIFT" in ref["reasons"]
    assert any(d["against"] == "pin" for d in ref["drift"])
    assert ref["status"] == LP.STATUS_DEGRADED
    res = GATE.run(str(world))
    assert res["verdict"] == "FAIL"
    assert res["checks"]["C3_no_ledger_drift"] is False


def test_report_gate_consumer_flags_overclaim_against_ledger():
    """报告 gate 消费者：正文断言强于台账对账等级 → 违规；与台账一致 → 放行。"""
    levels = RGL._ledger_levels(RGL.ledger_ref())
    assert levels, "台账必须给出可对账的能力等级"
    # 选一个对账等级尚非 REAL 的能力：断言 REAL 必违规，断言本等级必放行
    target = next(((cid, v) for cid, v in levels.items()
                   if RGL.LEVEL_RANK.get(str(v.get("reconciled_level") or v.get("claimed_level")).upper(), -1) < 4),
                  None)
    assert target is not None, "台账中应至少有一项未获 REAL（否则本负控无意义）"
    cid, lv = target
    basis = str(lv.get("reconciled_level") or lv.get("claimed_level"))
    bad = f"本报告结论：`{cid}` 证据等级 REAL。\n"
    good = f"本报告结论：`{cid}` 证据等级 {basis}。\n"
    res_bad = RGL.ledger_gate(bad)
    assert res_bad["ok"] is False
    assert res_bad["violations"] and res_bad["violations"][0]["capability"] == cid
    assert res_bad["violations"][0]["ledger_level"] == basis
    res_good = RGL.ledger_gate(good)
    assert res_good["violations"] == []
    assert res_good["ok"] is True
    assert res_good["ledger"]["sha256"] == LP.sha256_file(LEDGER)


# ══════════════════ B. 已交付声明复现门 ══════════════════
def _rpg():
    """复现门 B 单的模块（惰性导入：A 单用例不因 B 未就绪被整模块 skip）。"""
    return pytest.importorskip("tools.replay_gate.gate")


def test_replay_sample_stratified_24_and_manifest_sha(tmp_path):
    RPG = _rpg()
    """抽样：6 域 × 4 = 24 单，固定 seed 可复现，清单 sha256 落盘。"""
    a = RPG.build_sample(seed=20261106)
    b = RPG.build_sample(seed=20261106)
    assert a["n"] == 24
    assert a["sha256"] == b["sha256"]
    per = {}
    for c in a["claims"]:
        per[c["domain"]] = per.get(c["domain"], 0) + 1
    assert per == {"resistance": 4, "structure": 4, "seakeeping": 4, "lbm": 4, "fem": 4, "dem": 4}
    out = tmp_path / "sample_manifest.json"
    RPG.write_manifest(a, out)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["sha256"] == a["sha256"]
    assert saved["n"] == 24


def test_replay_run_clean_venv_rate_ci_and_root_cause_ledger(tmp_path):
    RPG = _rpg()
    """主跑：干净 venv 复跑 → 复现率 + 95%CI + 五类根因账本齐备。"""
    res = RPG.run_gate(seed=20261106, out_dir=tmp_path, use_clean_venv=True)
    assert res["denominator"] == 24
    assert 0 <= res["numerator"] <= 24
    lo, hi = res["ci95"]
    assert 0.0 <= lo <= res["reproduction_rate"] <= hi <= 1.0
    assert res["root_cause_ledger"].keys() >= {
        "ENV_DEPENDENCY_MISSING", "RANDOM_UNSEEDED", "ARTIFACT_MISSING",
        "CRITERION_NOT_EXECUTABLE", "TRUE_NUMERICAL_DRIFT"}
    counted = sum(v["count"] for v in res["root_cause_ledger"].values())
    assert counted == res["denominator"] - res["numerator"]
    assert (tmp_path / "sample_manifest.json").is_file()
    assert (tmp_path / "replay_report.json").is_file()


def test_replay_tampered_claim_is_numerical_drift(tmp_path):
    RPG = _rpg()
    """注入篡改：申报值被改 → 必判不可复现，且归入真实数值漂移类。"""
    res = RPG.run_gate(seed=20261106, out_dir=tmp_path, use_clean_venv=True,
                       tamper="first_within:resistance")
    tampered = res["tamper"]["claims"]
    assert tampered, "篡改注入必须落到具体 claim 上"
    for cid in tampered:
        row = next(r for r in res["results"] if r["id"] == cid)
        assert row["reproduced"] is False
        assert row["root_cause"] == "TRUE_NUMERICAL_DRIFT"
    assert res["tamper"]["detected"] is True
    assert res["verdict"] == "FAIL"  # 注入篡改后门必须红灯


def test_replay_root_cause_classification_negative_controls(tmp_path):
    """五类根因的负控：每类都能被单独造出来并正确归类。"""
    RPG = _rpg()
    cases = {
        "ENV_DEPENDENCY_MISSING": {"id": "x-env", "domain": "lbm", "kernel": "numpy_only",
                                   "params": {}, "declared": 1.0, "tol_rel": 0.03,
                                   "requires": ["numpy_is_fake_dep"]},
        "CRITERION_NOT_EXECUTABLE": {"id": "x-crit", "domain": "fem", "kernel": "no_such_kernel",
                                     "params": {}, "declared": 1.0, "tol_rel": 0.03},
        "ARTIFACT_MISSING": {"id": "x-art", "domain": "structure", "kernel": "plate_buckling_sigma_cr",
                             "params": {"t": 0.012, "b": 0.6}, "declared": 1.0, "tol_rel": 0.03,
                             "artifact": "does/not/exist.json"},
    }
    for expected, claim in cases.items():
        row = RPG.replay_claim(claim, use_clean_venv=True)
        assert row["reproduced"] is False
        assert row["root_cause"] == expected, (expected, row)
    # 随机未种子：stochastic 且 seed=None，两次跑给出不同值
    stoch = {"id": "x-rnd", "domain": "dem", "kernel": "stochastic_probe", "params": {},
             "declared": 0.5, "tol_rel": 0.03, "stochastic": True, "seed": None}
    row = RPG.replay_claim(stoch, use_clean_venv=True)
    assert row["root_cause"] in ("RANDOM_UNSEEDED", "TRUE_NUMERICAL_DRIFT")
    assert row["reproduced"] is False