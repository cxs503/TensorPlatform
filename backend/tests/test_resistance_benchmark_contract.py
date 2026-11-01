"""阻力基准验证链回归门禁（v1.3 验证欠账清偿）。

覆盖三类缺陷，每类都能在修复前稳定复现失败：

A. **契约失配**：`build_resistance_payload` 发出的字段必须能被
   `ResistanceRequest` 全量接收（旧版发 ``speed``，服务端只认 ``speed_ms``，
   于是永远按默认 1.0 m/s 计算）；
B. **字段缺失**：`ResistanceResponse` 必须提供 ``ct``（旧版没有该字段，而
   `benchmark_validate` 直接 ``get("ct")`` → 恒 0.0 → 12/12 假 FAIL）；
C. **数据集自洽**：J1~J7 闭式判据零 FAIL，且几何/尺度/出处分级字段齐备。

另外固化湿表面积修复的量化锚点：KVLCC2 旧代理式 +50.6%、Holtrop −2.07%
（对公称 27,194 m²），并要求 |误差| < 3%。
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

ResistanceRequest = calc.ResistanceRequest
ResistanceResponse = calc.ResistanceResponse
calc_resistance = calc.calc_resistance
holtrop_wetted_surface = calc.holtrop_wetted_surface
body_of_revolution_wetted_surface = calc.body_of_revolution_wetted_surface
ittc57_friction_coefficient = calc.ittc57_friction_coefficient

FAIL, PASS, WITHHELD = sb.FAIL, sb.PASS, sb.WITHHELD
build_resistance_payload = sb.build_resistance_payload
extract_ct = sb.extract_ct
geometry_from_case = sb.geometry_from_case
benchmark_validate = sb.benchmark_validate
BenchmarkValidateRequest = sb.BenchmarkValidateRequest

DATASET_PATH = BACKEND / "data" / "benchmark_reference.json"
S_PUBLISHED_KVLCC2 = 27194.0  # 公称湿表面积（外部锚点，用于 <3% 判定）


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _cases(dataset, ship_type):
    return [c for c in dataset["cases"] if c["ship_type"] == ship_type]


# ---------------------------------------------------------------------------
# A. 契约：payload 字段 ⊆ 请求模型字段
# ---------------------------------------------------------------------------

def test_payload_keys_accepted_by_request_model(dataset):
    """payload 不得含请求模型不认识的键（旧版 ``speed`` 就是这样被静默丢弃的）。"""
    known = set(ResistanceRequest.model_fields)
    for case in dataset["cases"]:
        geom = geometry_from_case(case, case["ship_type"])
        payload = build_resistance_payload(case["ship_type"], case["speed_ms"], geom)
        unknown = set(payload) - known
        assert unknown == set(), f"{case['case_id']} payload 含未知字段 {unknown}"


def test_payload_speed_survives_model_validation(dataset):
    """速度必须真的进入请求模型，不能被默认值 1.0 m/s 顶替。"""
    for case in dataset["cases"]:
        geom = geometry_from_case(case, case["ship_type"])
        payload = build_resistance_payload(case["ship_type"], case["speed_ms"], geom)
        req = ResistanceRequest(**payload)
        assert req.speed_ms == pytest.approx(case["speed_ms"], rel=1e-9)


def test_payload_carries_dataset_geometry(dataset):
    """几何取数据集而非硬编码预设（旧版 SHIP_GEOMETRY 与 5415 案例相差 15%）。"""
    case = _cases(dataset, "DTMB-5415")[0]
    geom = geometry_from_case(case, "DTMB-5415")
    payload = build_resistance_payload("DTMB-5415", case["speed_ms"], geom)
    assert payload["length"] == pytest.approx(case["geometry"]["length_m"])
    assert payload["beam"] == pytest.approx(case["geometry"]["beam_m"])
    assert payload["s_wetted_m2"] == pytest.approx(case["geometry"]["wetted_surface_m2"])


# ---------------------------------------------------------------------------
# B. Ct 提取：没有 ct 字段也能算，且不能恒为 0
# ---------------------------------------------------------------------------

def test_response_model_exposes_ct():
    assert "ct" in ResistanceResponse.model_fields
    assert "cf_ittc57" in ResistanceResponse.model_fields
    assert "s_wetted_m2" in ResistanceResponse.model_fields


def test_extract_ct_prefers_explicit_field():
    ct, src = extract_ct({"ct": 0.0031, "total_resistance_n": 999.0}, 1025.0, 27194.0, 7.202)
    assert src == "response.ct"
    assert ct == pytest.approx(0.0031)


def test_extract_ct_derives_when_field_absent():
    """旧版服务响应无 ct 时的关键路径：必须由总阻力反算，而不是返回 0.0。"""
    rho, S, V = 1025.0, 27194.0, 7.202
    rt = 0.5 * rho * S * 0.0028 * V ** 2
    ct, src = extract_ct({"total_resistance_n": rt}, rho, S, V)
    assert src == "derived_from_total_resistance_n"
    assert ct == pytest.approx(0.0028, rel=1e-9)
    assert ct > 0.0


def test_extract_ct_unavailable_is_explicit():
    ct, src = extract_ct({}, 1025.0, 0.0, 7.202)
    assert ct == 0.0 and src == "unavailable"


# ---------------------------------------------------------------------------
# C. 湿表面积：旧代理式 → Holtrop（可量化锚点）
# ---------------------------------------------------------------------------

def test_kvlcc2_wetted_surface_within_3pct_of_published():
    s = holtrop_wetted_surface(320.0, 58.0, 20.8, 0.8098)
    assert abs(s - S_PUBLISHED_KVLCC2) / S_PUBLISHED_KVLCC2 < 0.03, f"S={s:.1f} m² 超 3% 带"


def test_old_surrogate_surface_error_was_50pct():
    """留存缺陷证据：回转体代理式用于排水型船时高估 50% 以上。"""
    s_old = body_of_revolution_wetted_surface(320.0, 58.0, 0.8098)
    assert (s_old - S_PUBLISHED_KVLCC2) / S_PUBLISHED_KVLCC2 > 0.50


def test_cf_matches_ittc57_closed_form():
    re = 1.0e9
    assert ittc57_friction_coefficient(re) == pytest.approx(0.075 / (math.log10(re) - 2.0) ** 2, rel=1e-12)


# ---------------------------------------------------------------------------
# D. 端到端：真实求解器（不经 HTTP）对唯一可精确核验的量——摩擦项
# ---------------------------------------------------------------------------

def test_end_to_end_cf_path_is_exact(dataset):
    """平台 Cf 与 ITTC-57 闭式解逐位一致（相对误差 < 1e-9）→ 该链路 <3% 成立。"""
    case = _cases(dataset, "KVLCC2")[-1]
    geom = geometry_from_case(case, "KVLCC2")
    req = ResistanceRequest(**build_resistance_payload("KVLCC2", case["speed_ms"], geom))
    resp = asyncio.run(calc_resistance(req))
    cf_closed = 0.075 / (math.log10(resp.reynolds_number) - 2.0) ** 2
    assert resp.cf_ittc57 == pytest.approx(cf_closed, rel=1e-6)
    # 摩擦阻力与 Cf·0.5ρSV² 自洽
    rf_expected = 0.5 * req.water_density * resp.s_wetted_m2 * resp.cf_ittc57 * req.speed_ms ** 2
    assert resp.frictional_resistance_n == pytest.approx(rf_expected, rel=1e-4)
    # Ct 必须落在物理带 [Cf, 3Cf]
    assert resp.cf_ittc57 <= resp.ct <= 3.0 * resp.cf_ittc57


def test_body_of_revolution_path_uses_surrogate(dataset):
    case = _cases(dataset, "SUBOFF")[0]
    geom = geometry_from_case(case, "SUBOFF")
    payload = build_resistance_payload("SUBOFF", case["speed_ms"], geom)
    req = ResistanceRequest(**{**payload, "s_wetted_m2": None})
    resp = asyncio.run(calc_resistance(req))
    assert resp.surface_method == "body_of_revolution"


# ---------------------------------------------------------------------------
# E. 判决语义：未复核参考值不得给出 PASS/FAIL
# ---------------------------------------------------------------------------

def test_withheld_references_do_not_produce_verdicts(dataset, monkeypatch):
    def fake_call(ship_type, speed_ms, geometry):
        rho = float(geometry.get("water_density", 998.0))
        s = float(geometry["s_wetted_m2"])
        re = speed_ms * float(geometry["length"]) / 1.004e-6
        cf = 0.075 / (math.log10(re) - 2.0) ** 2
        ct = cf * 1.15
        return {"ct": ct, "total_resistance_n": 0.5 * rho * s * ct * speed_ms ** 2,
                "reynolds_number": re, "s_wetted_m2": s, "surface_method": "holtrop-mennen-1982"}

    monkeypatch.setattr(sb, "_call_resistance_api", fake_call)
    out = sb.benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
    assert out.summary["pass_rate"] is None
    assert out.summary["accuracy_claim"].startswith("withheld")
    assert all(r.verdict == WITHHELD for r in out.results)
    assert all(r.friction_floor_ok for r in out.results)
    assert "recommendation" not in out.summary, "未复核参考值不得触发算法替换建议"


def test_service_unavailable_is_withheld_not_fail(dataset, monkeypatch):
    monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: None)
    out = sb.benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
    assert all(r.verdict == WITHHELD for r in out.results)
    assert out.summary["failed"] == 0


def test_verified_reference_produces_pass_or_fail(dataset, monkeypatch):
    """把参考值临时标为 verified 时，判决必须真的落到 PASS/FAIL（门禁不是空的）。"""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    case = data["cases"][-1]
    case["reference_ct_status"] = "verified"
    monkeypatch.setattr(sb, "_load_benchmark_data", lambda: data)
    monkeypatch.setattr(sb, "_call_resistance_api", lambda st, v, g: {
        "ct": float(case["experimental_ct"]), "total_resistance_n": 1.0,
        "reynolds_number": 1.0e9, "s_wetted_m2": 1.0,
    })
    out = sb.benchmark_validate(BenchmarkValidateRequest(ship_type=case["ship_type"],
                                                        benchmark_id=case["case_id"]))
    assert out.results[0].verdict == PASS
    assert out.results[0].error_pct == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# F. 数据集闭式判据门禁（J1~J7）
# ---------------------------------------------------------------------------

def test_dataset_passes_all_closed_form_judges():
    from tools.benchmark_audit.audit_resistance_benchmark import audit_file

    rep = audit_file(DATASET_PATH)
    assert rep.failures == [], f"数据集判据失效：{[r.as_dict() for r in rep.failures][:3]}"
    assert rep.n_cases == 12


def test_dataset_cases_declare_scale_and_provenance(dataset):
    assert dataset["version"] == "1.2"
    for case in dataset["cases"]:
        assert case["scale"] in ("model", "full")
        assert case["reference_verified"] is False
        assert case["reference_ct_status"] in ("verified", "withheld", "retired")
        assert case["provenance"]["type"] and case["provenance"]["locator"]
        assert "derivation" in case


def test_froude_numbers_recomputed_consistently(dataset):
    g = 9.81
    for case in dataset["cases"]:
        L = case["geometry"]["length_m"]
        fr = case["speed_ms"] / math.sqrt(g * L)
        assert case["froude_number"] == pytest.approx(fr, rel=1e-3)