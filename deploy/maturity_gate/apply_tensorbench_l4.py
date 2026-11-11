#!/usr/bin/env python3
"""把 maturity_gate 守护进程的 L4 benchmark 判据切到 ``tensorbench_results``（部署补丁）。

用法（协调者部署，非本仓运行时）：
    python3 deploy/maturity_gate/apply_tensorbench_l4.py --target /root/tp-agent/maturity_gate.py --dry-run
    python3 deploy/maturity_gate/apply_tensorbench_l4.py --target /root/tp-agent/maturity_gate.py --apply

改动只有两处，锚点缺失即拒（fail-closed，绝不半patch）：

1. 在 ``def judge(`` 之前注入 ``bench_rows()`` / ``bench_verdict()``（走 psql，与守护同一条
   取证通道，不引入新依赖）；
2. 把 ``elif key == "benchmark" and not has_numbers(v):`` 分支换成
   「有实证行 VERIFIED 才通过，否则 claimed_not_proven / failed」。

判据语义：**有行=实证；无行=自述**。旧分支 ``has_numbers()`` 只看 evidence 里有没有数字，
手写一段「ref 3220.13 got 3225.33 误差 0.16%」即可过 L4（claimed-not-proven 不分）。
"""
from __future__ import annotations

import argparse
import difflib
import pathlib
import shutil
import sys

ANCHOR_FUNCS = "def judge(level, evidence, repo, audit_mid):"
ANCHOR_BRANCH = '            elif key == "benchmark" and not has_numbers(v):\n                miss.append(key + "(无数值对照)")'

INJECT = '''def bench_rows(audit_mid):
    """L4 实证行：tensorbench_results 里该 mission 的平台复算记录（无行=空表）。"""
    try:
        out = q("SELECT metric, ref_value, got_value, rel_error, tolerance_rel, passed, "
                "execution_status, source, cluster_job_id, run_id FROM tensorbench_results "
                f"WHERE mission_id={int(audit_mid)}")
    except Exception as exc:  # 表未建/库不通 → 如实报不可用，不给 L4
        log(f"tensorbench_results 读取失败 m{audit_mid}: {type(exc).__name__}")
        return []
    cols = ("metric", "ref_value", "got_value", "rel_error", "tolerance_rel", "passed",
            "execution_status", "source", "cluster_job_id", "run_id")
    return [dict(zip(cols, r)) for r in out]


def bench_verdict(audit_mid):
    """(VERIFIED|FAILED|CLAIMED_NOT_PROVEN, 理由)。只有 VERIFIED 放行 L4。"""
    rows = bench_rows(audit_mid)
    for r in rows:
        try:
            ok = (str(r["execution_status"]) == "success"
                  and str(r["source"]) == "cluster_job"
                  and r["cluster_job_id"] not in (None, "", "0")
                  and r["rel_error"] not in (None, "")
                  and r["tolerance_rel"] not in (None, "")
                  and float(r["rel_error"]) <= float(r["tolerance_rel"])
                  and str(r["passed"]).lower() in ("t", "true", "1"))
        except (TypeError, ValueError):
            ok = False
        if ok:
            return "VERIFIED", f"{r['metric']} rel={r['rel_error']} run={r['run_id']}"
    if rows:
        return "FAILED", "实证行存在但无一在容差内"
    return "CLAIMED_NOT_PROVEN", "无 tensorbench_results 实证行（L4 仅自述）"


'''

NEW_BRANCH = '''            elif key == "benchmark":
                _bv, _bd = bench_verdict(audit_mid)
                if _bv != "VERIFIED":
                    miss.append(key + f"({_bv}:{_bd})")'''


def patch_text(src: str) -> str:
    if ANCHOR_FUNCS not in src:
        raise SystemExit("anchor 'def judge' not found — refusing to patch (fail-closed)")
    if ANCHOR_BRANCH not in src:
        already = "bench_verdict(audit_mid)" in src
        raise SystemExit("benchmark branch anchor not found"
                         + ("（看起来已打过补丁）" if already else "") + " — refusing to patch")
    if src.count(ANCHOR_BRANCH) != 1 or src.count(ANCHOR_FUNCS) != 1:
        raise SystemExit("anchor not unique — refusing to patch")
    out = src.replace(ANCHOR_FUNCS, INJECT + ANCHOR_FUNCS, 1)
    return out.replace(ANCHOR_BRANCH, NEW_BRANCH, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="/root/tp-agent/maturity_gate.py")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    target = pathlib.Path(args.target)
    src = target.read_text(encoding="utf-8")
    patched = patch_text(src)
    diff = "".join(difflib.unified_diff(src.splitlines(True), patched.splitlines(True),
                                        fromfile=str(target) + " (L4 旧判据：has_numbers)",
                                        tofile=str(target) + " (L4 新判据：tensorbench_results)"))
    if args.dry_run or not args.apply:
        sys.stdout.write(diff)
        print("\n[dry-run] 用 --apply 落盘（自动留 .bak_tb_l4）")
        return 0
    shutil.copy2(target, str(target) + ".bak_tb_l4")
    target.write_text(patched, encoding="utf-8")
    print(f"patched {target} (backup: {target}.bak_tb_l4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
