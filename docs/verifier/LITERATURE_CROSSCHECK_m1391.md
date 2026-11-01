# 验证欠账复核：阻力试点题对文献基准的独立交叉验证（mission 1391）

| 字段 | 值 |
|---|---|
| mission_id | 1391 |
| 复核对象 | verifier 阻力试点（mission 537，15 道冻结题） |
| 题库 | `tools/verifier/question_bank/questions.db`（只读打开） |
| 生成时间 (UTC) | 2026-11-01T04:55:02.537353+00:00 |
| 证据工件 | `artifacts/m1391/literature_crosscheck.json` |
| 图 | `artifacts/m1391/fig1_friction_lines_crosscheck.png`, `artifacts/m1391/fig2_form_factor_vs_holtrop1982.png` |
| 判据 | 平台可接受误差 15.0 %；目标 3.0 %；循环判定阈值 rel < 1e-12 |

## 1. 案例介绍：为什么要复核

mission 537 的试点报告 `artifacts/pilot_verification_report.json` 给出 15/15 全过，且每道题的 deviation 恰为 `0.0`。这个数字不是正确性证据，而是**循环验证**的指纹：`tools/verifier/generate_questions.py`（出题/参考值侧）与 `tools/verifier/pilots/resistance_calculator.py`（候选实现侧）写的是**同一个公式字符串**，参考值实质是候选自身算术的拷贝——常数写错也会照样「通过」。

因此本单沿独立路径重做一次验证：参考值不再由候选代码复述，而是由 `tools/verifier/reference_formulas.py` 按公开文献独立实现，并用**文献中表格化的数值**（而非本仓库算出的数值）反过来审计参考实现自身（`PUBLISHED_ANCHORS`）。

复核的物理量：
- ITTC-1957 模型-实船相关线 $C_F = 0.075/(\log_{10}Re-2)^2$（摩擦阻力系数）；
- Blasius 层流平板 $C_F = 1.328/\sqrt{Re_L}$；
- Prandtl-Schlichting 湍流平板 $C_F = 0.455/(\log_{10}Re_L)^{2.58}$；
- Holtrop & Mennen (1982) 形状因子 $1+k$（三艘典型船：Series 60 $C_b$=0.6、散货船 0.7、油船 0.8）。

几何参数表（取自题库 specification，未修改）：

| 船型 | L [m] | B [m] | T [m] | Cb [-] | L/B | B/T |
|---|---|---|---|---|---|---|
| Bulk carrier Cb=0.7 | 150.0 | 22.0 | 8.5 | 0.70 | 6.82 | 2.59 |
| Series 60 Cb=0.6 | 100.0 | 15.0 | 6.0 | 0.60 | 6.67 | 2.50 |
| Tanker Cb=0.8 | 200.0 | 32.0 | 11.0 | 0.80 | 6.25 | 2.91 |

## 2. 怎么做的

1. **独立参考实现**（`reference_formulas.py`）：每条公式的常数、适用域、出处单独记录（`Provenance`），与候选实现物理隔离；单元测试断言该模块不 import 候选代码、不含候选的 `# Scaled for pilot` 修补因子。
2. **参考自审**（A）：把参考实现输出与文献表格值比对。文献值仅给到标注有效位，容许偏差取末位半个单位。
3. **题库复核**（B）：只读打开 `questions.db`，对每道题重算参考值，报告「题库期望值 vs 独立参考」和「候选 vs 独立参考」两个偏差，并把偏差 <1e-12 判为循环。
4. **跨公式水平检查**（C）：Prandtl-Schlichting 与 Schoenherr/ATTC-1947 隐式线（二分法求解，常数集完全不同）在同一 $Re_L$ 上比较水平。
5. **形状因子模型检查**（D）：候选的「Holtrop-Mennen」是与文献完整表达式的对比，另加 $C_m\in[0.97,0.995]$、LCB$\in[-3,0]\,\%L$ 的敏感性包络——题库只存了 (Cb, L, B, T)，Cm/LCB 未记录，故用包络而非单点假设，保证结论在不确定度下仍成立。
6. **回归门**：`tools/verifier/tests/test_literature_crosscheck.py` 把上述不变量写成 pytest；已知欠账用 `xfail(strict=True)` 记录（修好后会 XPASS → 强制清理标记，欠账不会烂在库里）。

工具链：Python 3.11 + numpy/matplotlib(Agg) + pytest；纯解析公式，无网格、无迭代求解器，单次全量审计 < 2 s（本机 load15 高于 100，按容量纪律选了纯轻量核验活）。

## 3. 计算结果

### 3.1 A：参考实现 vs 文献表格值

| 公式 | 输入 | 文献值 | 参考实现 | 相对偏差 | 允许 | 判定 |
|---|---|---|---|---|---|---|
| ittc_1957 | Re=1e+06 | 0.004688 | 0.0046875 | 0.011 % | 0.050 % | PASS |
| ittc_1957 | Re=1e+07 | 0.003 | 0.003 | 0.000 % | 0.050 % | PASS |
| ittc_1957 | Re=1e+08 | 0.002083 | 0.00208333 | 0.016 % | 0.050 % | PASS |
| ittc_1957 | Re=1e+09 | 0.001531 | 0.00153061 | 0.025 % | 0.050 % | PASS |
| blasius | Re_L=100000 | 0.0042 | 0.0041995 | 0.012 % | 0.050 % | PASS |
| blasius | Re_L=1e+06 | 0.001328 | 0.001328 | 0.000 % | 0.050 % | PASS |
| prandtl_schlichting | Re_L=1e+06 | 0.004471 | 0.00447076 | 0.005 % | 0.050 % | PASS |
| prandtl_schlichting | Re_L=1e+07 | 0.003004 | 0.00300371 | 0.010 % | 0.050 % | PASS |
| prandtl_schlichting | Re_L=1e+08 | 0.002128 | 0.00212833 | 0.016 % | 0.050 % | PASS |
| prandtl_schlichting | Re_L=1e+09 | 0.001571 | 0.0015706 | 0.025 % | 0.050 % | PASS |

结论：10/10 个锚点通过 → 参考实现本身可信（`A_anchor_check_pass = True`）。

### 3.2 B：题库 15 题 vs 独立参考

| 题 | 参考路径 | 题库期望 | 独立参考 | 题库-参考 | 候选-参考 | 循环？ | 容差(相对) | 判定 |
|---|---|---|---|---|---|---|---|---|
| Holtrop-Mennen form factor: Bulk carrier Cb=0.7 | holtrop_1982_full | 1.03928 | 1.20523 | 15.967 % | 13.769 % | 否 | 0.01 | PASS |
| Holtrop-Mennen form factor: Series 60 Cb=0.6 | holtrop_1982_full | 1.05474 | 1.16754 | 10.695 % | 9.662 % | 否 | 0.01 | PASS |
| Holtrop-Mennen form factor: Tanker Cb=0.8 | holtrop_1982_full | 1.02759 | 1.31599 | 28.066 % | 21.915 % | 否 | 0.01 | FAIL |
| ITTC-1957 Cf at Re=1e6 (low speed model) | ittc_1957 | 0.0046875 | 0.0046875 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| ITTC-1957 Cf at Re=1e7 (typical model) | ittc_1957 | 0.003 | 0.003 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| ITTC-1957 Cf at Re=1e8 (full ship) | ittc_1957 | 0.00208333 | 0.00208333 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| ITTC-1957 Cf at Re=1e9 (large ship) | ittc_1957 | 0.00153061 | 0.00153061 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| ITTC-1957 Cf at Re=5e6 (medium model) | ittc_1957 | 0.00339669 | 0.00339669 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| Laminar flat plate Cf at Re_L=1e+05 | blasius | 0.0041995 | 0.0041995 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| Laminar flat plate Cf at Re_L=1e+06 | blasius | 0.001328 | 0.001328 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| Laminar flat plate Cf at Re_L=5e+05 | blasius | 0.00187808 | 0.00187808 | 0.000 % | 0.000 % | 是 | 0.001 | PASS |
| Turbulent flat plate Cf at Re_L=1e+06 | prandtl_schlichting | 0.00447076 | 0.00447076 | 0.000 % | 0.000 % | 是 | 0.005 | PASS |
| Turbulent flat plate Cf at Re_L=1e+07 | prandtl_schlichting | 0.00300371 | 0.00300371 | 0.000 % | 0.000 % | 是 | 0.005 | PASS |
| Turbulent flat plate Cf at Re_L=1e+08 | prandtl_schlichting | 0.00212833 | 0.00212833 | 0.000 % | 0.000 % | 是 | 0.005 | PASS |
| Turbulent flat plate Cf at Re_L=1e+09 | prandtl_schlichting | 0.0015706 | 0.0015706 | 0.000 % | 0.000 % | 是 | 0.005 | PASS |

- 12 道摩擦题（Cf）与独立参考的偏差：最大 0.000 %（即数值恒等），且 12/15 道题被标记为循环。
- 3 道形状因子题（1+k）与文献的偏差见 3.4；它们同时也是 3.3 的容差语义问题的受害者。

### 3.3 C：跨公式水平检查（Prandtl-Schlichting vs Schoenherr）

| $Re_L$ | Prandtl-Schlichting | Schoenherr | 相对偏差 | <3 % ? |
|---|---|---|---|---|
| 1e+06 | 0.00447076 | 0.00440943 | 1.37 % | 是 |
| 1e+07 | 0.00300371 | 0.00293428 | 2.31 % | 是 |
| 1e+08 | 0.00212833 | 0.00207203 | 2.65 % | 是 |
| 1e+09 | 0.0015706 | 0.00153094 | 2.53 % | 是 |

两条完全独立的湍流平板线在 1e6..1e9 上一致（最大偏差 2.65 %），说明候选的湍流平板水平没有系统性偏高/偏低。

### 3.4 D：形状因子——候选 vs Holtrop & Mennen (1982) 完整表达式

| 船型 | 候选 1+k | 文献 1+k | 包络 [min,max] | 候选是否在包络外 | 误差(1+k) | 误差(k) | 判定 |
|---|---|---|---|---|---|---|---|
| Bulk carrier Cb=0.7 | 1.0393 | 1.2052 | [1.1938, 1.2158] | 是 | 13.8 % | 80.9 % | FAIL_LITERATURE |
| Series 60 Cb=0.6 | 1.0547 | 1.1675 | [1.1606, 1.1738] | 是 | 9.7 % | 67.3 % | FAIL_LITERATURE |
| Tanker Cb=0.8 | 1.0276 | 1.3160 | [1.2896, 1.3424] | 是 | 21.9 % | 91.3 % | FAIL_LITERATURE |

趋势（关键）：候选 1+k 随 Cb **下降**，文献随 Cb **上升**：

| 船型 | Cb | 候选 1+k | 文献 1+k |
|---|---|---|---|
| Series 60 Cb=0.6 | 0.60 | 1.0547 | 1.1675 |
| Bulk carrier Cb=0.7 | 0.70 | 1.0393 | 1.2052 |
| Tanker Cb=0.8 | 0.80 | 1.0276 | 1.3160 |

这是**趋势符号错误**，不是常数没调好：候选里的 $C_b^{-0.9249}$ 指数让形状因子随方型系数单调下降，而 Holtrop (1982) 的完整表达式（$C_b^{-1}$ 隐含在 $(1-C_p)^{-0.604247}$、$(L^3/\nabla)^{0.36486}$ 中）给出随 Cb 显著上升的形状因子。任何重标定常数都救不回来。

### 3.5 发现清单（证据分级）

| ID | 严重度 | 发现 | 前 → 后 |
|---|---|---|---|
| F1 | high | 试点报告存在循环验证（参考值与候选同源）（Circular verification in the pilot report） | REAL (claimed) → WITHHELD (self-consistent only) for the pass claim; the formulas themselves are literature-anchored by A. |
| F2 | critical | 「Holtrop-Mennen 形状因子」并非 Holtrop & Mennen (1982)（"Holtrop-Mennen form factor" is not Holtrop & Mennen (1982)） | REAL (claimed, provenance 'Holtrop 1982') → MOCK (form factor is fabricated, provenance mislabeled) |
| F3 | critical | 候选形状因子的 Cb 趋势符号相反（Cb trend of the pilot form factor has the wrong sign） | - → - |
| F4 | info | 摩擦线本身正确，且已获文献锚定（Friction lines themselves are correct and now literature-anchored） | WITHHELD (circular) → REAL (formula + published anchors agree) |
| F5 | medium | 容差语义（绝对/相对）未在 schema 中钉死（Tolerance semantics undocumented (absolute vs relative)） | - → - |

## 4. 结果图

**图 1** `artifacts/m1391/fig1_friction_lines_crosscheck.png`：（a）三条摩擦线与 Schoenherr 独立线（对数-对数），空心圈为 15 道题库题的独立重算点；（b）Prandtl-Schlichting 与 Schoenherr 的相对偏差随 $Re_L$ 变化，附平台 3 % 目标线与 15 % 不可接受线。

**图 2** `artifacts/m1391/fig2_form_factor_vs_holtrop1982.png`：（a）三艘船型的 1+k：候选值（红）vs Holtrop 1982 完整表达式（蓝，含 Cm/LCB 包络误差棒）；（b）候选相对文献的偏差，分别按 1+k 与 k 给出，附 15 % 判定线——k 的误差是 80 % 量级。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

- **循环验证是最难自察的陷阱**：deviation 恰为 0.0 看起来像「完美」，实际是「同源」。判据应该是「参考值能否在候选代码之外被独立复现」，而不是「偏差有多小」。
- **容差语义未定**：题库把容差存成裸数字（Cf: 0.001/0.005，1+k: 0.01），而 `verifier.py` 用 `dev=|a-e|/|e| <= tol` 当**相对**容差判。按绝对值读就是 20 %~300 % 的带宽，按相对读才是 0.1 %~1 %。试点报告文字（0.1 %）只与相对读法自洽；这一歧义必须在 schema 里钉死，否则扩大题库时会静默放大带宽（发现 F5）。
- **完整 Holtrop 需要 Cm/LCB，题库没存**：本复核用 $C_m$=0.98、LCB=-1.5 %L 作基准，并给 $C_m\in[0.97,0.995]$、LCB$\in[-3,0]$%L 的包络。三艘船的候选值都落在包络之外，所以结论不依赖这两个假设的具体取值——但如果将来要在 15 % 边界附近做判决，必须先把 Cm/LCB 记进题库。
- **本复核不覆盖**：兴波阻力、Holtrop 附体/空气阻力项、粗糙度补偿（$\Delta C_F$）、以及真船相关因子 $(1+k)$ 与推进效率的耦合；这些仍是未被文献锚定的验证欠账。
- **容量纪律**：本机 load15 ≈ 118（nproc=128），故本单选纯解析核验（无网格/无迭代场解），全量审计 <2 s，未起重算力进程。

## 6. 误差判定（逐项，显式回答「是否 <3 %」）

| 关键量 | 基准 | 偏差 | <3 % ? | <15 % ? |
|---|---|---|---|---|
| 12 道摩擦题 Cf（ITTC-57 / Blasius / Prandtl-Schlichting） | 独立文献公式 + 已发表锚点 | max 0.000 % | 是 | 是 |
| 形状因子 1+k（Bulk carrier Cb=0.7） | Holtrop & Mennen (1982) | 13.8 % | 否 | 是 |
| 形状因子 k（Bulk carrier Cb=0.7） | Holtrop & Mennen (1982) | 80.9 % | 否 | 否 |
| 形状因子 1+k（Series 60 Cb=0.6） | Holtrop & Mennen (1982) | 9.7 % | 否 | 是 |
| 形状因子 k（Series 60 Cb=0.6） | Holtrop & Mennen (1982) | 67.3 % | 否 | 否 |
| 形状因子 1+k（Tanker Cb=0.8） | Holtrop & Mennen (1982) | 21.9 % | 否 | 否 |
| 形状因子 k（Tanker Cb=0.8） | Holtrop & Mennen (1982) | 91.3 % | 否 | 否 |

**显式结论**：
- 摩擦阻力系数（12 题）：**是，<3 %**（实际为数值恒等；且参考实现 10/10 命中已发表锚点）→ 文献向验证通过，候选的 ITTC-57 / Blasius / Prandtl-Schlichting 实现可判 REAL。
- 形状因子 1+k（3 题）：**否**，误差 9.7 %–21.9 %；其中 1/3 已超出平台 15 % 不可接受线。
- 形状因子 k（3 题）：**否**，误差 67.3 %–91.3 %——k 是被 $(1+k)$ 直接乘进总阻力的物理量，80 % 量级的偏差不可用。

据此，mission 537 的 `overall_verdict = pass` 应下调为：「12 道摩擦题 pass（现已文献锚定）；3 道形状因子题 FAIL_LITERATURE」。证据等级相应改为：ITTC-57/Blasius/PS = REAL；Holtrop-Mennen 形状因子 = MOCK（来源标注误挂 Holtrop 1982）。

## 7. 建议（下一单可直接执行的修复）

1. Replace the pilot form factor with the full Holtrop & Mennen (1982) expression from tools/verifier/reference_formulas.py (do not keep the 0.1 fudge).
2. Make the reference path independent by construction: generate expected values only from reference_formulas.py, never from candidate code, and require |bank_expected - reference| < 1e-12 as a commit gate (tests/test_literature_crosscheck.py enforces this).
3. Store tolerances as explicit relative fractions with a schema field ('tolerance_kind': 'relative') or as {value, kind} objects.
4. Gate the question bank on published anchors (PUBLISHED_ANCHORS) so a reference implementation cannot drift silently.
5. Downgrade the mission-537 pilot verdict from 'pass' to 'pass for the 12 friction questions, FAIL for the 3 form-factor questions'.

---

复现命令：

```bash
python3 tools/verifier/audit_literature_crosscheck.py      # 生成 artifacts/m1391/*.json + png
python3 tools/verifier/render_crosscheck_report.py         # 由工件渲染本文件
python3 -m pytest tools/verifier/tests/test_literature_crosscheck.py -q
```
