#!/usr/bin/env python3
"""Scan: reports titles + missions (self-start) to avoid duplication."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools_api_probe import login, req  # noqa: E402

KW = ["推进", "螺旋桨", "propeller", "振动", "噪声", "noise", "冰", "ice", "水动力", "耐波", "稳性",
      "阻力", "操纵", "绿色", "结构"]

tok = login()
st, d = req("GET", "tensor/reports?limit=200", token=tok)
items = d.get("items") or d.get("data") or []
if isinstance(items, dict):
    items = items.get("items", [])
print("REPORTS:", len(items))
for kw in KW:
    hits = [(r.get("id"), (r.get("title") or "")[:80]) for r in items if kw in (r.get("title") or "")]
    print(f"\n[{kw}] n={len(hits)}")
    for h in hits[:6]:
        print("   ", h[0], h[1])