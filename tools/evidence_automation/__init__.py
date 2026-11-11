"""能力证据自动采集管线（capability evidence automation pipeline）。

模块边界
========
``mapping``    能力→测试/探针→证据工件 的**配置**装载与静态校验（零领域语义：
               具体能力名、测试路径、阈值全部来自 ``config/capability_evidence_map.json``）。
``probes``     运行时探针（git 管路、容器编排清单、反向代理配置清单、HTTP 探活、
               几何恒等式），每个探针自报 ``criterion_kind``（analytic_identity /
               behavioral），决定它是否有资格单独支撑 REAL。
``runner``     一次性跑完所有映射测试（junitxml），按测试模块把结果**归属**回能力。
``grader``     证据→等级判定（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED），并施加
               人工复核台账给出的等级上限（自动判级不得越过人工裁决）。
``acceptance`` 逐能力落 ``acceptance.json``（带 content_digest + sha256 旁车），
               并汇总覆盖率产物。

设计纪律
--------
* 不写墙钟时间进摘要：``generated_at`` 等易变字段排除在 ``content_digest`` 之外。
* 任何"缺证据"一律显式表达为 WITHHELD + reason，绝不静默补成 REAL。
* 所有产物可离线复算：同一仓库两次运行 content_digest 一致。
"""
from __future__ import annotations

__all__ = [
    "LEVELS",
    "LEVEL_RANK",
    "MappingError",
    "load_mapping",
    "collect_evidence",
]