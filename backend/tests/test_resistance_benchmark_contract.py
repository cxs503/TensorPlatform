"""阻力基准验证链回归门禁（v1.3 验证欠账清偿；2026-11 收集死复活补账）。

覆盖三类缺陷，每类都能在修复前稳定复现失败：

A. **契约失配**：发往 ``/calculations/resistance`` 的载荷字段必须能被
   ``ResistanceRequest`` 全量接收（历史病灶：载荷发 ``speed``，服务端只认
   ``speed_ms``，每个算例都按默认 1.0 m/s 计算——现行契约两别名都收）；
B. **字段缺失/反推**：``ResistanceResponse`` 必须直接暴露 ``ct``/``cf``
   （m1609），历史病灶是响应无 ``ct`` 而消费侧 ``get("ct")`` 恒 0.0；
   ``total_resistance_coefficient`` 负责"缺 ct 时由 R_T 反推、反推不出
   如实返回 None（不猜）"；
C. **数据集自洽**：现行裁判（``services.benchmark_dataset_audit``）可对全部
   案例定谳；几何/尺度/出处分级字段齐备（v3.0 数据集 schema）。

另外固化湿表面积修复的量化锚点：KVLCC2 旧回转体代理式 +50.6%、Holtrop
全系数式 −0.32%（对公称 27,194 m²，<3% 判带），并要求端点默认路径对
商船 hull 如实披露代理式适用域（validity_notes）。

绑定纪律：全部钉现役符号（main @ fae164a1 布局）——
``holtrop_wetted_surface``/``ittc57_cf``/``wetted_surface_axisym`` 在
``api/domain/{hull_geometry,ship_resistance}.py``；``case_geometry`` 在
``api/domain/benchmark_audit.py``；``total_resistance_coefficient`` 在
``services/benchmark_integrity.py``；判决域=``BenchmarkResult.pass_/withheld``
（m1388 语义），不是旧版 FAIL/PASS/WITHHELD 字符串。
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
for p in (str(BACKEND), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import importlib.util


def _load_module(name: str, path: Path):
    """按文件路径加载模块：绕开 api/__init__ 的 DB/加密依赖，保证本门禁在任何环境可跑。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


calc = _load_module("tp_calculations", BACKEND / "api" / "calculations.py")
sb = _load_module("tp_ship_benchmark", BACKEND / "api" / "domain" / "ship_benchmark.py")
ship_res = _load_module("tp_ship_resistance", BACKEND / "api" / "domain" / "ship_resistance.py")
hull_geom = _load_module("tp_hull_geometry", BACKEND / "api" / "domain" / "hull_geometry.py")
bench_audit = _load_module("tp_bench_audit_domain", BACKEND / "api" / "domain" / "benchmark_audit.py")
bench_integ = _load_module("tp_bench_integrity", BACKEND / "services" / "benchmark_integrity.py")
bench_ds_audit = _load_module(
    "tp_bench_ds_audit", BACKEND / "services" / "benchmark_dataset_audit.py")
# 镜像生产接线（api/__init__ 注册、本测试旁路了它）：判决带常数经 provider seam 取
physics_providers = _load_module(
    "tp_physics_providers", BACKEND / "api" / "domain" / "physics_providers.py")
physics_providers.register_ship_resistance_providers()

ResistanceRequest = calc.ResistanceRequest
ResistanceResponse = calc.ResistanceResponse
calc_resistance = calc.calc_resistance
holtrop_wetted_surface = hull_geom.holtrop_wetted_surface
body_of_revolution_wetted_surface = ship_res.wetted_surface_axisym  # (beam, length, cb)
ittc57_friction_coefficient = ship_res.ittc57_cf
case_geometry = bench_audit.case_geometry
total_resistance_coefficient = bench_integ.total_resistance_coefficient
benchmark_validate = sb.benchmark_validate
BenchmarkValidateRequest = sb.BenchmarkValidateRequest
load_and_assess = bench_ds_audit.load_and_assess
case_status_map = bench_ds_audit.case_status_map

DATASET_PATH = BACKEND / "data" / "benchmark_reference.json"
S_PUBLISHED_KVLCC2 = 27194.0  # 公称湿表面积（外部锚点，用于 <3% 判定）


def build_resistance_payload(ship_type: str, speed_ms: float, geom: dict) -> dict:
    """镜像 ``ship_benchmark._remote_resistance`` 的线上载荷契约（m1388/m1609 后字段集）。

    数据集几何优先（``case_geometry`` 已保证 case 自带键胜过预设），回转体
    案例自带实测湿面积时随载荷上送（服务端如实标注 caller_supplied_unverified）。
    """
    payload = {
        "ship_type": ship_type,
        "speed_ms": speed_ms,
        "length": geom["length"],
        "beam": geom["beam"],
        "draft": geom["draft"],
        "cb": geom["cb"],
    }
    if "wetted_surface" in geom:
        payload["wetted_surface_m2"] = geom["wetted_surface"]
    return payload


def _geom_for(case) -> dict:
    return case_geometry(case, sb.SHIP_GEOMETRY[case["ship_type"]])


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _cases(dataset, ship_type):
    return [c for c in dataset["cases"] if c["ship_type"] == ship_type]


# ---------------------------------------------------------------------------
# A. 契约：payload 字段 ⊆ 请求模型字段，速度不得静默落默认
# ---------------------------------------------------------------------------

def test_payload_keys_accepted_by_request_model(dataset):
    """payload 不得含请求模型不认识的键（旧版 ``speed`` 就是这样被静默丢弃的）。"""
    known = set(ResistanceRequest.model_fields)
    for case in dataset["cases"]:
        payload = build_resistance_payload(case["ship_type"], case["speed_ms"], _geom_for(case))
        unknown = set(payload) - known
        assert unknown == set(), f"{case['case_id']} payload 含未知字段 {unknown}"


def test_payload_speed_survives_model_validation(dataset):
    """速度必须真的进入请求模型：主键名与历史别名都不能被默认值 1.0 m/s 顶替。"""
    case = dataset["cases"][0]
    payload = build_resistance_payload(case["ship_type"], case["speed_ms"], _geom_for(case))
    assert ResistanceRequest(**payload).speed_ms == pytest.approx(case["speed_ms"], rel=1e-9)
    # 历史缺陷的反向锁：旧载荷只发 "speed"（别名独立成键时）也必须直达
    alias_only = {k: v for k, v in payload.items() if k != "speed_ms"}
    assert ResistanceRequest(**{**alias_only, "speed": 7.5}).speed_ms == pytest.approx(7.5)


def test_geometry_comes_from_dataset_not_stale_preset(dataset):
    """几何取数据集而非硬编码预设（旧版预设与 5415 案例相差 15%）。

    现行分工：case 自带键永远胜出，预设只补缺（SUBOFF 回转体案例的
    beam/draft/cb）。预设若再次与数据集漂移，本门禁即红。
    """
    case = _cases(dataset, "DTMB-5415")[0]
    geom = _geom_for(case)
    g = case["geometry"]
    assert geom["length"] == pytest.approx(g["length_m"])
    assert geom["beam"] == pytest.approx(g["beam_m"])
    assert geom["draft"] == pytest.approx(g["draft_m"])
    assert geom["cb"] == pytest.approx(g["cb"])
    # 预设与数据集一致性锚：只比主几何输入键（length/beam/draft/cb）；
    # displacement 是导出量（cb 公式值 vs 出处公布舍入值），不在比对域
    PRIMARY = ("length", "beam", "draft", "cb")
    for ship_type, preset in sb.SHIP_GEOMETRY.items():
        for case_ in _cases(dataset, ship_type):
            cg = case_geometry(case_)  # 不带预设：数据集自身提供了什么
            for key in PRIMARY:
                if key in cg:
                    assert preset[key] == pytest.approx(cg[key]), (
                        f"{ship_type} 预设 {key}={preset[key]} 与数据集 {cg[key]} 漂移")
    # 回转体案例自带实测湿面积，必须随载荷上送
    suboff = _cases(dataset, "SUBOFF")[0]
    payload = build_resistance_payload("SUBOFF", suboff["speed_ms"], _geom_for(suboff))
    assert payload["wetted_surface_m2"] == pytest.approx(suboff["geometry"]["wetted_surface_m2"])


# ---------------------------------------------------------------------------
# B. Ct 暴露与反推：没有 ct 字段也能算，且反推不出时如实 None（不猜）
# ---------------------------------------------------------------------------

def test_response_model_exposes_ct_family():
    assert "ct" in ResistanceResponse.model_fields
    assert "cf" in ResistanceResponse.model_fields
    assert "wetted_surface_m2" in ResistanceResponse.model_fields
    assert "total_resistance_coefficient" in ResistanceResponse.model_fields


def test_extract_ct_prefers_explicit_field():
    case = {"geometry": {"wetted_surface_m2": 27194.0}}
    ct = total_resistance_coefficient(
        {"ct": 0.0031, "total_resistance_n": 999.0}, case, 1025.0, 7.202)
    assert ct == pytest.approx(0.0031)


def test_extract_ct_derives_when_field_absent():
    """旧版服务响应无 ct 时的关键路径：由总阻力反推，而不是恒 0.0。"""
    rho, S, V = 1025.0, 27194.0, 7.202
    rt = 0.5 * rho * S * 0.0028 * V ** 2
    ct = total_resistance_coefficient(
        {"total_resistance_n": rt}, {"geometry": {"wetted_surface_m2": S}}, rho, V)
    assert ct == pytest.approx(0.0028, rel=1e-9)
    assert ct > 0.0


def test_extract_ct_unavailable_is_explicit():
    """湿面积/总阻力皆缺 → None（调用方如实记不可反推），绝不猜 0。"""
    assert total_resistance_coefficient({}, {"geometry": {}}, 1025.0, 7.202) is None
    assert total_resistance_coefficient(
        {}, {"geometry": {"wetted_surface_m2": 0.0}}, 1025.0, 7.202) is None


# ---------------------------------------------------------------------------
# C. 湿表面积：旧代理式 → Holtrop（可量化锚点）
# ---------------------------------------------------------------------------

def test_kvlcc2_wetted_surface_within_3pct_of_published():
    # 全量系数 KVLCC2（SIMMAN 2008）：Cm=0.9980、Cwp=0.9071、无球鼻 ABT=0
    s = holtrop_wetted_surface(320.0, 58.0, 20.8, 0.8098, 0.9980, 0.9071)
    assert abs(s - S_PUBLISHED_KVLCC2) / S_PUBLISHED_KVLCC2 < 0.03, f"S={s:.1f} m² 超 3% 带"


def test_old_surrogate_surface_error_was_50pct():
    """留存缺陷证据：回转体代理式用于排水型船时高估 50% 以上。"""
    s_old = body_of_revolution_wetted_surface(58.0, 320.0, 0.8098)  # (beam, length, cb)
    assert (s_old - S_PUBLISHED_KVLCC2) / S_PUBLISHED_KVLCC2 > 0.50


def test_cf_matches_ittc57_closed_form():
    re = 1.0e9
    assert ittc57_friction_coefficient(re) == pytest.approx(
        0.075 / (math.log10(re) - 2.0) ** 2, rel=1e-12)


# ---------------------------------------------------------------------------
# D. 端到端：真实求解器（不经 HTTP）对唯一可精确核验的量——摩擦项
# ---------------------------------------------------------------------------

def test_end_to_end_cf_path_is_exact(dataset):
    """平台 Cf 与 ITTC-57 闭式解一致（<1e-6）→ 该链路 <3% 成立。"""
    case = _cases(dataset, "KVLCC2")[-1]
    geom = _geom_for(case)
    req = ResistanceRequest(**build_resistance_payload("KVLCC2", case["speed_ms"], geom))
    resp = asyncio.run(calc_resistance(req))
    cf_closed = 0.075 / (math.log10(resp.reynolds_number) - 2.0) ** 2
    assert resp.cf == pytest.approx(cf_closed, rel=1e-6)
    # 摩擦阻力与 Cf·0.5ρSV² 自洽
    rf_expected = 0.5 * req.water_density * resp.wetted_surface_m2 * resp.cf * req.speed_ms ** 2
    assert resp.frictional_resistance_n == pytest.approx(rf_expected, rel=1e-4)
    # Ct 必须落在物理带 [Cf, 3Cf]
    assert resp.cf <= resp.ct <= 3.0 * resp.cf


def test_merchant_hull_defaults_disclose_surrogate_applicability(dataset):
    """端点默认路径对商船 hull 用回转体代理式（+50% 缺陷锚），必须随行披露
    适用域（Cb 超出轴对称拟合范围），不得冒充已验证物性。"""
    case = _cases(dataset, "KVLCC2")[-1]
    req = ResistanceRequest(**build_resistance_payload("KVLCC2", case["speed_ms"], _geom_for(case)))
    resp = asyncio.run(calc_resistance(req))
    assert resp.wetted_surface_source == "empirical_formula"
    assert resp.wetted_surface_m2 == pytest.approx(
        body_of_revolution_wetted_surface(req.beam, req.length, req.cb))
    assert resp.validity_status == "unvalidated_empirical_estimate"
    assert any("Cb" in note for note in resp.validity_notes), "Cb 超范围警示必须随行"


def test_body_of_revolution_path_uses_surrogate(dataset):
    case = _cases(dataset, "SUBOFF")[0]
    geom = _geom_for(case)
    # 不带实测湿面积：回转体代理式路径
    req = ResistanceRequest(**{**build_resistance_payload("SUBOFF", case["speed_ms"], geom),
                               "wetted_surface_m2": None})
    resp = asyncio.run(calc_resistance(req))
    assert resp.wetted_surface_source == "empirical_formula"
    assert resp.wetted_surface_m2 == pytest.approx(
        body_of_revolution_wetted_surface(req.beam, req.length, req.cb))
    # 带数据集实测湿面积：caller_supplied 路径且数值原样透传
    resp2 = asyncio.run(calc_resistance(ResistanceRequest(
        **build_resistance_payload("SUBOFF", case["speed_ms"], geom))))
    assert resp2.wetted_surface_source == "caller_supplied_unverified"
    assert resp2.wetted_surface_m2 == pytest.approx(case["geometry"]["wetted_surface_m2"])


# ---------------------------------------------------------------------------
# E. 判决语义（m1388）：未复核参考行 withheld，不给精度主张
# ---------------------------------------------------------------------------

def _fake_transport(ship_type, speed_ms, geometry):
    rho = 1025.0
    s = float(geometry.get("wetted_surface") or 0.0)
    re = speed_ms * float(geometry["length"]) / 1.004e-6
    cf = 0.075 / (math.log10(re) - 2.0) ** 2
    ct = cf * 1.15
    return {"ct": ct, "rt_n": 0.5 * rho * s * ct * speed_ms ** 2,
            "wetted_surface_m2": s, "froude_number": speed_ms / math.sqrt(9.81 * geometry["length"]),
            "method": "fake_transport", "transport": "in_process"}


def test_withheld_references_do_not_produce_accuracy_claims(monkeypatch):
    """KVLCC2 全部参考行 PARTIAL（非 CONSISTENT）→ withheld：不得产生精度主张。"""
    monkeypatch.setattr(sb, "_call_resistance_api", _fake_transport)
    out = sb.benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
    assert len(out.results) == 4
    assert all(r.withheld for r in out.results)
    assert all(not r.pass_ for r in out.results)
    assert all("DATASET-WITHHELD" in r.note for r in out.results)
    s = out.summary
    assert s["pass_rate_verified"] is None          # 无已核行 → 无已核通过率
    assert s["honest_fail"] is True
    assert s["algorithm_upgrade_recommended"] is False, "未复核参考值不得触发算法替换建议"
    assert s["withheld_count"] == 4


def test_service_unavailable_is_attributed_to_transport(monkeypatch):
    """传输失败不得被记成模型精度失败：method=N/A + note 明示 transport。"""
    monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: None)
    out = sb.benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
    assert all(r.method == "N/A" for r in out.results)
    assert all("unavailable" in r.note for r in out.results)
    assert out.summary["transport"] == "unavailable"
    assert out.summary["contract_violations"] == []


def test_consistent_reference_produces_real_pass_verdict(monkeypatch, dataset):
    """SUBOFF 参考行 CONSISTENT：判决必须真的落到 PASS（门禁不是空的）。"""
    case = _cases(dataset, "SUBOFF")[0]

    def exact_transport(ship_type, speed_ms, geometry):
        return {"ct": float(case["experimental_ct"]), "rt_n": 1.0,
                "wetted_surface_m2": float(geometry.get("wetted_surface") or 1.0),
                "froude_number": 0.3, "method": "exact", "transport": "in_process"}

    monkeypatch.setattr(sb, "_call_resistance_api", exact_transport)
    out = sb.benchmark_validate(BenchmarkValidateRequest(
        ship_type="SUBOFF", benchmark_id=case["case_id"]))
    r = out.results[0]
    assert not r.withheld
    assert r.pass_ is True, "CONSISTENT 行 + 误差 0 必须判 PASS"
    assert r.error_pct == pytest.approx(0.0, abs=1e-6)
    assert out.summary["verified_passed"] == 1


# ---------------------------------------------------------------------------
# F. 数据集自洽门禁（v3.0 + 现役裁判）
# ---------------------------------------------------------------------------

def test_dataset_adjudicable_by_live_judge(dataset):
    """现行裁判（load_and_assess，端点实际使用的那个）必须能对全部案例定谳。

    旧 v1.2 审计工具（tools/benchmark_audit J1~J7）未跟进 v3.0 schema，
    其对账另行立案；本门禁钉端点真正依赖的裁判与当前定谳格局。
    """
    m = case_status_map(load_and_assess(dataset))
    assert len(m) == len(dataset["cases"]) == 12
    statuses = {v["status"] for v in m.values()}
    assert statuses and statuses <= {"CONSISTENT", "PARTIAL"}
    # 当前定谳锚：SUBOFF 族全 CROSS-CONSISTENT，5415/KVLCC2 全 PARTIAL
    for cid, v in m.items():
        expected = "CONSISTENT" if cid.startswith("SUBOFF") else "PARTIAL"
        assert v["status"] == expected, f"{cid} 定谳漂移：{v['status']} != {expected}"


def test_dataset_cases_declare_scale_and_provenance(dataset):
    assert dataset["version"] == "3.0"
    for case in dataset["cases"]:
        assert case["scale"] in ("model", "full")
        assert case["reference_status"] in ("DERIVED", "WITHHELD")
        assert case["rt_status"] in ("DERIVED", "WITHHELD")
        assert case["froude_status"] in ("DERIVED", "WITHHELD")
        assert case["source_citation"]
        assert case["experimental_ct"] is None or case["experimental_ct"] > 0


def test_froude_numbers_recomputed_consistently(dataset):
    g = 9.81
    for case in dataset["cases"]:
        L = case["geometry"]["length_m"]
        fr = case["speed_ms"] / math.sqrt(g * L)
        assert case["froude_number"] == pytest.approx(fr, rel=1e-3)
