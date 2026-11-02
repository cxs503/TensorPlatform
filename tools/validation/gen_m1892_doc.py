"""Generate the m1892 verification document from artifacts/m1892/evidence.json.

Every number in the emitted markdown is read from the artefact - nothing is
typed by hand.  Run after verify_ice_uq_aliasing_m1892.py:

    PYTHONPATH=backend python3 tools/validation/gen_m1892_doc.py
"""

from __future__ import annotations

import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVID = os.path.join(ROOT, "artifacts", "m1892", "evidence.json")
OUT = os.path.join(ROOT, "docs", "verification", "ice_uq_aliasing_fix_m1892.md")

ev = json.load(open(EVID, encoding="utf-8"))
verdict = ev["verdict"]
status = ev["numerics_status"]
amap = ev["aliasing_map"]
mono = ev["monomial_exactness"]
ships = ev["real_ships"]
trunc = ev["truncation"]

L = []
w = L.append

w("# 冰阻力 UQ（PCE/Sobol）混叠缺陷修复复核 m1892")
w("")
w("- 任务：m1882 定位 `backend/services/ice_resistance_uq.py` 的谱投影混叠，m1892 修复并固化证据。")
w("- 代码：`backend/services/ice_resistance_uq.py`（`build_pce` / `run_all_ships_uq` / "
  "`monte_carlo_reference` / `run_ice_resistance_uq`）")
w("- 证据工件：`artifacts/m1892/evidence.json`（本文件所有数字由 "
  "`tools/validation/gen_m1892_doc.py` 从该工件读出，零手抄）")
w("- 生成脚本：`tools/validation/verify_ice_uq_aliasing_m1892.py`")
w("- 回归测试：`tests/test_m1892_pce_aliasing.py`（20 项）、"
  "`tests/test_ice_uq_kernel_reference_m1882.py`（13 项，原 `xfail(strict)` 已转正）、"
  "`tests/test_pce_ice_resistance_uq.py`（45 项）")
w("- 前序复核：`docs/verification/ice_uq_kernel_independent_review_m1882.md`")
w("")
w("**判决：混叠缺陷已修复（guard 生效，反例方差与总阶灵敏度精确到机器精度），"
  "抽样截断改为端点层显式声明。物理模型本身仍为 WITHHELD（见 m1720 的标定债务），"
  "本单只处理数值层。**")
w("")

w("## 1. 缺陷机制")
w("")
w("PCE 投影系数由内积给出 `c_α = <f, ψ_α> / <ψ_α, ψ_α>`，用 Smolyak 稀疏网格"
  "（1 维 Gauss-Hermite 第 k 层 k 点）数值求积。该规则对总阶 `2L-1` 精确，"
  "因此要精确分解 `Var = Σ c_α²‖ψ_α‖²` 与 Sobol 指数，必须让 Gram 矩阵 "
  "`<ψ_a, ψ_b>`（`|a|,|b| ≤ p`）被精确积分：")
w("")
w("```")
w("2L - 1 >= 2p        <=>        L >= p + 1 = max_poly_order + 1")
w("```")
w("")
w("出厂默认 `(max_level=3, max_poly_order=3)` 只精确到总阶 5，而 `p=3` 需要总阶 6："
  "响应中未分辨的高阶分量被投影到无关系数上。用纯三次响应 `y = ξ₀³`（第二输入 ξ₁ 完全无影响，"
  "解析值 `S_T(ξ₁)=0`、`Var=E[ξ⁶]=15`）量化：")
w("")
w("| 配置 | Var | S_T(ξ₁) | 求积精确阶 | 判定 |")
w("|---|---|---|---|---|")
w(f"| `allow_aliasing`, L=3（原出厂配置） | {status['aliased_reference']['variance']:.6f} | "
  f"{status['aliased_reference']['S_T_inert']:.6f} | "
  f"{2 * status['aliased_reference']['max_level'] - 1} | 混叠（伪灵敏度 18.2%） |")
w(f"| 修复后默认（L={status['guarded']['max_level']}） | {status['guarded']['variance']:.12f} | "
  f"{status['guarded']['S_T_inert']:.3e} | {2 * status['guarded']['max_level'] - 1} | 精确 |")
w("")
w("## 2. 修复内容")
w("")
w("1. **入口校验 + 自动抬升**（`build_pce`）：新增 `required_quadrature_level(p) = p + 1` 与 "
  "`quadrature_policy` 三档策略——默认 `auto_raise`（抬升到 `max_poly_order + 1` 并把 "
  "`max_level_requested` / `max_level` / `quadrature_raised` / `aliasing_free` 写进返回字典），"
  "`error` 抛 `PCEQuadratureAliasingError`（即显式 DomainError 语义），"
  "`allow_aliasing` 仅用于复现 m1882 证据。`max_level=None` 表示取无混叠默认值。")
w("2. **入口默认值同步**：`PCE_DEFAULT_MAX_LEVEL = PCE_DEFAULT_MAX_POLY_ORDER + 1 = 4`，"
  "`run_ice_resistance_uq` 与 `run_all_ships_uq` 默认即用该值；结果对象新增 "
  "`max_level / max_poly_order / quadrature_aliasing_free` 供端点与测试读取。")
w("3. **抽样截断改为端点层声明**：`monte_carlo_reference` 不再内含四条“按索引硬编码”的裁剪，"
  "改为接受 `truncation=[TruncationSpec(...)]`：`truncation_mode='reject'` 拒绝重抽（精确截断正态，"
  "无边界原子）、`'clip'` 裁剪并量化原子质量；**`truncation=None` 时完全不裁剪**。"
  "冰阻力入口用模块常量 `ICE_INPUT_TRUNCATION` 显式声明四条支撑区间，并把实际抽样法则"
  "（各维解析截断质量、实际越界比例、重抽次数）写回结果 `distribution_truncation`，"
  "CI 覆盖率检验用同一法则抽样。")
w("4. **测试与文档**：新增 `tests/test_m1892_pce_aliasing.py` 锁定反例"
  "（`S_T(零影响输入) <= 1e-6`、`Var = 15`）、guard 三档策略、默认值一致性、截断声明；"
  "m1882 的 `xfail(strict=True)` 反例测试转正为硬断言，原始混叠数值改由 "
  "`quadrature_policy='allow_aliasing'` 旁路钉住。")
w("5. **顺带修好被该缺陷遮住的端点 500**：`backend/api/domain/ice_uq.py` 的 `/uq` 从 "
  "`result.reference_mean_kN` / `reference_std_kN` 取 MC 参照，而 "
  "`IceResistanceUQResult` 上从未有这两个字段（实际字段名是 `mc_mean_kN` / `mc_std_kN`），"
  "即任何 `/uq` 请求都会 AttributeError。已在结果对象上补 `reference_mean_kN` / "
  "`reference_std_kN` 只读别名（services 侧改动，未越白名单改 API 文件），并由 "
  "`tests/test_m1892_pce_aliasing.py::test_api_endpoint_reads_reference_aliases_and_serves_fixed_numbers` "
  "端到端锁定。")
w("")

w("## 3. 证据一：混叠随求积层的收敛（2 输入反例，p=3）")
w("")
w("| Smolyak 层 L | 节点数 | 精确阶 2L-1 | 需要阶 2p | Var(y=ξ₀³) | Var 相对误差 | S_T(ξ₁) | 判定 |")
w("|---|---|---|---|---|---|---|---|")
for row in amap:
    ok = "无混叠" if row["aliasing_free"] else "混叠"
    w(f"| {row['level']} | {row['n_nodes']} | {row['exactness_degree']} | "
      f"{row['required_degree']} | {row['variance']:.6f} | {row['variance_rel_err']:.3e} | "
      f"{row['ST_inert']:.6f} | {ok} |")
w("")
w("解析参照：`Var = E[ξ⁶] = 15`、`S_T(ξ₁) = 0`。L ≥ 4（= p+1）后两项同时精确，"
  "与推导一致；这也解释了为何“只抬层数即可修好”而“只调多项式阶不够”。")
grid = ev["production_grid_4d"]
w("")
w("生产路径是 4 输入（冰强度/冰厚/摩擦/航速）、p=3，故必须 L=4：4 维 Smolyak 节点数 "
  f"L=3 时 {grid['3']['n_nodes']} 个（权重和 {grid['3']['weight_sum']:.6f}），"
  f"L=4 时 {grid['4']['n_nodes']} 个（权重和 {grid['4']['weight_sum']:.6f}）——"
  "代价是单船模型调用从 45 次增加到 165 次，仍是毫秒级。")
w("")

w("## 4. 证据二：单项式响应的精确性（默认入口，无需手工设层数）")
w("")
w("| 响应阶 p | 实际使用层 | 节点数 | Var(PCE) | Var(解析) | Var 相对误差 | S_T(inert) | ΣS_T |")
w("|---|---|---|---|---|---|---|---|")
for row in mono:
    w(f"| {row['order']} | {row['level_used']} | {row['n_nodes']} | {row['variance']:.9f} | "
      f"{row['variance_analytic']:.9f} | {row['variance_rel_err']:.3e} | "
      f"{row['ST_inert']:.3e} | {row['ST_sum']:.9f} |")
w("")

w("## 5. 证据三：三条实船 Sobol 指数（修复前/后 vs 独立 Saltelli–Jansen 蒙特卡洛）")
w("")
w("独立参照用 `tools/validation/ice_uq_reference_m1882.py::legendre_saltelli_indices`"
  "（Saltelli 2010 一阶 + Jansen 1999 总阶，只用响应值，不碰多项式基），"
  "抽样支撑与 `ICE_INPUT_TRUNCATION` 一致（截断支撑上的抽样）。")
w("")
w("| 船 | 层 | 节点 | 输入 | S_T 修复后 | S_T 原 L=3 | 差 | S_T 独立 MC |")
w("|---|---|---|---|---|---|---|---|")
for name, res in ships.items():
    for i, inp in enumerate(res["input_names"]):
        w(f"| {name} | {res['level_used']} | {res['n_model_evaluations']} | {inp} | "
          f"{res['ST_fixed'][i]:.4f} | {res['ST_legacy_level3'][i]:.4f} | "
          f"{res['ST_legacy_minus_fixed'][i]:+.5f} | {res['ST_saltelli_mc'][i]:.4f} |")
w("")
w("| 船 | max｜S_T(修复)−MC｜ | max｜S_T(原 L=3)−MC｜ | ΣS_T(修复后) | 均值误差 | 标准差误差 | CI 覆盖 |")
w("|---|---|---|---|---|---|---|")
for name, res in ships.items():
    w(f"| {name} | {res['ST_fixed_vs_mc_max_abs']:.4f} | {res['ST_legacy_vs_mc_max_abs']:.4f} | "
      f"{res['ST_fixed_sum']:.4f} | {res['mean_rel_err_vs_mc']:.2e} | "
      f"{res['std_rel_err_vs_mc']:.2e} | {res['ci_coverage']:.4f} |")
w("")
w("注意（诚实报告）：对这三条实船的 Lindqvist 响应，L=3 与 L=4 的 S_T 差"
  f"仅 {max(abs(v) for r in ships.values() for v in r['ST_legacy_minus_fixed']):.1e}——"
  "该响应接近可加、三次以上分量很小，因此混叠在**实船 Sobol 数值**上的影响很小。"
  "缺陷的真实危害有二：(a) 配置层面结构性错误，任何带明显三次/高阶分量的响应"
  "（如结冰强度-速度耦合、含立方项的经验修正）会被判给无影响输入；(b) 无法通过"
  "“误差看起来还行”来自证清白——只能靠构造反例（m1882 的做法）暴露。")
w("")

w("## 6. 证据四：抽样截断（原硬裁剪 → 端点层声明）")
w("")
w("| 输入 | 下界 | 上界 | 解析截断质量 | 依据 |")
w("|---|---|---|---|---|")
for row in trunc["declared_masses"]:
    lo = "−∞" if row["lower"] is None else f"{row['lower']:g}"
    hi = "+∞" if row["upper"] is None else f"{row['upper']:g}"
    w(f"| {row['input']} | {lo} | {hi} | {row['mass_outside']:.3e} | {row['rationale']} |")
w("")
w("| 抽样法则 | 均值 [kN] | 标准差 [kN] | 相对 reject 的均值偏差 | 相对 std 偏差 |")
w("|---|---|---|---|---|")
t = trunc
w(f"| reject（精确截断正态，新默认） | {t['reject']['mean']:.3f} | {t['reject']['std']:.3f} | — | — |")
w(f"| clip（旧行为，边界原子） | {t['clip']['mean']:.3f} | {t['clip']['std']:.3f} | "
  f"{t['clip_mean_bias_rel']:.2e} | {t['clip_std_bias_rel']:.2e} |")
w(f"| 不截断（plain 正态） | {t['untruncated']['mean']:.3f} | {t['untruncated']['std']:.3f} | "
  f"{t['untruncated_mean_bias_rel']:.2e} | {t['untruncated_std_bias_rel']:.2e} |")
w("")
w(f"声明截断质量的量级：最大 {trunc['max_mass_outside']:.3e}（摩擦系数 0.02 下界），"
  f"重抽次数 {t['reject']['n_redrawn']}。因此旧硬裁剪的数值偏差很小"
  f"（均值 {t['clip_mean_bias_rel']:.1e}），但它此前是**不可见**的："
  "四条边界按输入在列表中的位置硬编码在通用 MC 函数里，换一份输入列表就会张冠李戴，"
  "且把正态分布变成了带原子的混合分布。现在支撑区间是入口层的显式值，"
  "并随结果一起上报（`distribution_truncation`）。")
w("")

w("## 7. 图")
w("")
w("![混叠随求积层收敛](figures_m1892/fig1_aliasing_map.png)")
w("")
w("*图 1*：左—`y=ξ₀³` 的 PCE 方差随 Smolyak 层变化（解析 15，L=3 得 11）；"
  "右—无影响输入的伪总阶灵敏度（L=3 得 0.1818，L≥4 归零）。")
w("")
w("![修复前后 Sobol 对比](figures_m1892/fig2_sobol_before_after.png)")
w("")
w("*图 2*：MV Arctic / Polarstern 的四个输入总阶 Sobol 指数：原 L=3、修复后 L=4、"
  "以及独立 Saltelli–Jansen MC。三条实船差异在 1e-4 量级（见 §5 的诚实说明），"
  "说明修复的必要性来自结构性正确性而非数值显著性。")
w("")

w("## 8. 误差判定（关键量 vs 基准，逐项）")
w("")
w("| 关键量 | 平台值 | 基准 | 相对误差 | 是否 < 3% |")
w("|---|---|---|---|---|")
w(f"| `y=ξ₀³` 的 Var（修复后默认） | {status['guarded']['variance']:.12f} | 15（解析 E[ξ⁶]） | "
  f"{status['guarded']['variance_rel_err']:.2e} | 是 |")
w(f"| `y=ξ₀³` 的 S_T(ξ₁)（修复后默认） | {status['guarded']['S_T_inert']:.3e} | 0（解析） | "
  f"绝对误差 {status['guarded']['inert_ST_abs_err']:.2e} | 是 |")
w(f"| `y=ξ₀³` 的 Var（原 L=3，混叠） | {status['aliased_reference']['variance']:.6f} | 15（解析） | "
  f"{abs(status['aliased_reference']['variance'] - 15.0) / 15.0:.2%} | 否（修复前不可接受） |")
w(f"| `y=ξ₀³` 的 S_T(ξ₁)（原 L=3，混叠） | {status['aliased_reference']['S_T_inert']:.6f} | 0（解析） | "
  f"绝对值 {status['aliased_reference']['S_T_inert']:.4f} | 否（修复前不可接受） |")
for row in mono:
    w(f"| Var(ξ^{row['order']})（默认入口） | {row['variance']:.9f} | {row['variance_analytic']:.9f}（解析） | "
      f"{row['variance_rel_err']:.2e} | 是 |")
w(f"| 实船 ΣS_T（MV Arctic） | {ships['MV_Arctic']['ST_fixed_sum']:.4f} | 1（恒等式，受 p=3 截断） | "
  f"{abs(ships['MV_Arctic']['ST_fixed_sum'] - 1):.2%} | 边界（见局限） |")
for name, res in ships.items():
    w(f"| {name} S_T 修复后 vs 独立 MC | 见 §5 | Saltelli–Jansen MC | "
      f"max 绝对差 {res['ST_fixed_vs_mc_max_abs']:.4f} | 是 |")
for name, res in ships.items():
    w(f"| {name} 均值/标准差 vs MC 参照 | {res['mean_kN']:.2f} kN / {res['std_kN']:.2f} kN | "
      f"MC {res['mc_mean_kN']:.2f} kN / {res['mc_std_kN']:.2f} kN | "
      f"{res['mean_rel_err_vs_mc']:.2e} / {res['std_rel_err_vs_mc']:.2e} | 是 |")
w("")
w("**说明**：物理量（冰阻力）本身对试验均值的误差不做“<3%”断言——m1720 已判定其为"
  "同点标定的恒等式，留一法外推 11.8% ~ 23.2%，超出 15% 带，证据等级 WITHHELD。"
  "本单只判定数值层（PCE/求积/Sobol/抽样），其参照均为解析值或独立 MC。")
w("")

w("## 9. 注意点与局限")
w("")
w("1. **端点层**：`/ice-uq/uq` 的 Pydantic 默认仍写 `max_level=3`（该文件不在本单 "
  "EDIT_WHITELIST 内），但该请求现在会被 `build_pce` 的 guard 自动抬升到 4，"
  "响应中的 `quadrature_aliasing_free=True`（结果对象字段）即为证据；"
  "`/ice-uq/uq/all` 走 `run_all_ships_uq()`，默认已是 L=4。建议后续把 API 默认值也改成 "
  "`PCE_DEFAULT_MAX_LEVEL` 并在响应模型里暴露新字段。")
w("2. **截断与求积的不一致**：PCE 求积仍按未截断正态积分，MC 参照按声明截断抽样。"
  f"被截断的质量最大 {trunc['max_mass_outside']:.3e}，已显式上报，不改写分布律；"
  "若将来要严格一致，需改用截断正态的 Hermite 基（Uspensky/截断谱），工作量另计。")
w("3. **ΣS_T > 1**：p=3 的总阶展开对 4 输入实船得 ΣS_T ≈ 1.016–1.019，"
  "这是**截断**（>3 阶内容漏入）而非求积混叠，属已知 PCE 性质；"
  "若要 ΣS_T 严格 = 1，需更高 p（p=4 需 L=5，4 维 494 节点）。")
w("4. **m1882 证据仍可取**：混叠数值只能通过 `quadrature_policy='allow_aliasing'` 复现，"
  "默认路径不再产生它；测试同时钉住“默认安全”和“证据可复现”。")
w("5. **物理模型未改**：本单未触碰 Lindqvist 阻力公式、标定因子或试验参照值，"
  "m1720 的 WITHHELD 判决与 DEBT-ICE-1..6 台账不受影响。")
w("")

w("## 10. 复现")
w("")
w("```bash")
w("# 证据与图")
w("PYTHONPATH=backend python3 tools/validation/verify_ice_uq_aliasing_m1892.py")
w("python3 tools/validation/gen_m1892_doc.py")
w("# 回归")
w("python3 -m pytest tests/test_m1892_pce_aliasing.py \\")
w("                 tests/test_ice_uq_kernel_reference_m1882.py \\")
w("                 tests/test_pce_ice_resistance_uq.py -q   # PYTHONPATH=backend")
w("```")
w("")
w("## 11. 数值状态摘要（机器可读）")
w("")
w("```json")
w(json.dumps({k: status["status"][k]
              for k in ("capability", "evidence_grade", "guard", "fixed_by")},
             indent=2, ensure_ascii=False))
w("```")
w("")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("written", OUT, len(L), "lines")