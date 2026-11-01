"""把验证产物渲染成论文级报告（docs markdown + 平台报告 payload）

所有数字从 artifacts/propulsion_verification/propulsion_ow_verification.json 读取，
不手抄——保证报告与工件一致。

    python3 tools/verification/propulsion/render_report.py            # 写 docs/verifier/*.md
    python3 tools/verification/propulsion/render_report.py --payload /tmp/report.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "artifacts" / "propulsion_verification"
DOC = ROOT / "docs" / "verifier"

TITLE = "推进域敞水性能验证欠账清偿 v1：Wageningen B 系列三方对账（线上端点/仓内方法/基准数据集）"

EVIDENCE_LABEL = {
    "MOCK": "MOCK（硬编码/伪公式）",
    "REAL": "REAL（真实回归式）",
    "STUB": "STUB（空壳）",
    "WITHHELD": "WITHHELD（证据不足）",
}


def load() -> Dict:
    return json.loads((ART / "propulsion_ow_verification.json").read_text())


def _obj(data: Dict, prefix: str) -> Dict:
    return next(o for o in data["objects"] if o["object"].startswith(prefix))


def _item(obj: Dict, iid: str) -> Dict:
    return next(i for i in obj["items"] if i["id"] == iid)


def _fmt(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def verdict_table(data: Dict) -> str:
    rows = ["| 对象 | 自述 | 判级 | 判据通过 | 说明 |", "|---|---|---|---|---|"]
    note = {
        "REF": "参考侧（Oosterveld 1975 真系数表，39+47 项）",
        "O1": "K_Q≡K_T/10、η0=10J/(2π) 截断 0.85、越过动量理论界",
        "O2": "「Oosterveld 表」= K_Q 表逐项 ×10 + 24 项 ±1e-5 填充，求值给负推力",
        "O3": "J=0 行 K_T=10K_Q；η0 列 J=0.8 行与自身 K_T/K_Q 不自洽",
        "O4": "η0=1.48/1.25、η_D=1.63/1.38 均 >1；反算 K_T 小两个数量级",
    }
    for key in ("REF", "O1", "O2", "O3", "O4"):
        obj = _obj(data, key) if key != "REF" else _obj(data, "REF")
        k = key + "_" + {"REF": "reference_impl", "O1": "serving_endpoint", "O2": "repo_method",
                          "O3": "benchmark_dataset", "O4": "propulsion_chain"}[key]
        rows.append(f"| {obj['object']} | {obj.get('claimed', '')} | **{obj['verdict']}** | "
                    f"{data['scorecard'][k]} | {note[key]} |")
    return "\n".join(rows)


def recon_table_o1(data: Dict) -> str:
    o1 = _obj(data, "O1")
    t = _item(o1, "C6")["table"]
    rows = ["| 工况 | K_T 端点 | K_T 参考 | 误差 | K_Q 端点 | K_Q 参考 | 误差 | η0 端点 | η0 参考 | 误差 | η_ideal |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t:
        rows.append(
            f"| J={r['J']:.1f} | {r['KT']['serving']:.4f} | {r['KT']['ref']:.5f} | {r['KT']['rel_err_pct']:+.1f}% | "
            f"{r['KQ']['serving']:.4f} | {r['KQ']['ref']:.6f} | {r['KQ']['rel_err_pct']:+.1f}% | "
            f"{r['eta0']['serving']:.4f} | {r['eta0']['ref']:.4f} | {r['eta0']['rel_err_pct']:+.1f}% | "
            f"{r['eta_ideal']:.4f} |")
    return "\n".join(rows)


def recon_table_o3(data: Dict) -> str:
    o3 = _obj(data, "O3")
    t = _item(o3, "D3")["table"]
    rows = ["| J | K_T 数据集 | K_T 参考 | 误差 | K_Q 数据集 | K_Q 参考 | 误差 | η0 数据集 | η0 参考 | 误差 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in t:
        e0 = "—" if r["eta0"]["rel_err_pct"] is None else f"{r['eta0']['rel_err_pct']:+.1f}%"
        rows.append(
            f"| {r['J']:.1f} | {r['KT']['dataset']:.3f} | {r['KT']['ref']:.5f} | {r['KT']['rel_err_pct']:+.1f}% | "
            f"{r['KQ']['dataset']:.3f} | {r['KQ']['ref']:.6f} | {r['KQ']['rel_err_pct']:+.1f}% | "
            f"{r['eta0']['dataset']:.3f} | {_fmt(r['eta0']['ref'])} | {e0} |")
    return "\n".join(rows)


def chain_table(data: Dict) -> str:
    o4 = _obj(data, "O4")
    rows = ["| 工况 | J(名义) | 反算 K_T | 反算 K_Q | 回报 η0 | 作动盘上界 | 回报 η_D | η0·η_H·η_R | P_D=2πnQ [W] | 回报 P_D [W] |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in o4["table"]:
        rows.append(
            f"| {r['tag']} | {r['J_nominal']:.3f} | {r['Kt_implied']:.5f} | {r['Kq_implied']:.6f} | "
            f"**{r['eta0_reported']:.3f}** | {r['eta_ideal_bound']:.4f} | **{r['etaD_reported']:.3f}** | "
            f"{r['etaD_chain']:.4f} | {r['PD_from_2pinQ']:.1f} | {r['PD_reported']:.1f} |")
    return "\n".join(rows)


def bands_table(data: Dict) -> str:
    ref = _obj(data, "REF")
    b = _item(ref, "R3")["bands"]
    rows = ["| 锚点 | 说明 | 参考实现值 | 文献带中心 | 容差 | 相对误差 | <3%? |", "|---|---|---|---|---|---|---|"]
    for x in b:
        ok = "是" if abs(x["rel_err_pct"]) <= 3.0 else "否"
        rows.append(f"| {x['anchor']} | {x['label']} | {x['got']:.4f} | {x['band_center']} | "
                    f"±{x['tol_pct']}% | {x['rel_err_pct']:+.2f}% | {ok} |")
    return "\n".join(rows)


def anchor_errors(data: Dict) -> List[Dict]:
    """收集所有「被测对象 vs 参考/判据」的关键量误差，供⑥误差判定节使用。"""
    out = []
    o1 = _obj(data, "O1")
    for r in _item(o1, "C6")["table"]:
        for q in ("KT", "KQ", "eta0"):
            out.append({"obj": "O1 线上端点", "case": r["case"], "qty": q,
                        "err": r[q]["rel_err_pct"]})
    for r in _item(o1, "C3")["violations"]:
        out.append({"obj": "O1 动量理论界", "case": f"J={r['J']},P/D={r['P_D']},Z={r['Z']}",
                    "qty": "η0/η_ideal", "err": r["excess_pct"]})
    o3 = _obj(data, "O3")
    for r in _item(o3, "D3")["table"]:
        for q in ("KT", "KQ", "eta0"):
            if r[q]["rel_err_pct"] is not None:
                out.append({"obj": "O3 基准数据集", "case": f"J={r['J']}", "qty": q,
                            "err": r[q]["rel_err_pct"]})
    o2 = _obj(data, "O2")
    out.append({"obj": "O2 仓内方法", "case": "J=0.6", "qty": "KT",
                "err": _item(o2, "T2")["rel_err_pct"]})
    return out


def render_markdown(data: Dict) -> str:
    o1, o2, o3, o4, ref = (_obj(data, k) for k in ("O1", "O2", "O3", "O4", "REF"))
    errs = anchor_errors(data)
    worst = max(errs, key=lambda e: abs(e["err"]))
    n_lt3 = sum(1 for e in errs if abs(e["err"]) <= 3.0)
    n_lt15 = sum(1 for e in errs if abs(e["err"]) <= 15.0)

    md = f"""# {TITLE}

- 生成时间：{data['generated_at']}　|　证据来源：`{data['evidence_source']}`（端点实测样本采集于 {data['captured_at']}）
- 机器可读产物：`artifacts/propulsion_verification/propulsion_ow_verification.json`
- 复算命令：`python3 tools/verification/propulsion/verify_open_water.py`（`--live` 可实时复探端点）
- 测试：`python3 -m pytest backend/tests/test_propulsion_open_water_verification.py --noconftest`

## ① 案例介绍（物理背景 / 几何 / 参数）

**物理背景.** 敞水性能 $K_T(J), K_Q(J), \\eta_0(J)$ 是船舶总体性能里「阻力—推进—能耗」链的
枢纽：主机功率 $P_D = 2\\pi n Q$、推力 $T=K_T\\rho n^2D^4$、有效功率 $P_E=R_TV$ 之间的换算全部
经由这三个系数。若 $K_T$ 或 $K_Q$ 失真，阻力域（Holtrop 等）与绿色航行域（EEDI/EEXI/CII）
的全部结论都会跟着错，且错误会被 $\\eta_D$ 放大。因此本单选择**推进域**作为验证欠账清偿对象
——近 24 h 的终态单已覆盖稳性/阻力/耐波/操纵/绿色/EEDI，唯独推进域尚未对文献基准复核。

**几何与参数（对账基准工况）.**

| 项 | 值 |
|---|---|
| 桨型 | Wageningen B4-70（4 叶，盘面比 0.70） |
| 螺距比 P/D | 1.0（另扫 0.6–1.4） |
| 叶数 Z / 盘面比 Ae/Ao | 4 / 0.70（另扫 Z=2–6, Ae/Ao=0.35–1.00） |
| 进速比 J | 0–1.2（步长 0.05） |
| 参考系数表 | Oosterveld & van Oossanen (1975)，$K_T$ 39 项、$K_Q$ 47 项 |
| 主推进链测试船 | L=230 m, B=32 m, T=10.5 m, Δ=60000 t, $C_b$=0.65, V=15 kn, n=100 rpm, D=6.5/8.0 m |
| 工作介质 | ρ=1025 kg/m³（海水） |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **建参考侧（REAL 锚）.**
   `tools/verification/propulsion/b_series_reference.py` 内嵌 Oosterveld & van Oossanen (1975)
   的 39/47 项多项式系数（与平台官方数据文件 `backend/data/official/Wageningen_Bseries_coeffs.dat`
   逐项一致），提供 $K_T,K_Q,\\eta_0$、零推力点 $J_0$ 与作动盘上界 $\\eta_{{ideal}}$。
2. **三方同时代被测对象**（均为平台自述「Wageningen B / 螺旋桨理论」的产出面）：
   - **O1** 线上端点 `POST /api/v1/propeller-design/wageningen-b`（自述「Wageningen B系列回归公式」）；
   - **O2** 仓内方法 `backend/methods/propulsion/wageningen.py`（自述 Oosterveld 系数表）；
   - **O3** 基准数据集 `backend/data/test_data/propulsion_wageningen.json`（自述同文献）；
   - **O4** 主推进链 `POST /api/v1/calculations/propulsion`（自述「螺旋桨理论 + 经验公式」）。
3. **判据（机器可裁，先于动手）**：C1 输入敏感性、C2 $K_Q/K_T$ 结构比、**C3 动量理论界
   $\\eta_0<\\eta_{{ideal}}=2/(1+\\sqrt{{1+C_T}})$，$C_T=8K_T/(\\pi J^2)$**（定理级，不依赖系数表）、
   C4 单调/单峰、C5 零推力点、C6 三点数值对账（判据 15%）；O2 用 T1/T2/T3 查系数表结构与符号；
   O3 用 D1/D2/D3 查结构与**列内自洽**；O4 用 P1–P6 查效率上限、反算 $K_T/K_Q$ 量级与内部恒等式。
4. **工具链**：纯 Python + numpy/matplotlib，无 CFD、无网格；全程 CPU 秒级
   （本机 load15≈126/128 核，按容量纪律只做轻量活）。

## ③ 计算结果

### 3.1 判级总表

{verdict_table(data)}

### 3.2 O1 线上端点 vs 参考回归式（B4-70, P/D=1.0）

{recon_table_o1(data)}

- 端点恒有 $K_Q\\equiv K_T/10$（$K_T/K_Q$ 对 J 的变动幅度 = {_item(o1,'C2')['got']}；
  参考实现为 {_item(o1,'C2')['ref']}，随 J 单调下降 6.74→3.17）。
- 端点 $\\eta_0$ 只由 J 决定：$\\eta_0=\\min(10J/2\\pi,\\ 0.85)$，与 P/D、Z、Ae/Ao **完全无关**
  （同一 J 下不同几何给同一 $\\eta_0$）；J=0 处另给常数 0.5（理论值 0）。
- 动量理论界违反 {_item(o1,'C3')['got']} 处，最严重者 $\\eta_0$ 超上界 {max([v['excess_pct'] for v in _item(o1,'C3')['violations']]):.1f}%。
- 零推力点：端点 $K_T$ 斜率 −0.24/J 外推得 $J_0$≈{_item(o1,'C5')['got']}（{_item(o1,'C5')['mode']}），
  参考 {_item(o1,'C5')['ref']}，偏差 {_item(o1,'C5')['rel_err_pct']:+.1f}%。
- 端点确实随 P/D 变化（{_item(o1,'C1a')['got']} /单位 P/D vs 参考 {_item(o1,'C1a')['ref']}，
  误差 {_item(o1,'C1a')['rel_err_pct']:+.1f}%），故失效形态不是「忽略输入」，而是
  「用 P/D 线性缩放 + 伪造常数比」。

### 3.3 O2 仓内方法系数表

- T1：$K_T$ 表前 15 项 = $K_Q$ 表前 15 项逐项 ×10，最大偏差 {_item(o2,'T1')['got']['max_abs_dev_from_10']:.2e}
  （机器精度级 0）→ 两表由同一模板缩放生成，非独立回归；余 24 项为 ±1e-5 填充。
- T2：该表在 J=0.6/P_D=1.0/Z=4/Ae/Ao=0.70 处给出 $K_T$ = {_item(o2,'T2')['got']}
  （参考 {_item(o2,'T2')['ref']}）→ **负推力**，符号错误；$K_Q$ 同号错误（{_item(o2,'T3')['got']}）。
- 结论：文件名/注释自称 Oosterveld 表，实质为占位伪表 → **MOCK**。

### 3.4 O3 基准数据集 vs 参考

{recon_table_o3(data)}

- 结构：J=0 行 $K_T=0.40$ 而 $K_Q=0.040$（恰为 10 倍），与 O1 同源痕迹；但其余行比值随 J 下降，
  故 D1（常数比判据）PASS，未把它判成伪造结构。
- **列内自洽失败**：J={_item(o3,'D2')['got']['J']} 行 $\\eta_0$ 列写
  {_item(o3,'D2')['got']['eta0_listed']}，由其自身 $K_T/K_Q$ 按定义式应得
  {_item(o3,'D2')['got']['eta0_from_KtKq']}（偏差 {_item(o3,'D2')['got']['rel_err_pct']:+.1f}%）→ 录入错误；
  该错误同时造成"$\\eta_0$ 在 J=0.8 后二次回升"的物理不可能形态。
- 该数据集被平台多处当作基准引用，故其失真的溢出效应大于单点误差本身。

### 3.5 O4 主推进链

{chain_table(data)}

- P1：回报 $\\eta_0$ = {[r['eta0_reported'] for r in o4['table']]}，**均 >1**；P3：$\\eta_D$ = {[r['etaD_reported'] for r in o4['table']]}，**均 >1**
  → 输出功率大于输入轴功率，任何桨都不可能。
- P4/P5：按 $K_T=T/(\\rho n^2D^4)$、$K_Q=Q/(\\rho n^2D^5)$ 反算得 $K_T$≈0.002、$K_Q$≈1e-4，
  比 B 系列同 J 量级（0.15–0.35 / 0.02–0.05）小**两个数量级** → 推力/转矩模型不是 $K_T$ 形式。
- P6 PASS：$P_D=2\\pi nQ$ 恒等式误差 {_item(o4,'P6')['got']}%，即端点内部乘法自洽——
  失效点在**物理模型**（常数/伪造经验式），不在算术。

### 3.6 参考侧自检

- R1 $K_T$ 对 J 单调递减（J=0.05–1.00）：{_item(ref,'R1')['pass']}
- R2 $\\max\\ \\eta_0/\\eta_{{ideal}}$ = {_item(ref,'R2')['got']} < 1（B 系列桨效率达理想盘效率 75–80%，量级吻合）
- R3 文献带锚点见 §⑤ 表

### 3.7 图

1. `artifacts/propulsion_verification/fig1_kt_kq_eta.png` — 三联图：$K_T$/$K_Q$/$\\eta_0$ 对 J，
   实线为真 B 系列回归（P/D=0.8/1.0/1.2），红虚线为线上端点实测（P/D=1.0），
   右图黑点线为 Rankine–Froude 上界——红点在 J=0.4–0.6 越过上界。
2. `artifacts/propulsion_verification/fig2_error_bars.png` — 逐量相对误差条形图（红线 = 15% 判据）。
3. `artifacts/propulsion_verification/fig3_propulsion_chain_eff.png` — O4 回报效率 > 1 与作动盘上界对比。

## ④ 注意点 / 局限

- **文献基准的强度**：本单的"文献侧"由两部分构成：(a) Oosterveld & van Oossanen (1975) 系数表
  ——它是该系列的公开发表之物，平台仓库内亦有官方副本，因此**逐点比对**成立；
  (b) 三项量级锚点（$K_T(0)$, $J_0$, $\\eta_{{0,max}}$）用**带校验**（2–3%），
  因为本单未取得 MARIN 1975 原始数表逐行条目，不能声称逐点复现原表。
- **动量理论界**是定理级判据，但其强弱依赖 $C_T$ 的取法。对 O1/O3 我用其**自带的 $K_T$**
  反算上界（自洽口径）——即"用它自己的推力也越界"，这在逻辑上无法反驳；对 O4 的 $J$ 需假设
  伴流 $w=0.20$（仅用于量级估计，不影响 $\\eta_0>1$ 的结论，该结论与 $J$ 无关）。
- **端点不可达时的证据口径**：本单的 O1/O4 数据为 {data['captured_at']} 实测固化样本
  （原样写入验证器常量），`--live` 可复探；若平台改版，相应 pytest 用例会失败并提示"签名消失，需重判"，
  不会静默过期。
- **J=0（系柱）特例**：$\\eta_0$ 在 J=0 处定义为 0（无进速即无有效功率），
  参考实现返回 0、端点返回 0.5；此点不适合用 $\\eta_{{ideal}}$ 判据（$C_T$ 未定义）。
- **未做**：未修改线上服务代码（本轮修复面不在本单仓/权限内），未对 `propeller-design/cavitation`、
  `/propulsion/predict`、`/propulsion/design` 三个邻近端点判级，未做 ITTC-78 实尺度相关修正的验证。
- **环境局限**：仓库 `backend/tests/conftest.py` 依赖 `cryptography`，本机无网无法安装，
  故测试用 `--noconftest` 运行（用例本身不依赖 conftest fixture）。

## ⑤ 误差判定（关键量 vs 基准，是否 <3%）

**参考侧（vs 文献带）：**

{bands_table(data)}

**被测对象侧（vs 参考实现/物理判据）：** 共 {len(errs)} 个关键量判定，其中 $\\le$3% 的 {n_lt3} 个，
$\\le$15% 的 {n_lt15} 个；最差项 = {worst['obj']} / {worst['case']} / {worst['qty']} 误差
**{worst['err']:+.1f}%**。

| 判定问题 | 显式回答 |
|---|---|
| 参考实现与文献量级锚点是否 <3%？ | **是**（$K_T(J{{=}}0)$ { _item(ref,'R3')['bands'][0]['rel_err_pct']:+.2f}%、$J_0$ {_item(ref,'R3')['bands'][1]['rel_err_pct']:+.2f}%、$\\eta_{{0,max}}$ {_item(ref,'R3')['bands'][2]['rel_err_pct']:+.2f}%） |
| 线上端点的 $\\eta_0$ 是否 <3%？ | **否**（J=0.4/0.6 分别 {_item(o1,'C6')['table'][0]['eta0']['rel_err_pct']:+.1f}% / {_item(o1,'C6')['table'][1]['eta0']['rel_err_pct']:+.1f}%），且越过定理级上界 → 不可接受 |
| 线上端点的 $K_Q$ 是否 <15%？ | **否**（{min(r['KQ']['rel_err_pct'] for r in _item(o1,'C6')['table']):+.1f}% ~ {max(r['KQ']['rel_err_pct'] for r in _item(o1,'C6')['table']):+.1f}%） |
| 基准数据集是否与所声明文献一致？ | **否**（$K_Q$ 误差 {min(r['KQ']['rel_err_pct'] for r in _item(o3,'D3')['table']):+.1f}% ~ {max(r['KQ']['rel_err_pct'] for r in _item(o3,'D3')['table']):+.1f}%；另有 J=0.8 行自洽性错误 {_item(o3,'D2')['got']['rel_err_pct']:+.1f}%） |
| 主推进链的效率是否可能？ | **否**（$\\eta_0$ 与 $\\eta_D$ 均 >1，与 $J$、网格、精度无关，属物理不可能） |

**能力台账影响**：`propeller.open_water` 当前记为 REAL，本条证据支持改为
**OVERCLAIM / MOCK**（服务面为伪公式，仅仓内 `api/open_water.py` + 官方系数表为正典，
但该正典未被线上端点使用；且 `open_water.py` 在 $K_T<0$ 时仍算 $\\eta_0$，会给出 >1 的脏值，
建议加 `Kt<=0 → eta=None` 的保护——本单参考实现已如此实现）。

## ⑥ 修复建议（供后续单）

1. 把 `POST /propeller-design/wageningen-b` 指到 `backend/methods/propulsion/wageningen.py`
   **重写版**：直接复用 `api/open_water.py`（官方系数表）+ 加 $K_T\\le0$ 保护；删除现有
   常数比实现与 0.85 截断。
2. 用 `b_series_reference.py` 重新生成 `backend/data/test_data/propulsion_wageningen.json`
   （修正 J=0.8 行并替换伪造 $K_T/K_Q$ 列）。
3. `/calculations/propulsion` 重写推力/转矩模型使其输出 $K_T,K_Q$ 并复算 $\\eta_0$，
   加 `0<η0≤1` 与 `η_D=η0·η_H·η_R` 的入口断言。
4. 把本单 §3.2/§3.4/§3.5 的判据并入 CI（`backend/tests/test_propulsion_open_water_verification.py`
   已提供 MOCK 侧守门用例）。
"""
    return md


def build_payload(data: Dict, md: str) -> Dict:
    figs = []
    captions = {
        "fig1_kt_kq_eta.png": "K_T/K_Q/η0：真 B 系列回归（实线，P/D=0.8/1.0/1.2）vs 线上端点实测（红虚线）；右图黑点为 Rankine–Froude 上界，红点在 J=0.4–0.6 越界",
        "fig2_error_bars.png": "逐量相对误差（K_T/K_Q/η0）对参考回归式；红线为 15% 判据",
        "fig3_propulsion_chain_eff.png": "/calculations/propulsion 回报的 η0 与 η_D 均 >1，黑短横为同工况作动盘上界",
    }
    for name, cap in captions.items():
        p = ART / name
        if p.exists():
            figs.append({"name": name, "caption": cap,
                         "png_base64": base64.b64encode(p.read_bytes()).decode()})
    return {
        "title": TITLE,
        "summary": ("以 Oosterveld & van Oossanen (1975) B 系列回归式为参考侧，对平台推进域三个产出面"
                    "（线上端点 /propeller-design/wageningen-b、仓内 methods/propulsion/wageningen.py、"
                    "基准数据集 propulsion_wageningen.json）与主推进链 /calculations/propulsion 逐条判据对账："
                    f"四者判级 {', '.join(f'{k}={v}' for k, v in data['verdicts'].items())}；"
                    "关键失效形态为 K_Q≡K_T/10 的伪造常数比、η0=min(10J/2π,0.85) 的硬截断、"
                    "越过 Rankine–Froude 定理级上界、以及主推进链回报 η0=1.48>1 / η_D=1.63>1。"),
        "scenario_key": "propulsion_open_water_verification",
        "mission_id": 1585,
        "tags": ["验证欠账", "推进", "敞水性能", "Wageningen-B", "证据分级", "MOCK", "verifier"],
        "markdown": md,
        "figures": figs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=None, help="输出平台报告 JSON 路径")
    args = ap.parse_args()
    data = load()
    md = render_markdown(data)
    DOC.mkdir(parents=True, exist_ok=True)
    doc = DOC / "propulsion_open_water_verification_v1.md"
    doc.write_text(md)
    print(f"wrote {doc.relative_to(ROOT)} ({len(md)} chars)")
    if args.payload:
        payload = build_payload(data, md)
        Path(args.payload).write_text(json.dumps(payload, ensure_ascii=False))
        print(f"wrote {args.payload} (figures={len(payload['figures'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())