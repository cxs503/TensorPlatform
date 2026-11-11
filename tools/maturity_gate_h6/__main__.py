"""H6 成熟度门入口：``python3 -m tools.maturity_gate_h6 [run|verify|mutate]``。"""
from __future__ import annotations

import sys

from .cli import main

sys.exit(main())
