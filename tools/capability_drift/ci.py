"""CI 触发入口：PR/push 后按变更文件跑受影响能力的回归检测，退化则非零退出。

示例（GitHub Actions / 本地均可）::

    python3 -m tools.capability_drift.ci --registry-dir capdrift --base HEAD~1 --head HEAD
    python3 -m tools.capability_drift.ci --registry-dir capdrift --changed-files-file changed.txt

退出码：0 = 无退化（含"无受影响能力"）；1 = 检出退化；2 = 运行/配置错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import alerts, dashboard, model, pipeline, trigger


def _changed(args: argparse.Namespace) -> List[str]:
    if args.changed_files_file:
        text = Path(args.changed_files_file).read_text(encoding="utf-8")
        return [line.strip() for line in text.splitlines() if line.strip()]
    if args.changed_files:
        return [p for p in args.changed_files.split(",") if p]
    return list(trigger.changed_files(args.repo, args.base, args.head))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="capability drift monitor (CI trigger)")
    ap.add_argument("--registry-dir", required=True, help="capdrift/ 目录（含 registry/gates/baseline）")
    ap.add_argument("--repo", default=".", help="被检测的仓库根（同时也是 git 仓）")
    ap.add_argument("--base", default="HEAD~1")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--changed-files", default="", help="逗号分隔的变更文件（覆盖 git 推断）")
    ap.add_argument("--changed-files-file", default="", help="每行一个变更文件")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--threshold-scale", type=float, default=1.0, help="阈值缩放（>1 放宽，调试用）")
    ap.add_argument("--history", default="", help="健康度历史 jsonl 追加路径")
    ap.add_argument("--events", default="", help="告警事件 jsonl 追加路径")
    ap.add_argument("--post-event", action="store_true", help="尝试投递平台事件（失败不阻塞）")
    ap.add_argument("--json", default="", help="把本轮结果写到该 JSON 文件")
    args = ap.parse_args(argv)

    try:
        bundle = model.load_bundle(args.registry_dir, repo_root=args.repo)
    except model.RegistryError as exc:
        print(f"[capdrift] registry error: {exc}", file=sys.stderr)
        return 2
    changed = _changed(args)
    run = pipeline.monitor(
        bundle,
        changed_files=changed,
        workers=args.workers,
        timeout=args.timeout,
        threshold_scale=args.threshold_scale,
        repo_root=args.repo,
    )
    suspicions = pipeline.attribute_run(run, bundle)
    event = alerts.build_event(
        run.report,
        suspicions,
        run_meta={
            "changed_files": list(changed),
            "scope": list(run.scope),
            "elapsed_s": round(run.elapsed_s, 3),
            "repo": str(Path(args.repo).resolve()),
        },
    )
    if run.skipped:
        print("[capdrift] no affected capability (documentation-only change) — nothing to rerun")
    else:
        print(f"[capdrift] scope={len(run.scope)} caps, elapsed={run.elapsed_s:.1f}s, "
              f"health={event['health']}")
        for d in event["drifted"]:
            print(f"[capdrift] DRIFT {d['capability']}::{d['metric']} "
                  f"baseline={d['baseline']:.6g} observed={d['observed']:.6g} "
                  f"rel_err={d['rel_err'] * 100:.2f}% (gate {d['threshold'] * 100:.2f}%)")
            for s in d["suspected_commits"][:3]:
                print(f"[capdrift]   suspected {s['short']} {s['subject']}")
    if args.history:
        dashboard.history_append(
            args.history,
            dashboard.history_record(run.report, changed=changed, duration_s=run.elapsed_s,
                                     extra={"source": "ci"}),
        )
    if args.events:
        alerts.emit(event, args.events)
    if args.post_event:
        delivery = alerts.post_event(event)
        print(f"[capdrift] event delivery: {json.dumps(delivery, ensure_ascii=False)}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"event": event, "run": run.as_dict()}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 1 if event["severity"] == "red" else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())