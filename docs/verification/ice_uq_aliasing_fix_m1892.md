# 冰阻力 UQ（PCE/Sobol）混叠缺陷修复复核 m1892

- 任务：m1882 定位 `backend/services/ice_resistance_uq.py` 的谱投影混叠，m1892 修复并固化证据。
- 代码：`backend/services/ice_resistance_uq.py`（`build_pce` / `run_all_ships_uq` / `monte_carlo_reference` / `run_ice_resistance_uq`）
- 证据工件：`artifacts/m1892/evidence.json`（本文件所有数字由 `tools/validation/gen_m1892_doc.py` 从该工件读出，零手抄）
- 生成脚本：`tools/validation/verify_ice_uq_aliasing_m1892.py`
- 回归测试：`tests/test_m1892_pce_aliasing.py`（20 项）、`tests/test_ice_uq_kernel_reference_m1882.py`（13 项，原 `xfail(strict)` 已转正）、`tests/test_pce_ice_resistance_uq.py`（45 项）
- 前序复核：`docs/verification/ice_uq_kernel_independent_review_m1882.md`

**判决：混叠缺陷已修复（guard 生效，反例方差与总阶灵敏度精确到机器精度），抽样截断改为端点层显式声明。物理模型本身仍为 WITHHELD（见 m1720 的标定债务），本单只处理数值层。**

## 1. 缺陷机制

PCE 投影系数由内积给出 `c_α = <f, ψ_α> / <ψ_α, ψ_α>`，用 Smolyak 稀疏网格（1 维 Gauss-Hermite 第 k 层 k 点）数值求积。该规则对总阶 `2L-1` 精确，因此要精确分解 `Var = Σ c_α²‖ψ_α‖²` 与 Sobol 指数，必须让 Gram 矩阵 `<ψ_a, ψ_b>`（`|a|,|b| ≤ p`）被精确积分：

```
2L - 1 >= 2p        <=>        L >= p + 1 = max_poly_order + 1
```

出厂默认 `(max_level=3, max_poly_order=3)` 只精确到总阶 5，而 `p=3` 需要总阶 6：响应中未分辨的高阶分量被投影到无关系数上。用纯三次响应 `y = ξ₀³`（第二输入 ξ₁ 完全无影响，解析值 `S_T(ξ₁)=0`、`Var=E[ξ⁶]=15`）量化：

| 配置 | Var | S_T(ξ₁) | 求积精确阶 | 判定 |
|---|---|---|---|---|
| `allow_aliasing`, L=3（原出厂配置） | 11.000000 | 0.181818 | 5 | 混叠（伪灵敏度 18.2%） |
| 修复后默认（L=4） | 15.000000000000 | 3.270e-31 | 7 | 精确 |

## 2. 修复内容

1. **入口校验 + 自动抬升**（`build_pce`）：新增 `required_quadrature_level(p) = p + 1` 与 `quadrature_policy` 三档策略——默认 `auto_raise`（抬升到 `max_poly_order + 1` 并把 `max_level_requested` / `max_level` / `quadrature_raised` / `aliasing_free` 写进返回字典），`error` 抛 `PCEQuadratureAliasingError`（即显式 DomainError 语义），`allow_aliasing` 仅用于复现 m1882 证据。`max_level=None` 表示取无混叠默认值。
2. **入口默认值同步**：`PCE_DEFAULT_MAX_LEVEL = PCE_DEFAULT_MAX_POLY_ORDER + 1 = 4`，`run_ice_resistance_uq` 与 `run_all_ships_uq` 默认即用该值；结果对象新增 `max_level / max_poly_order / quadrature_aliasing_free` 供端点与测试读取。
3. **抽样截断改为端点层声明**：`monte_carlo_reference` 不再内含四条“按索引硬编码”的裁剪，改为接受 `truncation=[TruncationSpec(...)]`：`truncation_mode='reject'` 拒绝重抽（精确截断正态，无边界原子）、`'clip'` 裁剪并量化原子质量；**`truncation=None` 时完全不裁剪**。冰阻力入口用模块常量 `ICE_INPUT_TRUNCATION` 显式声明四条支撑区间，并把实际抽样法则（各维解析截断质量、实际越界比例、重抽次数）写回结果 `distribution_truncation`，CI 覆盖率检验用同一法则抽样。
4. **测试与文档**：新增 `tests/test_m1892_pce_aliasing.py` 锁定反例（`S_T(零影响输入) <= 1e-6`、`Var = 15`）、guard 三档策略、默认值一致性、截断声明；m1882 的 `xfail(strict=True)` 反例测试转正为硬断言，原始混叠数值改由 `quadrature_policy='allow_aliasing'` 旁路钉住。
5. **顺带修好被该缺陷遮住的端点 500**：`backend/api/domain/ice_uq.py` 的 `/uq` 从 `result.reference_mean_kN` / `reference_std_kN` 取 MC 参照，而 `IceResistanceUQResult` 上从未有这两个字段（实际字段名是 `mc_mean_kN` / `mc_std_kN`），即任何 `/uq` 请求都会 AttributeError。已在结果对象上补 `reference_mean_kN` / `reference_std_kN` 只读别名（services 侧改动，未越白名单改 API 文件），并由 `tests/test_m1892_pce_aliasing.py::test_api_endpoint_reads_reference_aliases_and_serves_fixed_numbers` 端到端锁定。

## 3. 证据一：混叠随求积层的收敛（2 输入反例，p=3）

| Smolyak 层 L | 节点数 | 精确阶 2L-1 | 需要阶 2p | Var(y=ξ₀³) | Var 相对误差 | S_T(ξ₁) | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 6 | 0.000000 | 1.000e+00 | 0.000000 | 混叠 |
| 2 | 5 | 3 | 6 | 2.166667 | 8.556e-01 | 0.230769 | 混叠 |
| 3 | 14 | 5 | 6 | 11.000000 | 2.667e-01 | 0.181818 | 混叠 |
| 4 | 30 | 7 | 6 | 15.000000 | 2.368e-16 | 0.000000 | 无混叠 |
| 5 | 55 | 9 | 6 | 15.000000 | 1.184e-15 | 0.000000 | 无混叠 |
| 6 | 91 | 11 | 6 | 15.000000 | 2.132e-15 | 0.000000 | 无混叠 |

解析参照：`Var = E[ξ⁶] = 15`、`S_T(ξ₁) = 0`。L ≥ 4（= p+1）后两项同时精确，与推导一致；这也解释了为何“只抬层数即可修好”而“只调多项式阶不够”。

生产路径是 4 输入（冰强度/冰厚/摩擦/航速）、p=3，故必须 L=4：4 维 Smolyak 节点数 L=3 时 45 个（权重和 1.000000），L=4 时 165 个（权重和 1.000000）——代价是单船模型调用从 45 次增加到 165 次，仍是毫秒级。

## 4. 证据二：单项式响应的精确性（默认入口，无需手工设层数）

| 响应阶 p | 实际使用层 | 节点数 | Var(PCE) | Var(解析) | Var 相对误差 | S_T(inert) | ΣS_T |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 5 | 1.000000000 | 1.000000000 | 4.441e-16 | 0.000e+00 | 1.000000000 |
| 2 | 3 | 14 | 2.000000000 | 2.000000000 | 2.220e-15 | 1.926e-32 | 1.000000000 |
| 3 | 4 | 30 | 15.000000000 | 15.000000000 | 2.368e-16 | 3.270e-31 | 1.000000000 |
| 4 | 5 | 55 | 96.000000000 | 96.000000000 | 1.332e-15 | 7.043e-31 | 1.000000000 |

## 5. 证据三：三条实船 Sobol 指数（修复前/后 vs 独立 Saltelli–Jansen 蒙特卡洛）

独立参照用 `tools/validation/ice_uq_reference_m1882.py::legendre_saltelli_indices`（Saltelli 2010 一阶 + Jansen 1999 总阶，只用响应值，不碰多项式基），抽样支撑与 `ICE_INPUT_TRUNCATION` 一致（截断支撑上的抽样）。

| 船 | 层 | 节点 | 输入 | S_T 修复后 | S_T 原 L=3 | 差 | S_T 独立 MC |
|---|---|---|---|---|---|---|---|
| MV_Arctic | 4 | 165 | ice_strength_MPa | 0.2391 | 0.2390 | -0.00007 | 0.2460 |
| MV_Arctic | 4 | 165 | ice_thickness_m | 0.6726 | 0.6726 | -0.00004 | 0.6686 |
| MV_Arctic | 4 | 165 | friction_coeff | 0.0840 | 0.0839 | -0.00005 | 0.0843 |
| MV_Arctic | 4 | 165 | ship_speed_ms | 0.0205 | 0.0204 | -0.00003 | 0.0203 |
| Polarstern | 4 | 165 | ice_strength_MPa | 0.2987 | 0.2987 | -0.00006 | 0.3073 |
| Polarstern | 4 | 165 | ice_thickness_m | 0.6535 | 0.6534 | -0.00006 | 0.6486 |
| Polarstern | 4 | 165 | friction_coeff | 0.0426 | 0.0425 | -0.00003 | 0.0427 |
| Polarstern | 4 | 165 | ship_speed_ms | 0.0239 | 0.0239 | -0.00003 | 0.0237 |
| Terry_Fox | 4 | 165 | ice_strength_MPa | 0.2996 | 0.2995 | -0.00006 | 0.3080 |
| Terry_Fox | 4 | 165 | ice_thickness_m | 0.6502 | 0.6501 | -0.00006 | 0.6452 |
| Terry_Fox | 4 | 165 | friction_coeff | 0.0467 | 0.0467 | -0.00003 | 0.0468 |
| Terry_Fox | 4 | 165 | ship_speed_ms | 0.0223 | 0.0223 | -0.00002 | 0.0221 |

| 船 | max｜S_T(修复)−MC｜ | max｜S_T(原 L=3)−MC｜ | ΣS_T(修复后) | 均值误差 | 标准差误差 | CI 覆盖 |
|---|---|---|---|---|---|---|
| MV_Arctic | 0.0069 | 0.0070 | 1.0161 | 1.09e-03 | 5.84e-03 | 0.9500 |
| Polarstern | 0.0085 | 0.0086 | 1.0187 | 1.30e-03 | 6.08e-03 | 0.9514 |
| Terry_Fox | 0.0084 | 0.0084 | 1.0188 | 1.46e-03 | 5.89e-03 | 0.9510 |

注意（诚实报告）：对这三条实船的 Lindqvist 响应，L=3 与 L=4 的 S_T 差仅 7.1e-05——该响应接近可加、三次以上分量很小，因此混叠在**实船 Sobol 数值**上的影响很小。缺陷的真实危害有二：(a) 配置层面结构性错误，任何带明显三次/高阶分量的响应（如结冰强度-速度耦合、含立方项的经验修正）会被判给无影响输入；(b) 无法通过“误差看起来还行”来自证清白——只能靠构造反例（m1882 的做法）暴露。

## 6. 证据四：抽样截断（原硬裁剪 → 端点层声明）

| 输入 | 下界 | 上界 | 解析截断质量 | 依据 |
|---|---|---|---|---|
| ice_strength_MPa | 0.3 | +∞ | 3.167e-05 | compressive strength is strictly positive; 0.3 MPa is the lower envelope of the ice covers the model is calibrated on |
| ice_thickness_m | 0.1 | +∞ | 9.866e-10 | level-ice thickness below 0.1 m is outside the Lindqvist calibration domain (bending term ~ h^2, crushing ~ h^2 with l_c ~ h^0.75) |
| friction_coeff | 0.02 | 0.5 | 2.635e-04 | hull-ice friction support observed in full-scale trials (0.02 - 0.5) |
| ship_speed_ms | 0.5 | +∞ | 5.551e-17 | below 0.5 m/s the ship is not advancing continuously in level ice (ramming regime, not modelled) |

| 抽样法则 | 均值 [kN] | 标准差 [kN] | 相对 reject 的均值偏差 | 相对 std 偏差 |
|---|---|---|---|---|
| reject（精确截断正态，新默认） | 4828.388 | 1117.382 | — | — |
| clip（旧行为，边界原子） | 4828.092 | 1117.395 | -6.13e-05 | 1.19e-05 |
| 不截断（plain 正态） | 4828.061 | 1117.426 | -6.77e-05 | 3.91e-05 |

声明截断质量的量级：最大 2.635e-04（摩擦系数 0.02 下界），重抽次数 27。因此旧硬裁剪的数值偏差很小（均值 -6.1e-05），但它此前是**不可见**的：四条边界按输入在列表中的位置硬编码在通用 MC 函数里，换一份输入列表就会张冠李戴，且把正态分布变成了带原子的混合分布。现在支撑区间是入口层的显式值，并随结果一起上报（`distribution_truncation`）。

## 7. 图

![混叠随求积层收敛](figures_m1892/fig1_aliasing_map.png)

*图 1*：左—`y=ξ₀³` 的 PCE 方差随 Smolyak 层变化（解析 15，L=3 得 11）；右—无影响输入的伪总阶灵敏度（L=3 得 0.1818，L≥4 归零）。

![修复前后 Sobol 对比](figures_m1892/fig2_sobol_before_after.png)

*图 2*：MV Arctic / Polarstern 的四个输入总阶 Sobol 指数：原 L=3、修复后 L=4、以及独立 Saltelli–Jansen MC。三条实船差异在 1e-4 量级（见 §5 的诚实说明），说明修复的必要性来自结构性正确性而非数值显著性。

## 8. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 平台值 | 基准 | 相对误差 | 是否 < 3% |
|---|---|---|---|---|
| `y=ξ₀³` 的 Var（修复后默认） | 15.000000000000 | 15（解析 E[ξ⁶]） | 2.37e-16 | 是 |
| `y=ξ₀³` 的 S_T(ξ₁)（修复后默认） | 3.270e-31 | 0（解析） | 绝对误差 3.27e-31 | 是 |
| `y=ξ₀³` 的 Var（原 L=3，混叠） | 11.000000 | 15（解析） | 26.67% | 否（修复前不可接受） |
| `y=ξ₀³` 的 S_T(ξ₁)（原 L=3，混叠） | 0.181818 | 0（解析） | 绝对值 0.1818 | 否（修复前不可接受） |
| Var(ξ^1)（默认入口） | 1.000000000 | 1.000000000（解析） | 4.44e-16 | 是 |
| Var(ξ^2)（默认入口） | 2.000000000 | 2.000000000（解析） | 2.22e-15 | 是 |
| Var(ξ^3)（默认入口） | 15.000000000 | 15.000000000（解析） | 2.37e-16 | 是 |
| Var(ξ^4)（默认入口） | 96.000000000 | 96.000000000（解析） | 1.33e-15 | 是 |
| 实船 ΣS_T（MV Arctic） | 1.0161 | 1（恒等式，受 p=3 截断） | 1.61% | 边界（见局限） |
| MV_Arctic S_T 修复后 vs 独立 MC | 见 §5 | Saltelli–Jansen MC | max 绝对差 0.0069 | 是 |
| Polarstern S_T 修复后 vs 独立 MC | 见 §5 | Saltelli–Jansen MC | max 绝对差 0.0085 | 是 |
| Terry_Fox S_T 修复后 vs 独立 MC | 见 §5 | Saltelli–Jansen MC | max 绝对差 0.0084 | 是 |
| MV_Arctic 均值/标准差 vs MC 参照 | 4827.33 kN / 1117.04 kN | MC 4832.58 kN / 1123.60 kN | 1.09e-03 / 5.84e-03 | 是 |
| Polarstern 均值/标准差 vs MC 参照 | 1813.83 kN / 459.55 kN | MC 1816.19 kN / 462.37 kN | 1.30e-03 / 6.08e-03 | 是 |
| Terry_Fox 均值/标准差 vs MC 参照 | 856.56 kN / 217.61 kN | MC 857.81 kN / 218.90 kN | 1.46e-03 / 5.89e-03 | 是 |

**说明**：物理量（冰阻力）本身对试验均值的误差不做“<3%”断言——m1720 已判定其为同点标定的恒等式，留一法外推 11.8% ~ 23.2%，超出 15% 带，证据等级 WITHHELD。本单只判定数值层（PCE/求积/Sobol/抽样），其参照均为解析值或独立 MC。

## 9. 注意点与局限

1. **端点层**：`/ice-uq/uq` 的 Pydantic 默认仍写 `max_level=3`（该文件不在本单 EDIT_WHITELIST 内），但该请求现在会被 `build_pce` 的 guard 自动抬升到 4，响应中的 `quadrature_aliasing_free=True`（结果对象字段）即为证据；`/ice-uq/uq/all` 走 `run_all_ships_uq()`，默认已是 L=4。建议后续把 API 默认值也改成 `PCE_DEFAULT_MAX_LEVEL` 并在响应模型里暴露新字段。
2. **截断与求积的不一致**：PCE 求积仍按未截断正态积分，MC 参照按声明截断抽样。被截断的质量最大 2.635e-04，已显式上报，不改写分布律；若将来要严格一致，需改用截断正态的 Hermite 基（Uspensky/截断谱），工作量另计。
3. **ΣS_T > 1**：p=3 的总阶展开对 4 输入实船得 ΣS_T ≈ 1.016–1.019，这是**截断**（>3 阶内容漏入）而非求积混叠，属已知 PCE 性质；若要 ΣS_T 严格 = 1，需更高 p（p=4 需 L=5，4 维 494 节点）。
4. **m1882 证据仍可取**：混叠数值只能通过 `quadrature_policy='allow_aliasing'` 复现，默认路径不再产生它；测试同时钉住“默认安全”和“证据可复现”。
5. **物理模型未改**：本单未触碰 Lindqvist 阻力公式、标定因子或试验参照值，m1720 的 WITHHELD 判决与 DEBT-ICE-1..6 台账不受影响。

## 10. 复现

```bash
# 证据与图
PYTHONPATH=backend python3 tools/validation/verify_ice_uq_aliasing_m1892.py
python3 tools/validation/gen_m1892_doc.py
# 回归
python3 -m pytest tests/test_m1892_pce_aliasing.py \
                 tests/test_ice_uq_kernel_reference_m1882.py \
                 tests/test_pce_ice_resistance_uq.py -q   # PYTHONPATH=backend
```

## 11. 数值状态摘要（机器可读）

```json
{
  "capability": "PCE surrogate + Sobol decomposition for ice-resistance UQ",
  "evidence_grade": "REAL",
  "guard": "build_pce raises max_level to max_poly_order + 1 (quadrature_policy='auto_raise'); 'error' raises PCEQuadratureAliasingError instead",
  "fixed_by": "m1892 (defect located by m1882)"
}
```

