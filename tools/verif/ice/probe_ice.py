"""冰区航行端点探测（只读探针）：参数扫描 + 原始响应对照工件落盘。

端点：POST http://127.0.0.1:8001/api/v1/extended-calculations/ice
用法：python3 tools/verif/ice/probe_ice.py --out artifacts/m1517/ice_probe.json
"""
from __future__ import annotations

import argparse
import json
import urllib.request

BASE = "http://127.0.0.1:8001/api/v1/extended-calculations/ice"

BASE_CASE = {
    "length": 150.0, "beam": 23.0, "draft": 8.5, "displacement": 25000.0,
    "speed": 5.0, "cb": 0.75, "ice_class": "PC7", "ice_thickness": 1.0,
}


def call(payload: dict) -> dict:
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def variants():
    out = [("base", {})]
    for h in (0.3, 0.5, 0.75, 1.0, 1.5, 2.0):
        out.append((f"h={h}", {"ice_thickness": h}))
    for v in (1.0, 3.0, 5.0, 7.0, 10.0):
        out.append((f"V={v}", {"speed": v}))
    for c in ("PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7"):
        out.append((f"class={c}", {"ice_class": c}))
    for d in (10000.0, 25000.0, 50000.0):
        out.append((f"disp={d}", {"displacement": d}))
    for b in (18.0, 23.0, 30.0):
        out.append((f"B={b}", {"beam": b}))
    for L in (100.0, 150.0, 200.0):
        out.append((f"L={L}", {"length": L}))
    for T in (6.0, 8.5, 12.0):
        out.append((f"T={T}", {"draft": T}))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/m1517/ice_probe.json")
    a = ap.parse_args()
    rows = []
    for name, patch in variants():
        p = dict(BASE_CASE)
        p.update(patch)
        try:
            r = call(p)
        except Exception as e:  # noqa: BLE001
            r = {"error": repr(e)}
        rows.append({"variant": name, "input": p, "response": r})
        print(name, json.dumps(r, ensure_ascii=False))
    json.dump({"endpoint": BASE, "base_case": BASE_CASE, "rows": rows},
              open(a.out, "w"), ensure_ascii=False, indent=1)
    print("saved", a.out)


if __name__ == "__main__":
    main()
