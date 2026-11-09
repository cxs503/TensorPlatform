# P143W2-N04 增补 · `fwer_controlled` 从恒真字段到可证伪保证

> 任务 m8724（PR#870 管到底第 3 轮）· 分支 `agent/m8724`
> 改动面：`tools/abm/six_dim_power.py`（缺陷修复 + 新增 A/A 实证函数）
> `tests/abm/test_six_dim_power.py`（+19 条）· `runs/m8724/fwer_control_probe.py`（红/绿基线探针）
> 复现：`python runs/m8724/fwer_control_probe.py`（默认基线 `origin/main`=修复前）→ `artifacts/m8724/fwer_control.json` + 图

## 1. 缺陷（N05 登记的"已知未修"项，本轮修掉）

`permutation_test_six_dim` 归来的 `fwer_controlled` 原实现：

```python
fwer_ok = all(
    (r["adjusted_p"] is None or r["adjusted_p"] >= alpha or r["significant"])
    for r in results
)
```

而 `significant` 的定义就是 `adjusted_p < alpha`，于是析取式恒真：
**`fwer_controlled` 在任何配置下都是 `True`** —— 一个不可证伪的装饰字段。
它在 `correction="none"`（六维各自按 5% 判）时仍然宣称"族系错误率受控"，
而此时的族系上界实为 `1-(1-alpha)^6 = 0.2649`，是声明值的 5.3 倍。

探针实测（同一份 A/A 零效应数据，n=8 对，n_perm=299，`correction` 三选一）：

| correction | 修复前 `fwer_controlled` | 修复后 | 修复后 `fwer_bound` |
|---|---|---|---|
| `none` | **True**（虚假保证） | **False** | 0.2649 |
| `bonferroni` | True | True | 0.0500 |
| `bonferroni_holm` | True | True | 0.0500 |

修复前该字段的取值集合只有一个元素（`baseline_field_constant=True`），
即"无论怎么配置都说同一句话"；修复后随配置变化（`head_field_varies=True`）。

## 2. 修复

新增 `fwer_guarantee(correction, alpha, n_tests)`：把"保证"写成有内容的四段事实。

| 情形 | `controlled` | `fwer_bound` | `alpha_per_comparison` |
|---|---|---|---|
| `m ≤ 1`（只有一个检验） | True | `alpha` | `alpha` |
| `correction ∈ {bonferroni, bonferroni_holm}` | True | `alpha` | `alpha/m`（Holm 的首个阈值） |
| `correction="none"` 且 `m ≥ 2` | **False** | `1-(1-alpha)^m` | `alpha` |

`permutation_test_six_dim` 的返回值相应补三个可审计字段：
`fwer`（上面那份明细）、`family_size`（**实际做了检验**的维数）、`n_untested`
（样本不足未入族系的维数——长度 <2 的维不构成多重性，把它算进去会虚高族系大小）。

关键设计取舍：**族系大小按"做了检验的维数"计，不按 `len(DIMENSIONS)` 计**。
否则 6 维里只有 1 维有数据时也会被判"多重性未受控"，那是把缺数据误报成缺陷。

## 3. 实证：保证不是空话（新增 `fwer_aa_sim`）

`fwer_aa_sim` 用**同一份零效应族数据**跑遍三种校正方法（同一 p 值矩阵，只换判决阈值），
因此方法之间是配对可比的。每族"≥1 维被拒"记一次族系误报。

实测（n_families=200，n_pairs=12，n_perm=199，α=0.05，seed=0）：

| 族系大小 m | 未校正实测 FWER | 未校正解析界 `1-(1-α)^m` | Holm 实测 FWER | Holm 声明上界 |
|---|---|---|---|---|
| 2 | 0.0700 | 0.0975 | 0.0450 | 0.0500 |
| 4 | 0.1500 | 0.1855 | 0.0250 | 0.0500 |
| 6 | 0.1950 | 0.2649 | 0.0100 | 0.0500 |

读法（三条都成立才算修复站得住）：

1. 未校正实测 FWER 随 m 单调上升，且恒在解析界之内 —— 界是保守的真实上界；
2. m=4/6 时未校正实测 FWER 已是 α 的 3–4 倍 —— "不校正即受控"被实测推翻；
3. Holm 实测 FWER 在所有 m 上 ≤ α=0.05 —— `controlled=True` 有实证支撑。

图：`artifacts/m8724/fwer_vs_family_size.png`（实测点带 95% Clopper-Pearson 区间 +
解析界曲线 + α 水平线）。

## 4. 判读纪律（写进函数 docstring，防下一轮误用）

`within_bound` 用 Clopper-Pearson **上界**对比保证上界，是强判据；
校正方法的真误报率通常只有 ~2%，n=160 族时其 95% 上界仍可能 > 5% ——
此时 `within_bound=False` 是**样本量不足**，不是校正失效。
报告里必须如实写，不得拿点估计（`point_within_bound`）冒充强判据。
本仓库的这轮修复只声明到「点估计 ≤ α 且区间上界 ≤ 2α」这一档。

## 5. 验证

```bash
python -m pytest tests/abm/ -q                        # 108 passed（原 89 + 新增 19）
python runs/m8724/fwer_control_probe.py               # 红/绿 + 实测表 + 图（约 3.5 s）
```

新增测试 19 条分三组：

- `TestFwerGuarantee`（8 条）：单检验/零族系/未校正/两种校正/界单调/非法输入；
- `TestFwerControlledFieldIsFalsifiable`（5 条）：未校正族系 → False、校正族系 → True、
  **字段随配置变化**（恒真字段在这一条上直接失败）、族系只计已检验维；
- `TestFwerEmpiricalAA`（6 条）：未校正实测超 α、Holm 点在界内、两者相差 3 倍以上、
  三方法共用一份零数据、非法方法名报错、跨调用确定性。

## 6. 局限（未解决的部分，如实登记）

- Holm 在 `n_families=160` 档的**强判据**（区间上界 ≤ α）未达到，属样本量问题；
  要把它做实需要 ~600 族以上（本节点未跑，避免测试时长失控）。
- `six_dim_advantage` 走的是 `holm_adjust`（已是族系校正），未受本缺陷影响；
  族判决另用 `min_perm_for_holm`/`n_perm_resolution_ok` 表达"分辨率不足"，语义清晰。
- 本修复只涉及**报出字段的语义**，不改任何 p 值/判决数值：修复前后
  `adjusted_p`、`significant`、`n_significant` 逐位一致（探针 `n_significant` 列可核）。