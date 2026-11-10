"""Test bootstrap: make the repository root (and this directory) importable."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)