#!/usr/bin/env python3
"""由 ``open_water_probe.json`` 生成敞水探针图（零手抄：图上的数全部读工件）。"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main(art: str, out: str) -> int:
    d = json.load(open(art, encoding="utf-8"))
    rows = [r for r in d["sweep"] if r["ok"]]
    js = [r["J"] for r in rows]
    kt = [r["KT"] for r in rows]
    kq = [r["KQ"] for r in rows]
    eta = [r["eta0"] for r in rows]
    # 定义式期望：eta = J*KT/(2*pi*KQ)
    import math
    expect = [j * a / (2 * math.pi * b) for j, a, b in zip(js, kt, kq)]

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0))
    ax = axes[0]
    ax.plot(js, kt, "o-", color="#3d6fa8", label="KT (endpoint)")
    ax.plot(js, [10 * v for v in kq], "s--", color="#a8483d", label="10 x KQ (endpoint)")
    ax.set_xlabel("advance ratio J")
    ax.set_ylabel("coefficient")
    ax.set_title("KT and 10/KQ coincide: KQ = KT/10 at every J")
    ax.legend()
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(js, eta, "o-", color="#2f7d4f", label="eta0 reported")
    ax.plot(js, expect, "--", color="#444444", label="eta0 from J*KT/(2*pi*KQ)")
    ax.axhline(0.85, color="#a8483d", lw=1, ls=":", label="hard cap 0.85")
    ax.set_xlabel("advance ratio J")
    ax.set_ylabel("open-water efficiency eta0")
    ax.set_title(f"Identity check: worst rel. error {d['checks'][0]['worst_rel_err_pct']}%")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.suptitle("Open-water endpoint debt probe (:8001 /api/v1/propeller-design/wageningen-b)", fontsize=10)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fig.savefig(out, dpi=150)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
