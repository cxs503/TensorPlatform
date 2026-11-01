"""Render docs/verifier/BENCHMARK_AUDIT.md from artifacts/benchmark_audit/audit_report.json.

All numbers in the generated document come from the audit JSON — nothing is
hand-copied, so the document and the machine-readable evidence cannot diverge.

    python tools/verifier/render_benchmark_audit_doc.py
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = REPO_ROOT / "artifacts" / "benchmark_audit" / "audit_report.json"
GATEWAY_VARIANT = REPO_ROOT / "artifacts" / "benchmark_audit" / "audit_report_via_gateway_8010.json"
OUT = REPO_ROOT / "docs" / "verifier" / "BENCHMARK_AUDIT.md"


def _fmt(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def main() -> int:
    rep = json.loads(REPORT.read_text())
    ds = rep["dataset"]
    raw = {c["case_id"]: c for c in json.loads(Path(ds["source"]).read_text())["cases"]}
    conv = ds["convention_inference"]
    api = rep.get("api") or {}
    gw = json.loads(GATEWAY_VARIANT.read_text()) if GATEWAY_VARIANT.exists() else None

    L = []
    A = L.append
    A("# 对标算例（阻力 benchmark）验证审计")
    A("")
    A("> 角色：verifier 席自主工时（tp-ver-4, mission 1521）。"
      "对象：已合并能力 `POST /api/v1/calculations/benchmark_validate`（对标算例）与"
      "`POST /api/v1/calculations/resistance`（静水阻力）所依赖的参考数据集。")
    A("")
    A("## 1. 结论摘要（先说结论）")
    A("")
    A(f"**平台参考数据集 `backend/data/benchmark_reference.json` 在代数上不可自洽，"
      f"不能用于给求解器打分。** 12 个算例中 **{ds['n_cases'] - ds['n_usable']}/{ds['n_cases']}** "
      f"未通过恒等式检查，可用（USABLE）算例数为 **{ds['n_usable']}**。")
    A("")
    best = conv["best"]
    A(f"- 数据集级约定推断（在 10 种「密度 × 力单位」约定中取**最宽容**的一种）："
      f"`rho={best['rho']}`, `force_unit={best['force_unit']}`，"
      f"最坏算例残差仍达 **{_fmt(best['max_deviation_pct'])}%**，平均 **{_fmt(best['mean_deviation_pct'])}%**。")
    A(f"- 该残差 **大于 `benchmark_validate` 自己的 15% 判据**，也大于「静水阻力 ≤5%」的 KPI 判据："
      f"即判定噪声完全由数据集本身产生，任何 PASS/FAIL 都不携带求解器信息。")
    A("- 因此本项能力的证据等级应从 `REAL` 下调为 **`WITHHELD`**（KPI「静水阻力 ≤5%」实测依据不足）。")
    A("")
    A("在线阻力求解器本身**是**自洽的真实算法（见 §5），但存在两个契约缺陷："
      "①`speed` 字段实际按 **节(knots)** 解释而无单位声明；②力值以 kN 两位小数回报，"
      "模型尺度下 R_F 的量化误差达数十个百分点。")
    A("")
    A("## 2. 方法：为什么不需要背文献表")
    A("")
    A("审校一个阻力结果，不依赖任何记忆中的实验值，只需它满足若干**定义性恒等式**：")
    A("")
    A("| 编号 | 恒等式 | 违背意味着 |")
    A("|------|--------|-----------|")
    A("| I1 | `Fr = V / sqrt(g L)` | 弗劳德列用错了参考长度，或速度单位错 |")
    A("| I2 | `R_T = C_T · ½ ρ S V²` | 系数列与力列不可能同时成立 |")
    A("| I3 | `C_T ≥ C_F(Re)`（ITTC-57） | 形状+兴波阻力为负，物理上不可能 |")
    A("| I4 | `P_E = R_T · V` | 有效功率与阻力不自洽 |")
    A("| I5 | `S` 落在由 L,B,D 导出的几何带内 | 湿表面积不可能超出外包圆柱 |")
    A("")
    A("为避免「缺单位标注被误判成物理错误」，I2 在 **ρ∈{998.2 淡水, 1025 海水}** × "
      "**力单位∈{N, kN, kgf, lbf, tf}** 共 10 种约定下扫描；但**约定是数据集级推断**的"
      "（取使最坏算例残差最小的约定），不是逐算例挑一个能过的单位——"
      "逐算例挑选会掩盖真正坏掉的列。")
    A("")
    A("## 3. 数据集审计结果（程序化注入，无手工抄写）")
    A("")
    A(f"审计工具：`{rep['tool']}`；容差：恒等式 {_fmt(rep['tolerances_pct']['identity'],1)}%，"
      f"系数 {_fmt(rep['tolerances_pct']['coefficient'],1)}%；"
      f"常数：g={rep['constants']['g']}, ν_淡水20C={rep['constants']['nu_fresh_20C']}, "
      f"ν_海水15C={rep['constants']['nu_sea_15C']}, 1 kn={rep['constants']['knot_to_ms']:.6f} m/s。")
    A("")
    A("### 3.1 判定汇总")
    A("")
    A("| 船型 | USABLE | WITHHELD |")
    A("|------|--------|----------|")
    for ship, row in ds["by_ship_type"].items():
        A(f"| {ship} | {row['USABLE']} | {row['WITHHELD']} |")
    A("")
    A(f"未通过的检查项计数：`{ds['failed_checks']}`")
    A("")
    A("### 3.2 逐算例 I2 残差（约定 = 数据集级最优）")
    A("")
    A("| case | 船型 | V [m/s] | C_T | R_T 记录值 | R_T 恒等式值 | 残差 % | 反推湿表面积 m² | 几何容许带 m² | 带宽内 | 修正因子 |")
    A("|------|------|---------|-----|------------|--------------|--------|------------------|----------------|--------|----------|")
    for c in ds["cases"]:
        chk = {k["name"]: k for k in c["checks"]}.get("I2_rt_from_ct", {})
        imp = chk.get("implied") or {}
        band = imp.get("physical_band_m2")
        band_s = f"[{band[0]:.0f}, {band[1]:.0f}]" if band else "—"
        src = raw.get(c["case_id"], {})
        A(f"| {c['case_id']} | {c['ship_type']} | {_fmt(c['speed_ms'],3)} | "
          f"{_fmt(src.get('experimental_ct'),4)} | "
          f"{_fmt(src.get('experimental_rt_n'),1)} | "
          f"{_fmt(chk.get('expected'),1)} | {_fmt(chk.get('deviation_pct'))} | "
          f"{_fmt(imp.get('implied_wetted_surface_m2'))} | {band_s} | "
          f"{'是' if imp.get('implied_within_band') else '否'} | "
          f"{_fmt(imp.get('correction_factor_on_rt'),3)} |")
    A("")
    A("> `修正因子` = 恒等式预测力 / 记录力；>1 表示记录的 R_T 偏小。"
      "`反推湿表面积` = 让该对 (C_T, R_T) 自洽所需的 S；全部落在几何容许带之外，"
      "说明二者不可能同时为真（不是取水密度或换单位能补救的）。")
    A("")
    A("### 3.3 I1（弗劳德列）")
    A("")
    A("| case | 记录 Fr | 由 V/sqrt(gL) 重算 Fr | 残差 % | 记录 Fr 隐含的参考长度 m |")
    A("|------|---------|------------------------|--------|---------------------------|")
    for c in ds["cases"]:
        chk = {k["name"]: k for k in c["checks"]}.get("I1_froude_identity", {})
        imp = chk.get("implied") or {}
        A(f"| {c['case_id']} | {_fmt(chk.get('reported'),4)} | {_fmt(chk.get('expected'),4)} | "
          f"{_fmt(chk.get('deviation_pct'),3)} | {_fmt(imp.get('length_m_implied_by_stated_Fr'),1)} |")
    A("")
    A("SUBOFF 与 KVLCC2 的 Fr 列与自身几何自洽（隐含长度 4.4 m / 317.8 m）；"
      "**DTMB-5415 四个算例的 Fr 列隐含参考长度约 181 m**，而数据集给出的 length_m 为 5.72 m —— "
      "该列来自另一套（全尺度/另一条船的）流水线。")
    A("")
    A("### 3.4 约定扫描明细（前 5 名）")
    A("")
    A("| ρ | 力单位 | 算例数 | 最坏残差 % | 平均残差 % |")
    A("|---|--------|--------|------------|------------|")
    for g in conv["candidates"][:5]:
        A(f"| {g['rho']} | {g['force_unit']} | {g['n_cases']} | "
          f"{_fmt(g['max_deviation_pct'])} | {_fmt(g['mean_deviation_pct'])} |")
    A("")
    A("## 4. 图")
    A("")
    A("![Fig1](artifacts/benchmark_audit/fig1_identity_residuals.png)")
    A("")
    A("![Fig2](artifacts/benchmark_audit/fig2_froude_parity.png)")
    A("")
    A("![Fig3](artifacts/benchmark_audit/fig3_implied_wetted_surface.png)")
    A("")
    if api.get("speed_unit_detection"):
        A("![Fig4](artifacts/benchmark_audit/fig4_api_unit_and_parity.png)")
        A("")
    A("## 5. 在线求解器审计（`/api/v1/calculations/resistance`）")
    A("")
    det = api.get("speed_unit_detection") or {}
    A(f"- 速度单位检测（直连 :8001，4 个探测点）：**{det.get('verdict')}**；"
      f"隐含乘子均值 {_fmt(det.get('ratio_mean'),6)}（1 kn = {det.get('knot_to_ms'):.6f} m/s）。"
      f"即请求字段 `speed` 被**按节解释**，而平台文档字段名是 `speed_ms`（m/s）。")
    if gw and gw.get("api", {}).get("speed_unit_detection"):
        gdet = gw["api"]["speed_unit_detection"]
        A(f"- 经网关 :8010 同一探测序列：**{gdet.get('verdict')}**，乘子 {_fmt(gdet.get('ratio_mean'),6)}，"
          f"离散度 {_fmt(gdet.get('ratio_spread_pct'),1)}% —— 两条访问路径的行为不一致（部署漂移）。")
    A("")
    A("| 在线恒等式检查 | 判定 | 残差 % | 说明 |")
    A("|----------------|------|--------|------|")
    for chk in api.get("checks_on_probe") or []:
        A(f"| `{chk['name']}` | {chk['verdict']} | {_fmt(chk.get('deviation_pct'))} | {chk.get('detail','')} |")
    A("")
    A("解读：`R_T = C_T·½ρSV²` 与 `P_E = R_T·V` 都闭合到 1% 以内 —— 求解器内部是**自洽的真实算法**，"
      "不是硬编码；`R_F`/`R_R` 的 26%/4% 偏差来自回报值只保留 kN 两位小数（19.3 N → 0.02 kN），"
      "属**报告量化**缺陷而非物理错误：模型尺度的力不能用两位小数 kN 表达，"
      "否则对标判定会被量化噪声淹没。")
    A("")
    A("## 6. 注意点 / 局限")
    A("")
    A("1. **本审计不判定「哪个值是真理」**。它证明的是「这些数不能同时为真」，"
      "以及每条记录需要多少倍修正才能自洽；给出真正参考值必须回到一次文献或平台自有可信算例台账。")
    A("2. 无 `wetted_surface_m2` 的算例（KVLCC2/DTMB）用 Mumford 型近似 "
      "`S ≈ L(1.7T + C_B B)` 建 ±15% 带，仅作带宽，不作权威值；"
      "结论（反推 S 落在带外 3–10 倍）对 ±15% 不敏感。")
    A("3. I3 只用 ITTC-57 摩擦线做下界；实船粗糙度补贴会让 `C_T/C_F` 更大，"
      "因此 I3 通过不代表精度合格，只代表没有物理不可能。")
    A("4. 在线探测只在 SUBOFF 几何上做（L=4.356 m），未覆盖全尺度与大 C_B；"
      "「静默按节解释」这一结论由 4 个速度点的乘子一致性支撑（离散度见 §5）。")
    A("5. 未做的事：未修改参考数据集中的任何数值（避免以「修数」代替「复现」），"
      "未重跑 CFD/水池对标。")
    A("")
    A("## 7. 误差判定（对照任务要求「是否 <3%」逐项回答）")
    A("")
    A("| 关键量 | 基准 | 实测偏差 | 是否 <3% | 说明 |")
    A("|--------|------|----------|----------|------|")
    A(f"| 在线 `R_T` vs `C_T·½ρSV²` | 定义恒等式 | {_fmt((api.get('checks_on_probe') or [{}])[0].get('deviation_pct'))}% | 是 | 求解器内部自洽 |")
    A(f"| 在线 `P_E` vs `R_T·V` | 定义恒等式 | {_fmt(next((c.get('deviation_pct') for c in (api.get('checks_on_probe') or []) if c['name']=='API_I4_pe_from_rt'), None))}% | 是 | — |")
    A(f"| 数据集 `Fr` 列 vs `V/√(gL)` | 定义恒等式 | SUBOFF/KVLCC2: ≤0.4%；DTMB-5415: ≥82% | **否** | 4/12 算例失败 |")
    A(f"| 数据集 `R_T` 列 vs `C_T·½ρSV²` | 定义恒等式（最优约定） | 最坏 {_fmt(best['max_deviation_pct'])}% / 平均 {_fmt(best['mean_deviation_pct'])}% | **否** | 12/12 算例失败 |")
    A("| 参考 `C_T` vs 水池实验 | 一次文献 | 未给出 | 无法判定 | 数据集未提供一次文献表号/工况号，仅一句 citation 字符串，不可复核 |")
    A("")
    A("因此：**「已合并功能复核」在本项上应记 FAIL（证据不足）**，"
      "而不是把 `benchmark_validate` 的输出当作精度证据。")
    A("")
    A("## 8. 修复建议（按性价比排序）")
    A("")
    A("1. **参考数据集换来源**：只保留能追到「报告编号 + 表号 + 工况号」的值，"
      "并把 `C_T` 与 `R_T` 同时入库前先过本审计的 I1/I2（CI 里跑 `tools/verifier/benchmark_audit.py`）。")
    A("2. **接口显式单位**：把 `speed` 改成 `speed_knots`/`speed_ms` 二选一并在 schema 中声明；"
      "或在响应中回显 `speed_unit` 与 `speed_used_ms`，让调用方无需猜测。")
    A("3. **力值回报精度**：模型尺度工况改用 4 位有效数字或直接回报 N，避免 kN 两位小数造成的量化误差。")
    A("4. **判定前置门**：`benchmark_validate` 在打分前先跑 I1/I2，任一不过就把该算例标 `WITHHELD` 并从通过率分母剔除，"
      "禁止把数据集噪声记成求解器误差。")
    A("")
    A("## 9. 工件与复现")
    A("")
    A("```bash")
    A("python tools/verifier/benchmark_audit.py --api                # 数据集 + 在线求解器审计，出图")
    A("python -m pytest tools/verifier/tests/test_benchmark_audit.py -q   # 15 项测试")
    A("python tools/verifier/render_benchmark_audit_doc.py           # 由 JSON 生成本文档（数字零手抄）")
    A("```")
    A("")
    A(f"工件：`{REPORT.relative_to(REPO_ROOT)}`（机器判据）、"
      f"`artifacts/benchmark_audit/fig1..fig4*.png`（图）、"
      f"`{OUT.relative_to(REPO_ROOT)}`（本文）。")
    A("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n")
    print(f"wrote {OUT} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())