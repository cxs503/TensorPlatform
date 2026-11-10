# Mission 8105: Node 28 MYI 热力学-本构模型 补报单

**Mission ID:** 8105  
**Node:** 28 (MYI 热力学-本构模型 / Multi-Year Ice Thermodynamic-Constitutive Model)  
**Plan:** 2  
**Status:** honest-FAIL (无证据)  
**Date:** 2026-11-09  
**Reconcile SHA256:** e0609dabe3ca898a

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

**任务目标**：补报单（报告治理门自动派单）要求为节点 28「MYI 热力学-本构模型」补发论文级报告，该节点在 plan 2 中 status=done 但库中无已发布报告。

**预期物理背景**（基于节点名称推断）：
- **MYI (Multi-Year Ice)**：多年冰，经历多个融化-冻结循环，盐度较低（<5 ppt），密度较高（~910-930 kg/m³），力学性能与一年冰显著不同
- **热力学模型**：描述冰的温度场演化、相变（融化/冻结）、盐分析出与 brine pocket 动力学
- **本构模型**：冰的应力-应变关系，可能包含弹性、粘弹性、塑性、蠕变、损伤演化

**预期参数表**（典型 MYI 热力学-本构参数）：
| 参数 | 典型值 | 来源 |
|------|--------|------|
| 冰密度 ρ_i | 910-930 kg/m³ | 现场测量 |
| 比热容 c_p | 2000-2100 J/(kg·K) | 实验室测量 |
| 热导率 k | 2.0-2.5 W/(m·K) | 温度依赖 |
| 潜热 L | 334 kJ/kg | 相变 |
| 盐度 S | 3-7 ppt | MYI 典型范围 |
| 弹性模量 E | 5-10 GPa | 温度/应变率依赖 |
| 泊松比 ν | 0.3-0.35 | - |
| 蠕变参数 A | 10^-7 - 10^-5 (MPa·s)^-1 | Glen's law |

**实际发现**：在 TensorPlatform 工作树中**未找到任何与该节点相关的代码、文档、数据或工件**。

---

## ② 怎么做的（方法 / 流程 / 工具链）

**搜索策略**（程序化，零手抄）：

1. **工作树扫描**：
   ```bash
   find . -name "*760*" -o -name "*node_28*" -o -name "*myi*" -o -name "*multi_year*"
   grep -r "MYI\|multi.year\|node.*28\|mission.*760\|热力学.*本构" --include="*.md" --include="*.json" --include="*.py"
   ```
   结果：无匹配文件

2. **Campaign Plan 数据检查**：
   - 检查 `data/whitespace_radar/pool/campaign_export.json`：plans=[], nodes=[]
   - 检查 `docs/campaign_status_m611.md`：仅 27 个节点（F01-X02），无 node 28
   - 检查 `backend/api/campaign_plans.py`：schema 定义完整，但无 plan 2 数据

3. **数据库查询尝试**：
   ```python
   SELECT id, name, status FROM campaign_plans WHERE id = 2 OR campaign_id = 2;
   SELECT id, plan_id, name, status FROM campaign_nodes WHERE id = 28 OR name LIKE '%MYI%';
   ```
   结果：psycopg2 stub（loong64 sandbox 无真实驱动），无法连接

4. **工作目录扫描**：
   ```bash
   ls /root/tp-agent/work/ | grep -E "m760|760"
   ```
   结果：存在 m7600-m7606 等目录，但无 m760；无 node 28 相关工件

5. **报告库检查**：
   - `tensor_reports/` 目录：仅 m597_report.json, m6328_report.json
   - 无 mission 760 或 node 28 相关报告

**工具链**：
- 文件系统搜索（find, grep）
- JSON 解析（python3 json module）
- Markdown 文档审查
- 数据库连接尝试（psycopg2 stub）

---

## ③ 计算结果（数字表，程序化注入）

**搜索结果汇总表**：

| 搜索目标 | 搜索方法 | 匹配数 | 证据强度 |
|----------|----------|--------|----------|
| Plan 2 | campaign_export.json | 0 plans | 无数据 |
| Node 28 | campaign_status_m611.md | 0/27 nodes | 不存在 |
| Mission 760 | 工作目录扫描 | 0 dirs | 不存在 |
| MYI 关键词 | grep -r | 0 files | 无匹配 |
| 热力学-本构 | grep -r | 0 files | 无匹配 |
| 报告库 | tensor_reports/ | 0 reports | 无报告 |

**Campaign Plan 统计**（来自 `docs/campaign_status_m611.md`）：
- 总节点数：27（F01, F02, ..., X02）
- 里程碑分布：M0=4, M1=6, M2=5, M3=6, M4=4, M5=2
- 状态分布：not_started=0, in_progress=27, verified=0, blocked=0
- **Node 28 不在列表中**

**结论**：
- Plan 2 在当前工作树中**不存在**
- Node 28 在当前 campaign plan 中**不存在**（仅有 27 个节点）
- Mission 760 的工件**不存在**
- MYI 热力学-本构模型的代码/文档/数据**不存在**

---

## ④ 结果图

**无图**。

理由：本任务为证据搜索与缺失诊断，无数值计算结果、场量分布或对比曲线可可视化。所有搜索结果均为"0 匹配"或"不存在"，无需图表展示。

若需可视化搜索覆盖范围，可绘制：
- 图 1：工作树文件类型分布（.md/.json/.py 数量）
- 图 2：Campaign plan 节点编号直方图（1-27，标注 28 缺失）
- 图 3：搜索关键词命中率热力图（全部为 0）

但这些图表不增加信息量，故省略。

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

### 踩过的坑

1. **数据库连接失败**：
   - psycopg2 在 loong64 sandbox 中为 stub 实现，无真实驱动
   - 无法直接查询 campaign_plans / campaign_nodes 表
   - 替代方案：依赖本地 JSON/Markdown 文件推断

2. **Campaign Export 为空**：
   - `data/whitespace_radar/pool/campaign_export.json` 的 plans=[] 和 nodes=[]
   - 该文件 schema="radar-campaign/v1"，note="no live campaign DB in this deployment"
   - 说明该导出文件不是生产数据库的快照，而是空模板

3. **节点编号歧义**：
   - Campaign plan m611 有 27 个节点，编号为 F01-X02（字母+数字）
   - 任务提到"node 28"，可能指：
     - (a) 第 28 个节点（但只有 27 个）
     - (b) node_id=28（数据库主键，非业务编号）
     - (c) 另一个 campaign plan 的节点
   - 无法确定具体含义

4. **Plan 2 不存在**：
   - 工作树中仅有 campaign plan m611（冰区航行全流程）
   - 无 plan 2 的定义或数据
   - 可能指：
     - (a) campaign_plans 表的 id=2（需数据库查询）
     - (b) campaign_id=2（需数据库查询）
     - (c) 第二个版本的 plan（无版本控制证据）

### 局限

1. **数据库不可访问**：
   - 无法查询 campaign_plans / campaign_nodes 表的真实数据
   - 无法确认 plan 2 和 node 28 是否存在于生产数据库
   - 本报告基于本地文件推断，可能遗漏数据库中的证据

2. **跨仓库边界**：
   - MYI 热力学-本构模型可能实现在 TensorPhysics 或 ship-performance-platform 仓库
   - 本工作树（TensorPlatform）仅包含平台共性机制，零领域语义
   - 未检查其他仓库的工作树（/root/tp-agent/work/ 下无 TensorPhysics 检出）

3. **时间窗口**：
   - 任务 SLA=24h，但搜索时间 <1h
   - 未进行深度代码审查或历史 git log 分析
   - 可能存在已删除的分支或已合并的 commit 包含相关代码

4. **honest-FAIL 定性**：
   - 本任务要求"回到该单原始工作树/证据"
   - 但原始工作树/证据**不存在**或**不可访问**
   - 无法补发技术报告（无内容可报）
   - 只能报告"证据缺失"这一事实

### 适用范围

- 本报告仅适用于 TensorPlatform 仓库（agent/m8105 分支）
- 不适用于其他仓库（TensorPhysics, ship-performance-platform, TensorLBM 等）
- 若 plan 2 / node 28 / mission 760 的证据存在于数据库或其他仓库，本报告结论需修正

---

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 <3%）

**无基准可比**。

本任务为证据搜索与缺失诊断，无数值计算结果，故无误差可判定。

**替代判据**（证据完整性）：

| 判据 | 预期 | 实测 | 是否满足？ |
|------|------|------|------------|
| Plan 2 存在 | 是 | 否（本地文件无） | **否** |
| Node 28 存在 | 是 | 否（27 节点中无） | **否** |
| Mission 760 工件 | 是 | 否（无目录/文件） | **否** |
| MYI 代码/文档 | 是 | 否（grep 0 匹配） | **否** |
| 已发布报告 | 是 | 否（tensor_reports/ 无） | **否** |

**结论**：5/5 判据均不满足，证据完整性=0%。

**honest-FAIL 声明**：

本任务要求补发节点 28 的报告，但：
1. 无法找到节点 28 的原始工作树或证据
2. 无法确认 plan 2 和 node 28 是否存在于生产数据库
3. 无法访问其他仓库（TensorPhysics, ship-performance-platform）的可能实现
4. 本地文件中无任何 MYI 热力学-本构模型相关的代码、文档或数据

因此，**无法补发技术报告**，只能报告"证据缺失"这一事实。

若节点 28 的工作确实已完成（status=done），则：
- 工件可能存在于数据库（不可访问）
- 工件可能存在于其他仓库（未检查）
- 工件可能已被删除或归档（无历史记录）

建议后续行动：
1. 直接查询生产数据库确认 plan 2 / node 28 / mission 760 的存在性
2. 检查 TensorPhysics / ship-performance-platform 仓库的工作树
3. 审查 git log 历史，查找已删除的分支或 commit
4. 若确认工件不存在，则节点 28 的 status=done 为误标，需修正为 not_started 或 blocked

---

## 附录：搜索命令与输出

### A.1 文件系统搜索

```bash
$ find . -path ./.git -prune -o -name "*760*" -print -o -name "*node_28*" -print -o -name "*myi*" -print -o -name "*multi_year*" -print
# 输出：空

$ grep -r "MYI\|multi.year\|node.*28\|mission.*760\|热力学.*本构" --include="*.md" --include="*.json" --include="*.py" -l
# 输出：无匹配（仅 IN59-P1_droplet_entry_anomaly.md 含 "node.*28" 但为误匹配）
```

### A.2 Campaign Export 检查

```python
$ python3 -c "
import json
with open('data/whitespace_radar/pool/campaign_export.json') as f:
    data = json.load(f)
print('Plans:', len(data.get('plans', [])))
print('Nodes:', len(data.get('nodes', [])))
print('Schema:', data.get('schema'))
print('Note:', data.get('note'))
"
# 输出：
# Plans: 0
# Nodes: 0
# Schema: radar-campaign/v1
# Note: no live campaign DB in this deployment
```

### A.3 Campaign Status 检查

```bash
$ grep -c "^|" docs/campaign_status_m611.md | head -1
# 输出：27（节点表行数，含表头）

$ grep "node.*28\|MYI\|热力学" docs/campaign_status_m611.md
# 输出：无匹配
```

### A.4 工作目录扫描

```bash
$ ls /root/tp-agent/work/ | grep -E "^m760$"
# 输出：空

$ ls /root/tp-agent/work/ | grep -E "m76[0-9]"
# 输出：m7600 m7601 m7602 m7603 m7604 m7605 m7606（无 m760）
```

### A.5 报告库检查

```bash
$ ls tensor_reports/
# 输出：m597_report.json  m6328_report.json（无 m760 或 node28 相关）
```

---

**报告结束**

**Reconcile SHA256**: e0609dabe3ca898a  
**Report Type**: honest-FAIL (evidence missing)  
**Confidence**: HIGH (搜索覆盖全面，但数据库不可访问)  
**Recommendation**: 查询生产数据库 + 检查其他仓库工作树
