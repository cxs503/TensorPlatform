"""静水力 Cb 参数化闭式的剖面族闭式锚点（m1858 审核席）。

护栏分两段：
1. **锚点可信**：脚本内的闭式 KB/BM 与独立 Gauss-Legendre 双重积分互校 <0.5%；
2. **适用域钉死**：平台 ``kb_from_cb`` / ``bm_transverse`` 在棱柱族（矩形/三角）上
   精确（误差 0），而在曲剖面族（半椭球、Wigley）上误差远超 15% —— 这是**已知且
   已登记**的验证欠账（见 docs/verification_debt/hydrostatics_form_family/REPORT.md），
   本测试把它写成显式断言，防止「悄悄修好」或「悄悄更坏」而无人知晓。
"""
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(
    REPO, "docs", "verification_debt", "hydrostatics_form_family", "verify_hydrostatics.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("vh_forms", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vh_forms"] = mod
    spec.loader.exec_module(mod)
    return mod


vh = _load()


def test_closed_forms_match_independent_quadrature():
    """锚点自洽：闭式 vs 独立数值积分的 Cb/KB/BM 三量都必须 <0.5%。"""
    for body in vh.BODIES:
        vol, kb, bm = vh.integrate_body(body)
        assert vol == pytest.approx(body.cb_exact, rel=0.01), body.key
        assert kb == pytest.approx(body.kb_over_t, rel=0.01), body.key
        assert bm == pytest.approx(body.bm_over_b2t, rel=0.01), body.key


def test_platform_kernel_exact_on_prismatic_family():
    """矩形（Cb=1）与三角棱柱（Cb=0.5）上平台闭式精确 —— 梯形族适用域内无缺陷。"""
    assert vh._hydro.kb_from_cb(1.0, 1.0) == pytest.approx(0.5, abs=1e-12)
    assert vh._hydro.bm_transverse(1.0, 1.0, 1.0) == pytest.approx(1.0 / 12.0, abs=1e-12)
    assert vh._hydro.kb_from_cb(1.0, 0.5) == pytest.approx(2.0 / 3.0, abs=1e-12)
    assert vh._hydro.bm_transverse(1.0, 1.0, 0.5) == pytest.approx(1.0 / 6.0, abs=1e-12)


@pytest.mark.parametrize(
    "cb,kb_exact,bm_exact",
    [
        (3.141592653589793 / 6.0, 0.625, 3.0 / 32.0),   # 半椭球（自龙骨起算）
        (4.0 / 9.0, 0.625, (16.0 / 35.0) * (9.0 / 4.0) / 12.0),  # Wigley
    ],
)
def test_curved_form_error_is_registered_debt(cb, kb_exact, bm_exact):
    """曲剖面族：平台 KB/BM 偏差 >15%（非保守方向），登记为已知欠账而非静默。"""
    kb_plat = vh._hydro.kb_from_cb(1.0, cb)
    bm_plat = vh._hydro.bm_transverse(1.0, 1.0, cb)
    assert abs(vh.rel_err(kb_plat, kb_exact)) > 15.0 or abs(vh.rel_err(bm_plat, bm_exact)) > 15.0
    # 误差方向必须是偏大（KM 高估 ⇒ 非保守），若哪天变成偏小要重新裁定
    assert vh.rel_err(bm_plat, bm_exact) > 0.0


def test_cb_wigley_matches_classic_four_ninths():
    """Wigley 型的 Cb=4/9 必须由独立积分复现（截面形状定义的唯一自证）。"""
    wig = [b for b in vh.BODIES if b.key == "wigley"][0]
    vol, _, _ = vh.integrate_body(wig)
    assert vol == pytest.approx(4.0 / 9.0, rel=0.01)
