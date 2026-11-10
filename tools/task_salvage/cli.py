"""CLI for task salvage: collect → build → backtest → figures.

```
python3 -m tools.task_salvage.cli collect   --work-root /path/to/work --limit 40
python3 -m tools.task_salvage.cli build     --mission-id 9379 --work-root /path/to/work
python3 -m tools.task_salvage.cli backtest
python3 -m tools.task_salvage.cli figures
```

``collect`` is the only command that touches the network (read-only mission
list queries) and the only one that reads foreign worktrees; it freezes
everything it saw into ``artifacts/task_salvage/corpus.json`` so ``backtest`` is
recomputable offline and byte-stable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import backtest, continuation, salvage, sift, taxonomy

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts" / "task_salvage"
DEFAULT_WORK_ROOT = Path("/root/tp-agent/work")

_NODE_TAG_RE = re.compile(r"【战役 DAG 节点\|([^|]+)\|")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def node_tag(goal: str | None) -> str | None:
    match = _NODE_TAG_RE.search(goal or "")
    return match.group(1) if match else None


def _api():
    from tools.salvage import platform_api as pa

    return pa


def project(record: dict, full: bool = True) -> dict:
    """Keep only the fields the pipeline reads (corpus input snapshot)."""
    evidence = (record.get("result") or {}).get("evidence") or {}
    checks = evidence.get("checks") or {}
    summary = evidence.get("summary") or {}
    goal = str(record.get("goal") or "")
    row = {
        "id": record.get("id"),
        "status": record.get("status"),
        "repo": record.get("repo"),
        "headline": record.get("headline") or (record.get("result") or {}).get("headline"),
        "goal": goal if full else goal[:300],
        "changed_files": summary.get("changed_files") or [],
    }
    if full:
        row.update(
            {
                "claimed_by": record.get("claimed_by"),
                "model": record.get("model") or (record.get("result") or {}).get("model"),
                "parent_mission_id": record.get("parent_mission_id"),
                "fail_stage": checks.get("fail_stage"),
            }
        )
    return row


def hydrate(row: dict) -> dict:
    """Rebuild the record shape the salvage/backtest code expects."""
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "repo": row.get("repo"),
        "headline": row.get("headline"),
        "goal": row.get("goal"),
        "claimed_by": row.get("claimed_by"),
        "model": row.get("model"),
        "parent_mission_id": row.get("parent_mission_id"),
        "result": {
            "model": row.get("model"),
            "headline": row.get("headline"),
            "evidence": {"checks": {"fail_stage": row.get("fail_stage")}, "summary": {"changed_files": row.get("changed_files") or []}},
        },
    }


def load_records(out_dir: Path, status: str, offline: bool, full: bool, keep_tags: set[str] | None = None) -> list[dict]:
    """Corpus input snapshot: fetch live when possible, else reuse the frozen copy."""
    path = Path(out_dir) / "records" / f"{status}.json"
    if not offline:
        try:
            rows = [project(r, full) for r in fetch_missions(status)]
            if keep_tags:
                rows = [row for row in rows if node_tag(row.get("goal")) in keep_tags]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            return rows
        except Exception as exc:  # noqa: BLE001 - network is best-effort by design
            if not path.is_file():
                raise
            print(f"WARN live fetch failed ({type(exc).__name__}); using frozen snapshot {path}", file=sys.stderr)
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_missions(status: str, page_size: int = 200, max_pages: int = 80) -> list[dict]:
    pa = _api()
    token = pa.login()
    rows: dict[int, dict] = {}
    offset = 0
    for _ in range(max_pages):
        payload = pa._request(f"/tensor/missions?status={status}&limit={page_size}&offset={offset}", token)
        items = payload.get("items") or []
        if not items:
            break
        for row in items:
            rows[int(row["id"])] = row
        offset += len(items)
        if offset >= int(payload.get("total") or 0):
            break
    return [rows[k] for k in sorted(rows)]


def _record_candidates(record: dict, worktree: Path) -> list[tuple[str, str]]:
    return salvage._candidate_rels(record, worktree)


def build_mission(record: dict, worktree: Path, repo_root: Path, out_dir: Path, smoke: bool = True) -> tuple[dict, dict]:
    payload = salvage.build(record, worktree, repo_root=repo_root, smoke=smoke)
    salvage.write(payload, out_dir)
    (Path(out_dir) / "continuation.json").write_text(
        salvage.canonical_json(payload["continuation"]), encoding="utf-8"
    )
    return payload


def _oracle_files(payload: dict, worktree: Path, repo_root: Path, workers: int = 8) -> list[dict]:
    entries = payload["inheritable"] + payload["blocked_files"]
    smoke_files = set((payload.get("policy") or {}).get("smoke_files") or [])

    def _one(row: dict) -> dict:
        verdict = backtest.oracle_sift(
            worktree / row["path"], row["path"], repo_root, worktree, smoke_files=smoke_files
        )
        return {
            "path": row["path"],
            "gate_verdict": row.get("verdict", sift.INHERITABLE if not row.get("failed_gates") else sift.BLOCKED),
            "verdict": verdict["verdict"],
            "gates": verdict["gates"],
            "failed_gates": verdict["failed_gates"],
        }

    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(_one, entries))


def collect(
    work_root: Path,
    out_dir: Path,
    limit: int,
    repo_root: Path,
    smoke: bool = True,
    offline: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    missions_dir = out_dir / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(repo_root).resolve()

    failed_rows = load_records(out_dir, "failed", offline, full=True)
    # the successor pool only matters for tags that also have a circuit-broken
    # task; pruning it keeps the frozen snapshot committable (11 MB -> <1 MB)
    fail_tags = {tag for tag in (node_tag(row.get("goal")) for row in failed_rows) if tag}
    succeeded_rows = load_records(out_dir, "succeeded", offline, full=False, keep_tags=fail_tags)
    failed = [hydrate(row) for row in failed_rows]
    succeeded = [hydrate(row) for row in succeeded_rows]

    in_scope = [r for r in failed if taxonomy.in_scope(r)]
    eligible = []
    for record in in_scope:
        worktree = Path(work_root) / f"m{record['id']}"
        if not worktree.is_dir():
            continue
        candidates = _record_candidates(record, worktree)
        present = [rel for rel, _ in candidates if (worktree / rel).is_file()]
        if not present:
            continue
        eligible.append((record, worktree, present))
    eligible.sort(key=lambda item: int(item[0]["id"]), reverse=True)
    selected = eligible[:limit]

    missions: list[dict] = []
    payload_cache: dict[int, dict] = {}
    for record, worktree, present in selected:
        mission_id = int(record["id"])
        target = missions_dir / str(mission_id)
        payload = build_mission(record, worktree, repo_root, target, smoke=smoke)
        payload_cache[int(record["id"])] = payload
        payload_second = salvage.build(record, worktree, repo_root=repo_root, smoke=smoke)
        first_bytes = (target / salvage.SALVAGE_NAME).read_bytes()
        second_bytes = salvage.canonical_json(payload_second).encode("utf-8")
        oracle_rows = _oracle_files(payload, worktree, repo_root)

        gate_rows = []
        for row in payload["inheritable"]:
            gate_rows.append({**{k: v for k, v in row.items()}, "verdict": sift.INHERITABLE})
        for row in payload["blocked_files"]:
            gate_rows.append({**{k: v for k, v in row.items()}, "verdict": sift.BLOCKED})

        print(f"[collect] m{mission_id} state={payload['state']} files={payload['counts']['inheritable']}", file=sys.stderr)
        mission_row = {
            "mission_id": mission_id,
            "repo": record.get("repo"),
            "bucket": taxonomy.bucket_of(record),
            "reason_code": taxonomy.reason_code(record),
            "raw_stage": taxonomy.raw_stage(record),
            "in_scope": taxonomy.in_scope(record),
            "headline": record.get("headline") or ((record.get("result") or {}).get("headline")),
            "node_tag": node_tag(record.get("goal")),
            "worktree_present": worktree.is_dir(),
            "candidates": len(present),
            "files": sorted(gate_rows, key=lambda r: r["path"]),
            "oracle_files": sorted(oracle_rows, key=lambda r: r["path"]),
            "salvage": {
                "written": (target / salvage.SALVAGE_NAME).is_file(),
                "state": payload["state"],
                "blocked_code": (payload.get("blocked") or {}).get("code"),
                "counts": payload["counts"],
                "fingerprint": salvage.fingerprint(payload),
                "bytes_sha256": salvage.sha256_bytes(first_bytes),
                "subtask_id": payload["continuation"]["subtask_id"],
            },
            "salvage_second": {
                "fingerprint": salvage.fingerprint(payload_second),
                "bytes_sha256": salvage.sha256_bytes(second_bytes),
                "equal_bytes": first_bytes == second_bytes,
            },
        }
        missions.append(mission_row)

    families = _families(in_scope, succeeded, work_root, repo_root, payload_cache)

    corpus = {
        "schema_version": "1.0.0",
        "collected_from": {
            "api_base": _api().BASE,
            "queries": ["/tensor/missions?status=failed", "/tensor/missions?status=succeeded"],
            "fetched_at": _now(),
            "work_root": str(work_root),
            "repo_root": str(repo_root),
            "failed_total": len(failed),
            "succeeded_total": len(succeeded),
            "in_scope_terminal": len(in_scope),
            "eligible_with_worktree": len(eligible),
            "selected": len(selected),
        },
        "taxonomy": taxonomy.distill(failed),
        "missions": missions,
        "families": families,
    }
    corpus["corpus_sha256"] = backtest.corpus_sha256({k: v for k, v in corpus.items() if k != "corpus_sha256"})
    (out_dir / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return corpus


def _norm_success_paths(changed: list, repo_root: Path) -> tuple[list[str], list[str]]:
    """Normalise the successor's delivered file list; return (all, code-only)."""
    all_paths, code_paths = [], []
    for raw in changed or []:
        text = str(raw or "").strip().replace("\\", "/")
        if not text or text.startswith("/"):
            continue
        parts = [seg for seg in text.split("/") if seg not in ("", ".")]
        if not parts or ".." in parts:
            continue
        rel = "/".join(parts)
        all_paths.append(rel)
        if salvage._file_class(rel) == "code":
            code_paths.append(rel)
    return sorted(set(all_paths)), sorted(set(code_paths))


def _families(
    in_scope: list[dict],
    succeeded: list[dict],
    work_root: Path,
    repo_root: Path,
    payload_cache: dict[int, dict] | None = None,
) -> list[dict]:
    fail_by_tag: dict[str, list[dict]] = {}
    for record in in_scope:
        tag = node_tag(record.get("goal"))
        if tag:
            fail_by_tag.setdefault(tag, []).append(record)
    ok_by_tag: dict[str, list[dict]] = {}
    for record in succeeded:
        tag = node_tag(record.get("goal"))
        if tag:
            ok_by_tag.setdefault(tag, []).append(record)

    families = []
    for tag in sorted(set(fail_by_tag) & set(ok_by_tag)):
        ok_records = sorted(ok_by_tag[tag], key=lambda r: int(r["id"]), reverse=True)
        fail_records = sorted(fail_by_tag[tag], key=lambda r: int(r["id"]), reverse=True)
        ok_record = ok_records[0]
        required, required_code = _norm_success_paths(
            ((ok_record.get("result") or {}).get("evidence") or {}).get("summary", {}).get("changed_files") or [],
            repo_root,
        )
        required_source = "evidence.changed_files"
        if not required:
            # fallback: the successor's retained worktree still carries the files
            # it delivered (dirty set).  Flagged as a proxy, and the evidence-only
            # subset is reported separately as the primary-source sensitivity.
            ok_worktree = Path(work_root) / f"m{ok_record['id']}"
            if ok_worktree.is_dir():
                required = sorted(
                    {rel for rel, _ in _record_candidates(ok_record, ok_worktree) if (ok_worktree / rel).is_file()}
                )
                required_code = [rel for rel in required if salvage._file_class(rel) == "code"]
                required_source = "successor_worktree"
        if not required:
            continue
        for fail_record in fail_records:
            worktree = Path(work_root) / f"m{fail_record['id']}"
            if not worktree.is_dir():
                continue
            candidates = _record_candidates(fail_record, worktree)
            present = [rel for rel, _ in candidates if (worktree / rel).is_file()]
            if not present:
                continue
            payload = (payload_cache or {}).get(int(fail_record["id"])) or salvage.build(
                fail_record, worktree, repo_root=repo_root, smoke=True
            )
            inherited = sorted({row["path"] for row in payload["inheritable"]})
            capacity = len(present)
            capacity_code = sum(1 for rel in present if salvage._file_class(rel) == "code")
            print(f"[collect] family {tag} dead=m{fail_record['id']} src={required_source}", file=sys.stderr)
            families.append(
                {
                    "node_key": tag,
                    "failed_mission_id": int(fail_record["id"]),
                    "ok_mission_id": int(ok_record["id"]),
                    "required": required,
                    "required_code": required_code,
                    "required_source": required_source,
                    "inherited": inherited,
                    "capacity": capacity,
                    "capacity_code": capacity_code,
                    "fail_bucket": taxonomy.bucket_of(fail_record),
                }
            )
            break  # one representative dead task per node family (paired design)
    return families


def run_backtest(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    corpus = json.loads((out_dir / "corpus.json").read_text(encoding="utf-8"))
    report = backtest.aggregate(corpus)
    report["corpus_sha256"] = corpus.get("corpus_sha256")
    report["collected_from"] = corpus.get("collected_from")
    report["generated_at"] = _now()
    (out_dir / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="task salvage (terminal-state hand-over)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="freeze a corpus from live mission records + worktrees")
    p_collect.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    p_collect.add_argument("--out", default=str(DEFAULT_OUT))
    p_collect.add_argument("--limit", type=int, default=40)
    p_collect.add_argument("--repo-root", default=str(ROOT))
    p_collect.add_argument("--offline", action="store_true", help="reuse the frozen record snapshot")

    p_build = sub.add_parser("build", help="build one salvage.json for a single mission id")
    p_build.add_argument("--mission-id", type=int, required=True)
    p_build.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    p_build.add_argument("--out", default=str(DEFAULT_OUT))
    p_build.add_argument("--repo-root", default=str(ROOT))
    p_build.add_argument("--no-smoke", action="store_true")

    p_bt = sub.add_parser("backtest", help="recompute the three machine gates from the frozen corpus")
    p_bt.add_argument("--out", default=str(DEFAULT_OUT))

    p_fig = sub.add_parser("figures", help="render report figures from acceptance.json")
    p_fig.add_argument("--out", default=str(DEFAULT_OUT))

    args = parser.parse_args(argv)
    if args.command == "collect":
        corpus = collect(Path(args.work_root), Path(args.out), args.limit, Path(args.repo_root), offline=args.offline)
        print(json.dumps({
            "selected": len(corpus["missions"]),
            "families": len(corpus["families"]),
            "in_scope_terminal": corpus["collected_from"]["in_scope_terminal"],
            "corpus_sha256": corpus["corpus_sha256"][:16],
        }, ensure_ascii=False))
        return 0
    if args.command == "build":
        pa = _api()
        token = pa.login()
        record = pa._request(f"/tensor/missions/{args.mission_id}", token)
        record = record.get("data", record) if isinstance(record, dict) else record
        worktree = Path(args.work_root) / f"m{args.mission_id}"
        target = Path(args.out) / "missions" / str(args.mission_id)
        payload = build_mission(record, worktree, Path(args.repo_root).resolve(), target, smoke=not args.no_smoke)
        print(json.dumps({
            "state": payload["state"],
            "blocked": payload.get("blocked"),
            "counts": payload["counts"],
            "fingerprint": salvage.fingerprint(payload)[:16],
            "subtask_id": payload["continuation"]["subtask_id"],
        }, ensure_ascii=False))
        return 0
    if args.command == "backtest":
        report = run_backtest(Path(args.out))
        print(json.dumps({"verdict": report["verdict"], "all_pass": report["all_pass"]}, ensure_ascii=False))
        return 0
    if args.command == "figures":  # pragma: no cover - plotting
        from . import figures

        written = figures.render(Path(args.out))
        print(json.dumps({"figures": written}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())