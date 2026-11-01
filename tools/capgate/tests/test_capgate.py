"""capgate 自检：注册表 schema 门 / 学科域覆盖 / 5 类探针 / 对账 / 端到端产物。

跑法（仓库根目录）：
    python3 -m pytest tools/capgate/tests -q
全部离线：不起网络、不写 worktree 之外的路径（端到端用例写 tmp_path）。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "capgate"))

import capgate  # noqa: E402

REG = capgate.load_registry(str(ROOT / "tools/capgate/fixtures"
                                / "capabilities_snapshot_20261101.json"))


# --------------------------------------------------------------------------- #
# 1) schema 门
# --------------------------------------------------------------------------- #
def test_schema_gate_flags_every_unproven_real():
    findings = capgate.schema_findings(REG)
    n_real = sum(1 for c in REG["capabilities"].values()
                 if str(c.get("evidence")).upper() in capgate.NEEDS_PROBE)
    kinds = {f["kind"] for f in findings}
    assert len(findings) == n_real, "每个 REAL/WITHHELD 都必须被 schema 门点出"
    assert kinds == {"UNVERIFIED_EVIDENCE"}
    assert len(findings) >= 18  # 快照里 18 项 REAL + 0 WITHHELD


def test_schema_gate_rejects_dangling_probe_id():
    reg = json.loads(json.dumps(REG))
    first = next(k for k, v in reg["capabilities"].items() if v.get("evidence") == "REAL")
    reg["capabilities"][first]["verified_by"] = ["no_such_probe"]
    kinds = {f["kind"] for f in capgate.schema_findings(reg) if f["capability"] == first}
    assert kinds == {"DANGLING_PROBE"}


def test_schema_gate_accepts_registered_probe():
    reg = json.loads(json.dumps(REG))
    first = next(k for k, v in reg["capabilities"].items() if v.get("evidence") == "REAL")
    reg["capabilities"][first]["verified_by"] = ["hydro_identities"]
    assert not [f for f in capgate.schema_findings(reg) if f["capability"] == first]


# --------------------------------------------------------------------------- #
# 2) 9 学科域覆盖
# --------------------------------------------------------------------------- #
def test_domain_coverage_marks_unregistered_domains():
    cov = capgate.domain_coverage(REG)
    verdict = {r["domain"]: r["verdict"] for r in cov["rows"]}
    assert cov["n_domains"] == 9
    for d in ("耐波", "操纵", "结构"):
        assert verdict[d] == "UNREGISTERED"
    for d in ("振动噪声", "绿色航行", "冰区航行"):
        assert verdict[d] == "REGISTERED_ONLY", "只经 misc.stubs 承载，不应算可服务"
    assert verdict["阻力"] == "SERVABLE_REAL"
    assert verdict["推进"] == "SERVABLE_REAL"
    assert cov["summary"]["SERVABLE_REAL"] == 3
    assert cov["summary"]["REGISTERED_ONLY"] == 3
    assert cov["summary"]["UNREGISTERED"] == 3


def test_domain_coverage_respects_reachability():
    """声明可达但探针判 NOT_MOUNTED → 不得算 SERVABLE_REAL。"""
    reach = {f"{c}:/api/v1/x": "NOT_MOUNTED"
             for c in capgate.DOMAIN_MAP["阻力"]}
    cov = capgate.domain_coverage(REG, reach)
    assert {r["domain"]: r["verdict"] for r in cov["rows"]}["阻力"] == "REGISTERED_ONLY"


def test_unreachable_endpoint_yields_reach_conflict():
    reach = {"propeller.open_water:/api/v1/propeller/open-water": "NOT_MOUNTED"}
    conflicts = capgate.reach_conflicts(REG, reach)
    assert [c["capability"] for c in conflicts] == ["propeller.open_water"]
    assert conflicts[0]["declared_reachable"] is True


# --------------------------------------------------------------------------- #
# 3) 探针：物理/解析基准
# --------------------------------------------------------------------------- #
FX = capgate.fixture_payloads()


def test_propeller_probe_rejects_kq_kt_div_10():
    r = capgate.probe_propeller_series(FX["regress_propeller_kq_kt_div10"]["points"])
    assert r["verdict"] == "FAIL"
    assert r["metrics"]["ratio_spread"] < 1e-9


def test_propeller_probe_accepts_physical_series():
    r = capgate.probe_propeller_series(FX["synthetic_propeller_ok"]["points"])
    assert r["verdict"] == "PASS"
    assert r["metrics"]["KT_monotone_decreasing"] is True
    assert 0.0 < r["metrics"]["eta_o_range"][1] <= 1.0


def test_ittc57_probe_exact_on_line_and_rejects_41pct():
    ok = capgate.probe_friction_line(FX["synthetic_friction_ok"]["points"])
    bad = capgate.probe_friction_line(FX["regress_friction_plus41"]["points"])
    assert ok["verdict"] == "PASS" and ok["metrics"]["max_rel_err"] < 1e-9
    assert bad["verdict"] == "FAIL"
    assert abs(bad["metrics"]["max_rel_err"] - 0.41) < 1e-3


def test_hydro_identity_probe_passes_analytic_box():
    r = capgate.probe_hydro_identities(FX["synthetic_hydro_ok"]["payload"])
    assert r["verdict"] == "PASS"
    assert r["metrics"]["ddisp_ddraft_rel_err"] == pytest.approx(0.0, abs=1e-9)
    # 矩形船型 Cb≡1
    assert r["metrics"]["cb_max_rel_err"] < 1e-9


def test_hydro_identity_probe_catches_missing_rho_and_bad_derivative():
    r = capgate.probe_hydro_identities(FX["regress_hydro_mtc_style"]["payload"])
    assert r["verdict"] == "FAIL"
    assert r["metrics"]["disp_max_rel_err"] == pytest.approx(0.02439, abs=1e-4)


def test_hydro_identity_probe_catches_derivative_only_error():
    """恒等量全对、只有水线面积错 10% → 只有微-积分交叉项报警。"""
    p = json.loads(json.dumps(FX["synthetic_hydro_ok"]["payload"]))
    p["curves"]["Awp"] = [a * 1.10 for a in p["curves"]["Awp"]]
    r = capgate.probe_hydro_identities(p)
    assert r["verdict"] == "FAIL"
    assert r["metrics"]["ddisp_ddraft_rel_err"] == pytest.approx(1.0 - 1.0/1.10, abs=1e-6)


def test_gm_identity_probe():
    assert capgate.probe_gm_identity(FX["synthetic_gm_ok"]["payload"])["verdict"] == "PASS"
    assert capgate.probe_gm_identity(FX["regress_gm_mismatch"]["payload"])["verdict"] == "FAIL"


def test_hardcode_echo_probe():
    bad = capgate.probe_hardcode_echo(FX["regress_stub_echo"]["runs"])
    ok = capgate.probe_hardcode_echo(FX["synthetic_vary_ok"]["runs"])
    assert bad["verdict"] == "FAIL" and bad["metrics"]["distinct_fingerprints"] == 1
    assert ok["verdict"] == "PASS" and ok["metrics"]["distinct_fingerprints"] == 2


# --------------------------------------------------------------------------- #
# 4) 对账与状态机
# --------------------------------------------------------------------------- #
def test_reconcile_detects_declared_real_probe_fail():
    reg = {"capabilities": {"propeller.open_water": {"evidence": "REAL", "endpoints": []},
                            "misc.stubs": {"evidence": "STUB", "endpoints": []}}}
    probes = [capgate.probe_propeller_series(FX["regress_propeller_kq_kt_div10"]["points"]),
              dict(capgate.probe_hardcode_echo(FX["regress_stub_echo"]["runs"]),
                   capability="misc.stubs")]
    recon = {r["capability"]: r for r in capgate.reconcile(reg, probes)}
    assert recon["propeller.open_water"]["status"] == "DRIFT_DECLARED_REAL_PROBE_FAIL"
    assert recon["misc.stubs"]["status"] == "CONSISTENT"


def test_reconcile_marks_unverified_real_without_probe():
    recon = {r["capability"]: r for r in capgate.reconcile(REG, [])}
    assert recon["calc.resistance"]["status"] == "UNVERIFIED"
    assert sum(1 for r in recon.values() if r["status"] == "UNVERIFIED") == 18


def test_classify_status_mapping():
    assert capgate.classify_status(200) == "MOUNTED_OK"
    assert capgate.classify_status(404) == "NOT_MOUNTED"
    assert capgate.classify_status(405) == "NOT_MOUNTED"
    assert capgate.classify_status(422) == "MOUNTED_VALIDATION"
    assert capgate.classify_status(500) == "MOUNTED_ERROR"


# --------------------------------------------------------------------------- #
# 5) 端到端：产物 + 数字一致
# --------------------------------------------------------------------------- #
def test_end_to_end_artifacts_and_doc_numbers(tmp_path):
    doc = tmp_path / "capgate.md"
    rep = tmp_path / "report.json"
    art = capgate.main(["--registry", str(ROOT / "tools/capgate/fixtures"
                                          / "capabilities_snapshot_20261101.json"),
                        "--out-dir", str(tmp_path / "out"),
                        "--doc", str(doc), "--report-json", str(rep),
                        "--mission-id", "1476", "--moniker", "unit-test"])
    saved = json.loads((tmp_path / "out" / "capgate.json").read_text(encoding="utf-8"))
    assert saved["summary"] == art["summary"]
    md = doc.read_text(encoding="utf-8")
    # 报告里的数字必须来自工件，不是手抄：抽查三个关键量
    for key in ("n_capabilities", "verified_by_coverage", "domain_real_rate"):
        assert f"| {key} | {art['summary'][key]} |" in md
    payload = json.loads(rep.read_text(encoding="utf-8"))
    assert payload["mission_id"] == 1476
    assert payload["figures"], "应内嵌图件（matplotlib 可用时）"
    assert sum(1 for p in art["probes"] if p["verdict"] == "FAIL") == 5
    assert sum(1 for p in art["probes"] if p["verdict"] == "PASS") == 5