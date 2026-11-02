"""验收入口（薄封装）：实现落在 ``optimizer/audit/optimizer_objective_audit.py``。

本单 EDIT_WHITELIST 只覆盖 optimizer/ tests/ docs/ artifacts/，因此实现写不进
``tools/``；这里只做路径注册与再导出，不含任何判据逻辑（避免两份实现漂移）。

    python3 tools/audit/optimizer_objective_audit.py --check
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimizer.audit.optimizer_objective_audit import (  # noqa: E402,F401
    ART, DEFAULT_REFS, DOC, ERR_GATE, ERR_TIGHT, FIGDIR, ROOT as PKG_ROOT, TRACKS,
    _canon_path, _figures, _fmt, _load_canonical, _md, _mock_path, _rel,
    audit, audit_full, main,
)

if __name__ == "__main__":
    raise SystemExit(main())
