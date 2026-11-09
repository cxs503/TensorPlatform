#!/usr/bin/env python3
"""四合一写入边界门 CLI（可被 pre-commit 钩子 / CI / 本地自检直接调用）。

用法::

    # 预提交门（读暂存区；pre-commit 钩子即调这条）
    python3 -m tools.write_gate.cli staged --root . --active-repo TensorPlatform \
        --edit-whitelist "tools/write_gate/**,docs/write_gate/**,tests/test_write_gate.py,data/write_gate/**,artifacts/write_gate/**"

    # 按路径判（白名单自检；内容可选）
    python3 -m tools.write_gate.cli paths backend/api/x.py docs/report.md

    # 由 JSON 变更清单判（CI 复用；元素 {path, kind?, content?, repo?}）
    python3 -m tools.write_gate.cli json changes.json

    # 只做凭证扫描（开发自检；不判路径）
    python3 -m tools.write_gate.cli content some_file.txt

退出码：0=PASS，2=FAIL（拦截），3=内部错误（策略/参数错）。
白名单亦可用环境变量 ``EDIT_WHITELIST``（逗号/分号/换行分隔）注入。
BLOCK 时自动写事件到账本（可配置关闭）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # 允许 `python tools/write_gate/cli.py` 直跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.write_gate import gate, ledger as ledger_mod, policy as policy_mod, secrets as secrets_mod  # type: ignore
    from tools.write_gate.model import Change, WriteGateError  # type: ignore
    from tools.write_gate.whitelist import whitelist_from_text  # type: ignore
else:  # pragma: no cover
    from . import gate, ledger as ledger_mod, policy as policy_mod, secrets as secrets_mod
    from .model import Change, WriteGateError
    from .whitelist import whitelist_from_text

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO_ROOT / "data" / "write_gate" / "policy.json"
EXIT_PASS, EXIT_BLOCK, EXIT_INTERNAL = 0, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="write_gate", description="四合一写入边界门")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="策略 JSON 路径")
    parser.add_argument("--root", default=".", help="工作树根（跨仓判据用）")
    parser.add_argument("--active-repo", default=None, help="本工作树所属仓名（跨仓判据用）")
    parser.add_argument("--edit-whitelist", default=None,
                        help="覆盖白名单（glob，逗号分隔）；缺省读 $EDIT_WHITELIST")
    parser.add_argument("--json", dest="json_out", default=None, help="判决 JSON 输出路径")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--quiet", action="store_true", help="只在拦截时输出摘要")
    parser.add_argument("--no-ledger", action="store_true", help="不写事件账本")
    parser.add_argument("--ledger-path", default=None, help="账本路径（覆盖策略配置）")
    parser.add_argument("--context", default=None, help="附加上下文 JSON（如 mission_id）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("staged", help="判暂存区变更（pre-commit 主入口）")
    p_paths = sub.add_parser("paths", help="判给定路径")
    p_paths.add_argument("paths", nargs="+")
    p_json = sub.add_parser("json", help="判 JSON 变更清单")
    p_json.add_argument("file")
    p_content = sub.add_parser("content", help="只做凭证扫描")
    p_content.add_argument("file")
    return parser


def _load_cfg(args) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    whitelist_text = args.edit_whitelist or os.environ.get("EDIT_WHITELIST", "")
    if whitelist_text.strip():
        overrides["whitelist"] = whitelist_from_text(whitelist_text)
    return policy_mod.load_policy(args.policy, overrides=overrides)


def _emit(verdict, args) -> int:
    payload = verdict.to_json()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if args.format == "json" or args.json_out and args.format != "text":
        print(text)
    elif not getattr(args, "quiet", False):
        print(render_text(payload))
    return EXIT_PASS if payload["verdict"] == "PASS" else EXIT_BLOCK


def _record_ledger(verdict, cfg, args) -> None:
    """BLOCK 时写事件到账本（失败不阻塞）。"""
    if args.no_ledger:
        return
    if not cfg.get("ledger", {}).get("enabled", True):
        return
    if verdict.verdict != "FAIL":
        return
    ledger_path = args.ledger_path or cfg.get("ledger", {}).get("path", "runs/write_gate/events.jsonl")
    # 相对路径基于 REPO_ROOT
    if not Path(ledger_path).is_absolute():
        ledger_path = str(REPO_ROOT / ledger_path)
    context = None
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError:
            pass
    ledger_mod.record_verdict(verdict, ledger_path, context=context)


def render_text(payload: Dict[str, Any]) -> str:
    """人读判决摘要（不打印凭证原文，只打印脱敏片段）。"""
    lines: List[str] = [
        f"write-gate: {payload['verdict']} ({payload['decision']}) "
        f"changes={payload['totals']['changes']} blocked={payload['totals']['blocked']} "
        f"{payload['totals']['by_category']}",
    ]
    for v in payload["violations"][:40]:
        where = v["path"] + (f":{v['line']}" if "line" in v else "")
        evidence = v.get("evidence_sha256", "")[:8] if v.get("evidence_sha256") else ""
        lines.append(f"  BLOCK [{v['category']}/{v['code']}] {where} "
                     f"rule={v['rule_id']} {v['detail']}"
                     + (f" match={v['excerpt']}" if v.get("excerpt") else "")
                     + (f" evidence={evidence}" if evidence else ""))
    if len(payload["violations"]) > 40:
        lines.append(f"  ... {len(payload['violations']) - 40} more")
    if payload["advisories"]:
        lines.append(f"  advisories={len(payload['advisories'])}（豁免/跳过留痕，见 JSON）")
    lines.append(f"  digest={payload['digest'][:16]}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = _load_cfg(args)
    except (WriteGateError, json.JSONDecodeError) as exc:
        print(f"write-gate: internal error (policy): {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    try:
        if args.command == "staged":
            verdict = gate.evaluate_staged(args.root, cfg, active_repo=args.active_repo,
                                           policy_path=args.policy)
        elif args.command == "paths":
            verdict = gate.evaluate_paths(args.paths, cfg, root=args.root,
                                          active_repo=args.active_repo, policy_path=args.policy)
        elif args.command == "json":
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            items = data["changes"] if isinstance(data, dict) else data
            changes = [Change(path=str(item["path"]), kind=str(item.get("kind", "MODIFY")),
                              content=item.get("content"), repo=item.get("repo"), source="json")
                       for item in items]
            verdict = gate.evaluate_changes(changes, cfg, root=args.root,
                                            active_repo=args.active_repo, policy_path=args.policy)
        else:  # content
            text = Path(args.file).read_text(encoding="utf-8", errors="replace")
            hits, suppressed = secrets_mod.scan_text(text, cfg, path=str(args.file))
            payload = {"schema": "tp.write_gate.secret_scan/v1", "path": str(args.file),
                       "hits": [h.__dict__ for h in hits], "suppressed": suppressed}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return EXIT_BLOCK if hits else EXIT_PASS
    except WriteGateError as exc:
        print(f"write-gate: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    _record_ledger(verdict, cfg, args)
    return _emit(verdict, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
