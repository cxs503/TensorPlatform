"""能力证据台账 CLI（扫描 → 复算 → 落盘 → 发布 → 查询）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="capability_ledger",
        description="能力证据台账：从注册表模式 + 报告元数据复算 21 条能力等级（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）",
    )
    ap.add_argument("--root", default=None, help="仓库根（默认按本文件定位）")
    ap.add_argument("--config", default=None, help="管线配置路径（默认 config/capability_ledger.v1.json）")
    ap.add_argument("--recompute", action="store_true", help="复算台账并打印（可加 --write 落盘）")
    ap.add_argument("--write", action="store_true", help="把复算产物写到 ledger_output_path")
    ap.add_argument("--out", default=None, help="覆盖落盘路径")
    ap.add_argument("--publish", action="store_true", help="以 harness schema 发布到平台期望路径（fail-soft）")
    ap.add_argument("--publish-path", default=None, help="覆盖平台发布路径")
    ap.add_argument("--selfcheck", action="store_true", help="结构校验 + 两次复算一致 + 与落盘产物一致")
    ap.add_argument("--query", action="store_true", help="查询落盘台账（缺失时 fail-soft 降级返回 21 条）")
    ap.add_argument("--sync", action="store_true", help="从 Tensor 报告 API 采集元数据快照")
    ap.add_argument("--from", dest="mfrom", type=int, default=None, help="mission 扫描窗口下界（--sync）")
    ap.add_argument("--to", dest="mto", type=int, default=None, help="mission 扫描窗口上界（--sync）")
    ap.add_argument("--no-merge", action="store_true", help="--sync 不合并既有快照")
    ap.add_argument("--capability", default=None, help="只输出指定 capability_id 的明细")
    ap.add_argument("--level", default=None, help="按等级过滤（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from capability_ledger import engine, sources  # noqa: E402

    root = engine.repo_root(args.root)
    cfg = engine.load_config(args.config, root)

    if args.selfcheck:
        report = engine.selfcheck(cfg, root)
        print(_dumps(report))
        return 0 if report["status"] == "ok" else 1

    if args.sync:
        res = sources.sync(cfg, root, lo=args.mfrom, hi=args.mto, merge=not args.no_merge)
        print(_dumps(res))
        return 0 if res.get("status") == "ok" else 0  # fail-soft：采集失败也不失败退出

    if args.query:
        res = engine.query(cfg=cfg, root=root)
        caps = res["capabilities"]
        if args.capability:
            caps = [c for c in caps if c["capability_id"] == args.capability]
        if args.level:
            caps = [c for c in caps if c["evidence"] == args.level]
        res = dict(res, capabilities=caps, capability_count=len(caps))
        print(_dumps(res))
        return 0

    # 默认：--recompute（无参数时等价）
    ledger = engine.build_ledger(cfg, root)
    out_path = args.out or cfg.get("ledger_output_path")
    written = None
    if args.write or args.out:
        p = Path(out_path)
        if not p.is_absolute():
            p = root / p
        written = engine.write_artifact(ledger, p)

    published = None
    if args.publish:
        published = engine.publish_to_platform(ledger, cfg, root, args.publish_path)

    caps = ledger["capabilities"]
    if args.capability:
        caps = [c for c in caps if c["capability_id"] == args.capability]
    if args.level:
        caps = [c for c in caps if c["evidence"] == args.level]
    payload = {
        "status": "ok" if not ledger.get("warnings") else "degraded",
        "schema_version": ledger["schema_version"],
        "as_of": ledger["as_of"],
        "sha256": ledger["sha256"],
        "capability_count": len(ledger["capabilities"]),
        "level_distribution": ledger["level_distribution"],
        "warnings": ledger.get("warnings", []),
        "written": written,
        "published": published,
        "capabilities": [
            {
                "capability_id": c["capability_id"],
                "evidence": c["evidence"],
                "evidence_count": c["evidence_count"],
                "grade_source": c["grade_source"],
                "registry_level": c["registry_level"],
                "reason": c["reason"],
                "matched_reports": c["matched_reports"],
            }
            for c in caps
        ],
    }
    print(_dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())