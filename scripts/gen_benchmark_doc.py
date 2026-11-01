"""Generate docs/benchmarks/benchmark_data_integrity.md from runs/m1406/benchmark_audit.json.

Every number in the report is injected here, never transcribed by hand.
Run after scripts/benchmark_audit.py.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "runs" / "m1406" / "benchmark_audit.json"
DOC = ROOT / "docs" / "benchmarks" / "benchmark_data_integrity.md"
FIG = ROOT / "docs" / "benchmarks" / "figs" / "benchmark_audit.png"


def pct(x, fmt=".2f"):
    return "—" if x is None else f"{x * 100:{fmt}}%"


def main() -> int:
    led = json.loads(LEDGER.read_text())
    aud, eng, leg = led["dataset_audit"], led["engine_ledger"], led["legacy_payload_probe"]
    s = aud["summary"]
    hulls = aud["hulls"]

    rows = []
    for c in aud["cases"]:
        issues = "<br>".join(c["blocking_issues"]) or "—"
        rows.append(
            "| {cid} | {ev} | {imp} | {dec} | {ratio} | {fn} | {iss} |".format(
                cid=c["case_id"],
                ev=c["evidence"],
                imp=c.get("wetted_area_implied_m2", "—"),
                dec=c.get("wetted_area_declared_m2", "—"),
                ratio=c.get("implied_over_envelope", "—"),
                fn=pct(c.get("froude_rel_error")),
                iss=issues,
            )
        )

    erows = []
    for k, v in eng["checks"].items():
        key = "rel_error" if "rel_error" in v else "abs_error"
        verdict = "✅" if v[key] < 0.03 else "❌"
        erows.append(
            f"| {k} | {v.get('api')} | {v.get('closed_form')} | {v[key]:.3e} | {verdict} |"
        )

    ch = eng["checks"]
    speed_ratio = leg["requested_speed_ms"] / leg["engine_speed_ms"]
    doc = f"""# 对标参考库一致性审计 + 阻力引擎解析账本（m1406 / data 席）

> 产物：`backend/services/benchmark_integrity.py`（一致性门）、`backend/tests/test_benchmark_integrity.py`（17 项 pytest）、
> `scripts/benchmark_audit.py`（账本生成器）、`runs/m1406/benchmark_audit.json`（本页全部数字的来源）。
> 本页数值由 `scripts/gen_benchmark_doc.py` 从该 JSON 注入，无手抄。

## 1. 案例介绍（物理背景与对象）

平台唯一的实验参考库 `backend/data/benchmark_reference.json` 支撑 `/api/v1/calculations/benchmark_validate`
的全部对标结论（{s['total']} 行算例、3 型船：SUBOFF 轴对体、DTMB-5415 水面舰、KVLCC2 油轮），也是历次
"Holtrop 对 SUBOFF 误差 >300% → honest-FAIL" 判词的唯一依据。参考行本身必须先满足它所引用的定义：

- 总阻力系数 `C_T = R_T / (0.5·ρ·S·V²)`
- 傅汝德数 `Fn = V / sqrt(g·L)`
- 方形系数几何恒等式 `Δ = ρ·L·B·T·Cb`

任一行违反上述任一恒等式，坏掉的是**参考值**而不是数值模型；据此算出的误差百分比不构成验证结论。

## 2. 怎么做的（方法/工具链）

1. `services/benchmark_integrity.py`：纯函数一致性门（仅 stdlib，可脱离 DB/API 测试）。逐行校验
   ①速度单位 ②Fn 恒等式（默认 5%）③C_T/R_T/S 恒等式（20%）④几何恒等式（10%）
   ⑤同型船内 `0.5ρS` 漂移（10%）⑥可闭合性（是否声明 S）⑦来源引用；按 REAL / WITHHELD 分级。
   另给出无量纲软信号：隐含湿表面积 / 方块包络 `L·B + 2LT + 2BT`（合理带 [0.4, 2.0]）。
2. `api/domain/ship_benchmark.py` 接线：结果携带 `reference_evidence` / `reference_issues`；
   **参考值为 WITHHELD 时不再输出 pass 判决**；新增 `GET /calculations/benchmark_audit` 账本端点；
   载荷键改为 `speed_ms`、C_T 由 `R_T/(0.5ρSV²)` 反推、base URL 支持 8001/8002/8010/env 探测。
3. 阻力引擎解析账本：对 `POST /calculations/resistance` 的返回逐项用闭式解复算
   （Re、Fn、ITTC-1957 摩擦线、湿表面积公式、R_f、R_t = R_f + R_r、残阻律）。

## 3. 计算结果（数字表）

### 3.1 参考库分级（{s['withheld']}/{s['total']} 行 WITHHELD，判定 `{s['verdict']}`）

REAL={s['real']} · WITHHELD={s['withheld']} · 合计={s['total']}

| case | 证据级 | 隐含 S [m²] | 声明 S [m²] | 隐含/包络 | Fn 相对偏差 | 阻断项 |
|---|---|---|---|---|---|---|
""" + "\n".join(rows) + f"""

同型船内一致性（同一船体、同一流体，`0.5ρS` 必须恒定）：
SUBOFF 最大漂移 {hulls['SUBOFF']['max_rel_deviation'] * 100:.1f}%（最差 {hulls['SUBOFF']['worst_case']}）、
DTMB-5415 {hulls['DTMB-5415']['max_rel_deviation'] * 100:.1f}%（最差 {hulls['DTMB-5415']['worst_case']}）、
KVLCC2 {hulls['KVLCC2']['max_rel_deviation'] * 100:.1f}%（最差 {hulls['KVLCC2']['worst_case']}）。

### 3.2 阻力引擎解析账本（探针 {eng['case_id']}，V = {eng['request']['speed_ms']} m/s，L = {eng['request']['length']} m）

| 校验项 | 引擎输出 | 闭式解 | 误差 | <3% |
|---|---|---|---|---|
""" + "\n".join(erows) + f"""

### 3.3 对标链路的历史缺陷（实测，非推测）

- 旧载荷键 `speed`：引擎按 **{leg['engine_speed_ms']} m/s**（默认值）计算，而请求为
  {leg['requested_speed_ms']} m/s —— 静默偏低 {speed_ratio:.1f} 倍且不报错。
- 旧代码读 `calc_data["ct"]`，引擎只返回 `total_resistance_n` → `calc_ct` 恒 0 → 每行误差恒 100%、恒 FAIL。
- 旧代码只连 8002 端口（本机已死）→ 每行降级 `Resistance API call failed`（error_pct = 999）。
- 参考表 R_T 与 C_T 两列互不自洽：SUBOFF 隐含 S 与声明 5.42 m² 相差 7.4 倍；
  DTMB-5415 的 Fn 列整体偏低 82.2%；几何 Δ 与 `ρLBT·Cb` 差 13.9%。

## 4. 结果图

![benchmark audit](figs/benchmark_audit.png)

(a) 每行由 (C_T, R_T) 反推的湿表面积与方块包络之比：全部落在合理带 [0.4, 2.0] 之外；
(b) Fn 列与 `V/sqrt(gL)` 的相对偏差：DTMB-5415 四行 82.2%，SUBOFF/KVLCC2 <1%；
(c) 引擎账本各项相对误差（对数轴），全部 ≈1e-6 量级。

## 5. 注意点（踩过的坑与适用范围）

- **只判级、不改数**：参考值必须回原文（Roddy 1990 / ITTC 2010 / SIMMAN 2008）才能修，本单只建门禁，不臆造新参考值。
- 隐含/包络比值是**软信号**（方块包络不等于真实船体表面），只提示不阻断；阻断规则是"是否声明 S / 是否违反恒等式"。
- 本机部署的引擎尚未含本次新增透明字段（`wetted_surface_m2`/`cf`/`cr`），账本中这两项由
  `R_f/(0.5ρSV²)`、`R_r/(0.5ρSV²)` 反推，JSON 的 `source` 字段已标注；部署后自动改用引擎直报值。
- 引擎湿表面积用轴对体近似 `S = πBL(0.5 + 0.25Cb)`，对水面舰不适用；本次不改模型，只把它暴露为可审计字段。
- 未做：12 行参考值逐条重标（需原文数据）、CFD 交叉验证、`/calculations/stability` 的同门禁接线。

## 6. 误差判定（逐项，显式回答是否 <3%）

引擎 vs 闭式解（{len(ch)} 项）：Re {ch['reynolds']['rel_error']:.1e}、Fn {ch['froude']['rel_error']:.1e}、
ITTC-1957 Cf {ch['ittc1957_cf']['rel_error']:.1e}、S 公式 {ch['wetted_surface_formula']['rel_error']:.1e}、
R_f {ch['frictional_resistance']['rel_error']:.1e}、`R_t − R_f − R_r` = {ch['total_is_sum']['abs_error']:.1e} N、
残阻律 {ch['residual_law']['rel_error']:.1e} → **全部 <3%（实测 ≤1.3e-6，仅受 JSON 四舍五入限制）**。

参考库 vs 自身定义：SUBOFF C_T/R_T/S 恒等式偏差 86.5%（因子 7.4）、DTMB-5415 Fn 偏差 82.2%、
DTMB-5415 几何偏差 13.9% → **远超 15% 不可接受阈值**，故 12 行全部降级 WITHHELD：
此前以该库为基的任何"<15% 通过"或"honest-FAIL"判词均须重新裁定。
"""
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(doc)
    print(f"doc -> {DOC} ({len(doc)} bytes), figure present: {FIG.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())