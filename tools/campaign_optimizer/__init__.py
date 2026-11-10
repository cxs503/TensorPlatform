"""campaign_optimizer —— 战役 DAG 自动瓶颈检测 / 根因分类 / 资源重分配建议 / 历史回测。

机制（零领域语义，只用状态 + 依赖 + 资源元数据）：

* :mod:`tools.campaign_optimizer.model`     —— DAG 纯函数层（拓扑序、关键路径、下游闭包、浮动）
* :mod:`tools.campaign_optimizer.corpus`    —— 只读快照装载（离线段可复算）
* :mod:`tools.campaign_optimizer.detect`    —— 瓶颈检测 + 严重度分级 + 风险节点预测
* :mod:`tools.campaign_optimizer.rootcause` —— 声明式规则引擎（规则在 config，非代码）
* :mod:`tools.campaign_optimizer.schedule`  —— 离散事件调度模拟器（容量/重试/优先级）
* :mod:`tools.campaign_optimizer.reallocate`—— 反事实建议生成（每条建议带可算节省量）
* :mod:`tools.campaign_optimizer.backtest`  —— 历史回测 + 混淆矩阵 + 随机 DAG 负控
* :mod:`tools.campaign_optimizer.report`    —— paper 级 markdown 与图

设计纪律（见 docs/CAMPAIGN_OPTIMIZER.md）：

1. **零领域语义**：任何判定只读 status / dependencies / node_type / mission 结构化字段；
   文本字段（节点名、block_reason）只作为证据写入产物，**不参与任何判定**（``tests`` 有
   「改名不变性」与「隐去文本不变性」两条行为证明）。
2. **口径单一**：状态集合、阈值、规则、调度参数全部来自 ``config/campaign_optimizer.v1.json``。
3. **幂等**：输出 JSON 排序确定、时间戳取内容派生时钟，同输入连跑逐字节一致。
"""

from __future__ import annotations

__all__ = ["SCHEMA_VERSION"]

SCHEMA_VERSION = "campaign_optimizer.v1"