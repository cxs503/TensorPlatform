#!/usr/bin/env python3
"""Side A: fatigue-life curve from the *producer* side implementation (env preset)."""
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("SN_WORKTREE", "m13989")
os.environ.setdefault("SN_MODULE", "backend/physics/pipe_laying/fatigue.py")
os.environ.setdefault("SN_FUNC", "dnv_rp_c203_d_curve")
sys.path.insert(0, str(HERE))
runpy.run_path(str(HERE / "sn_curve_side.py"), run_name="__main__")
