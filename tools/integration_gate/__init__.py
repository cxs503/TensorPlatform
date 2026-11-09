"""跨域集成与互裁门 (cross-domain integration & inter-adjudication gate).

平台机制，零领域语义：本包不包含任何物理域词条、域判据或域算例。所有域数据
(节点自证、互裁对、量测轨迹、判据阈值)都通过 ``data/campaigns/<campaign>/``
下的 JSON/脚本工件注入，骨架只提供四件通用能力：

1. ``sampler``  — 声明式量测：把「哪个模块的哪个可调用对象、在什么参数网格上、
   用什么常量参数、输出标量/向量」当作数据，产出一条可复算的轨迹并记 sha256。
2. ``compare``  — 轨迹/标量一致性代数：变换 (恒等/按参数幂缩放/仿射)、端点
   (稠密极限)一致性、跳变比、闭合残差、退化检测 (驱动量非零而响应恒零)。
3. ``baseline`` — 基线对齐：节点 ``base_commit`` 是否已在 main 祖先链上、领先
   提交数、改动文件；未合并分支强制要求声明接口契约。
4. ``ledger``   — 能力台账合并：证据等级 (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED)
   只能由证据推高、不得越证据自述；honest FAIL 不可被覆盖；产出确定性 sha256。

所有入口 fail-closed：缺字段、缺工件、导入失败、脚本超时一律降级为
``WITHHELD``/``DISAGREE`` 并在产物里留下原因，绝不静默放行。
"""

from __future__ import annotations

__all__ = [
    "model",
    "provenance",
    "schema",
    "sampler",
    "compare",
    "baseline",
    "ledger",
    "report",
    "figures",
    "cli",
]

SCHEMA_VERSION = "tp.integration.gate/v1"