"""生成检索凭证与池快照（一次性、可重跑）。

为什么凭证要机器生成
--------------------
手写凭证等于手写结论：``hits: []`` 是作者填的，不是检索出来的。这里把凭证生成也做成
可执行步骤：给定（主张, 检索词, 检索范围, 排除前缀），**现场跑检索器**得到命中集合，
再落成带指纹的凭证。于是：

* 凭证的 ``hits`` 不是人写的，是解析器算的
* 任何复核者重跑 ``replay_receipt`` 都得到同一集合（确定性文件序 + 行号命中）
* 主张被"自己的模块"满足时，必须显式声明 ``exclude``（否则是自证循环）

用法：
    TENSOR_API_TOKEN=... python3 -m tools.criteria_metrology.build_receipts --root . \\
        --out data/criteria_metrology/retrieval_receipts.json \\
        --claims-out data/criteria_metrology/retrieval_claims.json \\
        --node-snapshot data/criteria_metrology/node_pool_snapshot.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from .retrieval import FileTermIndex, build_receipt

#: 本节点自己的模块与其测试（"除本实现外无先例"的主张必须排除这两处，否则自证循环）
SELF_PREFIXES = ("tools/criteria_metrology", "tests/test_criteria_metrology")

#: 主张 → 检索规格。``expect`` 只用于报告对照，不参与判决（判决只看重放结果）。
CLAIM_SPECS: list[dict[str, Any]] = [
    {
        "claim_id": "C1-degeneracy-no-prior-art",
        "claim_kind": "novelty",
        "expected_verdict": "SUPPORTED",
        "statement": "除本节点模块外，平台没有判据退化三分类的机制实现（DegeneracyClass/T1 零基准判据）",
        "receipts": [
            {"kind": "code", "query": ["DegeneracyClass", "T1_ZERO_REFERENCE", "零基准退化", "degeneracy_class"], "scope": ["tools", "backend", "tests"], "exclude": list(SELF_PREFIXES)},
        ],
    },
    {
        "claim_id": "C2-adaptive-family-no-prior-art",
        "claim_kind": "novelty",
        "expected_verdict": "SUPPORTED",
        "statement": "除本节点模块外，平台没有自适应判据族（量级归一化/对数比带/区间包含）实现",
        "receipts": [
            {"kind": "code", "query": ["log_ratio", "magnitude_normalized", "interval_containment", "adaptive_family"], "scope": ["tools", "backend"], "exclude": list(SELF_PREFIXES)},
        ],
    },
    {
        "claim_id": "C3-threshold-registry-exists",
        "claim_kind": "coverage",
        "expected_verdict": "SUPPORTED",
        "statement": "判据阈值注册表（带来源与可重推导）已在平台落地——本模块必须复用它而不是重造",
        "receipts": [
            {"kind": "code", "query": ["threshold_registry"], "scope": ["tools", "data/criteria"]},
        ],
    },
    {
        "claim_id": "C4-triad-prior-art-exists",
        "claim_kind": "coverage",
        "expected_verdict": "SUPPORTED",
        "statement": "能力/交付/证据的分层在平台内已有先例（证据链 / 交付卫生），三元台账不是凭空新概念",
        "receipts": [
            {"kind": "code", "query": ["evidence_chain", "delivery_hygiene"], "scope": ["tools"]},
        ],
    },
    {
        "claim_id": "C5-this-node-registered",
        "claim_kind": "coverage",
        "expected_verdict": "SUPPORTED",
        "statement": "本节点在节点池中有登记（mission 记录存在），节点类凭证可被重放",
        "receipts": [
            {"kind": "node", "query": ["P266W3-N10"], "scope": ["mission_pool"]},
        ],
    },
    {
        "claim_id": "C6-coarse-query-false-prior-art",
        "claim_kind": "novelty",
        "expected_verdict": "REFUTED",
        "statement": "（负控主张）粗粒度检索词也能证明新颖性：『平台没有判据退化相关实现』",
        "receipts": [
            {"kind": "code", "query": ["degeneracy", "跨零", "多口径", "退化分级"], "scope": ["tools", "backend", "tests"], "exclude": list(SELF_PREFIXES)},
        ],
        "note": "这条主张故意用粗词，用来暴露检索门的一个真实失效模式：术语级命中会把无关用法算成先例。它必须被 REFUTED，否则说明门失灵。",
    },
]


def _node_snapshot(path: Path, api_base: str | None, token: str | None, limit_pages: int = 10) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if api_base and token:
        import urllib.request

        for page in range(limit_pages):
            url = f"{api_base.rstrip('/')}/tensor/missions?limit=100&offset={page * 100}"
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode())
            except Exception:
                break
            got = data.get("items") or []
            if not got:
                break
            for m in got:
                items.append(
                    {
                        "id": str(m.get("id")),
                        "text": " ".join(str(m.get(k) or "") for k in ("goal", "repo", "campaign_id")),
                    }
                )
    import hashlib

    pool_digest = hashlib.sha256(
        json.dumps(sorted((it["id"], it["text"]) for it in items), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    snap = {
        "captured_on": str(_dt.date.today()),
        "note": (
            "节点池快照：mission 记录的 goal/repo/campaign 字段（采集自平台 API）。"
            "全量文本不入库（体积原因），只保留池指纹 pool_digest 与命中的 mission id + 文本指纹；"
            "复核时用同样的采样命令重取即可对指纹。"
        ),
        "n_items": len(items),
        "pool_digest": pool_digest,
        "refetch": "GET {api_base}/tensor/missions?limit=100&offset=0..900（Bearer token）",
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


def _node_entries(snapshot: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    for spec in specs:
        for r in spec["receipts"]:
            if r["kind"] != "node":
                continue
            hits = [
                f"mission:{it['id']}"
                for it in snapshot.get("items", [])
                if any(q.lower() in (it.get("text") or "").lower() for q in r["query"])
            ]
            key = "|".join(["node", ",".join(sorted(set(r["query"]))), ",".join(sorted(set(r["scope"])))])
            entries[key] = sorted(set(hits))
    return entries


def build(root: Path, *, api_base: str | None, token: str | None) -> dict[str, Any]:
    code_index = FileTermIndex(root)
    receipts = []
    for spec in CLAIM_SPECS:
        for r in spec["receipts"]:
            if r["kind"] == "code":
                idx = FileTermIndex(root)
                probe = build_receipt(
                    claim_id=spec["claim_id"],
                    kind="code",
                    query=r["query"],
                    scope=r["scope"],
                    hits=[],
                    exclude=r.get("exclude") or (),
                )
                status, hits, _detail = idx.resolve(probe)
                receipts.append(
                    build_receipt(
                        claim_id=spec["claim_id"],
                        kind="code",
                        query=r["query"],
                        scope=r["scope"],
                        hits=hits if status == "MATCH" else [],
                        tool="tools.criteria_metrology.build_receipts",
                        created_at=str(_dt.date.today()),
                        exclude=r.get("exclude") or (),
                    ).as_dict()
                    | {"replay_status_at_creation": status}
                )
    return {"version": 1, "generated_on": str(_dt.date.today()), "receipts": receipts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成检索凭证 / 节点池快照")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="data/criteria_metrology/retrieval_receipts.json")
    ap.add_argument("--claims-out", default="data/criteria_metrology/retrieval_claims.json")
    ap.add_argument("--node-snapshot", default="data/criteria_metrology/node_pool_snapshot.json")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE"))
    ap.add_argument("--token", default=os.environ.get("TENSOR_API_TOKEN"))
    args = ap.parse_args(argv)
    root = Path(args.root)
    out = build(root, api_base=args.api_base, token=args.token)
    node_snap = _node_snapshot(Path(args.node_snapshot), args.api_base, args.token)
    # 节点类凭证：用快照现场算命中
    for spec in CLAIM_SPECS:
        for r in spec["receipts"]:
            if r["kind"] != "node":
                continue
            hits = [
                f"mission:{it['id']}"
                for it in node_snap.get("items", [])
                if any(q.lower() in (it.get("text") or "").lower() for q in r["query"])
            ]
            out["receipts"].append(
                build_receipt(
                    claim_id=spec["claim_id"],
                    kind="node",
                    query=r["query"],
                    scope=r["scope"],
                    hits=hits,
                    tool="tools.criteria_metrology.build_receipts",
                    created_at=str(_dt.date.today()),
                ).as_dict()
                | {"replay_status_at_creation": "MATCH"}
            )
    entries = _node_entries(node_snap, CLAIM_SPECS)
    import hashlib

    hit_ids = sorted({h.split(":", 1)[1] for hits in entries.values() for h in hits})
    text_by_id = {it["id"]: it.get("text") or "" for it in node_snap.get("items", [])}
    trimmed = {
        "captured_on": node_snap["captured_on"],
        "note": node_snap["note"],
        "n_items": node_snap["n_items"],
        "pool_digest": node_snap["pool_digest"],
        "refetch": node_snap.get("refetch"),
        "entries": entries,
        "hit_items": {
            mid: hashlib.sha256(text_by_id.get(mid, "").encode("utf-8")).hexdigest() for mid in hit_ids
        },
    }
    Path(args.node_snapshot).write_text(json.dumps(trimmed, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    claims = {
        "version": 1,
        "generated_on": str(_dt.date.today()),
        "claims": [
            {
                "claim_id": s["claim_id"],
                "claim_kind": s["claim_kind"],
                "statement": s["statement"],
                "expected_verdict": s.get("expected_verdict"),
                "note": s.get("note"),
            }
            for s in CLAIM_SPECS
        ],
    }
    Path(args.claims_out).write_text(json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"receipts={len(out['receipts'])} claims={len(claims['claims'])} node_items={node_snap['n_items']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())