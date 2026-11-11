"""tools.capability_drift —— 能力回归检测（Capability Drift Monitor）。

代码变更后自动识别受影响的求解器能力、重跑验证案例、对比基线数字，超阈值即告警，
并用 ``git blame`` 定位引入退化的提交。**零领域语义**：能力 id、案例路径、判据阈值、
基线数字、豁免记录全部来自 JSON 工件注入；本包只处理注册表、进程执行、误差比较、
签发校验与告警渲染。测试夹具统一用合成能力名 ``synth.*``。

流水线：``trigger``（变更→受影响能力）→ ``runner``（重跑案例取数字）
→ ``detector``（数字 vs 基线，判据/豁免）→ ``attribution``（git blame）
→ ``alerts``（平台事件）→ ``dashboard``（健康度/趋势/图）。

入口：

    python3 -m tools.capability_drift.run_acceptance     # A1–A8 验收 + 工件 + 图 + 报告
    python3 -m tools.capability_drift.ci                 # CI/PR 触发（退化则非零退出）
    python3 -m pytest tools/capability_drift/tests -q    # 单元测试
"""
from __future__ import annotations

__all__ = [
    "model",
    "trigger",
    "runner",
    "detector",
    "attribution",
    "approvals",
    "baseline",
    "alerts",
    "dashboard",
    "sandbox",
    "acceptance",
    "report",
]

__version__ = "1.0.0"