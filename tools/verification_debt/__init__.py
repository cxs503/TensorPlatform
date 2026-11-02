"""验证欠账（verification debt）台账：把散文式「已清偿」变成机器裁判的台账。

见 tools/verification_debt/ledger.py 与 docs/verification_debt/LEDGER.md。
"""

from .ledger import build_ledger, render_markdown, LEDGER_SCHEMA  # noqa: F401

__all__ = ["build_ledger", "render_markdown", "LEDGER_SCHEMA"]