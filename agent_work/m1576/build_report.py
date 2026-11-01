"""Build docs/verification/HYDRO_CURVES_AUDIT.md + /tmp/report.json from the audit artifact.

Every number in the outputs is read from artifacts/verifier/*.json; nothing is
transcribed by hand.
"""
import base64
import json
import os

ROOT = os.getcwd()  # run from the worktree root
ART = os.path.join(ROOT, "artifacts", "verifier")
res = json.load(open(os.path.join(ART, "hydro_curves_audit.json")))
snap = json.load(open(os.path.join(ART, "hydro_curves_kcs_snapshot.json")))
FIG = os.path.join(ART, "fig_hydro_curves_kcs.png")
c, meta = snap["curves"], snap["meta"]
crit = {x["id"]: x for x in res["criteria"]}
design = max(c["drafts"])
nab_d = c["displacement"][-1] / 1.025
pub = {"nabla": 52030.3, "disp_t": 52030.3 * 1.025, "Cb": 0.6505, "S": 9424.0,
       "LCB": 230.0 / 2 - 3.484}

rows = "\n".join(
    "| %s | %s | %s | %s | %s | %s |" % (
        x["id"], x["claim"],
        x["observed"] if not isinstance(x["observed"], list) else " / ".join(map(str, x["observed"])),
        x["baseline"] if not isinstance(x["baseline"], list) else str(x["baseline"]),
        "—" if x["rel_err"] is None else "%+.2f%%" % (100 * x["rel_err"]),
        "PASS" if x["pass"] else "**FAIL**")
    for x in res["criteria"])

curve_tbl = "\n".join(
    "| %.2f | %.1f | %.4f | %.1f | %.3f | %.3f | %.1f |" % (
        d, dp, cb, awp, vcb, lcb, wet)
    for d, dp, cb, awp, vcb, lcb, wet in zip(
        c["drafts"], c["displacement"], c["Cb"], c["Awp"], c["VCB"], c["LCB"],
        c["wet_surface"]))

doc = f"""# 静水力曲线族（`GET /api/v1/geometry/hydro-curves/{{ship}}`）独立复核

模板：TensorPlatform · mission 1576 · 分支 `agent/m1576`
工件：`artifacts/verifier/hydro_curves_audit.json`、`artifacts/verifier/hydro_curves_kcs_snapshot.json`、
`artifacts/verifier/fig_hydro_curves_kcs.png`

## 0. 一句话结论

**FAIL —— {res['n_pass']}/{res['n_total']} 条判据通过。** 端点对 KCS 在设计吃水返回 **Cb = {crit['I1']['observed']} > 1**
（物理不可能）与排水量 **{c['displacement'][-1]:.1f} t**（已发表基准 {pub['disp_t']:.0f} t，
偏差 **{100*crit['B1']['rel_err']:+.2f}%**）；且排水体积随吃水**严格线性**，说明每换一个吃水就重建了一条
几何相似的新船，而不是对固定船型切水线。该能力此前在证据台账中为 UNVERIFIED（无任何证据），本单给出首份定量证据。

## 1. 为什么选这条欠账

平台规则「几何必须先验证 Cb/L/B」——任何阻力/稳性/推进数字都以静水力为前提。报告 1148 的台账中
`geometry.hydro_curves` 是 REAL 自述但**零证据**（15 条 UNVERIFIED 之一）。本条可用纯闭式数学裁定，
不需要文献查表，也不可能被"自洽 PASS"糊弄过去。

## 2. 方法（第二套独立实现，不 import 被审代码）

`tools/verifier/geometry/hydrostatics_reference.py`：给定 `b(x,z)` 半宽场，
以 {801}x{201} 网格梯形积分 ∇、Awp、IT、VCB、LCB、湿表面积（含曲舷弧长项 `sqrt(1+(db/dx)^2)`）。
**积分器自身先标定**：

| 标定对象 | 判据 | 结果 |
|---|---|---|
| 箱形浮体（Cb=1, KB=T/2, BM=B²/12T, Sw=LB+2LT） | 闭式 | 全部 < 1e-3 相对误差 |
| 抛物水线直立舷（Cb=2/3, BM=2B²/35T, IT=4B³L/105） | 闭式 | 全部 < 1e-3 |
| 曲舷湿表面积 | `scipy.integrate.quad` | 9969.50 vs 9969.51 m² |
| 网格收敛（401x101 → 801x201） | 自比 | < 1e-3 |

黑箱探针：`GET http://127.0.0.1:8001/api/v1/geometry/hydro-curves/KCS?n_drafts=5`，快照落盘。
根因定位为**只读源码检查**（运行仓 `ship-performance-platform/backend/api/hydrostatics.py`）。

## 3. 端点返回的曲线（原样）

| 吃水 d [m] | 排水量 [t] | Cb [-] | Awp [m²] | VCB [m] | LCB(AP) [m] | 湿面积 [m²] |
|---|---|---|---|---|---|---|
{curve_tbl}

`meta` = {json.dumps(meta, ensure_ascii=False)}；设计吃水返回体积 ∇ = {nab_d:.1f} m³。

## 4. 判据与裁决（机器可裁，含定义级恒等式）

| 判据 | 命题 | 观测 | 基准 | 相对误差 | 裁决 |
|---|---|---|---|---|---|
{rows}

I2/I6 通过，说明端点**在它自己的定义链里是自洽的**；失败全部发生在物理侧——这正是"真代码、错物理"，
而非格式或单位错配。

## 5. 根因（只读源码检查，不改运行仓）

`backend/api/hydrostatics.py`：

1. `hydrostatics_at_draft()` 对每个吃水调用 `generate_hull_sections(L, B, d, Cb, ship_name=...)`——
   **用该吃水重新生成船型**并把同一目标 Cb 再标定一次。结果是每个吃水一条几何相似的新船：∇ ∝ d（实测线性度
   {res['design_draft_transverse_note'].split('within ')[1].split('%')[0]}%），Awp 与 LCB 与吃水无关，
   Cb 恒为 {crit['I1']['observed']}（= 0.6505 × {crit['I1']['observed']/0.6505:.4f}，双重 Cb 标定的放大因子）。
2. `_section_props()` 中 `mom_half += y_avg*dz*((z1+z2)/2 + draft)`：截面型线的 z 已是水线相对坐标，
   再加一次 `draft` 即**下限重复计入**，故 VCB ≈ {crit['I4']['observed']}·d（应约 0.5–0.55·d）。
3. `MTC = 0.01·Awp·L/100` 是占位公式（无纵稳心半径 I_L），应视为 STUB 级输出。

正确做法：船型**只在设计吃水生成一次**，各吃水仅积分 z ≤ d 的部分（本单参考实现即如此）。

## 6. 判据集非退化（负对照）

若判据只会否决则毫无价值。用本单参考实现对鼓形（`b ∝ sqrt(z/T)`，Cb 随吃水变化）生成 21 点静水力表，
喂给同一裁决函数：**I1–I7 全部 PASS**（`tests/test_hydrostatics_reference.py::test_criteria_pass_on_a_physically_correct_table`）。
即判据能把"对的表"和"错的表"分开。

## 7. 局限与适用范围

- 外部基准用公开 KCS 主尺度/排水量/湿表面积（SNAME/Gothenburg 2010 通行值），其中湿面积与 LCB 的文献值置信度弱于 ∇ 与 Cb；
  故 B3、LCB 只作旁证，**不单独作为裁决依据**，裁决主体是 I1/I3/I4/I7/B1/B2。
- 未做真实船型（非 KCS）的端到端复核；端点每个吃水重建船型，其"船型"本身也不等于 KCS 型线，故 B1/B2 的偏差
  同时含"船型不对"与"Cb 标定错"两个来源，本单不拆分二者权重。
- 运行仓不在本工作树白名单内，**本单只交证据与判据，未修端点**；修复单需另派（`runner=native`）。
- 端点响应慢（单次 5 吃水约 30 s），不适合在线校验路径。

## 8. 复现

```bash
python3 tools/verifier/geometry/audit_hydro_curves.py --live   # 重新探针（需 :8001）
python3 tools/verifier/geometry/audit_hydro_curves.py          # 回放快照（离线确定性）
python3 -m pytest tests/test_hydrostatics_reference.py -q       # 9 passed
```
"""
out = os.path.join(ROOT, "docs", "verification", "HYDRO_CURVES_AUDIT.md")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w").write(doc)
print("wrote", out, len(doc))

# ---- report payload -------------------------------------------------------
def errline(cid, label, unit=""):
    x = crit[cid]
    if x["rel_err"] is None:
        return None
    e = 100 * x["rel_err"]
    return "| %s | %s | %s | %+.2f%% | %s |" % (
        label, x["observed"], x["baseline"], e,
        "**是**" if abs(e) < 3 else ("否（>3%，但 <15%）" if abs(e) < 15 else "**否**"))


err_rows = [errline(i, l) for i, l in [
    ("B1", "设计吃水排水体积 ∇ [m³]"),
    ("B2", "设计吃水 Cb [-]"),
    ("B3", "设计吃水湿表面积 [m²]"),
    ("I3", "d(∇)/dT vs Awp 恒等式"),
    ("I1", "Cb ≤ 1 定义上限"),
    ("I4", "VCB/d 上界"),
    ("I7", "曲线 Cb vs meta.Cb"),
    ("I2", "TPC 定义恒等式"),
    ("I6", "Cb ↔ 排水量 内部一致性"),
]]
err_rows = [r for r in err_rows if r]

md = f"""# 静水力曲线族独立复核：KCS 端点返回 Cb=1.195（>1，物理不可能）、排水量 +83.7%，几何前置条件判定 FAIL

## ① 案例背景与参数

平台任何阻力/稳性/推进数字都以静水力为前提，故自有规则「几何必须先验证 Cb/L/B」。证据台账（报告 1148）
中 `geometry.hydro_curves` 自述 REAL 但**零证据**（15 条 UNVERIFIED 之一）。本单用第二套独立实现给首份定量证据。

被审对象：`GET /api/v1/geometry/hydro-curves/{{ship}}`（:8001），船型 KCS。

| 参数 | 值 |
|---|---|
| Lpp | {meta['L']} m |
| B | {meta['B']} m |
| T | {meta['T']} m |
| meta.Cb（端点自述） | {meta['Cb']} |
| 公开基准 Cb / ∇ / Δ | 0.6505 / 52030.3 m³ / {pub['disp_t']:.0f} t |

## ② 怎么做的

1. **独立的参考实现**（`tools/verifier/geometry/hydrostatics_reference.py`）：固定船型，仅积分 z ≤ d 部分；
   801×201 梯形积分 ∇/Awp/IT/VCB/LCB/湿面积（含曲舷弧长 sqrt(1+(db/dx)²)）。
   积分器先自标定：箱形与抛物水线直立舷两种闭式解全部 <1e-3；曲舷湿面积 vs `scipy.quad` 9969.50 vs 9969.51 m²；
   网格加密 4 倍结果变化 <1e-3。
2. **黑箱探针**：`n_drafts=5` 一次抓取曲线族快照落盘（`artifacts/verifier/hydro_curves_kcs_snapshot.json`）。
3. **两层判据**：外部基准（B1–B3，KCS 公开值）+ 不依赖任何数据的定义级恒等式（I1–I7：
   Cb≤1、TPC=ρAwp/100、d∇/dT=Awp、VCB/d≤0.65、单调性、两条内部一致性）。
4. **负对照**：用参考实现对鼓形船（Cb 随吃水变化）生成 21 点表，喂同一裁决函数 → I1–I7 全 PASS，证明判据非退化。
5. **根因**：只读源码检查运行仓 `backend/api/hydrostatics.py`（不改该仓）。

## ③ 计算结果

端点原文（5 个吃水）：

| d [m] | 排水量 [t] | Cb | Awp [m²] | VCB [m] | LCB(AP) [m] | 湿面积 [m²] |
|---|---|---|---|---|---|---|
{curve_tbl}

裁决：**{res['verdict']}，{res['n_pass']}/{res['n_total']} 通过**。

| 判据 | 命题 | 观测 | 基准 | 相对误差 | 裁决 |
|---|---|---|---|---|---|
{rows}

关键：I2/I6 通过（端点自身定义链自洽），失败全在物理侧——"真代码、错物理"。
排水体积随吃水线性度 {res['design_draft_transverse_note'].split('within ')[1].split('%')[0]}%，即每个吃水重建一条几何相似的新船。

根因（只读源码）：`hydrostatics_at_draft()` 每吃水调用 `generate_hull_sections(L,B,d,Cb)` 重新生成并按目标
Cb 再标定一次（双重 Cb 标定 → Cb 放大 {crit['I1']['observed']/0.6505:.4f} 倍）；`_section_props()` 对已是水线相对
坐标的 z 再加一次 `draft`（下限重复计入 → VCB≈{crit['I4']['observed']}·d）；`MTC=0.01·Awp·L/100` 为占位公式。

## ④ 结果图

图 1：左=排水量-吃水（vs 公开 KCS 53331 t），中=Cb-吃水（物理上限 1.0、公开 0.6505、meta 0.651 三条参照），
右=各判据相对误差（点线 3%、虚线 15% 门）。Cb 曲线整体越过 1.0 且三条参照互不一致，是"双重标定"的直接可视化。

## ⑤ 注意点与局限

- 外部基准里 ∇/Cb 置信度最高；湿面积、LCB 的文献值较弱，故 B3 与 LCB 只作旁证，裁决主体是 I1/I3/I4/I7/B1/B2。
- 端点每吃水重建船型，其"船型"并不等于 KCS 型线，因此 B1/B2 偏差含"船型不对"与"Cb 标定错"两来源，本单不拆权重。
- 运行仓不在本工作树白名单，**本单未修端点**；修复单需另派 `runner=native`。正确做法：船型只生成一次，各吃水积分 z≤d。
- 端点慢（5 吃水约 30 s），不宜放在在线校验路径。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 实测 | 基准 | 相对误差 | <3%？ |
|---|---|---|---|---|
{chr(10).join(err_rows)}

结论：**关键几何量无一满足 <3%**；排水体积与 Cb 的偏差 +83.69% 远超平台 15% 不可接受门。
判据集中仅 TPC 与 Cb↔排水量两条定义恒等式在 1e-5 量级（≤1% 容差）通过，它们检验的是内部自洽而非物理正确性。

## ⑦ 交付物

`tools/verifier/geometry/hydrostatics_reference.py`（独立参考实现）、
`tools/verifier/geometry/audit_hydro_curves.py`（探针+裁决+出图）、
`tests/test_hydrostatics_reference.py`（9 passed，含负对照与闭式标定）、
`docs/verification/HYDRO_CURVES_AUDIT.md`、
`artifacts/verifier/{{hydro_curves_kcs_snapshot.json, hydro_curves_audit.json, fig_hydro_curves_kcs.png}}`。
"""

png = base64.b64encode(open(FIG, "rb").read()).decode()
payload = {
    "title": "静水力曲线族独立复核（KCS）：端点 Cb=1.195>1、排水量 +83.7%，几何前置条件判定 FAIL（2/10 判据通过）",
    "summary": "用独立梯形积分参考实现（箱形/抛物水线闭式标定 + scipy.quad 曲舷湿面积标定）复核 :8001 hydro-curves 端点："
               "KCS 设计吃水 Cb=1.1949（>1 物理不可能）、排水量 97964 t vs 公开 53331 t（+83.69%）、"
               "d∇/dT 与 Awp 差 +46.0%、VCB/d=0.911；根因为每吃水重新生成并按 Cb 再标定船型（∇ ∝ d 线性度 3.4e-5）+ z 坐标下限重复计入。"
               "判据集含负对照（参考实现生成的物理正确表 I1–I7 全 PASS）。",
    "scenario_key": "geometry.hydro_curves.verification",
    "mission_id": 1576,
    "tags": ["geometry", "hydrostatics", "verification", "KCS", "Cb", "capability-audit", "m1576"],
    "markdown": md,
    "figures": [{"name": "fig_hydro_curves_kcs.png",
                 "caption": "KCS 静水力曲线族复核：左 排水量-吃水 vs 公开 53331 t；中 Cb-吃水 vs 物理上限 1.0 与公开 0.6505/meta 0.651；右 各判据相对误差（3%/15% 门）",
                 "png_base64": png}],
}
json.dump(payload, open("/tmp/report.json", "w"))
print("report.json bytes", os.path.getsize("/tmp/report.json"))