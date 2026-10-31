#!/usr/bin/env python3
"""
生成论文级进展报告 JSON

用于 curl POST 到 tensor/reports
"""

import json
import base64
from pathlib import Path

ARTIFACT_DIR = Path("/srv/tensor/agent/work/m604/artifacts/planner")
OUTPUT = Path("/tmp/report.json")


def load_figure(name: str) -> str:
    """加载图片并转为 base64"""
    path = ARTIFACT_DIR / name
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode()


def load_scoring_report() -> dict:
    """加载跑分报告"""
    path = ARTIFACT_DIR / "scoring_report.json"
    return json.loads(path.read_text())


def build_markdown() -> str:
    """构建报告 Markdown"""
    report = load_scoring_report()
    
    md = """# 规划师 Agent (m604) 进展报告

## 1. 案例介绍

### 1.1 问题背景

**定位**：机器门（m597 必要性三查）之上的**判断层**

- 机器查事实：已覆盖/机械可修/在途去重
- 规划师判意图与价值：挂靠谁/成本值不值/产出任务书

**核心问题**：机器判不了，agent 应该判的了，要培养出能分析目标布置任务的 agent。

### 1.2 黄金标注集来源

2026-10-31 全案即黄金标注集：

| 类型 | 数量 | 说明 |
|------|------|------|
| openhands 僵尸返工单 | 12 | 该拒：目标已被覆盖 |
| 报告重复 | 1 | 报告 34/35/36 三份全同重复（该机械修） |
| MD5 typo | 1 | 报告 8 MD5 typo（不值 140 次工具调用） |
| 好单范本 | 1 | EX-1 m541-550 系 |
| 教训案例 | 2 | m533 交付造假 / m583 治理假声明 |
| 历史采样 | 15 | succeeded/failed/zombie 各 5 条 |

**总计**：32 条标注（≥30 要求），其中 2026-10-31 案例 17 条（≥15 要求）

### 1.3 参数表

| 参数 | 值 | 说明 |
|------|-----|------|
| 标注集总数 | 32 | train: 20, held_out: 12 |
| Held-out 比例 | 37.5% | 目标 ~33% |
| Verdict 类别 | 4 | dispatch/reject/mechanical/merge |
| 决策引擎 | 规则+模式 | Phase 1 实现 |
| Schema 版本 | 1.0.0 | |

## 2. 方法

### 2.1 架构设计

```
候选任务 → [机器门 m597] → [规划师 Planner] → 决策
                ↓                    ↓
          事实检查             意图与价值判断
          - 已覆盖?            - 挂靠谁?
          - 机械可修?          - 成本值不值?
          - 在途去重?          - 产出任务书
```

### 2.2 决策引擎

基于规则 + 模式的决策逻辑：

1. **模式匹配快速路径**
   - 僵尸单模式：`openhands`, `返工.*已被覆盖`
   - 机械修复模式：`报告.*重复`, `MD5.*typo`
   - 好单模式：`EX-\\d+`, `m5[4-5]\\d`

2. **上下文分析**
   - 在飞单去重（关键词重叠检测）
   - 失败谱规避（相似失败单检测）
   - 复杂度评估（low/medium/high）

3. **成本收益估算**
   - 基于复杂度的成本映射
   - ROI 计算

### 2.3 影子模式

对进自动派单队列的候选并行出决策**不实际派单**：

- 决策入 `tensor_planner_decisions`（append-only 触发器）
- 输入快照/输出决策/对照方裁决/后续结果回填位
- 完整性哈希防篡改

### 2.4 培养回路

决策→后续结果回填→credit_ledger→playbooks 规划分册

**权限阶梯**：
1. advisory（纯建议）
2. shadow（并行对照跑分）
3. 低风险自动派（仅机械修+返工类）
4. 战役自动拆单

### 2.5 工具链

- Python 3.10+
- matplotlib（图表生成）
- JSON（数据存储）
- PostgreSQL（schema 设计）

## 3. 计算结果

### 3.1 跑分结果

"""
    
    # 训练集结果
    train = report["train"]
    md += f"""#### 训练集

| 指标 | 值 |
|------|-----|
| 总案例数 | {train['total_cases']} |
| 正确数 | {train['correct']} |
| **准确率** | **{train['accuracy']:.2%}** |
| 错例数 | {train['error_count']} |
| 错例率 | {train['error_rate']:.2%} |

"""
    
    # Held-out 结果
    held_out = report["held_out"]
    md += f"""#### Held-out

| 指标 | 值 |
|------|-----|
| 总案例数 | {held_out['total_cases']} |
| 正确数 | {held_out['correct']} |
| **准确率** | **{held_out['accuracy']:.2%}** |
| 错例数 | {held_out['error_count']} |
| 错例率 | {held_out['error_rate']:.2%} |

### 3.2 混淆矩阵（Held-out）

"""
    
    cm = held_out["confusion_matrix"]
    md += "| Predicted \\ Actual | dispatch | reject | mechanical | merge |\n"
    md += "|---|---|---|---|---|\n"
    for pred in ["dispatch", "reject", "mechanical", "merge"]:
        row = f"| **{pred}** |"
        for actual in ["dispatch", "reject", "mechanical", "merge"]:
            row += f" {cm.get(pred, {}).get(actual, 0)} |"
        md += row + "\n"
    
    md += "\n### 3.3 分类指标（Held-out）\n\n"
    md += "| 类别 | Precision | Recall | F1 | Support |\n"
    md += "|------|-----------|--------|-----|--------|\n"
    
    metrics = held_out["per_class_metrics"]
    for cls in ["dispatch", "reject", "mechanical", "merge"]:
        m = metrics.get(cls, {"precision": 0, "recall": 0, "f1": 0, "support": 0})
        md += f"| {cls} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | {m['support']} |\n"
    
    md += "\n### 3.4 错例详情\n\n"
    md += "#### 训练集错例\n\n"
    
    for err in train["errors"]:
        md += f"- **[{err['case_id']}]** {err['title']}\n"
        md += f"  - 预测: `{err['predicted_verdict']}`, 实际: `{err['actual_verdict']}`\n"
        md += f"  - 推理: {err['reasoning']}\n"
        md += f"  - GT理由: {err['gt_rationale']}\n\n"
    
    md += "#### Held-out 错例\n\n"
    
    for err in held_out["errors"]:
        md += f"- **[{err['case_id']}]** {err['title']}\n"
        md += f"  - 预测: `{err['predicted_verdict']}`, 实际: `{err['actual_verdict']}`\n"
        md += f"  - 推理: {err['reasoning']}\n"
        md += f"  - GT理由: {err['gt_rationale']}\n\n"
    
    md += """## 4. 结果图

### 4.1 混淆矩阵

"""
    md += "![混淆矩阵](fig1_confusion_matrix.png)\n\n"
    md += "**图 1**: Held-out 混淆矩阵。对角线为正确分类，非对角线为错误分类。\n\n"
    
    md += "### 4.2 Verdict 分布与准确率\n\n"
    md += "![Verdict 分布](fig2_verdict_distribution.png)\n\n"
    md += "**图 2**: 左：数据集 verdict 分布；右：训练集 vs held-out 准确率对比。\n\n"
    
    md += "### 4.3 分类指标\n\n"
    md += "![分类指标](fig3_per_class_metrics.png)\n\n"
    md += "**图 3**: Held-out 上各类别的 Precision/Recall/F1。\n\n"
    
    md += "### 4.4 错例分析\n\n"
    md += "![错例分析](fig4_error_analysis.png)\n\n"
    md += "**图 4**: 错误类型分布（预测→实际）。\n\n"
    
    md += "### 4.5 置信度分布\n\n"
    md += "![置信度分布](fig5_confidence_distribution.png)\n\n"
    md += "**图 5**: 正确/错误决策的置信度分布。\n\n"
    
    md += """## 5. 注意点

### 5.1 踩过的坑

1. **模式匹配过于简单**：当前引擎基于正则模式匹配，对复杂场景（如历史失败单）识别能力有限
   - 解决：后续可引入 LLM 做更复杂的意图理解

2. **僵尸单模式误判**：`MD5.*typo` 同时匹配僵尸单和机械修复，导致部分案例误判
   - 解决：调整模式优先级，机械修复优先于僵尸单

3. **失败谱规避不足**：当前仅基于关键词重叠检测相似性，可能漏检语义相似但表述不同的案例
   - 解决：引入语义嵌入做相似度计算

### 5.2 局限性

1. **Phase 1 规则引擎**：当前为规则+模式，未接入 LLM，复杂场景处理能力有限
2. **标注集规模**：32 条标注，规模较小，可能过拟合
3. **成本预估**：基于简单映射，未考虑实际运行数据
4. **影子模式**：未实际部署，仅本地演示

### 5.3 适用范围

- 适用于模式明确的决策场景（僵尸单、机械修复、好单识别）
- 不适用于需要深度语义理解的复杂决策
- 需要更多标注数据和实际运行数据来提升准确性

## 6. 误差判定

### 6.1 关键量 vs 基准

| 指标 | 当前值 | 目标值 | 误差 | 是否 <3% |
|------|--------|--------|------|----------|
| Held-out 准确率 | 58.33% | 65% | -6.67pp | ❌ 否 |
| 训练集准确率 | 65.00% | 70% | -5.00pp | ❌ 否 |
| 标注集总数 | 32 | ≥30 | +2 | ✅ 是 |
| 2026-10-31 案例数 | 17 | ≥15 | +2 | ✅ 是 |
| Held-out 比例 | 37.5% | ~33% | +4.5pp | ❌ 否（但在合理范围） |

### 6.2 分析

**未达标项**：
- Held-out 准确率 58.33% < 目标 65%
- 主要原因：规则引擎对历史失败单和僵尸返工单的识别能力不足

**改进方向**：
1. 引入 LLM 做意图理解（Phase 2）
2. 增加标注数据，特别是边界案例
3. 优化模式匹配规则，减少误判
4. 引入语义嵌入做相似度计算

### 6.3 诚实声明

当前 Phase 1 实现为规则引擎，准确率未达目标。这是预期内的——规则引擎是基线，后续需引入 LLM 提升。

**关键发现**：
- 僵尸单识别效果好（模式明确）
- 机械修复识别有改进空间（模式冲突）
- 历史失败单识别效果差（需要语义理解）

## 7. 展望

### 7.1 Phase 2 计划

1. **接入 LLM**：用 LLM 做意图理解和复杂决策
2. **增加标注**：扩充标注集到 100+ 条
3. **语义嵌入**：引入 sentence-transformers 做相似度计算
4. **实际部署**：影子模式上线，收集实际运行数据

### 7.2 升权路径

当前：advisory → 目标：shadow

升权条件：
- ✅ 决策数 ≥30（影子模式演示已完成）
- ❌ 准确率 ≥70%（当前 58.33%）
- ❌ Held-out 准确率 ≥65%（当前 58.33%）

需改进准确率后申请升权。

### 7.3 长期目标

培养能分析目标、布置任务的 agent，实现：
- 自动识别僵尸单并拒绝
- 自动识别机械修复并派单
- 自动拆解战役级任务
- 自动评估成本收益

---

**报告生成时间**: 2026-10-31
**Mission ID**: m604
**Schema 版本**: 1.0.0
"""
    
    return md


def build_figures() -> list:
    """构建图表列表"""
    figures = []
    
    for i in range(1, 6):
        name = f"fig{i}_*.png"
        # 查找实际文件名
        for path in ARTIFACT_DIR.glob(f"fig{i}_*.png"):
            figures.append({
                "name": path.name,
                "caption": f"图 {i}",
                "png_base64": load_figure(path.name)
            })
            break
    
    return figures


def main():
    """生成报告 JSON"""
    markdown = build_markdown()
    figures = build_figures()
    
    report = {
        "title": "规划师 Agent (m604) 进展报告：决策 Schema + 黄金标注集 + 影子模式 + 红队跑分",
        "summary": "实现规划师 Agent 的决策 schema、黄金标注集（32 条）、影子模式、培养回路和红队跑分。Held-out 准确率 58.33%，未达 65% 目标，需引入 LLM 提升。",
        "scenario_key": "planner_agent",
        "mission_id": 604,
        "tags": ["planner", "decision-schema", "annotation-set", "shadow-mode", "scoring"],
        "markdown": markdown,
        "figures": figures,
    }
    
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"✓ 报告 JSON 已生成: {OUTPUT}")
    print(f"  Markdown 长度: {len(markdown)} 字符")
    print(f"  图表数量: {len(figures)}")
    
    return report


if __name__ == "__main__":
    main()
