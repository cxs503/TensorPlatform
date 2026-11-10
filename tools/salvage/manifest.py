"""Salvage manifest builder: scan → classify → prune → package → manifest.

The manifest is the only thing downstream workers are allowed to trust, so it is
written **deterministically**: canonical JSON (``sort_keys``, 2-space indent,
UTF-8, trailing newline), no wall-clock timestamps (the clock is derived from git
commit times), every list sorted.  Re-running a scan over unchanged sources
therefore reproduces the manifest byte for byte (acceptance criterion A5).
"""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import classify, dedupe, gitutil, probe, resume

SCHEMA_VERSION = "1.0.0"
MANIFEST_NAME = "manifest.json"
PACKAGE_FILES_DIR = "files"

#: candidate source modes
MODE_DIRTY = "dirty"      # uncommitted work in the worktree (the real salvage target)
MODE_MISSION = "mission"  # the mission's delivered file set (negative-control replay)


def _iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


@dataclass
class ScanConfig:
    work_root: Path
    baseline_ref: str = "main"
    candidate_mode: str = MODE_DIRTY
    probe_limit: int = 8
    probe_workers: int = 4
    max_bytes: int = 32 * 1024 * 1024
    history_limit: int = 10
    use_history_tier: bool = True
    baseline_sha: str | None = None  # pin the baseline for reproducible replays
    mission_id_map: dict = field(default_factory=dict)


def _safe_rel_path(path: str) -> bool:
    parts = [seg for seg in path.split("/") if seg not in ("", ".")]
    if not parts or path.startswith("/"):
        return False
    return ".." not in parts


def _candidates(worktree: Path, cfg: ScanConfig) -> list[gitutil.StatusEntry]:
    if cfg.candidate_mode == MODE_MISSION:
        return gitutil.mission_changed_paths(worktree, cfg.baseline_ref)
    if cfg.candidate_mode == MODE_DIRTY:
        return gitutil.status_entries(worktree)
    raise ValueError(f"unknown candidate mode: {cfg.candidate_mode}")


def scan_mission(
    mission_key: str,
    out_root: Path,
    cfg: ScanConfig,
    meta: dict | None = None,
) -> dict:
    """Scan one dead mission and write ``<out_root>/<mission_key>/{files,manifest.json}``."""
    meta = dict(meta or {})
    worktree = Path(cfg.work_root) / mission_key
    out_dir = Path(out_root) / mission_key
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / PACKAGE_FILES_DIR).mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "schema_version": SCHEMA_VERSION,
        "mission_key": mission_key,
        "mission_id": meta.get("mission_id"),
        "plan_id": meta.get("plan_id"),
        "node_id": meta.get("node_id"),
        "node_status": meta.get("node_status"),
        "ext_id": meta.get("ext_id"),
        "repo": meta.get("repo"),
        "title": meta.get("title"),
        "source": {
            "worktree": str(worktree),
            "is_git_worktree": False,
            "head_sha": None,
            "baseline_ref": cfg.baseline_ref,
            "baseline_sha": cfg.baseline_sha,
            "candidate_mode": cfg.candidate_mode,
            "commit_epoch": 0,
        },
        "generated_from_commit_utc": _iso_utc(0),
        "status": "unscanned",
        "files": [],
        "dropped": [],
        "pruned": [],
        "deletions": [],
        "probes": [],
        "resume": {},
        "counts": {},
        "notes": [],
    }

    if not gitutil.is_git_worktree(worktree):
        manifest["status"] = "worktree_missing"
        manifest["notes"].append("no git worktree at source path; entry recorded as absent")
        (out_dir / MANIFEST_NAME).write_text(canonical_json(manifest), encoding="utf-8")
        return manifest

    head = gitutil.head_sha(worktree)
    baseline_sha = cfg.baseline_sha or gitutil.rev_parse_opt(worktree, cfg.baseline_ref)
    commit_epoch = gitutil.head_commit_epoch(worktree)
    manifest["source"].update(
        is_git_worktree=True,
        head_sha=head,
        baseline_sha=baseline_sha,
        commit_epoch=commit_epoch,
    )
    manifest["generated_from_commit_utc"] = _iso_utc(commit_epoch)

    deduper = dedupe.Deduper(
        str(worktree),
        baseline_ref=cfg.baseline_ref,
        history_limit=cfg.history_limit,
        use_history_tier=cfg.use_history_tier,
    )

    kept: list[dict] = []
    counts = {"candidates": 0, "kept": 0, "pruned": 0, "dropped_scratch": 0, "dropped_other": 0}

    for entry in sorted(_candidates(worktree, cfg), key=lambda e: e.path):
        counts["candidates"] += 1
        rel = entry.path
        cls, reason = classify.classify(rel)
        record = {"path": rel, "status": entry.label, "class": cls, "class_reason": reason}
        if entry.orig_path:
            record["orig_path"] = entry.orig_path
        if not _safe_rel_path(rel):
            counts["dropped_other"] += 1
            record["drop_reason"] = "unsafe_path"
            manifest["dropped"].append(record)
            continue
        if classify.is_drop_class(cls):
            counts["dropped_scratch"] += 1
            record["drop_reason"] = "scratch"
            manifest["dropped"].append(record)
            continue
        if entry.deleted:
            manifest["deletions"].append(record)
            continue
        src = worktree / rel
        if not src.is_file():
            counts["dropped_other"] += 1
            record["drop_reason"] = "not_a_regular_file"
            manifest["dropped"].append(record)
            continue
        size = src.stat().st_size
        if size > cfg.max_bytes:
            counts["dropped_other"] += 1
            record.update(drop_reason="oversize", bytes=size)
            manifest["dropped"].append(record)
            continue

        sha = gitutil.file_sha256(src)
        decision = deduper.decide(rel, sha)
        record.update(sha256=sha, bytes=size, dedupe=decision.as_dict())
        if decision.pruned:
            counts["pruned"] += 1
            manifest["pruned"].append(record)
            continue

        dest = out_dir / PACKAGE_FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied_sha = gitutil.file_sha256(dest)
        record["packaged"] = True
        record["packaged_sha256"] = copied_sha
        record["copy_verified"] = copied_sha == sha
        if not record["copy_verified"]:
            record["drop_reason"] = "copy_sha_mismatch"
            dest.unlink(missing_ok=True)
            record.pop("packaged")
            record["packaged"] = False
            counts["dropped_other"] += 1
            manifest["dropped"].append(record)
            continue
        counts["kept"] += 1
        kept.append(record)

    kept.sort(key=lambda r: r["path"])
    manifest["files"] = kept

    # A4: explicit static usability verdicts for code files (never "assumed usable")
    code_files = [r for r in kept if r["class"] == "code"][: cfg.probe_limit]
    # import probes spawn interpreters (some dead-mission modules JIT-compile on
    # import), so they run in a small thread pool; results are re-ordered by path
    # to keep the manifest byte-identical between runs.
    if code_files:
        with ThreadPoolExecutor(max_workers=cfg.probe_workers) as pool:
            probe_rows = list(pool.map(lambda r: probe.probe_file(worktree, r["path"]), code_files))
    else:
        probe_rows = []
    manifest["probes"] = sorted(probe_rows, key=lambda r: r["path"])
    manifest["probe_policy"] = {
        "py_compile": "always, .pyc redirected to a temp dir (source tree untouched)",
        "imports": "AST top-level imports resolved in an isolated subprocess",
        "dynamic_import": "attempted only when all top-level imports resolve",
        "limit": cfg.probe_limit,
        "candidates": len([r for r in kept if r["class"] == "code"]),
    }

    # resume analysis: what the next wave inherits vs what is still missing
    manifest["resume"] = resume.analyse(
        criteria=meta.get("acceptance_criteria") or [],
        files=kept,
        read_text=lambda rel: gitutil.read_text_limited(worktree / rel),
    )

    by_class = {c: 0 for c in classify.CLASSES}
    for record in kept:
        by_class[record["class"]] += 1
    counts["by_class_kept"] = by_class
    manifest["counts"] = counts
    manifest["status"] = "packaged"
    (out_dir / MANIFEST_NAME).write_text(canonical_json(manifest), encoding="utf-8")
    return manifest


def manifest_path(out_root: Path, mission_key: str) -> Path:
    return Path(out_root) / mission_key / MANIFEST_NAME


def manifest_sha256(out_root: Path, mission_key: str) -> str:
    return gitutil.file_sha256(manifest_path(out_root, mission_key))


def write_index(out_root: Path, entries: list[dict], extra: dict | None = None) -> dict:
    """Index over packaged missions (sorted, deterministic, hash-chained)."""
    out_root = Path(out_root)
    rows = []
    for entry in sorted(entries, key=lambda e: (str(e.get("mission_key")))):
        key = entry["mission_key"]
        path = manifest_path(out_root, key)
        rows.append(
            {
                "mission_key": key,
                "mission_id": entry.get("mission_id"),
                "plan_id": entry.get("plan_id"),
                "node_id": entry.get("node_id"),
                "node_status": entry.get("node_status"),
                "ext_id": entry.get("ext_id"),
                "repo": entry.get("repo"),
                "status": entry.get("status"),
                "manifest": f"{key}/{MANIFEST_NAME}",
                "manifest_sha256": gitutil.file_sha256(path) if path.is_file() else None,
                "counts": entry.get("counts", {}),
                "resume": {
                    "criteria_total": entry.get("resume", {}).get("criteria_total", 0),
                    "criteria_covered": entry.get("resume", {}).get("criteria_covered", 0),
                    "missing": [m["text"] for m in entry.get("resume", {}).get("missing", [])],
                },
            }
        )
    index = {
        "schema_version": SCHEMA_VERSION,
        "kind": "salvage_index",
        "entry_count": len(rows),
        "entries": rows,
    }
    if extra:
        index.update(extra)
    (out_root / "index.json").write_text(canonical_json(index), encoding="utf-8")
    return index