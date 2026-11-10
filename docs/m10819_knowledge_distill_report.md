# 【Mission 10819】跨战役知识蒸馏引擎：验收报告

**节点**: p318w3-n05 | **日期**: 2026-11-10 | **仓库**: TensorPlatform  
**模块**: `tools/knowledge_distill/` | **分支**: `agent/m10819`

---

## 1. 案例介绍

### 1.1 物理背景
战役 DAG 持续交付节点，每个节点的验收报告包含局限（limitation）、下一步（next_step）、honest FAIL 三类结构化证据。这些条目是**提案种子**的天然来源——但人工翻阅数百份报告既不现实也不一致。需要一个**域无关**的自动蒸馏引擎，把分散的局限/FAIL 聚类成结构化提案骨架，经新颖性门控后输出到提案池。

### 1.2 设计约束
- **零领域语义**：引擎不含任何物理域词条（阻力/耐波/冰区…），域/仓提示由报告字段注入
- **确定性**：同一输入两次运行逐字节相同（无随机种子、无集合遍历顺序依赖）
- **降级不崩溃**：坏文件→`unparsable`，不抛栈，但覆盖率必须体现
- **溯源链完整**：每个种子带来源节点 ID / 原始引用文本 / sha256，可回原文核对

### 1.3 参数表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ngram_min/max` | 2/3 | 字符 n-gram 区间 |
| `tfidf_weight` | 0.5 | TF-IDF 余弦在组合相似度中的权重 |
| `emb_dim` | 256 | 子词哈希嵌入维度 |
| `eps` | auto | DBSCAN 邻域半径（auto=k-距离 0.6 分位点） |
| `min_samples` | 2 | DBSCAN 核心点最小邻居数 |
| `min_cluster_size` | 2 | 成簇后过滤阈值 |
| `min_intra_similarity` | 0.20 | 簇内平均相似度下限 |
| `novelty_min` | 0.55 | 新颖性下限 |
| `mean_sim_max` | 0.40 | 与参考池平均相似度上限 |
| `min_item_chars` | 12 | 条目最短字符数 |

---

## 2. 方法

### 2.1 五阶段流水线

```
报告文件 → [1.解析] → 条目列表 → [2.特征] → 特征空间 → [3.聚类] → 簇画像
    → [4.骨架] → 候选种子 → [5.门控] → _accepted_ 提案种子 + 溯源链
```

### 2.2 各阶段关键设计

**阶段 1：报告解析**（`extract.py`，502 行）
- 双通道：结构化 JSON 字段（`limitation(s)` / `next_step(s)` / `honest_fail(s)` 及别名）+ markdown 小节（标题关键字经配置注入）
- 敏感信息脱敏：`PGPASSWORD=xxx` / `Bearer xxx` / 私钥块 → `<REDACTED>`
- 降级纪律：JSON 读不出 → `status="unparsable"`，错误进 `errors`，不抛栈
- 覆盖率双口径：`parse_coverage`（一致解析报告/总报告）+ `field_coverage`（抽出字段/出现字段）

**阶段 2：特征空间**（`features.py`，345 行）
- TF-IDF 字符 n-gram：无词典，零领域词表；重复模板句自动降权
- 子词哈希嵌入：blake2b 映射到固定维度、带符号累加、L2 归一化（确定性，无训练）
- 词元 IDF 索引：ASCII 词 + CJK 二元字（中文无词边界的最小语义单元）
- 三路互补相似度取最大：`char_cos` + `jaccard` + `token_similarity`

**阶段 3：聚类**（`cluster.py`，221 行）
- 余弦距离 DBSCAN：不需要预先指定 k，孤立点标为 `UNASSIGNED=-1`
- 确定性：点的处理顺序=输入索引顺序；簇编号按"首次成为核心点的索引"递增
- `auto_eps`：k-距离分布上分位点（剔除同文零距离后），默认 0.6 分位

**阶段 4：种子骨架**（`pipeline.py`，314 行）
- 每簇生成一个候选：`title`（medoid 文本截断）+ `gap_description`（代表引用拼接）+ `repo_hint` / `domain_hint`（多数投票）
- 种子指纹：内容寻址 sha256（来源顺序无关，身份字段自身不进指纹）

**阶段 5：新颖性门控**（`novelty.py`，283 行）
- 参考语料库：JSON/JSONL/markdown 递归抽取文本键，markdown 按段落切块
- 两阶段匹配：词元通道全量精确（便宜）+ 头部候选三通道精确（贵）
- 双硬约束：`novelty = 1 - max_sim ≥ 0.55` 且 `mean_sim ≤ 0.40`

### 2.3 工具链
- Python 3.11 + pytest 9.2
- 无外部 ML 依赖（无 sklearn / scipy / torch）
- 全部自实现：DBSCAN / TF-IDF / 哈希嵌入 / Jaccard

---

## 3. 计算结果

### 3.1 端到端运行（10 份合成报告 + 10 条参考提案）

| 指标 | 数值 | 判据 | 状态 |
|------|------|------|------|
| **(a) 解析覆盖率** | **1.000** (10/10) | ≥ 0.90 | ✅ PASS |
| 字段覆盖率 | 0.941 (16/17) | ≥ 0.90 | ✅ PASS |
| **(b) 聚类一致性** | **1.000** (4/4 簇) | ≥ 0.80 | ✅ PASS |
| 聚类纯度（真值主导率加权） | 1.000 | — | — |
| **(c) 新颖性 mean_sim** | **0.046** | < 0.40 | ✅ PASS |
| 最低新颖性 | 0.726 | ≥ 0.55 | ✅ PASS |
| **(d) 种子产出** | **3 个** | ≥ 3 | ✅ PASS |
| 候选种子 | 4 个 | — | — |
| 被门控拒绝 | 1 个 | novelty=0.142 < 0.55 | — |

### 3.2 产出种子明细

| 种子 ID | 标题（截断） | 簇大小 | 簇内相似度 | 新颖性 | mean_sim | 通过 |
|---------|-------------|--------|-----------|--------|----------|------|
| seed-2301809fa51c | 稀疏线性求解器只实现了对角预条件… | 3 | 0.360 | 0.933 | 0.018 | ✅ |
| seed-6a4fb8b94c5f | 长任务队列没有断点续跑能力… | 3 | 0.276 | 0.726 | 0.061 | ✅ |
| seed-08c02b1a728a | 报告模板缺少审计字段… | 3 | 0.361 | 0.142 | 0.167 | ❌ |
| seed-3300dab139bd | 配置加载只在启动时读一次… | 2 | 0.281 | 0.884 | 0.060 | ✅ |

### 3.3 测试覆盖

| 测试文件 | 用例数 | 状态 |
|---------|--------|------|
| test_knowledge_distill_domain_neutral.py | 6 | ✅ 全绿 |
| test_knowledge_distill_drivers.py | 5 | ✅ 全绿 |
| test_knowledge_distill_extract.py | 18 | ✅ 全绿 |
| test_knowledge_distill_features.py | 16 | ✅ 全绿 |
| test_knowledge_distill_novelty.py | 6 | ✅ 全绿 |
| test_knowledge_distill_pipeline.py | 31 | ✅ 全绿 |
| **合计** | **82** | **✅ 全绿** |

### 3.4 代码覆盖率（核心模块）

| 模块 | 语句数 | 覆盖率 |
|------|--------|--------|
| `__init__.py` | 8 | 100% |
| `cli.py` | 70 | 86% |
| `cluster.py` | 131 | 86-96% |
| `extract.py` | 299 | 87-90% |
| `features.py` | 217 | 85-94% |
| `novelty.py` | 169 | 80-98% |
| `params.py` | 90 | 98% |
| `pipeline.py` | 142 | 99% |

> 注：`m9107_acceptance.py` / `m9107_report.py` 为历史任务报告生成器（399+449行），不计入核心引擎覆盖率。

---

## 4. 结果图

本任务为纯后端/算法引擎，无数值计算场量，无云图/曲线图。

替代可视化：流水线各阶段数据流图（文本形式）：

```
10 reports ──→ parse ──→ 21 items (16 distinct)
                           │
                    FeatureSpace.fit_transform
                           │
                    16 × (tfidf_vec + emb_vec + tok_vec)
                           │
                    DBSCAN(eps=0.719, min_samples=2)
                           │
                    4 clusters + 5 unassigned
                           │
                    seed skeleton (4 candidates)
                           │
                    novelty gate vs 10 reference entries
                           │
                    3 accepted + 1 rejected
```

---

## 5. 注意点

### 5.1 踩过的坑
1. **同文条目压垮 k-距离分布**：如果多条完全相同的条目存在，它们的距离为 0，会把 k-距离分布的下端压到 0，导致 `auto_eps` 退化为 0。解决方案：估半径时剔除零距离。
2. **短片段被长句完全包含**：词元包含度 `Σidf(A∩B)/min(...)` 在长度悬殊时会假性给满分。解决方案：设长度比门控（`token_min_length_ratio=0.5`），不达标时只用加权 Jaccard。
3. **markdown 表格行串簇**：表格行（`| a | b |`）被当成条目时，表头会跨簇串联。解决方案：`_split_blocks` 里检测 `|` 开头的行，单独切块。
4. **参考池过大时全量精确比对超时**：1.7 万条参考逐条算字符 n-gram Jaccard 要 140s。解决方案：两阶段匹配——词元通道全量精确（便宜），头部 128 条再算字符级精确。

### 5.2 局限
1. **无真正语义嵌入**：子词哈希嵌入只捕捉子串共享，不具备同义改写召回能力（如"收敛慢"vs"迭代次数多"）。真实语义向量可通过 `HashedEmbedding.from_vectors()` 注入替换。
2. **DBSCAN 不适合超大规模**：O(n²) 邻域查询，>1000 条目时需切换近似方法。
3. **markdown 小节识别依赖标题关键字**：如果报告用非标准标题（如"坑"而不是"局限"），需要扩展 `markdown_section_aliases`。

### 5.3 适用范围
- 输入：JSON 格式的验收报告（`report.json`），含结构化字段或 markdown 正文
- 条目语言：中英混排（字符 n-gram 无语言依赖）
- 参考池规模：≤10 万条（两阶段匹配可处理）

---

## 6. 误差判定

### 6.1 判据逐项

| 判据 | 基准 | 实测 | 误差 | 是否达标 |
|------|------|------|------|---------|
| (a) 解析覆盖率 | ≥ 0.90 | 1.000 | +0.100 | ✅ 是 |
| (b) 聚类一致性 | ≥ 0.80 | 1.000 | +0.200 | ✅ 是 |
| (c) mean_sim | < 0.40 | 0.046 | -0.354 | ✅ 是 |
| (d) 种子产出 | ≥ 3 | 3 | 0 | ✅ 是 |
| (e) pytest | 全绿 | 82/82 | — | ✅ 是 |
| (f) 零域词 | 合成域名 | 合成域名 | — | ✅ 是 |

### 6.2 无基准声明
- 聚类质量用**真值标签**核验（合成夹具预设了条目→议题映射），纯度=1.0 表示每簇只含同一议题的条目
- 新颖性门控用**既有提案池**做参照，mean_sim=0.046 表示输出种子与参考池几乎无重叠

---

## 7. 产物清单

| 文件 | 路径 | 说明 |
|------|------|------|
| 验收 JSON | `docs/m10819_acceptance.json` | 全部判据数字 + 引擎组件说明 |
| 报告文档 | `docs/m10819_knowledge_distill_report.md` | 本文件 |
| 蒸馏产物 | `/tmp/distill_run/` | coverage/clusters/seeds/provenance/run/MANIFEST |
| 测试夹具 | `tests/fixtures/knowledge_distill/` | 10 份合成报告 + 10 条参考提案 + 真值映射 |
| 测试用例 | `tests/test_knowledge_distill_*.py` | 6 个测试文件，82 个用例 |

---

## 8. 审计链

- 参数指纹：`DistillParams().digest()` = sha256 of canonical JSON
- 种子指纹：`seed_digest(seed)` = sha256 of (title, gap_description, repo_hint, domain_hint, kind_counts, source_sha256 集合)
- 产物清单：`MANIFEST.json` 含每个输出文件的 sha256 + 字节数
- 输入清单：`run.json` 含每个输入文件的路径 + sha256 + 字节数
- 溯源链：`provenance.jsonl` 每行一个种子，含全部来源节点的 node_key / source_path / source_sha256 / quote / quote_sha256

---

*报告生成时间：2026-11-10T19:30:00Z*  
*引擎版本：tools/knowledge_distill/ @ agent/m10819*  
*Python：3.11.6 | pytest：9.2.0 | 平台：loongarch64*
