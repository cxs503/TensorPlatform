# 验证仪表自身计量：Holtrop-Mennen 参考值循环验证欠账清偿

> 本页由 `tools/verifier/gen_reference_integrity_figures.py` 程序化生成，所有数字读自 `artifacts/verifier_reference_integrity/`，无手抄。

## 1. 案例与背景

`tools/verifier/` 是平台的独立复核仪表：题库持有 hold-out 参考值，候选实现（worker 侧）只能拿到规格、拿不到答案。平台口径明确规定「自洽测试不计入」复核通过率——因为参考值若由被测代码同源产生，复核就是循环论证。

审计发现：题库 15 题中，12 题（ITTC-1957 / Blasius / Prandtl-Schlichting）的标准值确由已发表闭式给出；但 3 题 Holtrop-Mennen 形体系数题的标准值由生成器私有表达式合成，而 pilot 候选实现的正是同一表达式 —— **循环验证**。该私有表达式为

```
1+k = 0.93 + 0.1 * 0.487118 * Cb^(-0.9249) * (L/B)^0.15 * (B/T)^0.2
```

对照 Holtrop & Mennen (1982) 原文，它缺少 run 长度项 `(L/L_R)`、体积项 `(L^3/∇)`、棱形系数项 `(1-Cp)` 与艉形系数 `c14`，且把 `(B/L)^1.06806` 写成 `(L/B)^0.15` —— 函数形式不同，指数符号相反。

## 2. 怎么做

1. 新增独立参考实现 `tools/verifier/reference/holtrop_mennen_1982.py`，按原文逐项转写，并提供**两条代数独立的装配路径**（链式乘法 / 对数空间求和）互相校验。
2. 新增判据模块 `tools/verifier/reference/integrity.py`：C1 表达式指纹、C2 物理单调性、C3 量级窗口、C4 趋势锚、C5 独立重算。
3. 新增 `tools/verifier/reverify_question_bank.py`：审计 → 原位改写不可重算的题（保留 id/created_at，version+1，重算 commit_hash）→ 复审，输出前后对照工件。
4. 新增 `tools/verifier/tests/test_reference_integrity.py`：参考实现性质测试 + 判据判别力测试 + **故障注入矩阵**（证明仪表能抓住 MOCK）。
5. 同步修正 e2e pilot 的候选实现，避免测试本身继续使用退役表达式。

## 3. 计算结果

### 3.1 参考值迁移（3 题）

| 题 | 1+k（旧，循环） | 1+k（新，发表式） | 相对变化 | version |
|---|---|---|---|---|
| Holtrop-Mennen form factor: Bulk carrier Cb=0.7 | 1.03928 | 1.19845 | +15.3% | 1 → 2 |
| Holtrop-Mennen form factor: Series 60 Cb=0.6 | 1.05474 | 1.16456 | +10.4% | 1 → 2 |
| Holtrop-Mennen form factor: Tanker Cb=0.8 | 1.02759 | 1.29510 | +26.0% | 1 → 2 |

### 3.2 判据前后对照

| 判据 | 迁移前 | 迁移后 |
|---|---|---|
| C5_rederivation | FAIL | — |
| C5_rederivation | FAIL | — |
| C5_rederivation | FAIL | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C5_rederivation | PASS | — |
| C1_expression_fingerprint | FAIL | — |
| C2_monotonicity | FAIL | — |
| C3_magnitude_window | FAIL | — |
| C4_trend_anchor | FAIL | — |

迁移前失败判据：C5_rederivation, C5_rederivation, C5_rederivation, C1_expression_fingerprint, C2_monotonicity, C3_magnitude_window, C4_trend_anchor；迁移后失败判据：无。 12 题闭式题重算相对偏差最大 1.0e-17（门限 1e-9）。

### 3.3 故障注入（仪表判别力）

| 候选 | 期望 | 实测判决 | 通过/失败题数 |
|---|---|---|---|
| honest (published formula) | pass | pass | 15/0 |
| 0.2% bias (inside 1% band) | pass | pass | 15/0 |
| retired private expression | fail | fail | 12/3 |
| constant MOCK 1.10 | fail | fail | 12/3 |
| 5% bias | fail | fail | 12/3 |
| 15% bias | fail | fail | 12/3 |
| NaN | fail | fail | 12/3 |
| negated | fail | fail | 12/3 |

检出率 100%，特异度 100%。

### 3.4 趋势锚判别裕度

| C_B | 发表式 k | 退役式 k | Watanabe 锚 k | 发表式偏差比 | 退役式偏差比 |
|---|---|---|---|---|---|
| 0.6 | 0.1646 | 0.0547 | 0.2506 | 0.343 | 0.782 |
| 0.7 | 0.1984 | 0.0393 | 0.2905 | 0.317 | 0.865 |
| 0.8 | 0.2951 | 0.0276 | 0.4293 | 0.313 | 0.936 |

门限取相对值 0.5：发表式最大 0.343，退役式最小 0.782，分离裕度 2.28 倍。

## 4. 结果图

![reference integrity](artifacts/verifier_reference_integrity/figures/reference_integrity.png)

(a) 形体系数随丰满度变化：发表式单调上升（k=0.16→0.30），退役式单调下降（k=0.055→0.028），趋势符号相反；(b) 15 题全部可按发表式重算，偏差低于 1e-17（闭式精确），门限 1e-9；(c) 故障注入矩阵，含退役表达式候选在内全部被拒。

## 5. 注意点与局限

* **无独立数值表锚定**：离线环境无法引用 Holtrop 论文的试验表值，故形体系数的*精度* 对文献只做量级/趋势锚定，判据 C4 的 band 是为判别力而非精度标定的（发表式 31–34%，退役式 78–94%）。文献表值锚定标 **WITHHELD**。
* `Cm`、`lcb`、`C_stern` 在原文中是输入量而非回归量，本轮已显式写入题目规格，取值按系列代表值（0.98/0.985/0.99，lcb=1%L 正向），属声明性假设。
* 判据 C2/C3/C4 只对同族船（固定 L/B、B/T 带）成立；跨族比较需扩展矩阵。
* 未改动 verifier 的比较语义（相对偏差 + 逐题 tolerance）；本轮只清偿参考值来源，比较层已知弱点（零基准退化为绝对偏差、bool 与数值混比）留待后续单。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 12 题闭式参考值 | 发表闭式独立重算 | ≤ 1.0e-17 | 是 |
| 3 题形体系数参考值 | Holtrop-Mennen 1982 发表式 | ≤ 34.3% (相对锚) | 是（对发表式精确；对文献表值 WITHHELD） |
| 退役表达式 vs 发表式 | — | 9.4%–20.7% | 否（故退役） |
| 仪表检出率 | 故障注入矩阵 | 100% | 是 |

结论：参考值来源已从循环改为独立；仪表对错误实现的检出率 100%、对 0.2% 级微偏仍判通过（灵敏度合理）。形体系数对**实验**的精度未在本轮判定，原因见 §5 —— 无离线可引用的数值表，标注 WITHHELD 而非含糊通过。
