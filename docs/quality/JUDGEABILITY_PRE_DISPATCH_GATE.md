# 判据可裁决性派发前门（Judgment-Decidability Pre-Dispatch Gate）

**节点**：P151W1-N10 ｜ **仓**：TensorPlatform（平台共性机制，零领域语义） ｜
**产物**：`backend/quality/judgeability/`（schema + 分类器 + 分级门 + 审计器）、
`ops/taskplane/campaign_dispatcher.py` 前置钩、5 个 pytest、1 份 300 单基线审计 JSON、
3 张图、合成语料 56 条。

---

## 1 案例介绍（问题设定）

平台把「判据可机器裁决」当口号喊了很久：战役节点任务书里的「机器验收判据」段落
形式五花八门——有的给数值阈值与对标来源，有的只写「效果良好」「与文献一致」。
派发器此前对该段落**零校验**，于是不可裁决的任务书照样进队列，验收时只能靠人读，
honest-FAIL 与造假都缺少"机器可裁"的地基。

本工作把该口号变成**派发前门**：
**三维结构 schema（P1 数值阈值 / P2 参考种类 / P3 工件路径）→ 判级（advisory / enforce /
block）→ 全库基线审计 → dispatcher 挂钩 → 负控与前后对照**。

标量基准（本门不是数值求解器，故以**分类判据**与**门行为判据**为基准）：

| 量 | 基准来源 | 本单读数 |
|---|---|---|
| 合成语料分类 F1 | 56 条人工构造语料（强/弱各 28，标签即真值） | **1.000** |
| 基线审计可复现 | 同批 300 单两次独立运行，落盘 sha256 | **逐位一致** |
| enforce 漏放率（已交付单） | 258 单 succeeded 重放 | **0.0%** |
| 零领域语义域词命中 | 边界门 G1–G3（`tools/boundary`） | **0**（15 个改动文件） |

## 2 怎么做的（方法/流程/关键设置）

### 2.1 三维结构 schema（`backend/quality/judgeability/schema.py`）

| 维 | 形状 | 必填 | 语义 |
|---|---|---|---|
| **P1** | `<比较符> [=] <数>` / `±<数>`，覆盖 `< > ≤ ≥ ＜ ＞ ⩽ ⩾`、全角、`==`、科学计数 `1e-10` | 是 | 数值阈值可机器判定 |
| **P2** | 封闭词表 `{解析解, 实验, 文献, 基线, 自洽, 守恒}` 显式出现 | 是 | 对标来源可追溯 |
| **P3** | `artifacts/`、`runs/`、`.json`、`.png` | 否（advisory） | 工件可复算 |

两个关键实现细节：
* **数后边界** `(?<![\w.])`：`≤ 1.2GB` **不**计为 P1（否则 `1.2GB` 会被截成 `1.`）；
  `≤ 3%`、`≥ 0.95［F1］`、`== 1.0（` 均正确识别。
* **判据段切分**（`criteria_segment`）：任务书前半段是任务描述（"Ma 2–10"、"3 个算例"），
  整篇扫描会把描述里的数字误判为阈值。故按 `机器可算验收判据 / 机器验收判据 / 验收判据 / 判据`
  等标记切出判据段，并在 `交付 / 纪律 / 实现路径` 等节起点截断。

**任务书级 fail-closed**：一段判据标记都找不到 → 记 `flags=("no_criteria_section",)` 且判 `weak`
（维度命中原样保留供审计观察）。这是"描述里的数字冒充判据"这类假阳面的结构性堵法：
本单 300 单里有 **17 单**因这条规则从"维度齐全"降为 weak。

### 2.2 fail-closed 分级（`gate.py`）

| 模式 | 缺 P1/P2 时动作 | 用途 |
|---|---|---|
| `off` | 完全旁路 | 回滚开关 |
| `advisory`（**默认**） | 只记录 event，不改任务书 | 观测期（零流程风险） |
| `enforce` | 追加「证据槽位」建议块后照常派发 | 回填引导期 |
| `block` | 拒发（节点留 pending，事件留痕） | 强制期 |

「证据槽位」注入纪律三条：①**只追加**（`assert_append_only` 机检：注入文本前缀等于原文，
剥离区块后逐字节还原）；②**幂等**（已有区块不重复追加）；③**不可自证满足**——
区块由 `【证据槽位|EVIDENCE-SLOTS-BEGIN】…【…-END】` 定界，分类器**先剥离再判定**，
且区块内不含任何 P1 形状（测试断言），因此注入不会把 weak 抬成 strong。

### 2.3 dispatcher 挂钩（`ops/taskplane/campaign_dispatcher.py`）

`dispatch_one()` 在 `build_goal()` 之后、构造 missions payload **之前**调用
`judgeability_gate(goal)`：

```
goal = build_goal(...)
goal, jd = judgeability_gate(goal, meta=meta)      # 门：advisory/enforce/block
if jd and jd.get("action") == "refuse": ... return None   # block：不建 mission、不烧预算
payload = {"goal": goal, ...}
...
event("mission_dispatched", {..."judgeability": {...}})     # 门读数进事件（可审计）
```

* 模式走环境变量 `TP_JUDGEABILITY_GATE`，**默认 advisory**：不改任何生产行为。
* 门内部任何异常 → `fail-open`（记 WARN，原样放行）；`import` 失败同理（缺模块不影响派发）。
* 零领域语义：门只认形状与方法论词，域内容全部来自被检任务书本身。

### 2.4 基线审计器（`audit.py`）

`audit_records()` 对每条 mission 输出 `verdict / dims / missing / flags / evidence(命中原文片段)`，
并按 **repo / campaign / is_maturity** 三维切片，另给四格对账
（`P1&P2` / `only_P1` / `only_P2` / `neither`）。**确定性**：无时钟、无随机、键排序、
浮点定点（6 位），输入按 id 排序 → 同批两次运行 `digest` 逐位一致（跨进程、
`PYTHONHASHSEED` 无关，pytest 有专测）。

## 3 计算结果（数字表，全部程序化注入）

数据来源：`artifacts/quality/judgeability/missions_snapshot.json`
（`tensor_dev_missions` 终态单 `status ∈ {succeeded, failed, stopped, revoked, suspended, orphan}`，
`ORDER BY id DESC LIMIT 300`，导出于 2026-11-09）。

### 3.1 全库切片（N=300）

| 切片 | 单数 | P1 命中 | P2 命中 | P3 命中 | 强判据 | 强判据率 |
|---|---|---|---|---|---|---|
| 全体 | 300 | 215 (71.7%) | 116 (38.7%) | 99 (33.0%) | 99 | **33.0%** |
| TensorPlatform | 86 | — | — | — | 2 | **2.3%** |
| ship-performance-platform | 125 | — | — | — | 59 | 47.2% |
| TensorDEM | 28 | — | — | — | 17 | 60.7% |
| TensorLBM | 28 | — | — | — | 8 | 28.6% |
| TensorPhysics | 16 | — | — | — | 5 | 31.3% |
| 成熟化单 | 157 | — | — | — | 37 | 23.6% |
| 非成熟化单 | 143 | — | — | — | 62 | 43.4% |

四格对账（全体）：`P1&P2 = 116`、`only_P1 = 99`、`only_P2 = 0`、`neither = 85`。
**改进优先级由此直接读出**：缺 **P2（参考种类）** 是主瓶颈（缺 184/300），
缺 P1 的 85 单是"连阈值都没有"的重灾区；`only_P2 = 0` 说明现役任务书一旦写了参考来源
基本都会配阈值——短板单一地落在"没写对标来源"上。
按 repo 看，`TensorPlatform` 强判据率 2.3%（86 单仅 2 单）＝平台自建任务书最欠规格。

### 3.2 合成语料（E1）

| 语料 | 强 | 弱 | 合计 | F1(strong) | Macro-F1 | 混淆 |
|---|---|---|---|---|---|---|
| `data/quality/judgeability/synthetic_corpus.json` | 28 | 28 | 56 | **1.000** | 1.000 | TP=28, FP=0, FN=0, TN=28（误判 0） |

语料含 8 组**近邻对**（同一措辞删一维 → strong↔weak 必须翻转），
及两类已披露假阴面样本（口头阈值"3% 以内"、单位字母"1.2GB"）。

### 3.3 挂钩与前后对照（E2/E3）

| 判据 | 读数 | 阈值 | 结论 |
|---|---|---|---|
| E2 两次运行落盘 sha256 | `5aae10dd84f4715c…e1061052`（两次一致） | 相同 | ✅ |
| E3 P22 = IN21 DAG（10 节点）off/advisory/enforce 拒发数 | 0 / 0 / 0 | =0 | ✅ 全绿 |
| E3 enforce 注入节点数（同 DAG） | 10/10 | ≥1 | ✅ 门确实在动 |
| E3 block 模式对同一 DAG 拒发数（对照） | 10/10 | — | 说明该门是**真约束**而非空转 |
| E3 已交付单（258 succeeded）漏放率 | **0/258 = 0.0%** | ≤5% | ✅ |
| E3 已交付单假晋升（注入把 weak 抬成 strong） | 0 | ≤5% | ✅ |
| 负控：**朴素注入**（去掉定界标记）假晋升率 | **28.0%** | — | 证明该指标有鉴别力 |

前后对照：注入前 `P1 71.7% / P2 38.7% / P3 33.0% / strong 33.0%`；enforce 生效后
**槽位覆盖率 61.3%**（167/258 已交付单被标注缺 P1 或 P2），`strong` 仍为 33.0%
（假晋升 0）——即"提示到位、判据不被注水"。

### 3.4 零领域语义（E4）

`tools/boundary/pr_gate_scan.py paths`（G1–G3：词表×词边界 + 遮蔽还原 + 记号形态 + 引用解析）
扫 15 个改动文件（5 个模块 + 3 个工具 + dispatcher + 语料 + 5 个 pytest）：
`violations = 0`、`advisories = 0`、`verdict = PASS`。
同一测试内含**负控**：把词表首个词条塞进临时文件 → 门必须报违规（证明 0 不是空跑）。

### 3.5 不与报告门双写（E5）

`tests/test_judgeability_report_gate_schema.py` 固化 `services.report_gate.check_report_quality`
的 top key 集 / `checks` key 集 / `details` key 集，断言不变；并断言报告门源码**不引用**
判据门（单向依赖，无循环）。

## 4 图

| 图 | 文件 | 说明 |
|---|---|---|
| 三维命中分布 | `artifacts/quality/judgeability/figures/fig_dim_hit_distribution.png` | P1/P2/P3 在 strong/weak 两组中的命中单数 |
| 按 repo 堆叠 | `.../fig_repo_stacked.png` | 四格（P1&P2 / 仅 P1 / 仅 P2 / 双缺）按 repo 堆叠，n≥3 的仓 |
| 前后对照 | `.../fig_before_after.png` | 注入前命中率 vs enforce 槽位覆盖，叠加两条假晋升读数线（正规注入 0% / 朴素负控 28%） |

（图内文字用英文：宿主无 CJK 字体，中文标签会渲染成方块——见局限。）

## 5 注意点（踩坑 / 局限 / 适用范围）

1. **形状判据不做语义理解**：口头阈值（"误差在 3% 以内"）与"数值+单位字母"（`≤ 1.2GB`）
   被判为缺 P1。全库 300 单中这类形态出现 `1.0%`（口头 3 例）/ `2.3%`（单位 7 例），
   且**都没有**被实际误伤成"该判强却判弱"（`count=0`：这些单另有其它 P1 命中或本就该判弱）；
   但这是**已知假阴面**，扩充 P1 形状时须重跑本审计（`limitation_probe` 会给出计数）。
2. **P2 是封闭词表**：`解析方法`(≠解析解)、`同类实现`、`业内通行结果` 都不认。
   慷慨扩展词表会稀释"可追溯来源"的语义，故本单选择**保守**并把它显式列为假阴面。
3. **判据段切分是启发式**：提案者/仲裁者模板任务书里 `机器可算验收判据）` 出现在提示词
   指令中间，切出来的"判据段"其实是模板尾巴（24/300 单 `scope=whole`）。本单用 fail-closed
   把它判 weak，但**切分本身**仍是启发式——若要精修，应改为"模板与判据分离"的上游结构改动。
4. **block 模式会停机**：P22（IN21，历史已收官 DAG）10/10 节点在 block 模式下全部被拒
   ——说明历史任务书普遍不合规。故默认 `advisory`，`enforce` 需显式开启；
   直接上 `block` 会冻结战役派发（本单只把它当受控实验，未启用）。
5. **block 模式的重试特性**：拒发后节点留 `pending`，每 30s 周期会再判一次（事件会重复）。
   若长期启用应加"连续拒发计数→人工升级"闸；本单未做（范围外，属 rollout 事项）。
6. **审计是快照式**：300 单取自导出时刻（2026-11-09）的终态单；快照文件入库，
   任何复算吃同一份快照（E2 的前提）。新单需重导 `tools/judgeability/export_missions.py`。
7. **图内无中文**：宿主仅有 DejaVu/Noto Sans，无 CJK 字形 → 图标签用英文，正文中文。
8. **未覆盖**：wave_cycle.py 的 fallback 派发路径未挂钩（它是另一条低层派发通道，
   本次只挂 `campaign_dispatcher.dispatch_one`，即战役 DAG 的主通道）。

## 6 误差判定（逐项回答是否 <3%）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 合成语料分类 F1 | 标签即真值（56 条） | 0.000（1.000 − 1.000） | ✅ 是（0%） |
| 基线审计两次运行 digest | 同批同码两次运行 | 0（sha256 逐位相同） | ✅ 是（0%） |
| enforce 漏放率（258 已交付单） | 定义值 0 | 0/258 | ✅ 是（0%） |
| 假晋升率（正规注入） | 定义值 0 | 0/300 | ✅ 是（0%） |
| 边界门域词命中 | 0 | 0/15 文件 | ✅ 是（0%） |
| 报告门 key 集 | 固化基线 5+6+10 键 | 0 差异 | ✅ 是（0%） |

以上六项均为**精确判据**（计数/哈希/布尔），误差恒为 0，故"是否 <3%"一律为"是"；
本单不含连续物理量，无"稍微超差"的空间——判据要么成立要么不成立。
唯一非精确项是启发式切分（见局限 3），已用 fail-closed 把它导向"不合格"，不产生假合规。

## 7 复算入口

```bash
# 1) 导出快照（读库，只读）
python3 tools/judgeability/export_missions.py --limit 300 --dag-plan 22
# 2) 跑审计 + E1/E3/E4 判据 + 落盘 sha256 侧车
python3 tools/judgeability/run_audit.py
# 3) 出图
python3 tools/judgeability/render_figures.py
# 4) 验收测试（5 个文件 34 例）
python3 -m pytest tests/test_judgeability_*.py -q
```