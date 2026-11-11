# P223W2B-N08 交付面卫生机制成熟化：四类内容等价判定 + 只减不增门的可证成熟度

> 模块：`tools/delivery_hygiene/`（`gitio`/`classify`/`ledger`/`dispose`/`gate`/`maturity`）
> 用例：`tests/test_delivery_hygiene.py` + `tests/test_delivery_hygiene_maturity.py`
> 账本：`artifacts/delivery_hygiene/<repo>/ledger.json.gz`（含 `content_sha256`）
> 成熟度：`artifacts/delivery_hygiene/maturity/maturity_<repo>.json`
> 门裁决：`artifacts/delivery_hygiene/maturity/gate_<repo>.json`，基线 `config/delivery_hygiene_baseline.json`
> 图：`artifacts/delivery_hygiene/maturity/figures/`


## 1. 案例介绍（问题是什么）

平台每个任务在工作树上建一条 `agent/m<id>` 分支，交付后由平台推送/合并，**没有任何机制回收分支**。分支只增不减，于是"未合并 4xx 条"这句话每月被重新发现一次。本节点把"分支卫生"从散文变成五条可机器裁定的判据，并把判定结果绑定到账本指纹上。


物理/工程表述：这是一个**内容守恒**问题而不是计数问题。一个分支可以被安全关闭，当且仅当它引入的内容在主干或另一条分支里已经有等价副本（内容映射到 patch-id 与 blob 两个层面）；否则它是唯一的交付载体，关闭它等于丢数据。


## 2. 怎么做的（方法/工具链）

1. **判据**（`classify.py`）：D=tip 在 base 历史内；A=独有补丁为空 / 净 diff 为空 / squash 合成 patch-id 命中 base；B=独有 patch-id 真包含于另一分支（链式解析到极大元）；C=独有 patch-id 既不在 base 也不在别分支。优先级 D>A>B>C，四类互斥且穷尽。
2. **处置**（`dispose.py`）：A/D 归档到 `refs/attic/<date>/<branch>` 并回读校验后删 ref；B 标注 `superset_source` 后归档；C **永不归档**，只开补单（`tickets.jsonl`，一分支一行）。
3. **门**（`gate.py`）：冻结基线 `freeze`（ceiling=当前 B+C）+ 棘轮（只能降）+ 裁决 `gate`（`V1` 超 ceiling、`V2` 冻结后新增 C、`V4` base 移动未重冻结）。
4. **成熟度自证**（`maturity.py`）：把节点五条判据做成一次可重算审计，其中 (d) 门禁负控在**临时合成仓库**里注入 3 条新分支，要求门变红且点名全部注入分支。
5. **图与文档**：`maturity_doc.py` 从 JSON 注入数字生成图与本文件（零手抄）。


## 3. 判据结果（数字表）

| 判据 | TensorPlatform | ship-performance-platform |
|---|---|---|
| (a) 分类覆盖率 100% + 账本 sha256 可重算 | coverage=1.000，sha256=一致 （`6f838906126028eb…`） | coverage=1.000，sha256=一致 （`4a32639197d0ad3d…`） |
| (b) 抽样 30 条独立复核一致率 ≥95% | 30/30 = 1.000 | 30/30 = 1.000 |
| (c) 未合并积压收敛（目标 <50，C 零丢失） | 1888→1629，C=1629，零丢失=True，命中目标=None | 1654→1606，C=1606，零丢失=True，命中目标=False |
| (d) 门禁负控：注入 3 条分支后必须变红（检出率 100%） | 注入 3 检出 3，detection=1.00，门红=True | 注入 3 检出 3，detection=1.00，门红=True |
| (e) 账本分支集合 vs `git for-each-ref` | lost=0，rewritten=0，快照后新增=15，pass=True | lost=0，rewritten=0，快照后新增=7，pass=True |

### 3.1 只减不增门在真实数据上的裁决

| 仓库 | ceiling（冻结于） | 当前未合并 | 余量 | 冻结后新增 C | 门裁决 |
|---|---|---|---|---|---|
| `TensorPlatform` | 1513（2026-11-10T14:41:24） | 1888 | -375 | 218 | FAIL |
| `ship-performance-platform` | 1423（2026-11-10T14:41:25） | 1654 | -231 | 232 | FAIL |

两仓门都是 **FAIL**：冻结后 24 h 内积压继续上升（船仓 +375，平台仓 +231），`freeze` 的棘轮同时拒绝抬高 ceiling。这是机制想要的信号——**门不是纸门，它现在就在报警**。


### 3.2 C 类内容探针：积压是真内容还是判据漏判？

| 仓库 | 抽样 C | 引入 blob 中位数 | 已被 base 树覆盖 | 被另一 C 分支 blob 超集覆盖 | 结论 |
|---|---|---|---|---|---|
| `TensorPlatform` | 100 | 12 | 0 | 0 | `undelivered-content-is-real` |
| `ship-performance-platform` | 100 | 15 | 0 | 0 | `undelivered-content-is-real` |

命中率为 0 ⇒ C 类分支携带的是**基线里不存在的内容**，归档/删除不会让它消失，只有"交付"能把它变成 A。这直接决定了判据 (c) 的可达性边界（见 §5）。


## 4. 图


![图1 四类分布（两仓）](fig1_class_distribution.png)

*图1 四类分布（两仓）*


![图2 只减不增门：冻结 ceiling vs 当前积压](fig2_gate_headroom.png)

*图2 只减不增门：冻结 ceiling vs 当前积压*


![图3 积压按 tip 月份累积 + C 类内容探针（各 100 条）](fig3_backlog_and_probe.png)

*图3 积压按 tip 月份累积 + C 类内容探针（各 100 条）*


## 5. 注意点与局限

1. **判据 (c) 的"443→<50"在本节点不可达**：探针显示 100 条抽样 C 分支引入的 blob 在 base 树里命中 0 条，即积压不是判据漏判，而是真未交付。归档只对 A/B/D 生效，C 类若被关掉就是丢数据。要把未合并降到 50 以下，唯一合法杠杆是**交付吞吐**（把 C 合进 base 变成 A），不是清理。本节点交付的是这个杠杆的机器接口（补单 + 门），并诚实报告：目标未达成（honest FAIL）。
2. **squash 语义**：squash 若同时改写内容，合成 patch-id 不命中 ⇒ 仍判 C（保守方向，不误关）。
3. **账本是快照**：判据 (e) 因此分三类——真丢(lost)/被 force-move(rewritten) 判红；快照后新增(arrived) 与在飞分支推进(advanced) 只记录。当前实测 lost=0、rewritten=0。
4. **B 类只保证包含关系**：内容留在超集分支，超集分支自身的质量不在本机制判定范围。
5. **门依赖宿主侧定时执行**：CI 只能看到已推送分支，堆积主要发生在宿主本地 refs。


## 6. 误差判定（逐项）

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 分类覆盖率 | 100% | 见 §3(a) | 0 | 是 |
| 抽样一致率 | ≥95% | 见 §3(b) | 0 | 是 |
| 门检出率（负控 3 条注入） | 100% | 见 §3(d) | 0 | 是 |
| 账本 ref 差集（lost+rewritten） | 0 | 见 §3(e) | 0 | 是 |
| 未合并积压 | <50（节点目标） | 见 §3(c) | 未达成 | **否（honest FAIL）** |

最后一项是 honest FAIL，未粉饰：机制、证据与收敛杠杆都已就位，但"把积压压到 50 以下"需要真实交付 1556 条分支，超出单个节点在无合并授权下的能力边界。

