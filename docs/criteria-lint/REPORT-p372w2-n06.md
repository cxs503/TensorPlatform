# p372w2-n06 · 判据可判定性 lint 与交付语义漂移检测（平台质量门成熟化）

**节点**：`p372w2-n06-criteria-lint-semantic-maturity` ｜ **物理域**：platform/quality-gate ｜ **任务**：mission 10761 ｜ **仓**：TensorPlatform

**一句话**：把「判据能不能被机器裁」做成派发前的确定性静态判决（9 个机器可读原因码 + 四档门），并用 p372 波后 30 单实测交付语义分类器的域外衰减——实测衰减 43.98% 远超 10% 门限，**告警按设计触发，判据 c1 如实 FAIL**。

## 1 案例介绍（背景/对象/参数）

两道平台质量门解决同一个上游问题：**任务书里的判据在派发前是否可被机器裁决**。

| 对象 | 定义 | 规模 |
|---|---|---|
| 判据条目 | 任务书判据块按三形态切分（编号列表 / 分号分隔 / 阈值短语） | 人群抽样 1794 条（300 节点） |
| lint 三要素 | 阈值（可比较数值边界）/ 基准（参照面闭词表）/ 判定记号（比较符/决策动词/判定符号） | 原因码登记表 9 种 |
| 漂移语料 | `campaign_nodes.plan_id > 372` 且 `status='done'` 且 mission 创建晚于 plan 372 | 总体 1111 单 → 标注 30 单 |
| 分类器 | `backend/api/domain/delivery_state.assess_delivery`（三状态交付语义） | 基线与本波各 31 / 22 单 complete |

## 2 怎么做的（方法/流程/工具链）

### 2.1 判据 lint（`tools/criteria_lint.py`，纯函数、无时钟无随机）

1. **切块**：定位「机器验收判据」类区标题 → 截到下一个【…】章节；无标题则整篇兜底。
2. **切条目**：形态优先级 `numbered`（(1)/(a)/①/1./a)）> `semicolon`（；）> `threshold_phrase`（含阈值形状的短句）> `sentence`。
3. **逐条三要素**：阈值（4 条形状规则）/ 基准（闭词表 + 不变式锚 + 命名参照）/ 判定记号（比较符/决策动词/判定符号/无异常断言）；缺任一 → 拦截 + 专属原因码。
4. **结构化判据优先**：节点 `metadata.acceptance_criteria` 存在时直接 lint 结构化条目（渲染成 `metric <= threshold unit` 走同一套元素检查 → 单一实现，不双口径）。
5. **派发前门**：`off / advisory / enforce / block` 四档；`enforce` 只**追加**定界槽位块（无数字、可剥离 → 结构上不自证满足）；任何异常 **fail-open**。

集成点：`ops/taskplane/campaign_dispatcher.py::criteria_lint_gate()`（延迟导入、缺模块不影响派发），调用点在 judgeability 门之后、POST mission 之前；拒绝时节点留 pending 下轮重试。

### 2.2 漂移检测（`tools/delivery_semantics_drift/`）

1. **harvest.py**：总体 SQL（可复算、sha256 钉住）+ 分层确定性抽样（分层代理 = checks 是否含运行结果形状且实现清单非空；排序键 `sha256('p372w2-n06:<mission_id>')`，**无 RNG**）；原始 result 载荷整份落盘（不重建字段 → 无重构偏差）。
2. **labels.json**：预注册 rubric 下的语义标注（读载荷判「是否真的交付完成」，**不复述分类器正则**），逐单写理由；标注冻结在测量之前。
3. **evaluate.py**：读冻结工件 → 跑分类器 → 混淆矩阵/逐类 P-R-F1/macro-F1/bootstrap CI → 衰减判定 → 图（漂移曲线/混淆矩阵/逐类 F1）。同输入重跑除 `git_head` 外逐位一致。

## 3 计算结果（全部由工件程序化注入）

### 3.1 判据 lint 拦截统计

| 口径 | 节点 | 判据条目 | 被拦截条目 | 条目拦截率 | 原因码分布 |
|---|---|---|---|---|---|
| plan 372 节点（目标书原文） | 8 | 33 | 21 | 63.64% | NO_NUMERIC_THRESHOLD=9, NO_REFERENCE_ANCHOR=21, NO_VERDICT_MARK=6 |
| p372 波 2 节点（结构化判据） | 8 | 40 | 26 | 65.00% | NO_NUMERIC_THRESHOLD=12, NO_REFERENCE_ANCHOR=23, NO_VERDICT_MARK=6 |
| plan_id > 372 人群抽样 | 300 | 1794 | 1406 | 78.37% | NO_NUMERIC_THRESHOLD=508, NO_REFERENCE_ANCHOR=1329, NO_VERDICT_MARK=329, VAGUE_QUANTIFIER=7 |

* 原因码可达性：登记 9 个码，合成电池触发 **9/9**（不可达 0 个 → 无装饰品）。
* 人群口径下最缺的是**基准**（1329 条），其次阈值（508 条）、判定记号（329 条）——即「判据写了目标数，但没写对标什么」。
* 同一输入重跑：8/8 节点 `report_sha256` 逐位一致、批次聚合完全相等（确定性）。

### 3.2 交付语义分类器漂移（p372 波后 30 单）

| 变体 | macro-F1 | 准确率 | 衰减（相对基线） | 告警 |
|---|---|---|---|---|
| 冻结基线（n=31+5+24=60） | 0.9226 | 0.9667 | — | — |
| A2 当前版（默认口径） | 0.5168 | 0.6667 | 43.98% | 触发 |
| A3 opt-in（checks 空→回捞 headline + v1.2 中文运行记号） | 0.5897 | 0.9000 | 36.08% | 触发 |

* A2 混淆矩阵：`{"delivered_complete": {"delivered_complete": 16, "delivered_partial": 0, "suspect_undeclared": 6}, "delivered_partial": {"delivered_complete": 0, "delivered_partial": 1, "suspect_undeclared": 4}, "suspect_undeclared": {"delivered_complete": 0, "delivered_partial": 0, "suspect_undeclared": 3}}`
* bootstrap 95% CI（n_boot=5000, seed=20261110）：[0.3197, 0.7382] —— 区间宽说明 30 单里最小类只有 3 单，macro-F1 对单例翻转极敏感。
* 不一致单数 10/30，其中「人判 complete / 机判 suspect」= 6 单，根因是 **schema 漂移**：p372 波后 mission 的 `checks` 字段留空，验证结论迁到 `headline`（且 headline 被截到 180 字），v1.1 词表只认英文 `\d+ passed` 形态。
* A3 修复效果：complete 类 F1 0.84 → 1.00（22/22 全对）、准确率 0.67 → 0.90；但 suspect 类 F1 0.38 → 0.00（3 单被判成 partial）→ **macro-F1 仍不达 10% 门限**，说明只改词表不够，标签契约（『有实现无验证 = partial』）与分类器状态机也要对齐。

### 3.3 判据逐项判定（战役 metadata 五条）

| # | 判据 | 结果 | 数字 | 证据 |
|---|---|---|---|---|
| 1 | 判据三要素缺失拦截率 100% | PASS | gold 集 9/9 缺失条目全部拦截（100%），6 条齐备条目 0 误拦 | `tests/test_criteria_lint.py::test_block_recall_is_100_percent_on_gold_set` |
| 2 | 原因码机器可读且 ≥5 种 | PASS | 登记 9 种、可达 9 种、真实人群已触发 4 种 | `quality/criteria_lint/evidence/reason_code_coverage.json` |
| 3 | macro-F1 衰减 ≤10%（p372 波后 ≥20 单） | **FAIL（honest）** | 30 单实测衰减 43.98% > 10% | `quality/delivery_semantics/evidence/drift/metrics.json::drift.decay_rel` |
| 4 | 漂移检测告警触发正确 | PASS | decay 0.4398 > thr 0.10 → `drift_alert=True`、`verdict=ALERT_RETRAIN`；边界自检（12%/8%/恰好 10%）结论全对 | `metrics.json::criteria.c2_alert_logic_correct` |
| 5 | 同输入同种子 sha256 一致率 100% | PASS | 漂移评测两次运行 `58eb545a832d78cd…` 逐位相同；lint 8/8 节点重跑同 sha | `metrics.json::determinism_sha256`、`lint_plan9181.json` |

**结论**：`all_criteria_met = False` —— 四条 PASS、一条 **honest FAIL**。FAIL 不掩盖：告警已被这条 FAIL 正确触发，且 A3 变体证明修复方向有效但仍不足以回到门限内。

## 4 结果图

![drift](fig1)

*图 1 漂移曲线*：累积 macro-F1 随标注样本量（mission_id 升序）；绿虚线=冻结基线 0.9226，红点线=10% 衰减门限；橙线=A3 opt-in 变体。

![confusion](fig2)

*图 2 混淆矩阵（A2 当前版）*：主对角 16/1/3；错误集中在 `complete→suspect`（6 单，schema 漂移）。

![perclass](fig3)

*图 3 逐类 F1 对比*：基线 / A2 / A3 三组；A3 把 complete 从 0.84 拉到 1.00，但 suspect 类归零。

![lint](fig4)

*图 4 判据 lint 三口径拦截率与原因码分布*。

![codes](fig5)

*图 5 原因码可达性*：9/9 均可被合成电池触发。

## 5 注意点（坑/局限/适用范围）

1. **门会过拦，默认必须 advisory**：人群抽样条目拦截率 78.37%——多数判据确实缺「基准」。故集成默认 `advisory`（只记录 + 事件），`enforce` 只追加槽位，`block` 需显式开。
2. **闭词表的代价**：命名参照（如「(SIMMAN 带内)」）靠 `REF.named_ref`（括号内专名）兜，更冷门的域内参照仍会被判缺基准 → 这是 precision 上限，修法是 `--extra-terms` 注入域词（域词不进代码）。
3. **CJK 单位坑**：Python 的 `\w` 含 CJK，「小于2度」的 `2` 会被 `(?!\w)` 边界判失败 → 数字右边界只能用 `(?![A-Za-z0-9_])`。
4. **注入不能自证满足**：槽位块无数字、定界可剥离，分类前先剥；测试断言「注入前后原因码集合完全相同」。
5. **漂移测量的真值不独立**：标注者只有一个，且 rubric 与分类器共享三个状态名 → 本测量是**衰减下界估计**，不是无偏误差；`suspect` 类只有 3 单，macro-F1 对其极敏感（bootstrap CI [0.3197, 0.7382]）。
6. **headline 被截断到 180 字**：A3 只能看到截断后的文本，因此「回捞」上限受该截断限制；根治应在产侧（要求 checks 字段非空）。

## 6 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 关键量 | 基准 | 实测 | 相对偏差 | <3%？ |
|---|---|---|---|---|
| 判据拦截召回（gold 9 条缺失条目） | 100%（判据定义） | 100%（9/9） | 0.00% | **是** |
| 判据过拦（gold 6 条齐备条目） | 0 条 | 0 条 | 0.00% | **是** |
| lint 确定性（8 节点 report_sha256） | 100% 一致 | 100%（8/8） | 0.00% | **是** |
| 漂移评测确定性（两次运行内容 sha256） | 100% 一致 | 100% | 0.00% | **是** |
| 分类器 macro-F1（p372 波后 30 单） | 冻结基线 0.9226 | 0.5168 | −43.98% | **否（远超 3%，且超 10% 门限）** |
| A3 变体 macro-F1 | 同上 | 0.5897 | −36.08% | **否（改善但未达门限）** |

**误差判定结论**：确定性/拦截类判据逐项偏差 0.00%（<3% 成立）；**交付语义分类器的域外精度不达标**（衰减 43.98%），这是本单的核心 honest-FAIL，已由告警机制捕获，修复路径（A3）只走了一半。

## 7 审计链

| 工件 | 作用 | 钉住 |
|---|---|---|
| `quality/delivery_semantics/evidence/drift/preregistration.json` | 预注册（口径/阈值/停止规则/局限），标注前落盘 | sha256 `db4cb34130be2c9e…` |
| `.../drift/candidates.json` | 总体 + 抽样键（可复算抽样规则） | SQL sha256 `f0c06cbfa7c11ca7…` |
| `.../drift/corpus_raw.json` | 30 单原始 payload | corpus sha256 `0b61833158d13fe2…`（脚本启动即校验） |
| `.../drift/labels.json` | 人工标注 + 逐单理由 | sha256 `ec1b39aae2a9f387…` |
| `.../drift/metrics.json` | 全部被引用数字 | `determinism_sha256` 双跑一致 |
| `quality/criteria_lint/evidence/lint_{plan372,plan9181,population300}.json` | 拦截统计（batch lint 输出） | 由脚本产出 |
| `quality/criteria_lint/evidence/reason_code_coverage.json` | 原因码可达性 | 合成电池 9/9 |
| `tools/criteria_lint.py` | lint + 派发前门 | `--code-probe` 自检 |
| `ops/taskplane/campaign_dispatcher.py::criteria_lint_gate` | 派发管线集成（fail-open） | `tests/test_criteria_lint_dispatch_gate.py`（8 例） |
| `backend/api/domain/delivery_state.py` | A3 opt-in 变体（默认路径逐位不变） | 既有 57 例冻结验收测试全绿 |

测试：`tests/test_criteria_lint.py`(35) + `tests/test_criteria_lint_dispatch_gate.py`(8) + `tests/test_delivery_semantics_drift.py`(13) + 既有 `backend/tests/test_delivery_state.py` + `test_delivery_semantics_eval.py`(57) 全绿。

## 8 未做与下一步

1. **分类器重训未完成**：A3 只做词表+回捞，macro-F1 仍未进 10% 门限；下一步需对齐「有实现无验证」的标签契约（partial vs suspect）并扩样至 ≥100 单。
2. `block` 模式**未在生产派发中开启**（当前 `advisory`）：需先补一轮判据回填，否则会大面积拒发（人群拦截率 78%+）。
3. 漂移检测当前是**离线批次**（工具 + 预注册），尚未做成按波次自动跑的定时任务；机制化入口=`tools/delivery_semantics_drift/evaluate.py`。

