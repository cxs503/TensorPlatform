"""repro_settlement —— 复证缺口（blocked / drift）的机器可裁清偿流水线。

零领域语义：本包只认识「复证案（case）」的通用结构——
状态、环境规格、漂移证据四要素——不含任何领域公式、领域判据或领域词条。

三层：
  canonical.py   规范化 JSON + sha256（幂等与出处）
  envlock.py     环境锁：把运行环境钉成可校验的 sha256 快照（blocked 的清偿物）
  attribution.py 漂移四路归因（分辨率 / 求解器版本参数 / 随机种子非确定性 / 基准数据版本）
  settle.py      清偿流水线 + 成熟化门 C1..C7
"""
from tools.repro_settlement.settle import settle, GATES  # noqa: F401

__all__ = ["settle", "GATES"]
