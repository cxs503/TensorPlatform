"""命令行入口：``scan`` / ``freeze`` / ``gate`` / ``apply``。

::

    # 1) 扫描（dry-run：只出账本与处置计划，不动 ref）
    python3 -m tools.delivery_hygiene.cli scan --repo . --out artifacts/delivery_hygiene --figures

    # 2) 冻结基线（棘轮：ceiling 只能降）
    python3 -m tools.delivery_hygiene.cli freeze --repo . \
        --baseline config/delivery_hygiene_baseline.json --min-reduction 1

    # 3) 门裁决（CI 用，非 0 退出即拦截）
    python3 -m tools.delivery_hygiene.cli gate --repo . \
        --baseline config/delivery_hygiene_baseline.json --strict-new

    # 4) 真正执行处置（默认 dry-run；必须两次确认）
    python3 -m tools.delivery_hygiene.cli scan --repo . --out /tmp/dh \
        --apply --i-mean-it
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import CLASS_A, CLASS_B, CLASS_C, CLASS_D, CLASSES, CLASS_LABELS
from . import classify as classify_mod
from . import dispose, gate as gate_mod, gitio, ledger as ledger_mod


# ── 共用 ───────────────────────────────────────────────────────────────────────
def _scan(repo: str, base: str, pattern: str, include_remote: bool,
          with_files: bool = True, limit: Optional[int] = None,
          crosscheck: int = 0, squash_detect: bool = True,
          label: Optional[str] = None) -> Dict[str, Any]:
    refs = gitio.list_refs(repo, pattern, include_remote)
    if limit:
        refs = refs[:limit]
    facts = classify_mod.classify(repo, base, refs, with_files=with_files,
                                  squash_detect=squash_detect)
    lbl = label or gitio.repo_label(repo)
    rows = [ledger_mod.build_row(lbl, f) for f in facts]
    xc = (classify_mod.crosscheck(repo, base, refs, crosscheck)
          if crosscheck > 0 else None)
    return ledger_mod.build_ledger(repo, lbl, base, pattern, rows,
                                   include_remote=include_remote, engine_crosscheck=xc)


def read_json_maybe_gz(path: str) -> Dict[str, Any]:
    """读 JSON（支持 ``.gz``）：账本逐行证据很大，入库时压成 gz 省仓容，但仍可被门/冻结直接消费。"""
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_maybe_gz(path: str, payload: Dict[str, Any]) -> None:
    if path.endswith(".gz"):
        import gzip
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.write("\n")
        return
    ledger_mod.write_json(path, payload)


def _load_ledger(args: argparse.Namespace) -> Dict[str, Any]:
    if getattr(args, "ledger", None):
        return read_json_maybe_gz(args.ledger)
    return _scan(args.repo, args.base, args.pattern, args.include_remote,
                 with_files=False)


def _load_baseline(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": gate_mod.GATE_SCHEMA_VERSION, "repos": {}}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── scan ───────────────────────────────────────────────────────────────────────
def cmd_scan(args: argparse.Namespace) -> int:
    led = _scan(args.repo, args.base, args.pattern, args.include_remote,
                with_files=not args.no_files, limit=args.limit,
                crosscheck=getattr(args, "crosscheck", 0),
                squash_detect=not getattr(args, "no_squash_detect", False),
                label=getattr(args, "label", None))
    date_tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan = dispose.plan_dispositions(led, date_tag=date_tag, archive_b=not args.no_archive_b)
    led["dispositions"] = plan
    led["disposition_counts"] = dispose.summarize_plan(plan)

    out = args.out
    args.ledger_out = args.ledger_out or os.path.join(out, "ledger.json")
    write_json_maybe_gz(args.ledger_out, led)
    ledger_mod.write_text(os.path.join(out, "ledger.md"), ledger_mod.render_markdown(led))
    ledger_mod.write_json(os.path.join(out, "disposition_plan.json"),
                          {"schema_version": "1.0", "repo": led["repo"], "date_tag": date_tag,
                           "counts": led["disposition_counts"], "plan": plan})
    ticket_paths = dispose.write_tickets(led, os.path.join(out, "tickets"))
    # 聚合清单：一分支一行，便于任务机批量收养，也避免上万小文件进 git
    ledger_mod.write_text(os.path.join(out, "tickets.jsonl"),
                          "".join(json.dumps(t, ensure_ascii=False) + "\n"
                                  for t in led["tickets"]))

    manifest = None
    if args.apply:
        if not args.i_mean_it:
            raise SystemExit("[delivery_hygiene] --apply 需要同时给 --i-mean-it（会改 ref）")
        manifest = dispose.apply_plan(args.repo, plan, date_tag, apply=True,
                                      archive_prefix=args.archive_prefix,
                                      manifest_path=os.path.join(out, "apply_manifest.json"))
    if args.figures:
        try:
            write_figures(led, out)
        except Exception as exc:  # 画图失败不该毁掉账本
            print(f"[delivery_hygiene] figures skipped: {exc}", file=sys.stderr)

    s = led["stats"]
    print(f"[delivery_hygiene] repo={led['repo']} branches={s['total_branches']} "
          f"A={s['by_class'][CLASS_A]} B={s['by_class'][CLASS_B]} "
          f"C={s['by_class'][CLASS_C]} D={s['by_class'][CLASS_D]} "
          f"unmerged={s['unmerged']} tickets={len(led['tickets'])} "
          f"plan={led['disposition_counts']}")
    print(f"[delivery_hygiene] out={os.path.abspath(out)} tickets_written={len(ticket_paths)}")
    if manifest is not None:
        print(f"[delivery_hygiene] applied={manifest['counts']['applied']} "
              f"failed={manifest['counts']['failed']} (manifest written)")
    return 0


# ── freeze ─────────────────────────────────────────────────────────────────────
def cmd_freeze(args: argparse.Namespace) -> int:
    led = _load_ledger(args)
    previous = _load_baseline(args.baseline)
    updated = gate_mod.freeze_baseline(led, previous, min_reduction=args.min_reduction,
                                       accept_increase=args.accept_increase,
                                       allowance=args.allowance)
    ledger_mod.write_json(args.baseline, updated)
    entry = updated["repos"][led["repo"]]
    print(f"[delivery_hygiene] frozen repo={led['repo']} unmerged={entry['baseline_unmerged']} "
          f"ceiling={entry['ceiling']} base_sha={entry['base_sha'][:12]} -> {args.baseline}")
    return 0


# ── gate ───────────────────────────────────────────────────────────────────────
def cmd_gate(args: argparse.Namespace) -> int:
    led = _load_ledger(args)
    baseline = _load_baseline(args.baseline)
    result = gate_mod.evaluate_gate(led, baseline, strict_new=args.strict_new,
                                    grace_days=args.grace_days)
    print(gate_mod.render_gate_summary(result))
    if args.json_out:
        ledger_mod.write_json(args.json_out, result)
    return int(result.get("exit_code", 1))


# ── apply ──────────────────────────────────────────────────────────────────────
def cmd_apply(args: argparse.Namespace) -> int:
    with open(args.plan_file, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    plan = payload.get("plan", payload)
    date_tag = payload.get("date_tag") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest = dispose.apply_plan(args.repo, plan, date_tag, apply=args.i_mean_it,
                                  archive_prefix=args.archive_prefix,
                                  manifest_path=args.manifest)
    print(f"[delivery_hygiene] apply={manifest['apply']} planned={manifest['counts']['planned']} "
          f"applied={manifest['counts']['applied']} failed={manifest['counts']['failed']}")
    return 0 if manifest["counts"]["failed"] == 0 else 1


# ── 图 ─────────────────────────────────────────────────────────────────────────
def write_figures(led: Dict[str, Any], out_dir: str) -> List[str]:
    """两张图：① 四类分布 ② 按 tip 月份的未合并趋势（熵的累积曲线）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    s = led["stats"]
    counts = [s["by_class"][c] for c in CLASSES]
    labels = [f"{c}\n{CLASS_LABELS[c]}" for c in CLASSES]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(range(len(counts)), counts,
                  color=["#2e7d32", "#1565c0", "#c62828", "#616161"])
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("branches")
    ax.set_title(f"Delivery hygiene classes — {led['repo']} "
                 f"({s['total_branches']} refs vs {led['base']}@{led['base_sha'][:8]})")
    for b, v in zip(bars, counts):
        ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.axhline(s["unmerged"], color="#c62828", ls="--", lw=1,
               label=f"unmerged (B+C) = {s['unmerged']}")
    ax.legend(fontsize=8)
    p1 = os.path.join(out_dir, "fig_class_distribution.png")
    fig.tight_layout()
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    months = sorted(s.get("by_month", {}).keys())
    if months:
        series = {c: [s["by_month"][m].get(c, 0) for m in months] for c in (CLASS_B, CLASS_C)}
        fig, ax = plt.subplots(figsize=(8.0, 4.2))
        ax.bar(months, series[CLASS_C], label="C undelivered", color="#c62828")
        ax.bar(months, series[CLASS_B], bottom=series[CLASS_C], label="B superset-covered",
               color="#1565c0")
        ax.set_ylabel("branches by tip month")
        ax.set_title(f"Unmerged backlog by branch tip month — {led['repo']}")
        ax.tick_params(axis="x", labelrotation=70, labelsize=7)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p2 = os.path.join(out_dir, "fig_unmerged_by_month.png")
        fig.savefig(p2, dpi=150)
        plt.close(fig)
        paths.append(p2)
    return paths


# ── parser ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="delivery_hygiene",
                                 description="未合并 agent 分支的四类等价判定 + 处置 + 只减不增门")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", default=".", help="仓库路径")
        p.add_argument("--base", default="main", help="主干分支名")
        p.add_argument("--pattern", default="agent/*", help="分支 ref 模式（refs/heads/<pattern>）")
        p.add_argument("--include-remote", action="store_true", help="同时扫 origin/<pattern>")
        p.add_argument("--ledger", default=None, help="直接读已有 ledger.json（跳过扫描）")
        p.add_argument("--label", default=None,
                       help="账本里的仓库标签（默认取路径 basename；worktree 场景用来锁定仓名）")

    p = sub.add_parser("scan", help="扫描并出账本 + 处置计划（默认 dry-run）")
    common(p)
    p.add_argument("--out", default="artifacts/delivery_hygiene", help="输出目录")
    p.add_argument("--ledger-out", default=None,
                   help="账本路径（默认 <out>/ledger.json；以 .gz 结尾则 gzip）")
    p.add_argument("--no-files", action="store_true", help="不统计改动文件（更快）")
    p.add_argument("--no-squash-detect", action="store_true",
                   help="关闭 squash 合成 patch-id 判定（更快，但未合并数会被高估）")
    p.add_argument("--no-archive-b", action="store_true", help="B 类不归档（只标注）")
    p.add_argument("--limit", type=int, default=None, help="只扫前 N 条（调试）")

    p.add_argument("--date-tag", default=None, help="归档 ref 的日期标签")
    p.add_argument("--archive-prefix", default="refs/attic")
    p.add_argument("--crosscheck", type=int, default=12,
                   help="抽样 N 条用 git cherry 引擎复算分类（0=关；默认 12）")
    p.add_argument("--figures", action="store_true", help="出分布/趋势图")
    p.add_argument("--apply", action="store_true", help="真的执行归档+删分支")
    p.add_argument("--i-mean-it", action="store_true", help="二次确认：会改 ref")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("freeze", help="冻结/更新基线（棘轮：ceiling 只降）")
    common(p)
    p.add_argument("--baseline", default="config/delivery_hygiene_baseline.json")
    p.add_argument("--min-reduction", type=int, default=0, help="本次至少下降多少")
    p.add_argument("--allowance", type=int, default=None, help="并发在建分支豁免额度")
    p.add_argument("--accept-increase", default=None, help="显式接受抬高基线的理由（留痕）")
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser("gate", help="门裁决（非 0 退出=拦截）")
    common(p)
    p.add_argument("--baseline", default="config/delivery_hygiene_baseline.json")
    p.add_argument("--strict-new", action="store_true", help="冻结后新增 C 类直接失败")
    p.add_argument("--grace-days", type=int, default=0)
    p.add_argument("--json-out", default=None)
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("apply", help="执行既有处置计划")
    p.add_argument("--repo", default=".")
    p.add_argument("--plan-file", required=True)
    p.add_argument("--archive-prefix", default="refs/attic")
    p.add_argument("--manifest", default=None)
    p.add_argument("--i-mean-it", action="store_true")
    p.set_defaults(func=cmd_apply)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())