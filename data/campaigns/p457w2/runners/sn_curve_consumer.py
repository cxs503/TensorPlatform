#!/usr/bin/env python3
"""Side B: fatigue-life curve from the *consumer* side implementation (env preset)."""
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("SN_WORKTREE", "m13988")
os.environ.setdefault("SN_MODULE", "backend/physics/spectral_fatigue.py")
os.environ.setdefault("SN_FUNC", "sn_curve_cycles")
sys.path.insert(0, str(HERE))
runpy.run_path(str(HERE / "sn_curve_side.py"), run_name="__main__")
