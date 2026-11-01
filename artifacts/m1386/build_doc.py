#!/usr/bin/env python3
"""Regenerate the numeric content of docs/research/m1386_stability_gz_validation.md
from artifacts/m1386/results.json (numbers are injected, never hand-copied).

Section ①..⑤ tables are produced by artifacts/m1386/analysis.py
(`results_tables.md`); this script refreshes the section ⑥ error-verdict table
in place so that re-running the analysis keeps the document truthful.

Run: python3 artifacts/m1386/build_doc.py
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DOC = os.path.join(REPO, "docs", "research", "m1386_stability_gz_validation.md")
JSON = os.path.join(HERE, "results.json")


def verdict_table(res: dict) -> list[str]:
    cases = {c["case"]: c for c in res["cases"]}
    imo = {i["case"]: i for i in res["imo_indicators"]}
    a, c = cases["A"], cases["C"]
    imoa, imoc = imo["A"], imo["C"]
    in_domain = max(
        max(abs(r["rel_err_pct"]) for r in cs["envelope"]
            if 0 < r["heel_deg"] <= cs["wall_sided_limit_deg"])
        for cs in res["cases"]
    )
    vanish_exact = imoa["phi_vanishing_exact_deg"]
    rows = [
        "| 关键量 | 基准（精确箱体静水力） | 平台值 | 相对误差 | <3%？ |",
        "|---|---|---|---|---|",
        f"| GZ(φ)，φ ≤ φ_lim（案例 A/B/C 全域） | 独立静水力积分 | 壁式闭式解 | "
        f"≤ {in_domain:.1e} % | **是** |",
        f"| GZ(30°) 案例 A（φ_lim={a['wall_sided_limit_deg']:.1f}°） | {imoa['gz_30_exact_m']:.3f} m | "
        f"{imoa['gz_30_wall_m']:.3f} m | {(imoa['gz_30_wall_m']-imoa['gz_30_exact_m'])/imoa['gz_30_exact_m']*100:+.1f} % | "
        f"{'是' if abs(imoa['gz_30_wall_m']-imoa['gz_30_exact_m'])/imoa['gz_30_exact_m'] < 0.03 else '否（域外）'} |",
        f"| GZ(40°) 案例 A | {imoa['gz_40_exact_m']:.3f} m | {imoa['gz_40_wall_m']:.3f} m | "
        f"{(imoa['gz_40_wall_m']-imoa['gz_40_exact_m'])/imoa['gz_40_exact_m']*100:+.1f} % | "
        f"{'是' if abs(imoa['gz_40_wall_m']-imoa['gz_40_exact_m'])/imoa['gz_40_exact_m'] < 0.03 else '否（域外）'} |",
        f"| A30 案例 C（φ_lim={c['wall_sided_limit_deg']:.1f}°） | {imoc['a30_exact_mrad']:.4f} m·rad | "
        f"{imoc['a30_wall_mrad']:.4f} m·rad | "
        f"{(imoc['a30_wall_mrad']-imoc['a30_exact_mrad'])/imoc['a30_exact_mrad']*100:+.1f} % | "
        f"{'是' if abs(imoc['a30_wall_mrad']-imoc['a30_exact_mrad'])/imoc['a30_exact_mrad'] < 0.03 else '否（域外）'} |",
        f"| A40 案例 A | {imoa['a40_exact_mrad']:.4f} m·rad | {imoa['a40_wall_mrad']:.4f} m·rad | "
        f"{(imoa['a40_wall_mrad']-imoa['a40_exact_mrad'])/imoa['a40_exact_mrad']*100:+.1f} % | "
        f"{'是' if abs(imoa['a40_wall_mrad']-imoa['a40_exact_mrad'])/imoa['a40_exact_mrad'] < 0.03 else '否（域外）'} |",
        f"| 消失角 案例 A | {vanish_exact:.1f}° | 不存在（GZ 单调增） | 定性错误 | 否（域外） |",
        "| 等排水量守恒（参考解自检，测试断言） | B·T | B·T | ≤ 1e-10 | 是 |",
    ]
    return rows


def main() -> None:
    res = json.load(open(JSON))
    doc = open(DOC).read().splitlines()
    start = next(i for i, ln in enumerate(doc) if ln.startswith("| 关键量 | 基准"))
    end = next(i for i, ln in enumerate(doc) if ln.startswith("结论："))
    new = doc[:start] + verdict_table(res) + [""] + doc[end:]
    open(DOC, "w").write("\n".join(new) + "\n")
    print(f"section 6 table refreshed: {end - start} lines -> {len(verdict_table(res))}")


if __name__ == "__main__":
    main()