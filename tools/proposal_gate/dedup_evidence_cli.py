#!/usr/bin/env python3
"""去重证据复算门 CLI：取证 / 复算 / 判定（可挂进 CI 与收养流程）。

用法::

    # 1) 取证（提案侧，写进提案的 dedup_evidence 字段）
    python3 tools/proposal_gate/dedup_evidence_cli.py bootstrap \
        --snapshot artifacts/m9953/snapshot/snapshot.json \
        --text-file /tmp/proposal.txt --out /tmp/evidence.json

    # 2) 复算（收养侧，fail-closed）
    python3 tools/proposal_gate/dedup_evidence_cli.py verify \
        --snapshot artifacts/m9953/snapshot/snapshot.json --evidence /tmp/evidence.json

退出码：0 = ACCEPT/DEEPEN（一致），2 = REJECT（原因码见输出），1 = 用法/IO 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.proposal_gate.dedup_evidence import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    VERDICT_REJECT,
    EvidenceConfig,
    JsonSnapshotSource,
    build_evidence,
    enforce_adoption,
    specs_for_proposal,
    verify_evidence,
)


def _load_config(path: str | None) -> EvidenceConfig:
    return EvidenceConfig.load(path or DEFAULT_CONFIG_PATH)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
    if not text:
        print("缺少 --text-file / --text", file=sys.stderr)
        return 1
    source = JsonSnapshotSource(args.snapshot)
    specs = specs_for_proposal(text, cfg, query_prefix=args.query_prefix, source=source)
    evidence = build_evidence(source, specs, cfg)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(evidence.to_json(), encoding="utf-8")
    print(json.dumps({"evidence": str(out), "evidence_sha256": evidence.sha256(),
                      "snapshot_sha256": evidence.snapshot.sha256,
                      "specs": [s.query_id for s in specs],
                      "hit_counts": [o.hit_count for o in evidence.outcomes]}, ensure_ascii=False))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    source = JsonSnapshotSource(args.snapshot)
    if args.proposal_json:
        proposal = json.loads(Path(args.proposal_json).read_text(encoding="utf-8"))
        result = enforce_adoption(proposal, source, cfg)
    else:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        result = verify_evidence(evidence, source, cfg)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 2 if result.verdict == VERDICT_REJECT else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="在快照上取证并写证据工件")
    b.add_argument("--snapshot", required=True)
    b.add_argument("--text-file")
    b.add_argument("--text")
    b.add_argument("--query-prefix", default="q")
    b.add_argument("--out", required=True)
    b.set_defaults(func=cmd_bootstrap)

    v = sub.add_parser("verify", help="复算证据并给出判定（fail-closed）")
    v.add_argument("--snapshot", required=True)
    v.add_argument("--evidence")
    v.add_argument("--proposal-json", help="直接给提案 JSON（读其 dedup_evidence 字段）")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())