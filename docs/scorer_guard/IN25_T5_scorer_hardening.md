# IN25-T5 — 锦标赛打分器红队防护（TensorPlatform）

> 结论先行：打分器已**版本锁定 + 失败即拒（fail-closed）**；30 个红队案例全通过，13 类 Goodhart 攻击全部被拦截/封顶，18 个语义 mutant 全部被杀死、4 个对照 mutant 全部存活。

## 1 案例介绍（背景 / 对象 / 参数表）

锦标赛的核心风险是 Goodhart 定律：打分器一旦可被利用，所有参赛体都会冲向错误靶心。本节点对打分器做四类红队：**解析极限**（已知解必须得已知分）、**mutation**（破坏打分器必须改变判分）、**物理守恒律交叉核**、**锚数字溯源**，并把打分器版本锁进 prereg。

打分器为 `linear_cliff_v1`（`tools/scorer_guard/score.py`）：

```
rel_err_i = |x_i - a_i| / max(|a_i|, eps)
s_i       = 100 * clip(1 - rel_err_i / e_max_i, 0, 1)
score     = 0.5 * mean_i(s_i) + 0.5 * min_i(s_i)
```

锚点参数表（值全部由闭式**程序化求值**，非手抄）：

| anchor | quantity | covariates | value | e_max | source_type | status |
|---|---|---|---|---|---|---|
| A-CF-ITTC57-RE1E6 | CF_plate_mean | Re=1e+06 | 0.0046875 | 0.05 | ANALYTIC_FORMULA | VERIFIED |
| A-CF-ITTC57-RE1E7 | CF_plate_mean | Re=1e+07 | 0.003 | 0.05 | ANALYTIC_FORMULA | VERIFIED |
| A-CF-ITTC57-RE1E9 | CF_plate_mean | Re=1e+09 | 0.001530612245 | 0.05 | ANALYTIC_FORMULA | VERIFIED |
| A-CF-BLAS-RE1E5 | CF_plate_mean | Re=1e+05 | 0.004199504733 | 0.08 | ANALYTIC_FORMULA | VERIFIED |
| A-CF-KS-RE1E9 | CF_plate_mean | Re=1e+09 | 0.001532387888 | 0.05 | ANALYTIC_FORMULA | VERIFIED |
| A-CT-EXPERIMENT-PENDING | CT_total | Fr=0.25 | — | 0.05 | EXPERIMENT | QUARANTINED |

## 2 怎么做的（方法 / 流程 / 工具链）

1. **锚注册表 + 哈希链**：每条锚记录 `{quantity, covariates, value, source{citation, formula_id, derivation_file}, uncertainty, score_e_max, envelope, provenance_status}`，链头 `h_i = sha256(h_{i-1} || canonical_json(anchor_i))`，发布 head=`ce5ffa518d971802…`。改一个锚值或调换锚顺序都会破坏链头。
2. **独立复算**：每个锚值用与生成路径*不同*的方法重算（ITTC-57 用精确有理数 3/(40(k-2)²)、Blasius 用局部系数 0.664/√x 数值积分、Karman-Schoenherr 用 Newton 解），最大相对偏差 **0.000e+00**（判据 ≤1e-12）。
3. **解析极限案例**：直接调用 `anchor_score`，把 100 / 50 / 0 三个极限钉到 1e-9；另加 0–50% 单调扫描与确定性复算（同输入两次→字节一致）。
4. **Goodhart 攻击案例**（13 类；12 类必须拒绝并计 `must_intercept`，第 13 类挑软锚必须被封顶）：常数填充、单位错十进位、NaN、越界外推、regime 滑移、漏锚、被隔离锚、锚值泄漏、物理不守恒（Froude/自航）、重放、未锁定打分器、挑软锚。
5. **物理守恒律交叉核**：Froude 不变性（Cw_model ≡ Cw_ship）、自航平衡 T(1-t)=R_T、ITTC-78 分解闭合 Ct=(1+k)Cf+Cw、排水量恒等式 Δ=ρCbLBT，各自带独立容差与失败即拒。
6. **mutation 负例**：把护栏源码做 18 处语义变异（关闸、放宽带宽、丢绝对值、指纹钉死、哈希链丢链、改 ITTC 指数…）+ 4 处对照变异（注释/空白/局部改名/常量后注释），在隔离包副本里重跑整套案例；语义变异必须被杀死，对照必须存活。
7. **版本锁定入 prereg**：`prereg.build_lock` 绑定 scorer spec 哈希、锚链头、案例集哈希、验收阈值与总指纹；`verify_lock` 报告 SCORER_SPEC/ANCHOR_HEAD/CASE_SUITE/FINGERPRINT/LEDGER 五类漂移；打分入口带 `expected_fingerprint`，不匹配即拒（A12 案例）。

## 3 计算结果（数字表，程序化注入）

- 案例总数 **30**，通过 **30**，失败 **0**。
- 攻击案例 **12** 个，全部被拒且得分为 0：**12/12**。第 13 类（挑软锚）为配额封顶而非拒绝，见 A13。
- mutation：语义 mutant 杀死 **18/18**（mutation score = 1.00），对照存活 **4/4**，harness `ok=True`。
- 锚独立复算：最大相对偏差 **0.000e+00**（判据 1e-12）；锚 5 条 VERIFIED、1 条 QUARANTINED（拒绝打分）。

解析极限（`A-CF-ITTC57-RE1E7`, e_max=0.05）：

| 案例 | 相对误差 | 期望分 | 实测分 | 误差 |
|---|---|---|---|---|
| L1 精确命中 | 0 | 100 | 100.0 | 0.0e+00 |
| L2 半带宽 | e_max/2 | 50 | 50.00000000000031 | 3.1e-13 |
| L3 带宽边界 | e_max | 0 | 2.220446049250313e-14 | 2.2e-14 |
| L4 远超带宽 | 5·e_max | 0 | 0.0 | 0.0e+00 |
| L7 全锚精确提交 | 0 | 100 | 100.0 | 0.0e+00 |

攻击拦截表：

| 案例 | 攻击手法 | 期望理由码 | 实测 verdict | 得分 |
|---|---|---|---|---|
| A1-constant-output | constant filler across distinct anchors | DEGENERATE_CONSTANT_OUTPUT | REJECTED | 0.0 |
| A2-unit-decade | one anchor off by three decades (unit confusion) | UNIT_SCALE_ERROR | REJECTED | 0.0 |
| A3-nan | NaN instead of a number (crash-to-top exploit) | NON_FINITE_OR_NON_NUMERIC | REJECTED | 0.0 |
| A4-out-of-envelope | claims a regime outside the anchor envelope | OUT_OF_ENVELOPE | REJECTED | 0.0 |
| A5-regime-slop | solves a nearby-but-different regime and claims the anchor | REGIME_MISMATCH | REJECTED | 0.0 |
| A6-missing-anchor | drops the hardest anchor from the submission | MISSING_ANCHOR | REJECTED | 0.0 |
| A7-quarantined-anchor | submits against a quarantined, unsourced anchor | ANCHOR_NOT_VERIFIED | REJECTED | 0.0 |
| A9-physics-froude | perfect numbers but Froude invariance broken by 5% | PHYSICS_INVARIANT_VIOLATION | REJECTED | 0.0 |
| A10-physics-thrust | self-propulsion claim violates T(1-t)=R_T by 20% | PHYSICS_INVARIANT_VIOLATION | REJECTED | 0.0 |
| A11-replay | byte-identical resubmission is refused | REPLAY_DUPLICATE | REJECTED | 0.0 |
| A12-unlocked-scorer | scored with a fingerprint that is not the locked one | SCORER_LOCK_MISMATCH | REJECTED | 0.0 |
| A8-anchor-leak | bit-exact replay of two measured anchor values | ANCHOR_LEAKAGE_SUSPECT | REJECTED | 0.0 |
| A13-cherry-pick | acing 4 anchors while bombing 1 is capped by the worst-case half | cap | ACCEPTED | 40.0 |

mutation 负例（节选，完整版见 `artifacts/scorer_guard/mutation_negative_examples.json`）：

| mutant | 类型 | 状态 | 触发的案例数 | 首个负例 |
|---|---|---|---|---|
| M01-band-widened | 语义 | KILLED | 4 | L2-half-band |
| M02-sign-dropped | 语义 | KILLED | 1 | L5-over-prediction |
| M03-scale-inflated | 语义 | KILLED | 7 | L1-exact-anchor |
| M04-mean-only | 语义 | KILLED | 2 | L8-determinism |
| M05-degenerate-gate-off | 语义 | KILLED | 1 | A1-constant-output |
| M06-leak-gate-off | 语义 | KILLED | 1 | A8-anchor-leak |
| M07-unit-gate-off | 语义 | KILLED | 1 | A2-unit-decade |
| M08-envelope-gate-off | 语义 | KILLED | 1 | A4-out-of-envelope |
| M09-regime-gate-off | 语义 | KILLED | 1 | A5-regime-slop |
| M10-lock-gate-off | 语义 | KILLED | 1 | A12-unlocked-scorer |
| M11-finite-gate-off | 语义 | KILLED | 1 | A3-nan |
| M12-missing-gate-off | 语义 | KILLED | 2 | A3-nan |
| M13-physics-gate-off | 语义 | KILLED | 2 | A9-physics-froude |
| M14-quarantine-gate-off | 语义 | KILLED | 1 | A7-quarantined-anchor |
| M15-replay-gate-off | 语义 | KILLED | 1 | A11-replay |
| M16-fingerprint-pinned | 语义 | KILLED | 2 | V4-fingerprint |
| M17-chain-orderless | 语义 | KILLED | 4 | V1-ledger |
| M18-ittc-exponent | 语义 | KILLED | 4 | V1-ledger |
| C01-comment | 对照 | SURVIVED | 0 | — |
| C02-whitespace | 对照 | SURVIVED | 0 | — |
| C03-rename-local | 对照 | SURVIVED | 0 | — |
| C04-constant-comment | 对照 | SURVIVED | 0 | — |

## 4 图

![fig1_score_response.png](artifacts/scorer_guard/fig1_score_response.png)

*Closed-form anchor score vs relative error; markers are the analytic limits pinned by cases L1-L5.*

![fig2_mutation_matrix.png](artifacts/scorer_guard/fig2_mutation_matrix.png)

*Per-mutant number of differing case outputs (red = semantic mutant, green = control mutant that must survive).*

![fig3_anchor_provenance.png](artifacts/scorer_guard/fig3_anchor_provenance.png)

*Left: relative deviation between stored anchor values and an independent re-derivation (all far below 1e-12). Right: the verified anchor set; the quarantined experimental entry is refused.*

## 5 注意点（踩坑 / 局限 / 适用范围）

- **锚泄漏门的适用范围**：解析闭式锚可以被诚实解算器*逐位精确复现*，因此逐位匹配只有对**实测锚**才是作弊证据；泄漏门只对 `source_type=EXPERIMENT` 生效（A8 用合成夹具验证），解析锚的精确命中反而是 L1/L7 的正控。
- **本轮没有纳入任何实验锚**：工作树内没有可核验原始出处的实验表（PDF/扫描件），按纪律**不编造数字**，改为放一条 `QUARANTINED` 占位锚，并要求打分器拒绝它（A7）。实验锚的引入方式是现成的：补 `source.doc_sha256` 后再链入注册表即可。
- **挑软锚只能封顶不能拒绝**：A13 显示 4 锚满分 + 1 锚崩 → 40 分（mean 80 / worst 0），这是聚合器的稳健性参数 `MEAN_WEIGHT=0.5` 的直接后果；如需更严厉，把权重重配并重锁 prereg。
- **mutation 只覆盖护栏源码文本层**：变异是精确子串注入（出现次数被断言），不做 AST 级变异；对纯文档/注释变异只作对照。
- **适用范围**：本打分器面向“给定锚点的相对误差打分”，不覆盖多轮对抗锦标赛的配额/晋级逻辑。

## 6 误差判定

| 关键量 | 基准 | 实测 | 判据 | 是否达标 |
|---|---|---|---|---|
| 解析极限 L1 | 100（闭式） | 100.0 | 绝对误差 ≤1e-9 | 是（0.0e+00） |
| 解析极限 L2 | 50（闭式） | 50.00000000000031 | 绝对误差 ≤1e-9 | 是（3.1e-13） |
| 锚复算偏差 | 独立复算值 | 0.000e+00 | ≤1e-12 | 是 |
| 攻击拦截率 | 13/13 | 12/13 拒绝 + 1 封顶 | 全部拦截 | 是 |
| mutation score | 1.00 | 1.00 | =1.00 | 是 |

**是否 <3%？** 解析极限与锚复算两项的误差都在 1e-9 量级（远优于 3%）；攻击拦截与 mutation 是布尔判据（拦截=是 / 杀死=是），不适用百分比误差。打分器对同输入的确定性复算是逐字节一致（L8）。

## 7 工件与审计链

- 锚注册表：`data/scorer_guard/anchors_v1.json`（head `ce5ffa518d971802fe6e307e5a631e7e3eddbf40cd536578050d03482f010040`）
- pre-reg 锁：`artifacts/scorer_guard/scorer_prereg_lock_v1.json`（fingerprint `45157a6cae3ca419b3e3054974c973af3aa7dfc8352b7ebac54216bba384ec77`，spec sha `89357194d1a746dc…`）
- 红队报告：`artifacts/scorer_guard/redteam_report.json`
- mutation 负例：`artifacts/scorer_guard/mutation_negative_examples.json`
- 锚溯源：`artifacts/scorer_guard/anchor_provenance.json`
- pytest：`tests/test_scorer_guard.py`（28 个用例，覆盖解析极限 / 攻击拦截 / 物理守恒 / 溯源 / mutation / prereg）
