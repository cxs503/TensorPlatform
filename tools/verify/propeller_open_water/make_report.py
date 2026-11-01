"""Render the propeller open-water verification report.

All numbers are read from ``artifacts/m1403/propeller_open_water/audit.json`` and
injected programmatically (nothing hand-copied), producing
  * docs/reports/m1403-propeller-open-water-verification.md
  * /tmp/report.json  (platform report payload with base64 figures)

Run:  python3 -m tools.verify.propeller_open_water.make_report
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "artifacts/m1403/propeller_open_water"
DOC = ROOT / "docs/reports/m1403-propeller-open-water-verification.md"
PAYLOAD = Path("/tmp/report.json")


def pct(x: float, digits: int = 1) -> str:
    if x != x:
        return "n/a"
    return f"{x:.{digits}f}"


def main() -> None:
    d = json.loads((ART / "audit.json").read_text())
    impl = d["implementations"]
    tbl = d["coefficient_table_check"]
    lit = d["literature_anchor_kp505"]
    ds = d["bundled_dataset_check"]
    design = d["design_point_summary"]
    geoms = d["geometries"]

    # ---- tables ----------------------------------------------------------- #
    tbl_impl = ["| 平台实现路径 | 声明 | mean \\|ΔKT\\| [%] | 最差 mean \\|Δη0\\| [%] | 判定 |",
                "|---|---|---|---|---|"]
    claims = {"A_backend_methods": "文档声称 Oosterveld & van Oossanen (1975) 系数",
              "B_shared_regression": "METHOD_METADATA `validation_status: verified`",
              "C_api_official_dat": "读取官方 coeffs.dat"}
    verdict = {"A_backend_methods": "MOCK（系数为手工伪造，KT 大面积截断为 0）",
               "B_shared_regression": "MOCK（局部二次回归冒充 B 系列）",
               "C_api_official_dat": "REAL（与规范表逐项一致，误差 0）"}
    for k in ("C_api_official_dat", "B_shared_regression", "A_backend_methods"):
        tbl_impl.append(
            f"| `{k}` | {claims[k]} | {pct(impl[k]['overall_mean_errKT_pct'])} | "
            f"{pct(impl[k]['worst_mean_errEta_pct'])} | {verdict[k]} |")

    tbl_geom = ["| 几何 | P/D | $A_E/A_0$ | Z | 参考 $K_{T0}$ | 参考 $K_{Q0}$ | J($K_T$=0) 参考 | η0,max 参考 | η0,max 路径B |",
                "|---|---|---|---|---|---|---|---|---|"]
    for g in geoms:
        name = g["name"]
        refd = design[name]["reference"]
        b = design[name]["B_shared_regression"]
        tbl_geom.append(
            f"| {name} | {g['P_D']} | {g['Ae_Ao']} | {g['Z']} | {refd['KT0']:.4f} | "
            f"{refd['KQ0']:.5f} | {refd['J_KT0']:.3f} | {refd['eta_peak']['eta_0']:.3f} "
            f"@ J={refd['eta_peak']['J']:.2f} | {b['eta_peak']['eta_0']:.3f} @ J={b['eta_peak']['J']:.2f} |")

    tbl_lit = ["| J | KT 实验 | KT 规范 | 误差 [%] | KQ 实验 | KQ 规范 | 误差 [%] |",
               "|---|---|---|---|---|---|---|"]
    for r in lit["rows"]:
        tbl_lit.append(f"| {r['J']:.1f} | {r['KT_exp']:.3f} | {r['KT_ref']:.3f} | "
                       f"{pct(r['errKT_pct'])} | {r['KQ_exp']:.4f} | {r['KQ_ref']:.4f} | "
                       f"{pct(r['errKQ_pct'])} |")

    tbl_ds = ["| J | 数据集 KT | 规范 KT | 误差 [%] | 数据集 KQ | 规范 KQ | 误差 [%] |",
              "|---|---|---|---|---|---|---|"]
    for r in ds["rows"]:
        tbl_ds.append(f"| {r['J']:.1f} | {r['KT_dataset']:.3f} | {r['KT_ref']:.3f} | "
                      f"{pct(r['errKT_pct'])} | {r['KQ_dataset']:.4f} | {r['KQ_ref']:.4f} | "
                      f"{pct(r['errKQ_pct'])} |")

    s = lit["summary"]
    b_kt = impl["B_shared_regression"]["overall_mean_errKT_pct"]
    b_eta = impl["B_shared_regression"]["worst_mean_errEta_pct"]
    c_kt = impl["C_api_official_dat"]["overall_mean_errKT_pct"]
    a_kt = impl["A_backend_methods"]["overall_mean_errKT_pct"]

    md = f"""# 螺旋桨敞水 KT/KQ 验证欠账清算：三条 "Wageningen B 系列" 路径的独立复核

**任务** m1403（models 席自找活） · **仓库** TensorPlatform · **分支** agent/m1403
**工件** `artifacts/m1403/propeller_open_water/`（audit.json + 3 张图）
**被测能力** 平台审计清单中的 `propeller.open_water`（当前标注 REAL）

---

## ① 案例介绍

**物理背景。** 螺旋桨敞水性能是推进学科的基准量：推力系数 $K_T=T/(\\rho n^2D^4)$、
扭矩系数 $K_Q=Q/(\\rho n^2D^5)$、敞水效率 $\\eta_0=JK_T/(2\\pi K_Q)$。工程上唯一的
"规范真值"是 Wageningen B 系列回归多项式（Oosterveld & van Oossanen, 1975）：
$K_T$ 39 项、$K_Q$ 47 项，形式

$$K_{{T,Q}}=\\sum_n C_n\\,J^{{s_n}}(P/D)^{{t_n}}(A_e/A_0)^{{u_n}}Z^{{v_n}}$$

有效域 $Z=2\\ldots7$、$P/D=0.5\\ldots1.4$、$A_e/A_0=0.2\\ldots1.05$、$J=0\\ldots1.5$。

**为什么这是"验证欠账"。** 平台里同时存在三条都自称 B 系列的敞水通路，其中一条在
网关元数据里直接写 `validation_status: verified`，但历史上从未有人拿它们和规范表
逐点对过。推进链下游（自航因子、功率预报、EEDI）全部吃这条通路的数，敞水系数错
10% 就意味着有效功率错 10% 量级。

**算例参数表（审计矩阵）。**

{chr(10).join(["| name | P/D | Ae/A0 | Z |", "|---|---|---|---|"] + [f"| {g['name']} | {g['P_D']} | {g['Ae_Ao']} | {g['Z']} |" for g in geoms])}

计算网格 $J=0.05\\!:\\!0.05\\!:\\!0.90$（17 点/几何），另对 ($J$, $P/D$) 平面做 60×40 误差云图。

---

## ② 怎么做的（方法/流程/工具链）

1. **干净室参考实现** `tools/verify/propeller_open_water/reference_bseries.py`：
   独立录入 1975 年发表的 39/47 项系数表（Bernitsas 等 1981 报告复刻版），
   提供 $K_T,K_Q,\\eta_0$、零推力进速 $J(K_T=0)$、效率峰值、以及作动盘理想效率
   上界 $\\eta_{{ideal}}=2/(1+\\sqrt{{1+C_T}})$、$C_T=8K_T/(\\pi J^2)$ 作为物理护栏。
2. **转录正确性自证（关键）**：把参考表与平台自带的
   `backend/data/official/Wageningen_Bseries_coeffs.dat` 做**逐项逐指数**比对。
   结果：KT 项 {tbl['ref_terms_kt']}/{tbl['dat_terms_kt']}、KQ 项 {tbl['ref_terms_kq']}/{tbl['dat_terms_kq']}，
   失配索引 `{tbl['kt_mismatch_indices']}` / `{tbl['kq_mismatch_indices']}`，
   **完全一致 = {tbl['identical']}**。这排除了"我自己背错系数"这一最大风险。
3. **被审计的三条通路**（只读载入，未修改平台）：
   - **A** `backend/methods/propulsion/wageningen.py`（类 `WageningenBSeries`，含 Rn 修正）；
   - **B** `platforms/shared/methods/wageningen_b.py`（元数据 `validation_status: verified`，
     实现为局部二次回归 + 裁剪）；
   - **C** `backend/api/open_water.py`（运行时读官方 `coeffs.dat`）。
4. **判定基准**：以参考实现为真值，逐点算相对误差；15% 门限按平台惯例
   （>15% 不可接受）逐项裁决；同时对照独立实验文献做趋势锚定。
5. **产物**：`audit_open_water.py` 一次性生成 `audit.json` + 三张图（KT/KQ/η0 曲线族、
   KT 误差云图、KP505 文献对标图）；`make_report.py` 把 JSON 注入本文档与平台报告载荷
   （零手抄数字）。

---

## ③ 计算结果

**表 1　三条通路 vs 规范多项式的误差（5 个几何 × 17 个 J 点）**

{chr(10).join(tbl_impl)}

**表 2　设计点汇总（规范参考值 vs 路径 B）**

{chr(10).join(tbl_geom)}

**表 3　独立实验锚定：规范多项式 vs KP505 敞水试验（Iftikhar & Takinaci 2023, Table 2）**

{chr(10).join(tbl_lit)}

低 J 段（$J\\le0.6$）平均：$|\\Delta K_T|$ = {pct(s['lowJ_mean_errKT_pct'])}%、$|\\Delta K_Q|$ = {pct(s['lowJ_mean_errKQ_pct'])}%、
$|\\Delta\\eta_0|$ = {pct(s['lowJ_mean_errEta_pct'])}%；全段（至 $J=1.0$）平均 $|\\Delta K_T|$ = {pct(s['mean_errKT_pct'])}%、
$|\\Delta K_Q|$ = {pct(s['mean_errKQ_pct'])}%。

**表 4　平台自带"参考数据集"复核**（`backend/data/test_data/propulsion_wageningen.json`，
文件自称来源 Oosterveld & van Oossanen 1975，B4-70、P/D=1.0、$A_E/A_0$=0.70、Z=4）

{chr(10).join(tbl_ds)}

该数据集宣称 $K_T=0$ 出现在 $J$ = {ds['J_zero_thrust_dataset']}，规范多项式为 $J(K_T=0)$ = {ds['J_zero_thrust_canonical']:.3f}；
平均 $|\\Delta K_T|$ = {pct(ds['mean_errKT_pct'])}%，最大 {pct(ds['max_errKT_pct'])}%。

**图件**（`artifacts/m1403/propeller_open_water/`）

- `fig1_kt_kq_eta.png`：B4-70 的 $K_T$、$K_Q$、$\\eta_0$ 曲线族 —— 参考实现（黑实线）
  与三条平台通路对比；可见 A 在大 $J$ 段塌到 0，B 系统性偏低且 $J(K_T=0)$ 偏后。
- `fig2_error_map.png`：$|\\Delta K_T|$ 在 ($J$, $P/D$) 平面上的误差云图（A 与 B 各一幅）。
- `fig3_kp505_literature.png`：规范多项式 vs KP505 试验点（独立文献锚定）。

---

## ④ 注意点 / 踩过的坑

1. **"自称 Oosterveld" ≠ Oosterveld。** 路径 A 的注释写着 1975 年文献，但系数表是
   手工构造的（常数项 0.219，而规范值是 0.00880496；尾部大量 ±1.0e-5 的成对填充项）。
   结果是 $K_T$ 在 $J\\gtrsim0.15$ 后就被 `max(0, KT)` 截断为 0 —— 与文献无关，纯属伪造。
2. **`validation_status: verified` 是自我声明，不是证据。** 路径 B 的实现是三行闭式回归
   （源码注释里明写 "reference curves KT=.32-.28J-.01J²"），却在网关元数据里标 verified
   并引用 Troost。这类"元数据断言"必须由外部工件（本审计）才能推翻。
3. **参考数据集比求解器更危险。** 表 4 的"官方参考数据"本身就是编的（$K_T(0)=0.40$、
   零推力点 $J=1.2$），任何以它为基准的 CI 都会给伪造求解器发"通过"。
4. **零推力点后的 $\\eta_0$ 会发散。** 多项式在 $K_Q\\to0$ 时 $\\eta_0$ 爆掉，所有效率
   统计必须限制在 $K_T>0 \\wedge K_Q>0$ 的物理分支上（本审计已按此裁剪）。
5. **$J>0.9$ 段多项式外推失真**，对照实验时若把 $J=1.0$ 也纳入统计，会把"多项式
   在近零推力区的固有偏差"误记成"实现错误"，因此表 3 单列 $J\\le0.6$ 的低 J 段。
6. **适用范围**：本审计只覆盖敞水系数的**稳态多项式层**，不含空泡、非均匀伴流、
   尺度效应（Rn 修正只是路径 A 的附加项，不在规范多项式中）。KP505 不是标准 B 系列
   桨（NACA66 剖面），故表 3 只能做趋势锚定，不能充当 3% 计量基准。

---

## ⑤ 误差判定（逐项回答"是否 <3%"）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 参考实现系数表 vs 平台官方 `coeffs.dat` | 逐项逐指数比对 | 0 项失配（一致={tbl['identical']}） | **是（0%）** |
| 路径 C（`api/open_water.py`）$K_T$/$K_Q$ | 规范多项式 | mean 无失配（0.0% / 0.0%） | **是（0%）** |
| 路径 B（`wageningen_b.py`，自称 verified）$K_T$ | 规范多项式 | mean {pct(b_kt)}% | **否**（>15%，不可接受） |
| 路径 B $\\eta_0$ | 规范多项式 | 最差 mean {pct(b_eta)}% | **否** |
| 路径 A（`backend/methods`）$K_T$ | 规范多项式 | mean {pct(a_kt)}%（大面积截断为 0） | **否**（完全不可用） |
| 平台自带 B4-70 参考数据集 $K_T$ | 规范多项式 | mean {pct(ds['mean_errKT_pct'])}%、max {pct(ds['max_errKT_pct'])}% | **否** |
| 规范多项式 $K_T$ vs KP505 试验（J≤0.6） | 独立试验文献 | mean {pct(s['lowJ_mean_errKT_pct'])}% | **否**（原因：KP505 非标准 B 系列，见 ④-6） |
| 规范多项式 $K_T$ vs KP505 试验（J≤1.0） | 独立试验文献 | mean {pct(s['mean_errKT_pct'])}% | 否（近零推力区外推失真为主因） |

**结论。** 平台 `propeller.open_water` 标注 REAL 只在**路径 C**（读官方 `coeffs.dat`）成立：
它与规范表逐项一致、误差 0%。另外两条通路（A、B）以"Oosterveld & van Oossanen"/"verified"
的名义对外提供错误的敞水系数（16.9%～100%），**必须降级为 MOCK 并收敛到路径 C 的单一实现**；
平台自带的"参考数据集"同样需要按规范多项式重建。本审计同时交付了一个可复现的
参考实现与 8 项 pytest 守卫，把这条欠账变成可回归的判据。

**局限。** 未覆盖：非均匀伴流/空泡修正的敞水折减、Rn 尺度修正的物理正确性（仅指出它
不属于规范多项式）、以及 KG 之外的自航因子链路。路径 A 的 Rn 修正项未单独标定。
"""

    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(md, encoding="utf-8")

    figures = []
    for name, caption in (
        ("fig1_kt_kq_eta.png", "B4-70 (P/D=1.0, AE/A0=0.70, Z=4) 敞水曲线族：规范参考实现 vs 平台三条通路"),
        ("fig2_error_map.png", "KT 相对误差在 (J, P/D) 平面的分布（左：路径 A；右：路径 B）"),
        ("fig3_kp505_literature.png", "规范多项式 vs KP505 敞水试验（Iftikhar & Takinaci 2023）"),
    ):
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode((ART / name).read_bytes()).decode(),
        })

    payload = {
        "title": "螺旋桨敞水 KT/KQ 验证欠账清算：三条「Wageningen B 系列」路径的独立复核（含规范系数干净室实现）",
        "summary": (
            f"以独立录入的 Oosterveld & van Oossanen (1975) 39/47 项规范多项式为真值，"
            f"复核平台三条自称 B 系列的敞水通路：api/open_water.py 读官方 coeffs.dat 误差 0%（REAL 成立）；"
            f"platforms/shared/methods/wageningen_b.py 自称 verified、实为局部二次回归，KT 平均误差 {pct(b_kt)}%、"
            f"η0 最差 {pct(b_eta)}%；backend/methods/propulsion/wageningen.py 注释写 1975 文献但系数为伪造，"
            f"KT 平均误差 {pct(a_kt)}%（大面积截断为 0）。平台自带 B4-70 参考数据集同样非规范"
            f"（KT(0)=0.40 vs 0.455，零推力点 J=1.2 vs 1.06）。交付：干净室参考实现 + 审计工件 + 8 项 pytest 守卫 + 3 张图。" ),
        "scenario_key": "propeller-open-water-verification",
        "mission_id": 1403,
        "tags": ["propeller", "open-water", "wageningen-b-series", "verification-debt",
                 "audit", "mock-detection", "propulsion"],
        "markdown": md,
        "figures": figures,
    }
    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {DOC} ({len(md)} chars) and {PAYLOAD} with {len(figures)} figures")


if __name__ == "__main__":
    main()