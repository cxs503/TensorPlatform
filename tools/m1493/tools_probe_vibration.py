#!/usr/bin/env python3
"""Absence probe: candidate vibration/noise endpoint paths on the live ship platform.

Read-only. Writes JSON evidence to artifacts/m1493/
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools_api_probe import login, req  # noqa: E402

CANDIDATES = [
    "calculations/vibration",
    "calculations/noise",
    "calculations/vibration/beam_natural",
    "calculations/vibration/plate_natural",
    "calculations/noise/panel_tl",
    "calculations/acoustics",
    "calculations/vibration/modal",
    "calculations/noise/cabin_spl",
]

# control paths that are documented as REAL (must answer 200/422, else probe is broken)
CONTROLS = ["calculations/stability", "calculations/resistance"]

tok = login()
out = {"api_base": os.environ.get("TENSOR_API_BASE"), "candidates": {}, "controls": {}}
for p in CANDIDATES:
    st, d = req("GET", p, token=tok)
    out["candidates"][p] = {"status": st, "body": (d if isinstance(d, str) else json.dumps(d, ensure_ascii=False))[:160]}
for p in CONTROLS:
    st, d = req("GET", p, token=tok)
    out["controls"][p] = {"status": st}

print(json.dumps(out, ensure_ascii=False, indent=2))
json.dump(out, open("/tmp/vib_probe.json", "w"), ensure_ascii=False, indent=2)