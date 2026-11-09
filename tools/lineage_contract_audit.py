#!/usr/bin/env python3
"""谱系继承契约机检 · 真实谱系回放审计（只读）。

对平台库里的真实父子计划跑一遍继承契约门：父计划未偿缺口提取 → 子计划判据覆盖矩阵 →
播种裁决，并把载荷/矩阵/裁决/复算 digest/图 落成可复算工件。

只读纪律：本脚本只发 SELECT（row_to_json），不写任何表；出口在 artifacts/ 下。

用法::

    python tools/lineage_contract_audit.py --parent 173 --child 388 \
        --out-dir artifacts/m8058 --figure artifacts/m8058/coverage_matrix.png
    # 播种路径端到端（与 campaign_dispatcher._validate_new_plan 同一入口）：
    python tools/lineage_contract_audit.py --seeding-check 438 --out-dir artifacts/m8058
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import inheritance_contract as ic  # noqa: E402

PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-t", "-A", "-F", "\x02", "-c"]


def q(sql: str):
    """只读查询：单列 row_to_json 结果（分隔符 \x02 仅作多列兜底）。"""
    proc = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败: {proc.stderr[:400]}")
    return [line.split("\x02") for line in proc.stdout.splitlines() if line.strip()]


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def figure(report: dict, png: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    heat = ic.coverage_heatmap(report)
    matrix = report.get("matrix") or {}
    rows = matrix.get("rows") or []
    cols = heat["node_labels"]
    grid = heat["grid"]
    n_rows, n_cols = len(grid), (len(cols) if cols else 0)
    fig_h = max(3.2, 0.42 * max(n_rows, 1) + 1.8)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.5, fig_h),
                                 gridspec_kw={"width_ratios": [max(3.0, 0.55 * max(n_cols, 1)), 2.6]})
    cmap = LinearSegmentedColormap.from_list("cov", ["#f2f2f2", "#ffd27f", "#4c9f70"])
    if n_rows and n_cols:
        im = ax.imshow(grid, cmap=cmap, vmin=0.0, vmax=1.0, aspect="auto")
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(cols, rotation=45, ha="right", fontsize=7)
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([f"{r['gap_id']}\n[{r['kind']}/{r['severity']}]" for r in rows], fontsize=6)
        for i, line in enumerate(grid):
            for j, v in enumerate(line):
                if v > 0:
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=5.5)
        blocked = [i for i, r in enumerate(rows) if r["intercept"]]
        for i in blocked:
            ax.add_patch(plt.Rectangle((-0.5, i - 0.5), n_cols, 1, fill=False, edgecolor="#b3392b", lw=1.4))
        fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label="coverage")
    ax.set_title("parent gaps (rows) x child criteria (cols)\nred box = uncovered blocking gap", fontsize=8)
    # 右：按 kind 的覆盖/未覆盖堆叠
    kinds = sorted({r["kind"] for r in rows})
    cov = [sum(1 for r in rows if r["kind"] == k and r["status"] == "covered") for k in kinds]
    unc = [sum(1 for r in rows if r["kind"] == k and r["status"] == "uncovered") for k in kinds]
    ax2.barh(kinds, cov, color="#4c9f70", label="covered")
    ax2.barh(kinds, unc, left=cov, color="#b3392b", label="uncovered")
    for i, k in enumerate(kinds):
        ax2.text(cov[i] + unc[i] + 0.1, i, f"{cov[i]+unc[i]}", va="center", fontsize=7)
    ax2.legend(fontsize=7, loc="lower right")
    ax2.set_xlabel("gap count", fontsize=8)
    ax2.set_title(f"gaps by kind · verdict={report.get('verdict')}", fontsize=8)
    s = matrix.get("summary") or {}
    fig.suptitle(f"{title}  |  gaps={s.get('n_gaps')} covered={s.get('n_covered')} "
                 f"uncovered={s.get('n_uncovered')} blocked={s.get('n_intercepted')} "
                 f"digest={(report.get('matrix_digest') or '')[:16]}", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=170)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="谱系继承契约机检 · 真实谱系回放审计（只读）")
    ap.add_argument("--parent", type=int, help="父计划 id")
    ap.add_argument("--child", type=int, help="子计划 id")
    ap.add_argument("--seeding-check", type=int, help="按播种入口跑（child 的 parent_plan 自动发现）")
    ap.add_argument("--config", help="门配置 JSON 路径")
    ap.add_argument("--out-dir", default="artifacts/m8058")
    ap.add_argument("--figure", help="覆盖矩阵 PNG 输出路径")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    env = {"cwd": os.getcwd(), "module": "ops/taskplane/inheritance_contract.py",
           "module_sha256": _sha_file(ROOT / "ops/taskplane/inheritance_contract.py"),
           "git_head": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                      capture_output=True, text=True).stdout.strip(),
           "config": cfg}

    if args.seeding_check:
        child_id = int(args.seeding_check)
        ok, reasons, report = ic.seeding_gate_from_db(child_id, q, cfg)
        _write(out / f"seeding_gate_p{child_id}.json",
               {"seeding_ok": ok, "reasons": reasons, "report": report, "env": env})
        print(json.dumps({"child": child_id, "seeding_ok": ok, "verdict": report.get("verdict"),
                          "reasons": reasons, "digest": report.get("matrix_digest")},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 2

    if not (args.parent and args.child):
        print("需要 --parent 与 --child（或 --seeding-check）", file=sys.stderr)
        return 3
    parent = ic.fetch_plan_payload(int(args.parent), q)
    child = ic.fetch_plan_payload(int(args.child), q)
    _write(out / f"parent_p{args.parent}_payload.json", parent)
    _write(out / f"child_p{args.child}_payload.json", child)

    r1 = ic.evaluate_seeding(parent, child, cfg)
    r2 = ic.evaluate_seeding(json.loads(json.dumps(parent)), json.loads(json.dumps(child)), cfg)
    deterministic = (r1["matrix_digest"] == r2["matrix_digest"]
                     and json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True))
    _write(out / f"gate_p{args.parent}_to_p{args.child}.json",
           {"parent": args.parent, "child": args.child, "report": r1,
            "recompute": {"digest_run1": r1["matrix_digest"], "digest_run2": r2["matrix_digest"],
                          "byte_identical": deterministic},
            "env": env})
    _write(out / f"gate_p{args.parent}_to_p{args.child}.md", ic.render_markdown(r1))
    if args.figure:
        figure(r1, Path(args.figure),
               f"lineage inheritance contract · plan {args.parent} -> {args.child}")
    summary = {
        "parent": args.parent, "child": args.child, "verdict": r1["verdict"],
        "intercepted": r1["intercepted"], "summary": r1["summary"],
        "deterministic": deterministic, "matrix_digest": r1["matrix_digest"],
        "gaps_digest": r1["gaps_digest"], "gate_digest": r1["digest"],
        "payload_sha256": {"parent": hashlib.sha256(
            json.dumps(parent, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "child": hashlib.sha256(json.dumps(child, ensure_ascii=False,
                                               sort_keys=True).encode()).hexdigest()},
        "env": env,
    }
    _write(out / f"audit_p{args.parent}_to_p{args.child}.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())