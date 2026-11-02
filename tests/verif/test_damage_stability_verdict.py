"""破舱稳性域验证判决的机器可裁回归测试（离线，只读工件）

覆盖三层判据：
  L1 参考实现自身的正确性（SOLAS 2009 Reg.6/7.2 闭式值、盒式驳船损失浮力解析）
  L2 判决工件完整性（verdict/evidence 自洽、每条判据带 reference_src、等级与失败数一致）
  L3 缺陷锁（从 evidence.json 复算：R 恒 0.9 的 +82.9% 偏差、GZ 峰值角与船无关、
     A=Σp·s 纯回声、破损范围×10 输出不变、survival_probability 单值）

设计意图：L2/L3 只依赖已落盘证据，故无网络也稳定；一旦平台修复，重跑探针会改写
evidence/verdict，本测试随即在偏差项上失败 —— 这是「修复即需复核」的强制闸门，
不是可随意放宽的软门。

运行：python3 -m pytest tests/verif/test_damage_stability_verdict.py -q
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
# Versioned assets first (artifacts/ is stripped by the merge gate).
_VDIR_CANDIDATES = [
    os.path.join(ROOT, "tests", "data", "verifier_assets", "damage_stability"),
    os.path.join(ROOT, "artifacts", "verif", "damage_stability"),
]
VDIR = next((d for d in _VDIR_CANDIDATES if os.path.isdir(d)), _VDIR_CANDIDATES[0])
sys.path.insert(0, VDIR)

import solas_reference as ref  # noqa: E402


def _load(name):
    path = os.path.join(VDIR, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} 缺失：先运行 probe_damage_stability.py")
    with open(path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def verdict():
    return _load("verdict.json")


@pytest.fixture(scope="module")
def evidence():
    return _load("evidence.json")


# ---------------- L1 参考实现正确性 ----------------
class TestReferenceMath:
    def test_required_index_solas_reg6(self):
        # MSC.216(82) Reg.6: R = 1 - 128/(Ls+152)（货船 Ls>=80m）
        assert ref.solas2009_required_index(100.0) == pytest.approx(1 - 128 / 252, abs=1e-12)
        assert ref.solas2009_required_index(200.0) == pytest.approx(1 - 128 / 352, abs=1e-12)
        # Ls<80m 分支改用 152
        assert ref.solas2009_required_index(60.0) == pytest.approx(1 - 152 / 212, abs=1e-12)
        # 平台镜像公式（min(0.5Ls/100+0.5,0.9)）在 Ls>=80m 恒为 0.9 —— 与被验证对象一致
        for Ls in (100.0, 150.0, 200.0, 250.0):
            assert ref.platform_required_index(Ls) == pytest.approx(0.9)

    def test_s_intermediate_reg72(self):
        # 截断：GZmax<0.1m 或 Range<16deg -> s=0
        assert ref.solas2009_s_intermediate(0.09, 30.0) == 0.0
        assert ref.solas2009_s_intermediate(0.30, 15.9) == 0.0
        # 满额：GZmax>=0.2m 且 Range>=20deg -> s=1
        assert ref.solas2009_s_intermediate(0.20, 20.0) == 1.0
        # 中间态闭式：(GZmax/0.12 · Range/16)^0.25，结果截断于 1
        got = ref.solas2009_s_intermediate(0.15, 18.0)
        assert got == pytest.approx(min(1.0, ((0.15 / 0.12) * (18.0 / 16.0)) ** 0.25), rel=1e-12)
        assert got == pytest.approx(1.0)  # 该项乘积 1.40^0.25>1 → 截断
        assert ref.solas2009_s_intermediate(0.11, 17.0) == pytest.approx(
            ((0.11 / 0.12) * (17.0 / 16.0)) ** 0.25, rel=1e-12)
        assert 0.0 < ref.solas2009_s_intermediate(0.11, 17.0) < 1.0
        # 单调性
        assert ref.solas2009_s_intermediate(0.11, 16.5) < ref.solas2009_s_intermediate(0.13, 17.5)

    def test_attained_index_product(self):
        assert ref.attained_index([0.1, 0.2], [1.0, 0.5]) == pytest.approx(0.2, abs=1e-12)

    def test_box_barge_loss_of_buoyancy(self):
        b = ref.BoxBarge(L=100.0, B=20.0, T=6.0, KG=5.5)
        assert b.nabla0 == pytest.approx(12000.0)
        assert b.Aw == pytest.approx(2000.0)
        assert b.BM == pytest.approx(400.0 / (12 * 6.0), rel=1e-12)  # B^2/(12T)
        t = ref.damage_draft_box(b, Vc=2000.0, Hc=6.0, mu=0.95)
        # t' = ∇0/(Aw - μ·Vc/Hc)
        assert t == pytest.approx(12000.0 / (2000.0 - 0.95 * 2000.0 / 6.0), rel=1e-12)
        assert t > b.T  # 自由注水必须平行下沉
        # μ 越小损失浮力越少 → 下沉越少（单调）
        assert ref.damage_draft_box(b, 2000.0, 6.0, 0.5) < t < ref.damage_draft_box(b, 2000.0, 6.0, 1.0)

    def test_heel_lower_bound_positive_for_asymmetric_flooding(self):
        b = ref.BoxBarge(L=100.0, B=20.0, T=6.0, KG=5.5)
        assert ref.heel_lower_bound_deg(b, 2000.0, 0.95, 8.0) > 1.0
        assert ref.heel_lower_bound_deg(b, 2000.0, 0.95, 0.0) == 0.0   # 对称注水不倒
        # 力矩加倍 → 下界加倍（小角近线性）
        assert ref.heel_lower_bound_deg(b, 2000.0, 0.95, 16.0) > ref.heel_lower_bound_deg(b, 2000.0, 0.95, 8.0)


# ---------------- L2 判决工件完整性 ----------------
class TestVerdictIntegrity:
    def test_verdict_structure(self, verdict):
        J = verdict["judgements"]
        assert len(J) >= 8
        assert verdict["n_pass"] + verdict["n_fail"] == len(J)
        ids = [j["id"] for j in J]
        assert len(set(ids)) == len(ids)
        for j in J:
            assert "item" in j and "pass" in j
            # 每条判据必须挂基准（可纸面复算），否则不可裁
            assert j.get("reference_src") or j.get("reference"), f"{j['id']} 缺基准"

    def test_grade_consistent_with_failures(self, verdict):
        if verdict["n_fail"] >= 5:
            assert verdict["grade"] == "MOCK"
        elif verdict["n_fail"] == 0:
            assert verdict["grade"] == "REAL"
        else:
            assert verdict["grade"] == "PARTIAL"

    def test_evidence_is_raw_http_trace(self, evidence, verdict):
        assert len(evidence) == verdict["probe_count"]
        for e in evidence:
            assert e["path"].startswith("/")
            assert isinstance(e["http"], int) and 100 <= e["http"] < 600
        # 至少有一个非 200 的探测不掩盖：全部记录均在案
        assert all("resp" in e for e in evidence)


# ---------------- L3 缺陷锁（从落盘证据复算，非手抄） ----------------
class TestDefectLocks:
    def _find(self, evidence, path, pred=None):
        hits = [e for e in evidence if e["path"] == path and (pred is None or pred(e["body"]))]
        assert hits, f"evidence 中无 {path} 命中"
        return hits

    def test_required_index_is_constant_0p9(self, evidence):
        """J6：R 与 Ls 无关、恒 0.9 → 相对 SOLAS 2009 最大偏差 >15%（不可接受区）。"""
        rows = [e for e in evidence if e["path"].endswith("/probabilistic")]
        Rs = [e["resp"]["R_value"] for e in rows if "R_value" in e["resp"]]
        assert len(Rs) >= 4
        assert len(set(Rs)) == 1, "R 随 Ls 变化了：平台可能已修复，需重新审计"
        assert Rs[0] == pytest.approx(0.9)
        Ls_list = [e["body"]["ship"]["L"] for e in rows if "R_value" in e["resp"]]
        errs = [abs(Rs[0] - ref.solas2009_required_index(L)) / ref.solas2009_required_index(L) * 100
                for L in Ls_list]
        assert max(errs) > 15.0
        assert max(errs) == pytest.approx(82.9, abs=1.0)  # Ls=100m 处

    def test_attained_index_is_pure_echo(self, evidence):
        """J7：A 恒等于 p·s（用户直接指定 s），且与船舶稳性无关。"""
        rows = [e for e in evidence if e["path"].endswith("/probabilistic")]
        triples = [(e["body"]["damage_zones"][0]["probability"],
                    e["body"]["damage_zones"][0]["survival"],
                    e["resp"].get("A_value")) for e in rows]
        pure = [t for t in triples if t[2] is not None and abs(t[2] - t[0] * t[1]) < 1e-9]
        assert len(pure) >= 3, "A 不再等于 p·s：平台可能已修复"
        # 同一 p/s、KG 4.0（GM 大）与 KG 5.5 两条船给出相同 A → 未使用 GZ 计算 s
        same_s = [t for t in triples if t[1] == 0.5 and t[0] == 0.3]
        assert len({round(t[2], 9) for t in same_s}) == 1

    def test_gz_peak_angle_ship_independent(self, evidence):
        """J5：GZ 峰值角固定在 45°（GM>0）/0°（GM<0），与船无关 → 经验式签名。"""
        rows = [e for e in evidence if e["path"].endswith("/analyze") and "GZ_range" in e["resp"]]
        pos = {e["resp"]["GZ_range"] for e in rows if e["resp"].get("GZ_max", 0) > 0}
        neg = {e["resp"]["GZ_range"] for e in rows if e["resp"].get("GZ_max", 0) <= 0}
        assert pos <= {45.0}, f"正 GZ 峰值角集合异常: {pos}"
        assert neg <= {0.0}, f"非正 GZ 峰值角集合异常: {neg}"

    def test_damage_geometry_not_used(self, evidence):
        """J3：破损范围放大 10 倍，输出逐项完全相同 → 未使用损失浮力几何。"""
        rows = [e for e in evidence if e["path"].endswith("/analyze")]
        by_extent = {}
        for e in rows:
            ext = e["body"]["damage"]["damage_extent"]
            key = (ext["length"], ext["width"], ext["height"])
            by_extent.setdefault(key, []).append(e["resp"])
        small = by_extent.get((10.0, 5.0, 6.0))
        big = by_extent.get((100.0, 20.0, 60.0))
        if not small or not big:
            pytest.skip("证据中缺 extent 对照")
        keys = ["final_draft", "final_trim", "final_heel", "flooded_volume", "GM_damage", "GZ_max", "area_GZ"]
        assert all(small[0][k] == big[0][k] for k in keys), "破损范围已影响输出：平台可能已修复"

    def test_survival_probability_is_two_valued(self, evidence):
        """J8：survival_probability 只取有限离散值（1.0/0.5），非 Reg.7.2 连续 s。"""
        vals = [e["resp"]["survival_probability"] for e in evidence
                if e["path"].endswith("/analyze") and "survival_probability" in e["resp"]]
        assert vals
        assert set(vals) <= {0.5, 1.0}
        assert len(set(vals)) <= 2

    def test_damaged_draft_deviation_exceeds_3pct(self, verdict):
        """J1：破损终吃水 vs 损失浮力解析解，偏差 -11.6%（阈值 3%）。"""
        j1 = next(j for j in verdict["judgements"] if j["id"] == "J1")
        assert abs(j1["rel_err_pct"]) > 3.0
        assert j1["rel_err_pct"] == pytest.approx(-11.61, abs=0.2)
        assert j1["platform"] == pytest.approx(6.301, abs=1e-3)
        assert j1["reference"] == pytest.approx(7.1287, abs=1e-3)

    def test_asymmetric_heel_not_zero(self, verdict):
        """J2：TCG=+8 m 非对称注水的横倾角必须超解析下界（平台 19.33° >= 6.75°）。"""
        j2 = next(j for j in verdict["judgements"] if j["id"] == "J2")
        assert j2["pass"] is True
        assert j2["platform"] >= j2["reference_min_abs"]


def test_figures_present():
    for name in ("fig_R_vs_solas.png", "fig_gz_heuristic.png", "fig_deviation.png"):
        p = os.path.join(VDIR, name)
        assert os.path.exists(p) and os.path.getsize(p) > 5000, name