# knowledge_distill —— 跨战役知识蒸馏引擎（域无关机制层）

把「战役收口时手抄各节点结论」做成一条机器可复算的流水线：

```
报告（work/*/report.json、验收报告）
   │  extract.py      结构化字段通道 + markdown 小节通道（别名由参数注入，零域词）
   ▼
证据条目（limitation / next_step / honest_fail）+ 覆盖率统计 + 全局去重
   │  features.py     TF-IDF 字符 n-gram × 子词哈希嵌入 × 词元 IDF 通道（三通道取最大）
   ▼
余弦 DBSCAN（cluster.py，k-距离分位点自适应半径）+ 簇凝聚度门
   │  pipeline.py     每簇 → 提案骨架（title / repo_hint / domain_hint / gap_description / sources）
   ▼
新颖性门控（novelty.py，novelty = 1 - max_sim ≥ 0.55 且 mean_sim ≤ 0.40）
   ▼
提案种子 + 逐条目 sha256 溯源链 + MANIFEST（每产物逐文件校验和）
```

## 快速开始

```bash
# 单次蒸馏
python3 -m tools.knowledge_distill.cli \
    --reports '/root/tp-agent/work/m*/report.json' \
    --references /root/tp-agent/proposals \
    --out runs/x --max-reports 300

# 合成夹具端到端（含真值聚类一致性）
python3 -m tools.knowledge_distill.cli \
    --reports tests/fixtures/knowledge_distill/reports \
    --references tests/fixtures/knowledge_distill/reference \
    --truth-items tests/fixtures/knowledge_distill/truth_items.json \
    --out runs/m9107/synthetic
```

退出码：`0` 全部判据通过；`1` 有判据不通过；`2` 输入/配置非法（不落任何产物）。

## 产物

| 文件 | 内容 |
|---|---|
| `coverage.json` | 解析覆盖率 / 字段覆盖率 / 条目类别 / 去重统计 / 硬错误清单 |
| `clusters.json` | 逐簇画像（成员、平均簇内相似度、代表点、真值主导率）、凝聚度门丢弃清单 |
| `seeds.json` | 候选种子 + 门控判定（三路相似度、最近邻参考、拒绝理由） |
| `provenance.jsonl` | 每个种子的来源链：节点 ID / 报告路径 / 报告 sha256 / 原文引用 / 引用 sha256 |
| `run.json` | 参数 + 参数指纹 + 输入清单（sha256）+ 全部判据数字 |
| `MANIFEST.json` | 上述每个文件的 sha256 |

## 设计要点（踩过的坑换来的）

* **三通道取最大**：字符级余弦对同义改写只有 0.2~0.4，词元 IDF 通道负责召回；但包含度必须
  设长度比门，否则短碎片会把无关条目串成一条长链。
* **去重先行**：同文条目会把 k-距离分布压到 0，让自适应半径失效；按文本 sha256 全局去重后
  仍保留全部来源（同文重复出现本身就是证据强度）。
* **凝聚度门**：簇内平均相似度低于阈值说明是单链串联出来的假簇，整簇退回未归属。
* **门控双约束**：只卡最近邻会放行「一条像、其余全像模板」的伪新颖；平均相似度是硬上限。
* **判据 (b) 必须有证据**：真值标签与人工抽检两档都没有时判「未判定 = 不通过」，
  不允许静默通过。

## 测试与覆盖率

```bash
python3 -m pytest tests/test_knowledge_distill_*.py -q
python3 -m coverage run --source=tools/knowledge_distill -m pytest tests/test_knowledge_distill_*.py
python3 -m coverage report -m
```

域中立由 `tools/audit/domain_leak_scan.py` 机器裁决（本模块与夹具已登记进
`config/domain_word_denylist.json` 的 `scan_paths`）。

## 复现本次验收

```bash
python3 tools/knowledge_distill/m9107_acceptance.py   # 合成链 + 真实链 + 图 + 判据汇总
python3 tools/knowledge_distill/m9107_report.py       # 生成本次报告与发帖 payload
```

参数全部集中在 `params.py:DistillParams`（可 `--config` 覆盖 JSON；未知字段/越界值直接报错）。