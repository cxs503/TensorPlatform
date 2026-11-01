# ITTC 7.5-03-01/02 数值不确定度（GCI）吸收与 15% 实验闸门升级

*标准*: ITTC 7.5-03-01-01 / 7.5-03-02-01 (2017)  
*平台闸门*: 15%  *安全系数 Fs*: 1.25  
*提交*: `ad6c982c2958837ab27bc888bc4e23b073eea645`  *Python*: 3.11.6

## 1. 结论摘要

1. 平台原有判据「实验/参考偏差 >15% 不可接受」被升级为**双判据**：
   `U_V`（数值不确定度，取 `GCI_fine`）与偏差 `|S_num - S_ref|/|S_ref|` 共同决定 `PASS / FAIL / WITHHELD` 三态。`U_V >= 15%` 时一律 `WITHHELD`，
   禁止判 PASS 或 FAIL —— 这正是「求解器失真」与「验证不足」的分界线。
2. 端到端算例（`tools.gci.cases.circle_added_mass (2D circular-section added mass, exact reference 1.0)`）在三套系统加密网格上给出 `p = 1.9943`、`R_G = 0.25099`、`U_V = 0.0141%`、偏差 `0.0162%` → **PASS**。
3. 锚库 8 条（7 条规范闭式 + 1 条仓内回归），全部 sha256 自校验通过；
   任一字段改动一位即被 `AnchorTamperError` 拒绝（负向测试）。

## 2. 吸收的规范条款与入库清单（判据 (g)）

| # | 规范条款 | 实现位置 | 覆盖锚 / 证据 |
|---|---|---|---|
| 1 | ITTC 7.5-03-02-01 §3.1 (systematic grid refinement, refinement ratio r) | `tools.gci.core.gci(r21, r32)`, `tools.gci.cases.refinement_levels` | r21 / r32 fields in every CLI record |
| 2 | ITTC 7.5-03-02-01 Eqs. (1)-(2) (relative differences eps21, eps32) | `tools.gci.core.gci` (`eps21 = (f2-f1)/f1`, `eps32 = (f3-f2)/f2`) | ITTC-Q2-RELATIVE-P (documents the p-shift caused by relative normalisation) |
| 3 | ITTC 7.5-03-02-01 Eqs. (3)-(4) (convergence ratio R_G classification) | `tools.gci.core._convergence_class`, field `convergence` | ITTC-OSCILLATORY-FALLBACK (R_G<0), ITTC-DIVERGENT-FALLBACK (R_G>1) |
| 4 | ITTC 7.5-03-02-01 Eqs. (5)-(7) (observed order p_obs = ln(eps32/eps21)/ln r) | `tools.gci.core.gci` -> `p_obs`, `p_source` | ITTC-P2-EXACT (p = 2.000), ITTC-P1-EXACT (p = 1.000) |
| 5 | ITTC 7.5-03-02-01 §3.3 (limiting order fallback when p_obs is unreliable) | `tools.gci.core.gci(p_limit=0.5, p_max=4.0)` -> `p_used`, `p_source='fallback:<reason>'` | the three *-FALLBACK anchors |
| 6 | ITTC 7.5-03-02-01 Eq. (8) (Richardson extrapolation) | `tools.gci.core.richardson_extrapolate`, field `phi_ext` | figure 2 (Richardson plot) + `test_exact_p2_family_converges_to_the_exact_limit` |
| 7 | ITTC 7.5-03-02-01 Eq. (15) (GCI = Fs|e_a|/(r^p - 1), Fs = 1.25 / 3.0) | `tools.gci.core.gci` -> `gci21`, `gci32`, `fs` | ITTC-P2-EXACT, ITTC-TWO-GRID-FS3 |
| 8 | ITTC 7.5-03-01-01 §5 (validation uncertainty U_V, |D| <= U_V) | `tools.gci.core.gci(u_input=...)` -> `u_v`; `ittc_validation_state` | `U_V = hypot(GCI21, U_input)`; `ittc_state` field in the CLI record |
| 9 | ITTC 7.5-03-01-01 / platform gate (>15% deviation unacceptable) | `tools.gci.core.verdict` (PASS / FAIL / WITHHELD) | CLI exit codes 0/1/2; figure 3 |

## 3. 方法与关键设置

三网格链（`tools/gci/core.py`），输入 `S1`(细)、`S2`(中)、`S3`(粗) 与细化比 `r21, r32`：

```text
eps21 = (S2 - S1)/S1                     # ITTC Eq.(1)
eps32 = (S3 - S2)/S2                     # ITTC Eq.(2)
R_G   = eps21/eps32                      # 0<R_G<1 单调, R_G<0 振荡, R_G>1 发散
p_obs = ln(eps32/eps21)/ln(r21)          # R_G<=0 或 p_obs<=0 或 p_obs>p_max 时回退 p_lim=0.5
phi_ext = S1 + (S1 - S2)/(r21^p - 1)     # Richardson 外推
GCI21 = Fs*|eps21|/(r21^p - 1)           # Fs=1.25(三网格估 p) / 3.0(假定价 p)
GCI32 = Fs*|eps32|/(r32^p - 1),  R_G == GCI21/GCI32
U_V   = hypot(GCI21, U_input)            # ITTC 7.5-03-01-01 验证不确定度
```

裁决（`tools/gci/core.py::verdict`，机器可判、无人工介入）：

| 条件 | 判词 | 含义 |
|---|---|---|
| `U_V >= 15%` | **WITHHELD** | 数值不确定度已达闸门宽度，验证不足，不得判 PASS/FAIL |
| `U_V < 15%` 且 `|偏差| <= 15%` | **PASS** | 在可分辨的带内复现参考值 |
| `U_V < 15%` 且 `|偏差| > 15%` | **FAIL** | 带已足够窄，偏差必须归因于求解器（失真） |

CLI（`python -m tools.gci.cli`）输出单行 JSON，含 `r21/r32/p/R_G/GCI21/GCI32/U_V/verdict` 及溯源字段；退出码 0=PASS、1=FAIL、2=WITHHELD、3=错误。

## 4. 端到端算例：二维圆截面辐射问题（附加质量系数 C_a）

**物理背景**：无限域理想流体中圆截面做 x 向单位速度辐射运动，即条带理论附加质量的标准截面问题（平台耐波性模块的核）。**几何**：半径 a=1 的圆，外边界 R_out=200a。**控制方程与边界**：
`Laplace(phi)=0`，物面 `dphi/dn = cos(theta)`（Neumann），远场 `phi -> 0`。
**解析参考**：`phi = -a^2 cos(theta)/r`，`A11 = rho*pi*a^2`，故 `C_a = A11/(rho*pi*a^2) = 1`（精确）。
**数值**：对数径向坐标 `s = ln(r/a)` 下的二阶中心差分（该坐标下 Laplace 退化为常系数 `phi_ss + phi_thetatt = 0`，网格自相似）+ 物面 ghost-node 二阶 Neumann；`A11` 由 `r=a` 上的周期梯形围线积分得到。三套网格按 r=2 同步加密。

| 网格 (nr × ntheta) | ds | dtheta | C_a | 相对解析误差 | 线性解残差 |
|---|---|---|---|---|---|
| 40 × 64 | 0.13246 | 0.098175 | 0.9981661197 | 0.001834 | 1.5e-13 |
| 80 × 128 | 0.066229 | 0.049087 | 0.9995025003 | 4.97e-04 | 7.5e-13 |
| 160 × 256 | 0.033114 | 0.024544 | 0.99983803 | 1.62e-04 | 3.5e-12 |

### 4.1 GCI 与裁决结果

| 算例 | 类型 | p | R_G | GCI21 | GCI32 | U_V | 参考偏差 | 判词 |
|---|---|---|---|---|---|---|---|---|
| circle-section 40..160 | real | 1.9943 | 0.25099 | 1.41e-04 | 5.60e-04 | 1.41e-04 | 1.62e-04 | **PASS** |
| circle-section 10..40 | real | 1.9116 | 0.2658 | 0.002383 | 0.008964 | 0.002383 | 0.001834 | **PASS** |
| SYNTHETIC-FAIL | synthetic | 2 | 0.25 | 4.17e-04 | 0.001667 | 4.17e-04 | 0.2 | **FAIL** |
| SYNTHETIC-WITHHELD | synthetic | 0.5 | -3.9 | 0.9053 | 0.2321 | 0.9053 | 0 | **WITHHELD** |

合成行（`kind = synthetic`）仅用于演示闸门三态，理由逐行记录在 `artifacts/gci/gci_e2e.json` 的 `reason` 字段，**不构成任何验证结论**。

### 4.2 锚库复现表（判据 (b)）

| 锚 | 来源 | p(库) | p(工具) | p 相对偏差 | GCI21(库) | GCI21(工具) | GCI21 相对偏差 | <1% |
|---|---|---|---|---|---|---|---|---|
| `ITTC-P2-EXACT` | clause | 2 | 2 | 1.3e-15 | 0.0041666667 | 0.0041666667 | 3.3e-15 | 是 |
| `ITTC-P1-EXACT` | clause | 1 | 1 | 1.6e-15 | 0.0125 | 0.0125 | 1.4e-15 | 是 |
| `ITTC-Q2-RELATIVE-P` | clause | 1.973364873 | 1.973364873 | 0 | 0.0079580745 | 0.0079580745 | 0 | 是 |
| `ITTC-OSCILLATORY-FALLBACK` | clause | 0.5 | 0.5 | 0 | 0.03017767 | 0.03017767 | 0 | 是 |
| `ITTC-DIVERGENT-FALLBACK` | clause | 0.5 | 0.5 | 0 | 0.12071068 | 0.12071068 | 0 | 是 |
| `ITTC-P-ABOVE-MAX-FALLBACK` | clause | 0.5 | 0.5 | 0 | 0.0091349258 | 0.0091349258 | 0 | 是 |
| `ITTC-TWO-GRID-FS3` | clause | 2 | 2 | 1.3e-15 | 0.01 | 0.01 | 3.5e-15 | 是 |
| `REPO-CIRCLE-CA-E2E` | internal | 1.994302905 | 1.994302905 | 0 | 1.405653e-04 | 1.405653e-04 | 0 | 是 |

## 5. 图

1. `artifacts/gci/fig1_convergence.png` —— 三网格收敛史 + Richardson 极限 + 解析值。
2. `artifacts/gci/fig2_richardson.png` —— log-log 离散误差 vs 网格尺度，拟合斜率 `-p_obs` 对照理论 `-2`。
3. `artifacts/gci/fig3_u_v_gate.png` —— 各算例 `U_V` 与 15% 闸门、参考偏差对照（颜色=判词）。

![fig1](../artifacts/gci/fig1_convergence.png)

![fig2](../artifacts/gci/fig2_richardson.png)

![fig3](../artifacts/gci/fig3_u_v_gate.png)

## 6. 注意点与局限

1. **相对差分的 p 偏移（真实陷阱）**：ITTC 用 `eps=(S_{i+1}-S_i)/S_i` 归一化，即使误差严格按 `C h^2` 衰减，回代得到的 `p_obs` 也不是 2 而是 `1.973364873`（锚 `ITTC-Q2-RELATIVE-P` 钉死此值）。不要为了让 p「好看」而改成绝对差分——规范就是这么定义的。
2. **p 回退必须留痕**：`p_obs<=0`、`R_G<=0` 或 `p_obs>p_max=4` 时改用 `p_lim=0.5`，记录 `p_source='fallback:<原因>'`，且该情形下的 GCI 只能溯源不可当验证带。
3. **远场截断是「共同误差」**：`R_out=200a` 带来 ~`(a/R_out)^2 ≈ 2.5e-5` 的相对偏差，不随网格加密下降，因此不出现在 `U_V` 里，只出现在「偏差」里。
4. **|D| <= U_V 判据在细网格上临界**：本算例 `ittc_state = SIMULATION_ERROR`（偏差 1.62e-04 对 U_V 1.41e-04，比值 1.152），说明三网格尚未完全进入渐近区——如实记录，未做任何钳位、常数修正或拟合式掩盖。
5. **锚的公开来源边界**：本轮**没有**抄录任何记忆中的 ITTC/Roache 出版网格三元组。ITTC 官网在构建环境内返回 403（不可达），逐条编造「公开示例数值」比声明缺口更糟。因此 `provenance='clause'` 的锚全部是规范方程的闭式构造（可手算复核，见判据 (c) 测试），`provenance='internal'` 的锚是本仓求解器的回归锚（容差 1e-3）。若后续拿到官方示例表，只需追加 spec + 重跑 `tools.gci.gen_anchor_digests --write`。
6. **适用范围**：GCI 只回答「离散误差」，不覆盖模型误差（湍流模型、经验公式）、输入不确定度（`U_input` 需另行给出，本单默认 0）与程序实现错误（用解析算例另行把关）。

## 7. 误差判定（逐项回答「是否<3% 或 <15%」）

| 关键量 | 数值 | 基准 | 相对误差 | 判据 | 结论 |
|---|---|---|---|---|---|
| C_a（最细网格 160×256） | 0.99983803 | 解析 1.0 | 1.62e-04 | <3% | **是** |
| C_a（Richardson 外推 phi_ext） | 0.999950464 | 解析 1.0 | 4.95e-05 | <3% | **是** |
| p_obs | 1.9943 | 理论二阶 2 | 0.00285 | <15% | **是** |
| U_V | 1.41e-04 | 15% 闸门 | — | <15% | **是**（可判词） |
| 参考偏差 | 1.62e-04 | 15% 闸门 | — | <15% | **是** → PASS |
| 锚 p / GCI / U_V 复现 | 见 §4.2 | 规范闭式值与回归值 | 最大 1.4e-14 | <1% | **是** |
| 手算 GCI 复现（50 位 Decimal） | 5 组三元组 | 独立高精度计算 | <1e-10 | <1e-10 | **是** |

无基准项声明：本单没有实验（EFD）数据可用，端到端算例的参考值取**解析解**（自含、可无限精度复核），因此「实验偏差」一栏在本算例中等价于「解析偏差」；对真实船型/阻力系数，`U_V` 的接线方式相同（`tools.gci.cli --reference <EFD>`），但需要对应算例的三套网格解，本轮未做，属未完成部分。

## 8. 复现方式

```bash
python -m tools.gci.run_e2e            # 跑算例 + 写 artifacts/gci/{gci_e2e.json,fig*.png}
python -m tools.gci.gen_report         # 重建本报告（数字全部来自 JSON）
python -m tools.gci.cli --f1 ... --f2 ... --f3 ... --r 2 --reference 1.0   # 单行 JSON
python -m pytest tests/test_gci_core.py tests/test_gci_anchors.py tests/test_gci_cli.py -q
```
