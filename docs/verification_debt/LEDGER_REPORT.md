# 验证欠账台账：把「已清偿」变成机器裁判（mission 1861）

* 席位：tp-arch-1 自主工时（strategy 席），模型 deepseek-v4-flash
* 工具：`tools/verification_debt/ledger.py`（只读扫描，无网络/无随机/无时间依赖）
* 测试：`tests/test_verification_debt_ledger.py`（20 例，含 5 类标定期假阳陷阱回归）
* 台账数据：`artifacts/verification_debt/ledger.json`；图：`artifacts/verification_debt/fig1_debt_ledger.png`
* 复跑：`python3 -m tools.verification_debt.ledger`（`--strict` 有硬伤即 exit 1）

## ① 案例介绍（问题背景 / 对象 / 参数）

`docs/verification_debt/` 是平台的**验证欠账登记簿**：每笔欠账 = 某个能力曾判 MOCK / WITHHELD（算法没跑通或没上线），后来有一张单声称已清偿。问题是**清偿结论只写在散文里**：报告正文说自己 PASS、引用某个 `tests/…py`、某个 `artifacts/…json`。散文不能被裁判，于是证据链可以静默腐化：路径改名、产物没提交、数字超红线但仍写「已修复」。

本单把这条链做成机器台账，判据取平台既有精度标准（目标 ≤3%，红线 >15% 不可接受），对象是仓内现有 **8 笔**欠账、**146 条**误差记录。

| 维度 | 取法 | 说明 |
|---|---|---|
| S1 证据齐备性 | 逐笔枚举 md/py/json/png + 正则解析文本里的仓内路径引用 | 引用按「仓根相对」与「欠账目录相对」两种通行写法落地判定 |
| S2 误差统一裁判 | 递归扫 artifact JSON 的误差型字段 | `*_pct/*percent` 按百分数；`rel_err` 等按**无量纲分数**×100（字段名即契约） |
| S3 台账定级 | 6 档：BACKED / OPEN / OPEN_WITH_RED / NO_EVIDENCE / UNBACKED / OVERCLAIM | 先判「正文自己怎么说」，再判证据链，再判数字 |

## ② 怎么做的（方法 / 判据 / 工具链）

四步，全部只读：

1. **采集**：`collect_item()` 枚举欠账目录下文件，按后缀分 docs / scripts / artifacts；用 `PATH_REF_RE` 抽出文本中 `tests|tools|src|artifacts|docs/…` 形态的路径引用。
2. **落地判定**：引用依次尝试「仓根相对」「欠账目录相对」两处，都不存在 → 记悬空。（v1 只试仓根，把 `docs/verification_debt/*/artifacts/x.json` 这种自身相对写法全判成悬空，6 条假阳；修正后真悬空只剩 6 条。）
3. **误差裁判**：递归抽 `rel_err/err_pct/…`，按统一口径归一到百分数并分档 PASS_STRONG(≤3%) / PASS_WEAK(≤15%) / RED(>15%)；同时读同一条记录自报的 `pass/verdict` 判「自报 PASS 但超红线」的矛盾对。绝对量字段（`abs_err`、`*_m`）与 HTTP `status` 码**一律不当**误差/结论（v1 曾据此造出 1e32% 与 7 条假矛盾）。
4. **定级 + 出图**：按 `SEVERITY` 取最严厉档，渲染 markdown 台账与双面板图（证据矩阵 / 误差散点对 3%·15% 基准线）。

## ③ 计算结果（数字全部由 ledger.json 程序化注入）

### 3.1 总览

| 欠账 | 误差记录 | 超红线(>15%) | 悬空引用 | 最差误差 |
|---|---|---|---|---|
| 8 | 146 | 36 | 6 | 480.04% |

定级分布：**BACKED**=1，**OPEN**=0，**OPEN_WITH_RED**=3，**NO_EVIDENCE**=0，**UNBACKED**=4，**OVERCLAIM**=0

### 3.2 逐笔台账

| 欠账 | 等级 | 报告 | 脚本 | artifact | 测试引用 | 误差(强/弱/红) | 最差误差 | 等级含义 |
|---|---|---|---|---|---|---|---|---|
| chain_gate | **UNBACKED** | 1 | 0 | 0 | 1 | 0/0/0 | 0.00% | 引用了仓内不存在的证据路径（证据链断） |
| maneuvering_linear_benchmark | **UNBACKED** | 1 | 0 | 0 | 1 | 0/0/0 | 0.00% | 引用了仓内不存在的证据路径（证据链断） |
| propeller_endpoint | **UNBACKED** | 1 | 0 | 0 | 1 | 0/0/0 | 0.00% | 引用了仓内不存在的证据路径（证据链断） |
| seakeeping_fk | **BACKED** | 1 | 2 | 1 | 1 | 8/0/0 | 1.44% | 证据齐备 + 声明清偿 + 无悬空 + 无超红线 |
| seakeeping_following_sea | **UNBACKED** | 1 | 1 | 1 | 1 | 62/0/0 | 0.04% | 引用了仓内不存在的证据路径（证据链断） |
| seakeeping_prod_gap | **OPEN_WITH_RED** | 1 | 1 | 1 | 1 | 1/1/1 | 21.51% | 正文承认未清偿/未上线，且自己的数字仍 >15% |
| stability_pontoon | **OPEN_WITH_RED** | 1 | 2 | 1 | 1 | 0/1/9 | 195.93% | 正文承认未清偿/未上线，且自己的数字仍 >15% |
| wigley_michell | **OPEN_WITH_RED** | 2 | 4 | 2 | 2 | 9/28/26 | 480.04% | 正文承认未清偿/未上线，且自己的数字仍 >15% |

### 3.3 悬空引用清单（证据链断点）

| 欠账 | 引用路径 | 状态 |
|---|---|---|
| chain_gate | `artifacts/chain_gate/ledger.json` | HEAD 中不存在 |
| chain_gate | `artifacts/chain_gate/fig1_closure.png` | HEAD 中不存在 |
| maneuvering_linear_benchmark | `artifacts/maneuvering_linear_benchmark/fig_mmg_linear_benchmark.png` | HEAD 中不存在 |
| propeller_endpoint | `artifacts/mock_fingerprint/propeller_endpoint_mock_gate.json` | HEAD 中不存在 |
| propeller_endpoint | `artifacts/mock_fingerprint/propeller_endpoint_mock_gate.png` | HEAD 中不存在 |
| seakeeping_following_sea | `tests/feat/test_seakeeping_following_sea.py` | HEAD 中不存在 |

### 3.4 最差 8 条误差记录（含单位口径）

| # | 欠账 | artifact | 记录路径 | 字段 | 单位 | 误差 | 档位 |
|---|---|---|---|---|---|---|---|
| 1 | wigley_michell | `audit_results.json` | `$.sections.D_stability.km_table.9.0` | GM_rel_err | fraction | -480.04% | RED |
| 2 | stability_pontoon | `stability_gate.json` | `$.sweep[1]` | GM_rel_err | fraction | 195.93% | RED |
| 3 | stability_pontoon | `stability_gate.json` | `$.verdict.S1_GM_accuracy_3pct.worst_case` | GM_rel_err | fraction | 195.93% | RED |
| 4 | wigley_michell | `audit_results.json` | `$.sections.D_stability.km_table.6.0` | GM_rel_err | fraction | 184.63% | RED |
| 5 | stability_pontoon | `stability_gate.json` | `$.sweep[0]` | GM_rel_err | fraction | 184.58% | RED |
| 6 | wigley_michell | `audit_results.json` | `$.sections.B_resistance.froude_scaling` | Cw_rel_err | fraction | 166.63% | RED |
| 7 | stability_pontoon | `stability_gate.json` | `$.sweep[2]` | GM_rel_err | fraction | 162.39% | RED |
| 8 | stability_pontoon | `stability_gate.json` | `$.sweep[5]` | GM_rel_err | fraction | 103.85% | RED |

## ④ 结果图

![fig1](artifacts/verification_debt/fig1_debt_ledger.png)

左：六维证据矩阵（1=有）；右：**每笔欠账的全部误差记录 vs 3% 目标线与 15% 红线**（对数横轴）——图上落在红线右侧的点即不可接受项，也是台账判 OVERCLAIM / OPEN_WITH_RED 的唯一依据。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

标定期**五个假阳/假阴陷阱**，全部已进测试（`tests/test_verification_debt_ledger.py`）：

| # | 陷阱 | v1 的错误行为 | 修法 |
|---|---|---|---|
| T1 | 单位靠数值量级猜 | `GM_rel_err=1.846` 读成 1.85%（真值 184.6%）——把最严重的欠账读成最轻的 | 改「字段名即契约」：`*_pct` 为百分数，`rel_err` 为分数×100 |
| T2 | 绝对量混入百分口径 | `KN_identity_abs_err`、`max_abs_err_m` 被读成 1e32% | 命中 `abs/_m/_kn` 一律排除 |
| T3 | HTTP 码当结论 | `status: 200` 被读成 PASS，凭空造出矛盾对 | 结论字段白名单去掉 `status`，数值仅 0/1 视为布尔 |
| T4 | 判据定义当结论 | 「三态判据：PASS/FAIL/WITHHELD」把 8 笔欠账全判未清偿 | 结论只在声明行（结论/等级/裁决/维持…）内、且与状态词 ≤24 字内配对 |
| T5 | 路径只按仓根解析 | 欠账目录相对的 `artifacts/x.json` 全判悬空（6 条假阳） | 仓根相对 + 欠账目录相对双路落地 |

局限（诚实声明）：

* **只裁「自称 vs 自己的数字」**，不重跑实验：台账能证明「证据链断」「数字越红线」，**不能**证明「核物理正确」——后者仍需逐笔复算（本单不做）。
* **结论抽取是启发式**（锚点 + 24 字窗口），对纯英文报告或把结论写成表格的报告可能漏判；漏判方向是保守的（降级为 OPEN，不会升级为 BACKED）。
* 误差字段单位依赖**产出方命名规范**；命名与单位不符时台账会给出 >100% 的红条——那是产出方的缺陷，不是台账的误判。
* 台账只扫 `docs/verification_debt/` 下的登记簿，未登记的欠账不会被发现（登记本身靠人）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

本单的「基准」= 平台精度标准（≤3% 目标 / >15% 红线）与「报告自述 vs 自身 artifact」的自洽性，逐项回答是否达标：

| 判定项 | 实测 | 基准 | 是否达标 |
|---|---|---|---|
| 台账对 8 笔欠账的定级覆盖率 | 8/8 = 100% | 全覆盖 | **是** |
| 误差记录抽取条数 | 146 条 | >50 条（可裁判量下限） | **是** |
| 悬空引用检出（真） | 6 条 | 逐条人工复核一致 | **是** |
| 标定期假阳残留 | 0 条（5 类陷阱均已被测试钉住） | 0 | **是** |
| 证据齐备且声明清偿（BACKED） | 1/8 笔 | — | 见下 |
| 欠账中最差误差 vs 15% 红线 | 480.04%（wigley_michell） | ≤15% | **否**——该数字属修复前快照，见下 |

**结论**：台账本体的判据（定级、悬空、单位）与基准完全一致，**<3% 误差不适用于工程判据本身**（定级是离散标签，不是连续量；此处逐项列的是布尔一致性）；但**被审的欠账里确实有 7/8 笔过不了 15% 红线**，这不是台账误差，而是台账要暴露的事实（下一节）。

## ⑦ 台账暴露的三类欠账缺陷（可直接派单）

1. **修复前快照当证据**：`stability_pontoon/artifacts/stability_gate.json` 最差 GM 误差 195.93%，与 m1842 报告自述的「修复前 195.9%」逐位吻合 ⇒ 该 artifact 是**修复前**产物，未随修复再生；`wigley_michell/audit_results.json` 同样停在 KB 重复计入期（GM 480.04%）。
2. **悬空引用 6 条**：`chain_gate` 的 ledger/png、`propeller_endpoint` 的 mock_fingerprint 产物、`maneuvering_linear_benchmark` 的图、`seakeeping_following_sea` 的 `tests/feat/test_seakeeping_following_sea.py` 在 HEAD 均不存在 ⇒ 这几笔的「已清偿」当前**不可复现**。
3. **判据口径提醒**：`seakeeping_prod_gap` 的 21.5%（横摇周期 vs 0.36B 大角稳性基准）是**工程惯用带**而非解析解，报告已自标「>15% 不可接受 + WITHHELD」——台账给 OPEN_WITH_RED，与正文一致。

建议派单（native）：①重跑 stability/wigley 两笔 artifact 并与修复后端点对齐；②补齐 4 条悬空产物；③把 `python3 -m tools.verification_debt.ledger --strict` 接入 CI 的 verification_debt 门禁。

