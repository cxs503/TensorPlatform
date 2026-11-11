"""``python -m verification_vitality`` 入口。"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())