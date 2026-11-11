#!/usr/bin/env python3
"""否定性知识账本入口——加载、验证、查询。

用法：
    python scripts/negative_knowledge_entry.py load      # 加载账本到 SQLite
    python scripts/negative_knowledge_entry.py verify    # 验证账本完整性
    python scripts/negative_knowledge_entry.py stats     # 显示统计
    python scripts/negative_knowledge_entry.py search "关键词"  # 检索
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.negative_knowledge import (
    NegativeKnowledgeConfig,
    NegativeKnowledgeSearcher,
    NegativeKnowledgeStore,
    load_config,
)


def load_ledger():
    """加载账本到 SQLite 并返回 store。"""
    ledger_path = REPO / "artifacts" / "negative_knowledge_ledger" / "negative_knowledge_ledger.json"
    if not ledger_path.exists():
        print(f"ERROR: 账本文件不存在: {ledger_path}")
        sys.exit(1)

    with open(ledger_path) as f:
        entries = json.load(f)

    print(f"加载账本: {len(entries)} 条")

    # 创建临时 SQLite
    db_path = tempfile.mktemp(suffix=".db")
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    cfg = load_config()
    store = NegativeKnowledgeStore.for_engine(engine, cfg)

    loaded = 0
    for e in entries:
        try:
            _, created = store.register(
                title=e["title"],
                hypothesis=e["hypothesis"],
                failure_kind=e["failure_kind"],
                falsification_evidence=e.get("falsification_evidence"),
                failure_reason=e.get("failure_reason"),
                mission_id=e.get("mission_id"),
                node_id=e.get("node_id"),
                keywords=json.loads(e.get("keywords", "[]")),
                source_artifacts=json.loads(e.get("source_artifacts", "[]")),
                confidence=e.get("confidence", 0.8),
                registered_by=e.get("registered_by", "entry_point"),
            )
            if created:
                loaded += 1
        except Exception as exc:
            print(f"  跳过 {e.get('entry_id')}: {exc}")

    print(f"成功加载 {loaded}/{len(entries)} 条")
    return store, entries


def verify_ledger():
    """验证账本完整性。"""
    ledger_path = REPO / "artifacts" / "negative_knowledge_ledger" / "negative_knowledge_ledger.json"
    with open(ledger_path) as f:
        entries = json.load(f)

    errors = []
    for e in entries:
        if not e.get("entry_id"):
            errors.append(f"缺少 entry_id: {e.get('title', '?')}")
        if not e.get("source_artifacts") or e["source_artifacts"] == "[]":
            errors.append(f"缺少证据指针: {e['entry_id']}")
        if not e.get("failure_reason"):
            errors.append(f"缺少 failure_reason: {e['entry_id']}")
        if not e.get("falsification_evidence"):
            errors.append(f"缺少 falsification_evidence: {e['entry_id']}")
        if e.get("failure_kind") not in ("hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression"):
            errors.append(f"非法 failure_kind: {e['entry_id']} = {e.get('failure_kind')}")

    if errors:
        print(f"验证失败 ({len(errors)} 项):")
        for err in errors[:10]:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print(f"验证通过: {len(entries)} 条，全部合规")


def show_stats():
    """显示统计。"""
    ledger_path = REPO / "artifacts" / "negative_knowledge_ledger" / "negative_knowledge_ledger.json"
    with open(ledger_path) as f:
        entries = json.load(f)

    by_kind = {}
    for e in entries:
        kind = e.get("failure_kind", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1

    print(f"总条目: {len(entries)}")
    print("按失败类型:")
    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"  {kind}: {count}")


def search_ledger(query: str):
    """检索账本。"""
    store, entries = load_ledger()
    cfg = load_config()
    searcher = NegativeKnowledgeSearcher(store, cfg)
    hits = searcher.search(query, top_k=5, min_score=0.1)
    print(f"检索 '{query}': {len(hits)} 条命中")
    for h in hits:
        print(f"  [{h.score:.3f}] {h.entry.title}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "load":
        load_ledger()
    elif cmd == "verify":
        verify_ledger()
    elif cmd == "stats":
        show_stats()
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法: search <关键词>")
            sys.exit(1)
        search_ledger(sys.argv[2])
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
