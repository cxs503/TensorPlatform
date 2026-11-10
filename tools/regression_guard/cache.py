"""Content-addressed cache of acceptance-test outcomes.

Key = sha256(test id, pytest args, hash of the test file, hashes of every
intra-repo module in the test file's transitive import closure, guard schema).
A cache hit means *nothing observable changed* for that test, so re-running it
would burn cluster time for zero information — criterion (e) requires the
guard to reuse it rather than re-execute.

The cache is a single JSON file (one ``results.json`` per cache dir) so it can
be committed as an artifact, diffed and audited.  Deleted entries are never
reused implicitly: ``prune`` drops keys whose dependency files vanished.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .model import sha256_obj

CACHE_SCHEMA = "1.0"


@dataclass
class CacheEntry:
    """Cached result of one *selector* (a test file may expand to N test cases).

    Storing the whole expansion keeps the per-test delta identical between a
    cold and a warm run: replaying a cached file must reproduce
    ``tests/x.py::test_a``/``::test_b`` individually, not collapse them into
    ``tests/x.py``.
    """

    key: str
    selector: str
    outcomes: List[Dict[str, Any]] = field(default_factory=list)
    code_digest: str = ""
    created_utc: str = ""
    hits: int = 0

    @property
    def worst(self) -> str:
        order = {"error": 3, "failed": 2, "passed": 1, "skipped": 0}
        return max((o.get("outcome", "not_run") for o in self.outcomes),
                   key=lambda x: order.get(x, -1), default="not_run")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "selector": self.selector, "outcomes": self.outcomes,
            "code_digest": self.code_digest, "created_utc": self.created_utc,
            "hits": self.hits,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CacheEntry":
        return CacheEntry(
            key=str(d["key"]), selector=str(d.get("selector", "")),
            outcomes=list(d.get("outcomes", [])),
            code_digest=str(d.get("code_digest", "")),
            created_utc=str(d.get("created_utc", "")), hits=int(d.get("hits", 0)),
        )


class ResultCache:
    def __init__(self, path: Optional[Path | str] = None, enabled: bool = True) -> None:
        self.path = Path(path) if path else None
        self.enabled = enabled and self.path is not None
        self.entries: Dict[str, CacheEntry] = {}
        if self.enabled and self.path and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, val in raw.get("entries", {}).items():
                self.entries[key] = CacheEntry.from_dict({"key": key, **val})
        self.lookups = 0
        self.hits = 0

    # -- keys --------------------------------------------------------------
    @staticmethod
    def make_key(
        test_id: str,
        args: Sequence[str],
        file_digests: Dict[str, str],
        test_source_digest: str = "",
        schema: str = CACHE_SCHEMA,
    ) -> str:
        payload = {
            "schema": schema,
            "test_id": test_id,
            "args": list(args),
            "test_source": test_source_digest,
            "deps": {k: v for k, v in sorted(file_digests.items())},
        }
        return sha256_obj(payload)

    # -- access ------------------------------------------------------------
    def get(self, key: str) -> Optional[CacheEntry]:
        self.lookups += 1
        entry = self.entries.get(key)
        if entry is None:
            return None
        entry.hits += 1
        self.hits += 1
        return entry

    def put(self, entry: CacheEntry) -> None:
        self.entries[entry.key] = entry

    def prune(self, live_keys: Iterable[str]) -> List[str]:
        live = set(live_keys)
        dropped = sorted(k for k in self.entries if k not in live)
        for k in dropped:
            del self.entries[k]
        return dropped

    def gc(self, existing_files: Iterable[str]) -> List[str]:
        """Drop entries whose selector file vanished (dead tests cost nothing)."""
        files = set(existing_files)
        dropped: List[str] = []
        for key, entry in sorted(self.entries.items()):
            rel = entry.selector.split("::", 1)[0]
            if files and rel not in files:
                dropped.append(key)
        for key in dropped:
            del self.entries[key]
        return dropped

    @property
    def hit_rate(self) -> float:
        return (self.hits / self.lookups) if self.lookups else 0.0

    def save(self) -> None:
        if not self.enabled or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CACHE_SCHEMA,
            "entries": {
                k: {kk: vv for kk, vv in v.to_dict().items() if kk != "key"}
                for k, v in sorted(self.entries.items())
            },
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dependency_digests(root: Path | str, test_id: str, graph, max_depth: int = 64) -> Dict[str, str]:
    """Hashes of the test file plus every intra-repo module it imports (closure)."""
    root = Path(root)
    rel = test_id.split("::", 1)[0]
    digests: Dict[str, str] = {}
    if graph is None:
        target = root / rel
        if target.exists():
            from .model import sha256_file
            digests[rel] = sha256_file(target)
        return digests
    seen: Set[str] = set()
    frontier: List[tuple[str, int]] = [(rel, 0)]
    while frontier:
        cur, depth = frontier.pop()
        if cur in seen or depth > max_depth:
            continue
        seen.add(cur)
        facts = graph.files.get(cur)
        if facts is None:
            continue
        digests[cur] = facts.sha256
        for dep in sorted(facts.imports):
            frontier.append((dep, depth + 1))
    return digests