"""AIL 记忆内核（Issue #74 隔离实验路径）。

本包只做**派生视图**：把已有的真实记录（mission/event/工件/commit）转换成
结构化记忆条目、做意图感知检索、按 token 预算装配上下文。

纪律（见 docs/adr/AIL_ADR_001.md 与 docs/specs/AIL_DATA_SPEC.md）：
- 不写、不改、不删任何来源记录；不新增事件表；不接入正式工作流；
- 不做新符号语法 / 二进制编码 / 学习型编码；
- 检索与装配必须确定性可复现（无随机、无外部模型）。
"""

AIL_VERSION = "1.0"
SCHEMA_VERSION = 1

__all__ = ["AIL_VERSION", "SCHEMA_VERSION"]