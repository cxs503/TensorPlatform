# P315W1-N10 平台治理套件成熟化：工件注册表 × 变异计量 × 判据 gate power × 度量回写门

**节点** p315w1-n10　**仓库** TensorPlatform　**分支** agent/m10864　**运行日** 2026-11-10T20:36:23Z　**参照日** 2026-11-10

**总判定**：`PASS`　（未通过项：无）

> 第二次尝试：首轮产物在本工作树无残留（基线里只有一个被门禁滤除的提交），本轮不重跑旧路径，改为把四项机制做成可复算仪器——所有判据数字由 `run_maturity.py` 写入 JSON，报告从 JSON 程序化注入，禁止手抄。

## ① 案例介绍

本节点把平台治理中三项长期靠人工确认的判断改成机器裁决：

1. **工件注册表**：跨任务工件统一为信封（artifact_type / schema_version / payload / checksum / provenance），下游消费者声明可接受的主版本与最小次版本；「上游产出能否喂下游」= 一次 `validate()`。
2. **变异计量**：AST 级、领域无关的语义变异算子（删语句／条件取反／边界翻转／常数×1e3／索引±1／参数交换／布尔翻转），分片重跑声明的测试命令，得到代码变异杀伤率与 95% 区间；等价变异体（插入 `pass`、死分支）作负控必须存活。
3. **判据 gate power**：对判据工件注入物理腐败（残差放大／锚值扰动／量纲翻转／阈值放宽／比较符翻转／出处替换／评审过期／未注册来源／非有限阈值），gate_power = 检出 / 注入；单列恒真判据与上游不可推导判据。
4. **度量回写门**：KPI 写入前必须过出处守卫（sha256）、表达式重算、派生自洽恒等式、新鲜度 SLO、幂等键与回退检测；账本是 sha256 哈希链，事后可验证。

### 参数表

| 项 | 值 |
|---|---|
| 工件契约语料 | valid=10，cases=96，腐化类=10 |
| 变异目标 | 3 模块 / 2 仓；每目标语义变异体 190–288 |
| 变异分片 | 8 shards/target（`--shard i/8`），合并时核对完整性 |
| 判据语料 | 59 条（`data/criteria/threshold_registry.json`） |
| 判据注入 | 427 次（9 类） |
| 回写新鲜度 SLO | 86400 s |
| 领域词表 | 数据注入 `config/domain_word_denylist.json`（扫描器 `tools/audit/domain_leak_scan.py`） |

## ② 怎么做的

- 新模块：`tools/governance_maturity/{artifact_registry,adapters,mutation_scan,gate_power,writeback_gate,domain_scan,run_maturity,report_writer}.py`；
- 数据注入：`data/governance_maturity/{artifact_schemas,engine_profiles,mutation_targets,writeback_registry}.json`；
- 上游契约全部收敛在 `adapters.py`（平台 metric row / criterion / mission report + 外部引擎 anchor ledger），上游接口漂移只改适配层；
- 变异目标：1 个平台模块（`tools/metric_gate/expr_registry.py`，跑仓库自带 pytest）+ 2 个外部仓模块（TensorBEM `geometry.py`、`series60.py`，按行为契约加载）。外部仓是共享可写检出，测量期间观测到上游改动，故以快照钉桩（sha256 见 `artifacts/governance_maturity/targets/TensorBEM/MANIFEST.json`），账本记录同一 sha256；
- 每个变异体在独立沙箱（`copy2` 独立副本，不用硬链接——硬链接会让变异写穿源文件）运行一次测试命令；超时单列、不计入杀死；变异体清单与渲染分离，分片只渲染自己那部分；
- 分片执行：本机 8 分片并行（同一 `--shard i/8` 协议，可平移集群）。集群递交通道当时排队 >250 作业（递交 `cj594` 19 分钟仍 queued），故本轮分片在本机执行；账本保留分片文件与合并完整性核对，集群复跑只需同一条命令改由调度器派发。
- 结果落 `artifacts/governance_maturity/*.json` 与图 `figures/*.png`，本报告数字由 `run_maturity.py` 注入。

## ③ 计算结果

### (a) 工件注册表：覆盖率与契约校验准确率

- 覆盖 4 个工件类型、4 个模块、2 个仓（TensorFEM, TensorPlatform）；schema 版本 4 个，消费者契约 2 个。
- 语料：valid 10 件（来自真实上游文件/记录），单缺陷腐化 86 件，共 96 例。
- **准确率 1.0000**；误收 0 例，误拒 0 例；
- 生产者实跑：`TensorFEM::benchmarks`（1 件）, `TensorPlatform::tools/criteria_governance`（4 件）, `TensorPlatform::tools/metric_gate`（4 件）, `TensorPlatform::tools/report_pipeline`（1 件）。

| 腐化类 | 注入次数 | 期望 |
|---|---|---|
| `array_item_type` | 1 | 拒绝（单点缺陷） |
| `drop_required` | 10 | 拒绝（单点缺陷） |
| `enum_violation` | 9 | 拒绝（单点缺陷） |
| `extra_field` | 10 | 拒绝（单点缺陷） |
| `malformed_digest` | 10 | 拒绝（单点缺陷） |
| `out_of_range` | 6 | 拒绝（单点缺陷） |
| `payload_tamper` | 10 | 拒绝（单点缺陷） |
| `unknown_version` | 10 | 拒绝（单点缺陷） |
| `unregistered_producer` | 10 | 拒绝（单点缺陷） |
| `wrong_type` | 10 | 拒绝（单点缺陷） |

### (b) 变异杀伤率（分片合并，95% 区间）

| 目标 | 仓 | 模块 | 语义变异体 | 杀死 | 存活 | 超时 | kill rate | Wilson 95% | 负控精度 |
|---|---|---|---|---|---|---|---|---|---|
| `external_geometry_contract` | TensorBEM | `src/tensorbem/geometry.py` | 190 | 155 | 34 | 0 | 0.8201 | [0.759, 0.868] | 1.000 |
| `external_regression_contract` | TensorBEM | `src/tensorbem/series60.py` | 191 | 98 | 80 | 0 | 0.5506 | [0.477, 0.622] | 1.000 |
| `platform_expression_registry` | TensorPlatform | `tools/metric_gate/expr_registry.py` | 288 | 202 | 83 | 3 | 0.7088 | [0.654, 0.758] | 0.978 |

- 合并 kill rate **0.6979**，Wilson 95% [0.6615, 0.7319]，分层 bootstrap 95% [0.6641, 0.7301]（2000 次重采样）。
- 分片完整性：True；未变异基线全通过：True；超时合计 3 个（单列）；基础设施失败 14 个（磁盘满/无法建临时目录，单列 `infra_error`，不计入杀死也不计入分母——否则会把环境故障算成测试能力）。

| 算子 | external_geometry_contract | external_regression_contract | platform_expression_registry |
|---|---|---|---|
| `boundary_swap` | 0.08（1/13） | 0.00（0/12） | 0.12（5/41） |
| `delete_statement` | 0.89（81/91） | 0.65（41/63） | 0.76（123/161） |
| `flip_bool` | 1.00（1/1） | — | — |
| `negate_condition` | 0.88（15/17） | 0.73（8/11） | 0.86（42/49） |
| `scale_constant` | 0.88（35/40） | 0.55（47/85） | 1.00（9/9） |
| `shift_index` | 0.80（8/10） | — | 0.89（17/19） |
| `swap_args` | 0.82（14/17） | 0.29（2/7） | 1.00（6/6） |

**测试迭代前后对比**（同一变异体清单，v0 = 初始契约套件，v1 = 按账本补了边界用例后）：

| 目标 | v0 kill rate | v1 kill rate | Δ | v0 存活 | v1 存活 |
|---|---|---|---|---|---|
| `external_geometry_contract` | 0.8158 | 0.8201 | +0.0043 | 35 | 34 |
| `external_regression_contract` | 0.4869 | 0.5506 | +0.0637 | 98 | 80 |
| `platform_expression_registry` | 0.7088 | 0.7088 | +0.0000 | 83 | 83 |

注：v0 中 `boundary_swap` 在浮点比较上几乎全存活——部分属于**真等价变异**（如 `|x-0.6| < 0.05` 的 0.05 在二进制下不可精确表示，边界点不存在），部分属于测试缺精确边界用例；v1 补了计数/长度/面积下界与插值分支用例，其余仍存活的等价变异应在报告中如实保留，不能通过改阈值洗掉。

**存活变异体热点**（按行号计，取前 5）：

| 目标 | 存活数 | top 行号:次数 | 控制变异被误杀 |
|---|---|---|---|
| `external_geometry_contract` | 34 | L42:4, L70:3, L135:3, L25:2, L62:2 | 无 |
| `external_regression_contract` | 80 | L179:5, L149:5, L88:4, L135:3, L170:3 | L61(insert_pass), L84(dead_branch), L100(insert_pass), L149(dead_branch), L172(insert_pass), L197(dead_branch), L23(insert_pass), L62(dead_branch), L86(insert_pass), L107(dead_branch), L154(insert_pass), L173(dead_branch) |
| `platform_expression_registry` | 83 | L250:6, L184:4, L209:3, L191:3, L226:3 | L204(insert_pass), L212(insert_pass), L246(dead_branch), L279(dead_branch) |

读法：控制变异体（插入 `pass` / 死分支）被误杀通常意味着测试耦合了源码行号/文本；存活密集的行则指向未被测试触达的分支（例如外部模块中一个按相对导入拆分、无法被按路径加载器执行的入口）。两类都应在后续节点修测试，而不是调低阈值。

### (c) 判据 gate power

- 判据 59 条（历史阈值注册表），注入 427 次，检出 427 次 → **gate_power = 1.0000**；
- 干净语料误拒率 0.0000（0 例）；
- **恒真判据清单：0 条**——在非负观测假设下，语料中没有对所有探针都成立的判据；恒真检测器另以合成判据做负控（pytest 覆盖），故 0 条是测量结果而非未实现；
- 上游声明不可推导判据 49 条：本 gate 仍能约束其出处/派生/结论一致性，但源侧自认无法独立重推导，列为数据质量待办；
- 可探测边界（stealth 类，不计入 gate_power）：观测值小幅偏移 [1.5] 且声明一致时，检出 0/59（设计期望 0）——没有独立复算来源时，gate 无法识别「自洽但错误」的数值，这是本仪器的适用边界。

| 腐化类 | 注入 | 检出 | gate power |
|---|---|---|---|
| `residual_amplification` | 59 | 59 | 1.000 |
| `anchor_perturbation` | 7 | 7 | 1.000 |
| `dimension_flip` | 59 | 59 | 1.000 |
| `tolerance_widening` | 7 | 7 | 1.000 |
| `comparator_flip` | 59 | 59 | 1.000 |
| `provenance_swap` | 59 | 59 | 1.000 |
| `stale_dating` | 59 | 59 | 1.000 |
| `unknown_source_type` | 59 | 59 | 1.000 |
| `nonfinite_threshold` | 59 | 59 | 1.000 |

### (d) 等价变异体负控

- 负控变异体 144 个（插入 `pass` / 死分支），存活率（precision）**0.9922**（阈值 0.8）。
- per-target precision：`external_geometry_contract` 1.000，`external_regression_contract` 1.000，`platform_expression_registry` 0.978。

### (e) 度量回写门

| 腐化类 | 注入 | 检出 | 命中码 |
|---|---|---|---|
| `rollback` | 12 | 12 | `rollback_to_historical_value` |
| `regression` | 6 | 6 | `regression_without_new_evidence` |
| `stale` | 5 | 5 | `stale_write` |
| `recompute_mismatch` | 5 | 5 | `recompute_mismatch` |
| `identity_violation` | 5 | 5 | `identity_violation` |
| `forged_provenance` | 5 | 5 | `provenance_malformed` |

- **回退检测率 1.000**；无新证据回归检测率 1.000；
- 幂等：重复写 6 次全部返回 duplicate = 6；账本长度 24 → 24（不变=True）；重载后哈希链校验 True（长度 24）；重放状态 `duplicate`，回执链与首次一致=True；
- 污染战役后合法写入接受 5/5（误拒率 0.000）。

### (f) 零领域语义扫描

- 扫描 21 个文件（机制码 + 测试 + 数据），命中 **0**——`PASS`；
- 扫描器 `tools.audit.domain_leak_scan`（sha256 `sha256:4a007c998667c326…`），词表以数据注入；
- 夹具全部使用合成域名（`synthetic/...`、`ratio_metric`、`run_a`），未引入任何域词条。

### (g) 测试与产物

- `pytest tools/governance_maturity/tests` 收集 **101** 个用例（≥30），其中两个外部契约套件在未设置 `GM_TARGET_MODULE` 时按模块级 skip 处理；
- 产出：`artifacts/governance_maturity/{artifact_contract_ledger,mutation_ledger,gate_power_ledger,writeback_ledger,domain_scan,summary}.json`，变异体清单 `mutants/*.json`，分片账本 `shards/*.json`，图 `figures/*.png`。

## ④ 结果图

![mutation_kill_rate.png](artifacts/governance_maturity/figures/mutation_kill_rate.png)

图 1　变异杀伤率与 Wilson 95% 区间（等价变异体不计入分母）。

![mutation_by_operator.png](artifacts/governance_maturity/figures/mutation_by_operator.png)

图 2　分算子杀伤率：各测试面对不同语法破坏的敏感度。

![gate_power_by_class.png](artifacts/governance_maturity/figures/gate_power_by_class.png)

图 3　判据腐败注入的 gate power（虚线为 0.9 验收线）。

![writeback_detection.png](artifacts/governance_maturity/figures/writeback_detection.png)

图 4　度量回写门各腐化类检出率。

![contract_confusion.png](artifacts/governance_maturity/figures/contract_confusion.png)

图 5　工件契约混淆矩阵（valid + 单缺陷腐化语料）。

## ⑤ 注意点（踩坑 / 局限 / 适用面）

1. **外部仓可变**：TensorBEM 检出在工作树外且被其他任务并发写入，首轮分片因模块 sha256 变化被`ValueError` 拒绝（这是 fail-closed 的预期行为）。改为快照 + MANIFEST 后重跑；账本记录同一 sha256。
2. **超时不是杀死**：变异体运行超时单列统计，不并入 kill rate；否则仪器会虚高。
3. **等价变异体是负控**：插入 `pass` / 死分支属于语义等价改动，必须存活；precision<1 说明测试对无关改动敏感（虚假杀伤），本项目 precision 见 (d)。
4. **gate power 的边界**：只覆盖「工件自洽性」类腐败；对「自洽但数值错误」的观测（stealth 类）本 gate 无独立复算来源时不可检出，已单列披露，不计入 gate_power。
5. **上游判据质量**：语料中相当比例的判据被源侧标记为不可推导，本 gate 只能约束一致性；这批判据进入数据质量待办，不冒充可裁判据。
6. **契约准确率语料是自建单缺陷集**：准确率 100% 只说明检查器对该缺陷集合无漏检/误拒，不构成对任意上游形态的完备性保证；新增上游形态必须先加进语料再造适配器。
7. **归零重跑**：分片结果与变异体清单同源（sha256 一致检查），不同 revision 的清单无法混用。

## ⑥ 误差判定（逐项）

| 关键量 | 实测 | 判据 | 结论 |
|---|---|---|---|
| 契约校验准确率 | 1.0000 | =1.0 | PASS |
| 合并 kill rate | 0.6979 | 报告值 + 95% 区间 | PASS |
| gate_power | 1.0000 | ≥0.9 | PASS |
| 负控 precision | 0.9922 | ≥0.8 | PASS |
| 回退检测率 | 1.0000 | =1.0 | PASS |
| 幂等写不变 | True | True | PASS |
| 领域语义命中 | 0 | =0 | PASS |
| pytest 用例数 | 101 | ≥30 | PASS |
| 基础设施失败单列 | 14 | 不计入 kill rate | 已单列 |

**与基准的误差说明**：本节点是机制/治理任务，没有物理实验基准；上表以「验收判据数值」为基准，逐项给出实测与判定。变异杀伤率本身不是「越接近 1 越好」的物理量，它的正确用法是相对比较：报告中同时给出分算子与分目标差异、95% 区间与负控 precision，避免单一数字被过度解读。

## 附：可复现命令

``` bash
# 1) 枚举变异体（每个目标一次）
python3 -m tools.governance_maturity.mutation_scan --targets data/governance_maturity/mutation_targets.json \
        enumerate --target <target> --json-out artifacts/governance_maturity/mutants/<target>.json
# 2) 分片运行（i=1..8，可分发到集群：同一条命令 + --shard i/8）
python3 -m tools.governance_maturity.mutation_scan --targets data/governance_maturity/mutation_targets.json \
        run --target <target> --shard i --n-shards 8 \
        --mutants artifacts/governance_maturity/mutants/<target>.json \
        --json-out artifacts/governance_maturity/shards/<target>_s<i>.json
# 3) 汇总 + 图 + 报告
python3 -m tools.governance_maturity.run_maturity --out artifacts/governance_maturity
```

*报告由 `tools/governance_maturity/report_writer.py` 从账本程序化生成；生成时间 2026-11-10T20:36:23Z；所有数字可在 `artifacts/governance_maturity/*.json` 中对账。*
