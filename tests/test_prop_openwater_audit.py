#!/usr/bin/env python3
"""推进敞水性能审计的回归测试（离线，不依赖 :8001 在线）。

分两层：
1. **判据单元层**：用合成样本注入"已知错"与"已知对"两种行为，断言判据能分辨
   （判据本身不能是永远 FAIL 的死判据，否则它不构成证据）。
2. **工件回放层**：若存在 artifacts/audit/prop_openwater_probe.json 与
   ..._verdict.json（探针/审计产物），断言裁决里关键判据的结论与已发布裁决一致；
   产物缺失则 skip（保持离线跑得通）。

运行：python3 -m pytest tests/test_prop_openwater_audit.py -q
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "audit"))

import prop_openwater_audit as A  # noqa: E402

PROBE = os.path.join(ROOT, "artifacts", "audit", "prop_openwater_probe.json")
VERDICT = os.path.join(ROOT, "artifacts", "audit", "prop_openwater_verdict.json")
REF = os.path.join(ROOT, "docs", "audit", "data", "propulsion_wageningen_ref.json")


# --------------------------------------------------------------------------- #
# 合成样本工厂
# --------------------------------------------------------------------------- #
def fake_samples(mode: str, pds=(0.6, 0.8, 1.0, 1.2, 1.4), aes=(0.4, 0.7, 1.0),
                 zs=(4, 5), js=None) -> list:
    """mode='platform' 复刻 :8001 端点的线性+clamp 行为（KQ=KT/10, eta 截断 0.85）；
    mode='physical' 用一个自带零推力点、几何相关的玩具敞水模型（仅用于验证判据可分辨）。"""
    js = list(js if js is not None else [round(0.05 * k, 6) for k in range(29)])
    out = []
    for z in zs:
        for pd in pds:
            for ae in aes:
                for j in js:
                    if mode == "platform":
                        kt = 0.3 * pd - 0.2 * j
                        kq = 0.03 * pd - 0.02 * j
                        if z == 5:
                            kt *= 1.02; kq *= 1.02
                        kt *= (0.5 + ae); kq *= (0.5 + ae)
                        kt = max(0.05, min(0.5, kt)); kq = max(0.005, min(0.05, kq))
                        eta = (j * kt / (2 * math.pi * kq)) if (j > 0 and kq > 0) else 0.5
                        eta = min(eta, 0.85)
                    else:
                        # 玩具"物理"模型：零推力点随 P/D 平移、KQ 非 KT 线性复制、Ae/Ao 弱影响
                        j0 = 0.85 * pd
                        kt = max(0.0, 0.32 * pd * (1 - j / j0)) * (1 + 0.05 * (ae - 0.7))
                        kq = 0.045 * pd * (1 - 0.75 * j / j0) * (1 + 0.05 * (ae - 0.7))
                        kq = max(kq, 1e-6)
                        eta = A.eta0_def(j, kt, kq)
                    out.append({"Z": z, "P_D": pd, "Ae_Ao": ae, "J": j,
                                "KT": kt, "KQ": kq, "eta0": eta, "http_status": 200})
    return out


# --------------------------------------------------------------------------- #
# 工具函数单元
# --------------------------------------------------------------------------- #
def test_eta0_definition():
    assert A.eta0_def(0.0, 0.3, 0.03) == 0.0
    assert A.eta0_def(0.6, 0.0, 0.03) == 0.0
    assert A.eta0_def(0.6, 0.2, 0.0) == 0.0
    # J·KT/(2π·KQ) 手算：0.6*0.2/(2π*0.03)
    assert A.eta0_def(0.6, 0.2, 0.03) == pytest.approx(0.6 * 0.2 / (2 * math.pi * 0.03))


def test_interp_exact_and_clamped():
    xs, ys = [0.0, 0.5, 1.0], [1.0, 0.75, 0.5]
    assert A.interp(0.5, xs, ys) == pytest.approx(0.75)
    assert A.interp(0.25, xs, ys) == pytest.approx(0.875)
    # 端点外按首/末段线性外推（不引入常数外推）
    assert A.interp(1.5, xs, ys) == pytest.approx(0.25)


def test_rel_err():
    assert A.rel_err(1.1, 1.0) == pytest.approx(0.1)
    assert A.rel_err(1.0, 0.0) == float("inf")
    assert A.rel_err(0.0, 0.0) == 0.0


# --------------------------------------------------------------------------- #
# 判据可分辨性（关键：判据必须对"错""对"两种行为给出不同结论）
# --------------------------------------------------------------------------- #
def test_platform_like_samples_are_rejected():
    s = fake_samples("platform")
    assert A.check_g2_eta_at_zero_j(s)["verdict"] == "FAIL"
    assert A.check_g3_eta_geometry_sensitivity(s)["verdict"] == "FAIL"
    assert A.check_g4_kqkt_ratio(s)["verdict"] == "FAIL"
    assert A.check_g5_monotonic(s)["verdict"] == "PASS"       # 线性模型确实单调
    assert A.check_g6_zero_thrust(s)["verdict"] == "FAIL"
    assert A.check_g7_saturation(s)["verdict"] == "FAIL"
    assert A.check_g8_area_ratio_insensitivity(s)["verdict"] == "FAIL"


def test_physical_like_samples_pass_key_criteria():
    s = fake_samples("physical")
    assert A.check_g2_eta_at_zero_j(s)["verdict"] == "PASS"
    assert A.check_g3_eta_geometry_sensitivity(s)["verdict"] == "PASS"
    assert A.check_g4_kqkt_ratio(s)["verdict"] == "PASS"
    assert A.check_g5_monotonic(s)["verdict"] == "PASS"
    assert A.check_g6_zero_thrust(s)["verdict"] == "PASS"
    assert A.check_g7_saturation(s)["verdict"] == "PASS"
    assert A.check_g8_area_ratio_insensitivity(s)["verdict"] == "PASS"


def test_g1_detects_internal_inconsistency():
    good = fake_samples("physical")
    assert A.check_g1_eta_identity(good)["verdict"] == "PASS"
    bad = [dict(x) for x in good]
    for x in bad:                      # 人为把 eta0 抬 20% → 定义式必然对不上
        x["eta0"] = x["eta0"] * 1.2
    assert A.check_g1_eta_identity(bad)["verdict"] == "FAIL"


def test_g9_flags_zero_reference_as_infinite_error():
    ref = {"data_points": {"J": [0.5], "KT": [0.2], "KQ": [0.03], "eta_0": [0.53]}}
    exact = [{"Z": 4, "P_D": 1.0, "Ae_Ao": 0.70, "J": 0.5, "KT": 0.2, "KQ": 0.03,
              "eta0": A.eta0_def(0.5, 0.2, 0.03), "http_status": 200}]
    assert A.check_g9_reference_agreement(exact, ref)["verdict"] == "PASS"
    off = [dict(exact[0], KT=0.0)]
    assert A.check_g9_reference_agreement(off, ref)["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# 工件回放（离线）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.exists(PROBE), reason="探针产物缺失（未跑 tools/audit/prop_openwater_probe.py）")
def test_published_probe_has_full_coverage():
    doc = json.load(open(PROBE, encoding="utf-8"))
    ok = sum(1 for s in doc["samples"] if s["http_status"] == 200)
    assert ok / len(doc["samples"]) >= 0.99, "探针覆盖率 <99%，证据不足以下裁决"


@pytest.mark.skipif(not os.path.exists(VERDICT), reason="裁决产物缺失（未跑 tools/audit/prop_openwater_audit.py）")
def test_published_verdict_is_mock_with_7_endpoint_fails():
    doc = json.load(open(VERDICT, encoding="utf-8"))
    assert doc["verdict"] == "MOCK"
    # 端点侧（不含 G10 基准表自洽）失效判据应为 7 条
    assert len(doc["endpoint_fails"]) == 7, doc["endpoint_fails"]
    assert set(doc["endpoint_fails"]) == {"G2", "G3", "G4", "G6", "G7", "G8", "G9"}
    # 恒定 KQ/KT 与零几何敏感性是 MOCK 的结构性指纹
    g4 = next(c for c in doc["criteria"] if c["id"] == "G4")
    assert g4["detail"]["min"] == g4["detail"]["max"]
    assert doc["summary"]["eta0_independent_of_geometry"] is True


@pytest.mark.skipif(not os.path.exists(REF), reason="文献基准快照缺失")
def test_reference_snapshot_is_traceable():
    ref = json.load(open(REF, encoding="utf-8"))
    assert "Oosterveld" in ref["source"]
    dp = ref["data_points"]
    assert len(dp["J"]) == len(dp["KT"]) == len(dp["KQ"]) == len(dp["eta_0"])
    # 基准表 eta_0 列自身有一处算术不一致（J=0.8）——审计 G10 已标 FAIL，
    # 这里锁定"已知不一致点数 == 1"，防止有人静默改基准表来洗白端点。
    bad = [r for r in A.check_g10_reference_self_consistency(ref)["rows"]
           if r["err"] is not None and r["err"] > 0.01]
    assert len(bad) == 1 and bad[0]["J"] == 0.8