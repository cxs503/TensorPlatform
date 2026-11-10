"""跨域工件集成与回归基线门 (generic mechanism, 零领域语义).

本包只提供机制: 工件清单汇总 / 信封契约校验 (单位·参考系·格式) /
跨生产者契约一致性 / 回归基线比对 / 判据自检 / 报告与作图。
领域内容 (节点清单、量名、单位与基线数值) 一律经 manifest JSON 注入,
代码里不出现任何领域词条。
"""

from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
MANIFEST_SCHEMA = "tp.cdi.manifest/v1"
ENVELOPE_SCHEMA = "tp.cdi.envelope/v1"
REPORT_SCHEMA = "tp.cdi.report/v1"

__all__ = ["SCHEMA_VERSION", "MANIFEST_SCHEMA", "ENVELOPE_SCHEMA", "REPORT_SCHEMA"]