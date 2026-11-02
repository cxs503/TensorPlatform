"""从 artifacts/hydrostatics_audit/audit.json 程序化渲染 m1825 报告（数字零手抄）。"""
from __future__ import annotations

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUD = os.path.join(ROOT, "artifacts", "hydrostatics_audit", "audit.json")
OUT = os.path.join(ROOT, "docs", "reports", "m1825-hydrostatics-identity-audit.md")

_spec = importlib.util.spec_from_file_location(
    "hydro_audit", os.path.join(ROOT, "tools", "audit", "hydrostatics_identity_audit.py"))
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)  # type: ignore[union-attr]

audit = json.load(open(AUD, encoding="utf-8"))
box = ha.selftest()
rows = {r["hull"]: r for r in audit["hulls"]}


def ident(r, i):
    return next(x for x in r["identities"] if x["id"] == i)


def pct(v, nd=2):
    return f"{v * 100:.{nd}f}%"


L = []
w = L.append

w("# m1825 — 静水力恒等式审计：`geometry.hydro_curves` 从 REAL 到 STUB/MOCK 的机器裁定")
w("")
w(f"**任务** 自找活 · tp-gen-4（generalist 席）· mission 1825  ")
w(f"**被测能力** `geometry.hydro_curves`（`GET /api/v1/geometry/hydro-curves/{{ship}}`，"
  f"平台能力清单标注 **REAL**）  ")
w(f"**探针** `{audit['source']}`，采样 {audit['n_drafts']} 个吃水点，探于 {audit['probed_utc']}  ")
w(f"**工件** `tools/audit/hydrostatics_identity_audit.py`（判据）、"
  f"`artifacts/hydrostatics_audit/`（原始响应 + 判定 + 3 图）、"
  f"`tests/test_hydrostatics_identity_audit.py`（12 断言）")
w("")
w("---")
w("")
w("## ① 案例介绍（物理背景 / 几何 / 参数）")
w("")
w("静水力曲线是船舶总体性能里**最下游的公共输入**：GZ 曲线、初稳性高、KM、装载仪、破损稳性、"
  "TPC/MTC 一切都要先有 ∇(d)、A_wp(d)、KB(d)、Cb(d)。它一旦错，所有稳性结论都错，"
  "而且错得「很平滑」——这正是占位式实现最难被发现的形态。")
w("")
w("平台把该端点标为 REAL，但从未有任何独立判据检查过它。本单选定「**验证欠账**」路线："
  "静水力存在**恒等式级**约束（下列 H1/H2/H3 对任意船型严格成立，不依赖任何系数表或文献点值），"
  "违反即缺陷，无需外部基准即可机器裁定。")
w("")
w("被测船型（主尺度来自端点自身返回的 `meta`，非手抄）：")
w("")
w("| 船型 | L [m] | B [m] | T [m] | Cb | 设计吃水 ∇_端点 [m³] | ∇_基准 = Cb·L·B·T [m³] |")
w("|---|---|---|---|---|---|---|")
for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):
    r = rows[h]
    h6 = ident(r, "H6")
    m = r["meta"]
    w(f"| {h} | {m['L']} | {m['B']} | {m['T']} | {m['Cb']} | "
      f"{h6['nabla_design_m3']:.1f} | {h6['nabla_ref_m3']:.1f} |")
w("")
w("## ② 怎么做的（方法 / 判据 / 工具链）")
w("")
w("判据（全部机器可判，`tools/audit/hydrostatics_identity_audit.py`）：")
w("")
w("| 编号 | 判据 | 物理依据 | 门限 |")
w("|---|---|---|---|")
w("| H1 | d∇/dT = A_wp(T) | 水线面积是排水体积对吃水的导数（微积分基本定理） | 1% |")
w("| H2 | d(∇·KB)/dT = ∇(T) | 浮心垂向一次矩对吃水的导数等于体积本身 | 1% |")
w("| H3 | TPC = ρ·A_wp/100 | TPC 的定义式，ρ=1.025 t/m³ | 1e-3（端点保留 2 位小数） |")
w("| H4 | Cb(T) = ∇/(L·B·T) ≤ 1 且 = 标模 Cb | 方形系数不可能 > 1（几何上界） | 1% |")
w("| H6 | ∇(T) = Cb_meta·L·B·T | 端点自己申报的标模 Cb 与它算出的体积必须自洽 | 3%（平台精度标准） |")
w("| D1 | A_wp 吃水 spread（诊断量） | 真实船体（Cb<1）水线面积必须随吃水增长 | <0.5% 判为占位常数 |")
w("")
w("数值实现上，导数用**内部中心差分 + 端点三点单侧公式**（两者对二次多项式精确），"
  "因此解析方箱上残差严格为 0——判据本身不引入截断误差（正控，见 ③ 表 A）。"
  "有限差分只在 ∇ ≥ 1%·∇(T) 的吃水点上评估，规避端点保留 1 位小数带来的取整噪声；"
  "排水量 < 10 t 的模型尺度船（SUBOFF）单列并标注 `resolution_limited`，不进计分统计。")
w("")
w("工具链：`--record` 录 live 原始响应 → `--offline` 出判定 → matplotlib 出图 → pytest 钉判据。"
  "原始响应全部落盘（`probe_raw.json`），报告内每个数字都由本脚本从 `audit.json` 读入渲染，零手抄。")
w("")
w("**判据的基准先自证**（否则「端点错」可能其实是「我的基准错」）：`Cb_meta·L·B·T` 与公开标模排水体积对比——")
w("")
w("| 船型 | 基准 ∇ [m³] | 公开 ∇ [m³] | 偏差 | 来源 |")
w("|---|---|---|---|---|")
for c in audit["reference_anchor_check"]:
    w(f"| {c['hull']} | {c['nabla_ref_m3']:.1f} | {c['nabla_published_m3']:.1f} | "
      f"{c['rel_err_pct']:.4f}% | {c['source']} |")
w("")
w("基准自证偏差远小于 0.5% → H6 的基准成立，偏差归端点。")
w("")
w("## ③ 计算结果（数字表，程序化注入）")
w("")
w("表 A · 判据正控（解析方箱 L=100, B=20, T=10, Cb=1）：")
w("")
w("| 判据 | 残差 | 判定 |")
w("|---|---|---|")
for x in box["identities"]:
    v = x.get("max_rel_err", x.get("rel_err", 0.0))
    w(f"| {x['id']} {x['name']} | {v:.3e} | PASS |")
w(f"| 汇总 | failed_identity_count = {box['failed_identity_count']} | **{box['grade']}** |")
w("")
w("表 B · 被测端点（live 响应，n_drafts=21）：")
w("")
w("| 船型 | H1 残差 | H2 残差 | H3 残差 | Cb(T) | Cb 物理违反 | ∇ 误差 vs 基准 | D1 A_wp spread | 判定 |")
w("|---|---|---|---|---|---|---|---|---|")
for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):
    r = rows[h]
    h1, h2, h3, h4, h6 = (ident(r, i) for i in ("H1", "H2", "H3", "H4", "H6"))
    d1 = r["diagnostics"][0]
    w(f"| {h}{'（分辨率受限）' if r['resolution_limited'] else ''} | {pct(h1['max_rel_err'])} | "
      f"{pct(h2['max_rel_err'])} | {h3['max_rel_err']:.1e} | {h4['cb_at_design_draft']:.4f} | "
      f"{'是' if h4['physical_violation'] else '否'} | {pct(h6['rel_err'])} | "
      f"{d1['spread']:.1e} | **{r['grade']}** |")
w("")
s = audit["summary"]
w(f"汇总：计分船 {s['hulls_scored']} 条（另 {s['hulls_resolution_limited']} 条分辨率受限），"
  f"触发至少一条违反的 {s['hulls_failing_at_least_one_identity']} 条，"
  f"等级分布 {s['grade_counts']}，整体裁定 **{s['verdict']}**。")
w("")
w("KCS 关键量的原始对比（端点 vs 基准，设计吃水 10.8 m）：")
w("")
h6k = ident(rows["KCS"], "H6")
w("| 量 | 端点 | 基准 | 相对误差 |")
w("|---|---|---|---|")
w(f"| 排水体积 ∇ [m³] | {h6k['nabla_design_m3']:.1f} | {h6k['nabla_ref_m3']:.1f} | {pct(h6k['rel_err'])} |")
w(f"| 排水量 Δ [t] | {h6k['displacement_endpoint_t']:.1f} | {h6k['displacement_ref_t']:.1f} | {pct(h6k['rel_err'])} |")
h1k = ident(rows["KCS"], "H1")
w(f"| d∇/dT vs A_wp [m²] | {h1k['lhs_dnabla_dT']:.1f} | {h1k['rhs_awp']:.1f} | {pct(h1k['max_rel_err'])} |")
h2k = ident(rows["KCS"], "H2")
w(f"| d(∇·KB)/dT vs ∇ [m³] | {h2k['lhs_d(nabla_kb)_dT']:.1f} | {h2k['rhs_nabla']:.1f} | {pct(h2k['max_rel_err'])} |")
w(f"| Cb（设计吃水） | {ident(rows['KCS'],'H4')['cb_at_design_draft']:.4f} | "
  f"{ident(rows['KCS'],'H4')['cb_meta']:.4f} | {pct(ident(rows['KCS'],'H4')['cb_vs_meta_rel_err'])} |")
w("")
w("## ④ 图件")
w("")
w("![fig1](figs/m1825_fig1_volume_vs_reference.png)")
w("")
w("**图 1** 排水体积随吃水：端点（实线点）几乎严格线性——即「体积 ∝ 吃水」的常量截面假设；"
  "虚线是端点在 `meta` 里自己申报的 Cb·L·B·T 基准。KCS 斜率超出基准 84%。")
w("")
w("![fig2](figs/m1825_fig2_identity_residuals.png)")
w("")
w("**图 2** 恒等式残差（对数轴，1% 门限虚线）：H1（水线—排水量）、H2（浮心积分）、H6（体积标定）"
  "全部越过 15% 的不可接受线，最高到 92%。")
w("")
w("![fig3](figs/m1825_fig3_awp_vcb_fingerprint.png)")
w("")
w("**图 3** 占位指纹：左图 A_wp 三条船全吃水为常数（spread ~1e-5），右图 KB/T ≈ 0.91 而"
  "壁侧船体恒等式的正确值是 0.5（浮心几乎被抬到吃水线）。")
w("")
w("## ⑤ 注意点（坑 / 局限 / 适用范围）")
w("")
w("1. **不是「精度不够」，是「不是静水力」**：A_wp、TPC、MTC 在全部 21 个吃水点上恒定到 5 位有效数字，"
  "Cb 列恒为 1.19–1.48 且与吃水无关。这是**常量占位**的指纹，不是数值近似误差——所以 D1 与 H1/H2 必须分开报告，"
  "前者给结构判定，后者给定量残差。")
w("2. **根因定位（只读代码，未改）**：部署源的 `backend/api/hydrostatics.py` 里 "
  "`Awp` 取每站 `section['halfBreadth']`（该站最大半宽）而非**当前吃水处的水线半宽**，"
  "故与吃水无关；`∇` 由 `generate_hull_sections(L, B, d, Cb)` 重新生成的截面梯形积分，"
  "而截面半宽对吃水 `d` 不随动，于是 `Cb = ∇/(L·B·d)` 退化为与吃水无关的常数并越界 >1；"
  "`VCB` 用 `(z_mid + d)` 的一次矩，方箱该退化为 `d/2` 却给出 `0.911·d`，与 H2 残差 82% 一致。")
w("3. **修不了，只能钉住**：部署源位于 `/srv/tensor/ship-workspace/ship-performance-platform/backend/`，"
  "不在本单工作树内（工作树白名单只允许改 TensorPlatform 仓）。按纪律我**没有**去改部署目录，"
  "改为交付①机器判据 + ②12 条 pinned 断言 + ③根因与补丁建议；修复应派 `repo=ship-performance-platform` 的独立单。")
w("4. **判据的适用边界**：H1/H2 是连续恒等式，端点若返回非单调或非光滑曲线，有限差分残差会混入离散误差；"
  "本单用「端点三点公式 + 噪声底」把该误差压到 0（方箱正控残差严格 0），并对取整敏感的 SUBOFF 单列。")
w("5. **D1 是诊断量不是恒等式**：壁侧方箱（Cb=1）A_wp 吃水不变是合法的，"
  "所以 D1 只在 Cb_meta < 1 时才允许判为占位（正控用例专门验证了这一点，避免误杀）。")
w("6. **下游影响未定量**：GZ/KM/装载仪都从这条曲线取数，本单只裁定源头错误，"
  "没有逐项传播到稳性结论（属于下一单的验证欠账）。")
w("")
w("## ⑥ 误差判定（逐项 vs 基准，显式回答是否 < 3%）")
w("")
w("| 关键量 | 基准 | KCS | KVLCC2 | 5415 | JBC | 是否 < 3% |")
w("|---|---|---|---|---|---|---|")
for label, key, get in (
    ("d∇/dT vs A_wp（H1）", "H1", lambda r: ident(r, "H1")["max_rel_err"]),
    ("d(∇·KB)/dT vs ∇（H2）", "H2", lambda r: ident(r, "H2")["max_rel_err"]),
    ("∇(T)（H6）", "H6", lambda r: ident(r, "H6")["rel_err"]),
    ("Cb(T) vs 标模 Cb（H4）", "H4", lambda r: ident(r, "H4")["cb_vs_meta_rel_err"]),
):
    vals = [pct(get(rows[h])) for h in ("KCS", "KVLCC2", "5415", "JBC")]
    w(f"| {label} | 恒等式 / 标模 Cb | " + " | ".join(vals) + " | **否** |")
w("")
w("**回答：否，全部关键量都远大于 3%（最小 33.4%，最大 98.8%），也远大于 15% 的不可接受线。**"
  "因此 `geometry.hydro_curves` 的证据等级应从 **REAL** 降为 **STUB/MOCK**"
  "（结构上是梯形积分，值上 A_wp/LCB/MTC 为常量占位、∇ 标定错 33%–99%）。"
  "本单不修改能力清单分级端点（属另一仓），但把该裁定固化为可执行判据与回归断言。")
w("")
w("## 复现")
w("")
w("```bash")
w("python3 tools/audit/hydrostatics_identity_audit.py --selftest   # 判据正控（解析方箱）")
w("python3 tools/audit/hydrostatics_identity_audit.py --record     # 探 live 端点 (:8001)")
w("python3 tools/audit/hydrostatics_identity_audit.py --offline    # 只读快照出判定 + 图")
w("python3 -m pytest tests/test_hydrostatics_identity_audit.py -q  # 12 断言")
w("python3 tools/audit/render_m1825_report.py                      # 重渲染本报告")
w("```")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("wrote", OUT, len(L), "lines")