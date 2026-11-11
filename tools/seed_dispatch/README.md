# seed_dispatch —— 跨战役知识蒸馏 → EVOI 派单排序（域无关机制层，零领域语义）

把「蒸馏出的提案种子」接进「派单决策」：**先派哪一单**由 ``P(验收通过) × 单位成本信息增益``
（EVOI）排序给出，用真实平台派单日志做时序 walk-forward 评估，用 IPS/SNIPS 做 off-policy 校正，
并有双侧泄漏审计与三级 sha256 溯源链。

```
tools.knowledge_distill                    本包
──────────────────────────                ─────────────────────────────────────────────
report.json → limitation/next_step/    ┌─→ log.py        真实任务日志 → 派单时刻特征+结局+成本
  honest_fail 条目                      │      (严格回看；result.*/status/finished_at 禁入特征)
  ↓ TF-IDF×嵌入×词元 IDF 三通道          │
  余弦 DBSCAN 聚类 + 凝聚度门            │
  ↓ 新颖性门控 novelty≥0.55, mean≤0.40   │
提案种子 seeds.json ─────────────────────┴─→ integration.seed_candidates  同构设计矩阵
                                                  ↓
                                            policy.rank_seeds  EVOI = P·gain/cost
                                                  ↓
                                            seeds_ranked.json（含三级溯源链）
派单日志（平台任务库快照）→ policy.walk_forward（5 窗、严格向前）
                              ├ precision@20% vs FIFO/随机/最省成本
                              ├ IPS / SNIPS / ESS（propensity 重建）
                              └ integration.audit_leakage（扰动替换 + 注入负对照）
```

## 设计矩阵（历史任务与种子**逐字相同**，否则接不进同一模型）

| 特征 | 含义 | 派单时刻可得？ |
|---|---|---|
| `f_repo_pass` | 该仓历史通过率（先验平滑，只用更早记录） | ✅ |
| `f_repo_recent_fail` | 该仓最近 40 条失败率 | ✅ |
| `f_global_recent_fail` | 全平台最近 100 条失败率（在跑的"年代/健康度"信号） | ✅ |
| `f_log_len` | log1p(任务书/种子描述字符数) | ✅ |
| `f_log_budget` | log1p(预算工具调用数)；种子取来源任务中位数 | ✅ |
| `f_novelty` | 1 − 与既有内容的最大相似度（滑动池余弦 / 蒸馏层门控值） | ✅ |
| `f_is_child` / `f_co_repos` | 是否子单 / 跨仓协作数（种子恒 0） | ✅ |

**禁止进特征的**：`result.*`（accepted / decision_note / fail_stage / checks / tool_calls …）、
`status`、`finished_at` —— 它们只做标签与成本口径。``audit_leakage`` 双侧验证这一点：
扰动替换后特征矩阵逐字节相同、指标漂移 ≤ 容差；把结局本身当特征注入则指标必须跳变（否则审计无鉴别力，判负）。

## 数学口径

* 验收概率：标准化 + L2 逻辑回归，确定性全批梯度（无随机初始化）。
* 成本：历史 ``tool_calls`` 只作拟合标签；候选成本用 ``log cost = a + b·[log_budget, log_len]`` 预测。
* ``EVOI = P(pass) · max(novelty, gain_floor) / clip(cost, 1, ∞)``（主口径，参数化可改）。
* 排序效用：``precision@20%``；基线 = FIFO（平台今天的排队顺序）、随机、最省成本。
* off-policy：slate = 时间桶，日志实际只派出 FIFO 最早一条；``π0 = (1-ε)·softmax(β·FIFO 优先度) + ε/|s|``，
  IPS / SNIPS / ESS / 重叠度一并报出（propensity 是**重建**而非记录，报告须注明）。

## 文件

| 文件 | 作用 |
|---|---|
| `params.py` | 参数面（阈值/窗口/口径），`--config` 覆盖，未知字段/越界值直接报错 |
| `log.py` | 快照读取（`.gz` 透明）、`fetch_missions`（只读 API）、日志适配、流式新颖性 |
| `policy.py` | 逻辑回归 / 成本模型 / EVOI / walk-forward / IPS-SNIPS / precision@k / AUC |
| `integration.py` | 种子候选（同构特征）+ EVOI 排名 + 泄漏审计 + 判据汇总 |
| `fixtures.py` | 合成 DGP（已知真值）：仓质量 + 主题复用新颖性 + 对数线性成本 |
| `report.py` | 五张图 + 论文级 markdown（数字程序化注入，六要素） |
| `acceptance.py` | 一条命令跑完 (a)~(g)：pytest / coverage / 域词扫描 / 真值聚类 / 敏感性分析 |
| `cli.py` | `fetch`（快照）/ `evaluate`（评估） |

## 复现

```bash
# 1) 快照（只读 API；把派单时刻字段与结局裁成可复算工件，gz 约 4.5 MB)
python3 -m tools.seed_dispatch.cli fetch --out runs/p318w2/n10/mission_snapshot.json.gz

# 2) 蒸馏（复用知识蒸馏引擎）
python3 -m tools.knowledge_distill.cli --reports '/root/tp-agent/work/*/report.json' \
    --references /root/tp-agent/proposals --out runs/p318w2/n10/distill --max-reports 400

# 3) 评估 + 判据 + 图 + 报告（退出码 0=全通过，1=有 FAIL，2=输入非法）
python3 -m tools.seed_dispatch.cli evaluate \
    --snapshot runs/p318w2/n10/mission_snapshot.json.gz \
    --distill runs/p318w2/n10/distill --out runs/p318w2/n10
```

产物：`eval_report.json`（判据数字+溯源）、`eval_report.md`（六要素报告）、
`seeds_ranked.json`（种子排名+溯源链）、`fig1..fig5*.png`、`MANIFEST.json`（逐文件 sha256）。

## 测试

```bash
python3 -m pytest -q tests/test_seed_dispatch_core.py tests/test_seed_dispatch_integration.py \
    tests/test_seed_dispatch_surface.py
python3 -m coverage run --source=tools/seed_dispatch -m pytest -q tests/test_seed_dispatch_*.py
python3 -m coverage report -m
```

## 纪律

* **零领域语义**：代码/测试不含任何领域词条；repo、领域、任务书全部来自日志与蒸馏工件。
  已登记进 `config/domain_word_denylist.json` 的 `scan_paths`，由
  `tools/audit/domain_leak_scan.py` 机器裁决（命中 0 且零缺失才算过）。
* **honest FAIL 不粉饰**：判据缺证据即 `ok=False`（含 (b) 无真值即不判通过、(f) pytest/coverage 未真跑即不通过）。
* **数字零手抄**：报告里每个数字都从 `eval_report.json` 的实算值插值。