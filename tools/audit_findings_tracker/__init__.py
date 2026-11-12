"""交差审计发现结构化追踪（audit-findings tracker）。

把「审计报告里的发现」变成**机器可读、可计数、可拦截**的结构化台账：

* 输入：任意形态的审计报告 JSON（15311/15312 等交差审计产物）+ 分类法 schema
  （类别词表 / 别名词表 / 容器键 / 分类字段全部工件注入，代码零领域语义）；
* 输出：统一 findings 记录（类别、来源指针、匹配范围、严重度、主体、原因码），
  以及逐类别计数、布局信息、输入 sha256；
* 退出码：``0=PASS``（发现已结构化）/ ``1=FAIL``（解析失败或发现未识别）。

五类发现（可执行词表，非观点）：``fake_pass`` / ``hand_copy`` / ``label_mismatch`` /
``undefined_criteria`` / ``other``。任何无法归类的条目降级为 ``WITHHELD`` 并令退出码为 1
——「没识别出来」不许静默算通过。

设计边界：纯函数、无时钟、无随机、无网络（同输入必同输出）。
"""

from .core import (  # noqa: F401
    CANONICAL_CATEGORIES,
    DEFAULT_SCHEMA_PATH,
    REASON_CODES,
    WITHHELD,
    TrackerError,
    analyze_report,
    load_schema,
    render_summary,
)

__all__ = [
    "CANONICAL_CATEGORIES",
    "DEFAULT_SCHEMA_PATH",
    "REASON_CODES",
    "WITHHELD",
    "TrackerError",
    "analyze_report",
    "load_schema",
    "render_summary",
]