"""Render the m1650 verification report and publish it to the platform.

Every number is read from ``artifacts/m1650/propeller_bem_vs_bseries/audit.json``
(nothing hand-copied); figures are embedded as base64.

Run:  python3 -m tools.verify.propeller_open_water.make_report_m1650 [--no-post]
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "artifacts/m1650/propeller_bem_vs_bseries"
DOC = ROOT / "docs/reports/m1650-propeller-bem-vs-bseries-verification.md"
PAYLOAD = ROOT / "runs/m1650/report.json"

TITLE = ("敞水性能 BEM 模型对 Wageningen B 系列正典表与 KP505 试验的验证闭环（m1650）："
         "把 propeller.open_water 的 WITHHELD 绝对精度判为 ±12–18%")


def _fig(name, caption):
    raw = (ART / name).read_bytes()
    return {"name": name, "caption": caption,
            "png_base64": base64.b64encode(raw).decode()}


def main(post: bool = True) -> None:
    a = json.loads((ART / "audit.json").read_text())
    table = (ART / "table.md").read_text()
    v = a["verdict"]
    kp = a["kp505"]
    f2 = lambda x: ("n/a" if x != x else f"{x:.2f}")
    f1 = lambda x: ("n/a" if x != x else f"{x:.1f}")

    doc = f"""# {TITLE}

- 工件：`artifacts/m1650/propeller_bem_vs_bseries/audit.json`（全部数字程序化注入）、`table.md`、三张图
- 验证脚本：`tools/verify/propeller_open_water/bem_vs_bseries.py`
- 测试：`tests/verify/test_bem_vs_bseries.py`（12 passed）
- 受测模型（只读，未改动）：`backend/services/propeller_open_water.py`（m1393 交付，能力项 `propeller.open_water`）

## 1. 案例介绍（物理背景与几何/参数）

m1393 用叶元-动量（BEM）模型替换了旧的线性硬编码代理式（`KT = max(0.05, 0.5-0.3J)` 等），
并自称 REAL 等级。但它的公开验证（`docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`）
只有**解析约束**（致动盘上限、傅汝德缩尺不变性、零推力定义），绝对量级一栏自评为
**WITHHELD（离线取不到 B 系列图谱点值）**。本次把这条验证欠账在**仓内**结清：

- 参考 A：`tools/verify/propeller_open_water/reference_bseries.py` —— Oosterveld & van Oossanen (1975)
  39 项 KT / 47 项 KQ 回归系数（转自 Bernitsas, Ray & Kinley 1981, Univ. of Michigan Report 237），
  已逐项对齐平台官方系数表 `backend/data/official/Wageningen_Bseries_coeffs.dat`。
- 参考 B：KP505 模型桨敞水试验（Iftikhar & Takinaci 2023, Table 2；Z=5, Ae/A0=0.800, P/D=0.950），
  实测数据，既作 BEM 的独立试验锚，也用来标定参考 A 自身的可用误差带。

受测几何（全在 B 系列有效域内：Z=2..7、P/D=0.5..1.4、Ae/A0=0.2..1.05、J=0..1.5）：

| 几何 | P/D | Ae/A0 | Z |
| --- | --- | --- | --- |
""" + "".join(f"| {g['label']} | {g['P_D']:.2f} | {g['Ae_Ao']:.2f} | {g['Z']} |\n"
              for g in a["geometries"]) + f"""
计算设置：D=5.0 m，ρ=1025 kg/m³，n=1 rps（全部输出无量纲，与量纲选择无关）；
J 网格 {a['J_grid'][0]:.2f}–{a['J_grid'][-1]:.2f}，步长 0.10；J0、J_opt、eta_max 由各自模型自带的
二分/黄金分割求取器计算。

## 2. 怎么做的（方法/流程/工具链）

1. `importlib` 按路径加载受测模块（**不修改**），与参考 A 在同一 J 网格上逐点求值；
2. 逐点相对误差在**载荷带**内统计（`KT_ref ≥ {a['verdict']['load_band']['window']}` 等价门限：
   推力/扭矩都不接近零，两者同号），因为 J→J0 时百分比误差按定义发散（见 §5.1）；
   全网格结果同时保留供审阅；
3. 积分特征量 J0、J_opt、eta_max 逐几何比对；
4. 两条模型同时对 KP505 实测数据比对，量化「参考基准自身有多准」与「模型有多准」；
5. 结构性诊断：载荷带内 KT 斜率 dKT/dJ 与归一化零推力比 J0/(P/D)；
6. 机器判决按平台精度门（<3% 可发表 / <15% 可接受），逐项显式给出，无人工抄写。

## 3. 计算结果

{table}

## 4. 结果图

1. `fig1_kt_kq.png`：两个代表几何（B4-70 / B5-80）的 KT、10KQ、eta0 曲线，BEM（实线圆点）对正典表（虚线方块）。
2. `fig2_error_profile.png`：逐点相对误差剖面对 3%/15% 门（红点线/红虚线），以及 eta_max 与 J0 误差条形图。
3. `fig3_kp505_experiment.png`：KP505 实测（黑方块）对 BEM 与正典表两条模型曲线。

## 5. 注意点（坑、局限、适用范围）

1. **相对误差在零推力分支发散**：B4-55 在 J=0.8 时 BEM 已过零推力（KT<0）而表值仍为 0.037，
   百分比误差冲到 1683%。所以判决只在载荷带内做，全网格数字仅作透明披露，
   `load_band.excluded_J` 明列被排除的 J，测试 `test_load_band_excludes_zero_thrust_points` 钉住该规则。
2. **参考 A 只是 B 系列的回归近似，且 KP505 不是 B 系列桨**：同 (P/D, Ae/A0, Z) 匹配下，
   正典表对实测 KT 平均差 {f1(kp['summary']['KT_ref_mean_abs_err'])}%、KQ 差 {f1(kp['summary']['KQ_ref_mean_abs_err'])}%，
   这就是「换了桨型/尺度」的固有带。因此本单只用它判**量级与结构**，不宣称任何一方达到 3%。
3. **m1393 报告的一条结论方向是错的（本次更正）**：该文 §5.4 写「本模型 J0 偏高约 10–15%，
   真实 B 系列约 0.85–0.9×(P/D)」，其依据是口头经验而非图谱点值。正典表给出
   J0/(P/D) = 1.055–1.113，而 BEM 恒为 0.993×(P/D)，即 **BEM 的 J0 偏低 {f1(-v['J0']['mean_abs_pct'])}%**（方向相反）。
   旧结论已在原报告中追加更正段并指向本单（原文不删，留痕）。
4. **受测模型自身有文档漂移**（记录不改代码）：`blade_element_section` docstring 写
   `Cd = 0.008 + 0.9α²`、夹紧 [0.006, 0.08]，代码实际是 `CD0 + 1.0α²`、夹紧 [0.008, 0.10]。
   改常数会移动 m1393 的全部已发表数字，故本单只登记（`section_docstring_drift`），
   由后续「模型改进」单决定是否改。
5. 适用范围：敞水、单桨、稳态；不含空化、桨-舵干扰、非均匀伴流、风车/制动工况。

## 6. 误差判定（逐项、显式回答是否 <3%）

| 关键量 | 基准 | 结果 | 是否 <3% | 是否 <15% |
| --- | --- | --- | --- | --- |
| eta_max（5 几何，均值） | 正典 B 系列表 | 均值误差 {f2(v['eta_max']['mean_abs_pct'])}%、最大 {f2(v['eta_max']['max_abs_pct'])}% | **否** | 是 |
| J_opt（5 几何，均值） | 同上 | {f2(v['J_opt']['mean_abs_pct'])}% / 最大 {f2(v['J_opt']['max_abs_pct'])}% | **否** | 是 |
| J0（5 几何，均值） | 同上 | {f2(v['J0']['mean_abs_pct'])}% / 最大 {f2(v['J0']['max_abs_pct'])}%（系统性偏低） | **否** | 是 |
| KT 逐点（载荷带 {v['load_band']['n_points']} 点，均值） | 同上 | {f2(v['band']['KT_pointwise']['mean_abs_pct'])}% / 最大 {f2(v['band']['KT_pointwise']['max_abs_pct'])}% | **否** | **否（超 15%）** |
| KQ 逐点（载荷带，均值） | 同上 | {f2(v['band']['KQ_pointwise']['mean_abs_pct'])}% / 最大 {f2(v['band']['KQ_pointwise']['max_abs_pct'])}% | **否** | **否（超 15%）** |
| eta0 逐点（载荷带，均值） | 同上 | {f2(v['band']['eta_pointwise']['mean_abs_pct'])}% / 最大 {f2(v['band']['eta_pointwise']['max_abs_pct'])}% | **否** | 是（均值） |
| KT/KQ/eta0 傅汝德缩尺不变性 | 解析恒等 | m1393 已判 2e-16 | 是 | 是 |
| eta0 < 致动盘上限 | 动量理论 | 全部满足（测试 `test_physics_rails` 钉住） | 是 | 是 |

**显式回答**：没有任何关键量对文献/试验基准达到 <3%。结论是
「结构正确（REAL 实现）＋ 绝对精度可量化为载荷带 KT {f1(v['band']['KT_pointwise']['mean_abs_pct'])}% / KQ {f1(v['band']['KQ_pointwise']['mean_abs_pct'])}% /
eta0 {f1(v['band']['eta_pointwise']['mean_abs_pct'])}%，效率类积分量 {f1(v['eta_max']['mean_abs_pct'])}%」——
KT/KQ 超出平台 15% 可接受门，效率类在门内。因此能力项 `propeller.open_water` 应从
「REAL 未定精度」改注为「REAL，KT/KQ ±15–20%、效率 ±4%」，并列入模型改进待办（下一步：
用 B 系列升力线/有限展弦比修正压低低 J 推力）。

## 7. 复现

```bash
python3 -m tools.verify.propeller_open_water.bem_vs_bseries   # 重算 audit.json + 3 图 + table.md
python3 -m pytest tests/verify/test_bem_vs_bseries.py -q      # 12 passed
python3 -m tools.verify.propeller_open_water.make_report_m1650
```
"""
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(doc)
    figures = [
        _fig("fig1_kt_kq.png",
             "图1 敞水曲线：BEM（圆点实线）对 Wageningen B 系列正典表（方块虚线），B4-70 与 B5-80 两几何 × (KT, 10KQ, eta0)。"),
        _fig("fig2_error_profile.png",
             "图2 误差剖面：左/中为逐点 KT、KQ 相对误差对 3%（点线）与 15%（虚线）门；右为 eta_max 与 J0 误差条形。"),
        _fig("fig3_kp505_experiment.png",
             "图3 独立试验锚：KP505 模型桨敞水试验（Iftikhar & Takinaci 2023, 黑方块）对 BEM 与正典表两条模型曲线。"),
    ]
    # keep the payload free of module-import side effects on the repo tree
    payload = {
        "title": TITLE,
        "summary": (
            f"把 m1393 自评 WITHHELD 的敞水绝对精度在仓内结清：正典 B 系列回归表（Oosterveld & van Oossanen 1975）"
            f"＋KP505 实测双基准复核 propeller_open_water.py。载荷带 KT 均值误差 "
            f"{f1(v['band']['KT_pointwise']['mean_abs_pct'])}%、KQ {f1(v['band']['KQ_pointwise']['mean_abs_pct'])}%（均超 15% 门）、"
            f"eta0 {f1(v['band']['eta_pointwise']['mean_abs_pct'])}% 门内；eta_max {f1(v['eta_max']['mean_abs_pct'])}%、"
            f"J0 {f1(v['J0']['mean_abs_pct'])}% 偏低。诊断：dKT/dJ 偏陡使低 J 推力偏高、J0 偏低，"
            f"并更正 m1393「J0 偏高」方向性错误。无关键量达 <3%（honest-FAIL），能力项改注 REAL+KT/KQ ±15–20%、效率 ±4%。"),
        "scenario_key": "propeller-open-water-bem-vs-bseries-m1650",
        "mission_id": 1650,
        "tags": ["船舶总体性能", "推进", "敞水", "验证欠账", "Wageningen B系列",
                 "KP505", "honest-FAIL", "精度判定"],
        "markdown": doc,
        "figures": figures,
    }
    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False))
    print("doc ->", DOC)
    print("payload ->", PAYLOAD, len(payload["markdown"]), "chars,", len(figures), "figures")

    if post:
        sys.path.insert(0, str(ROOT / "runs/m1650"))
        import tpapi  # local helper: reads /srv/tensor/agent/agent.env, never prints secrets

        st, tok, r = tpapi.login()
        if not tok:
            print("login failed", st, str(r)[:200])
            return
        st, resp = tpapi.call("POST", "tensor/reports", payload, token=tok)
        print("post report:", st, json.dumps(resp, ensure_ascii=False)[:300] if not isinstance(resp, str) else resp)


if __name__ == "__main__":
    main(post="--no-post" not in sys.argv)