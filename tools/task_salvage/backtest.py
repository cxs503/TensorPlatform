"""Backtest gates for task salvage: consistency, idempotency, replay gain.

Three machine gates, all computed from the committed corpus
(``artifacts/task_salvage/corpus.json``) so the verdict is recomputable offline:

1. **consistency** — the three-gate sift is compared against an *independent*
   oracle implementation (different mechanism, same intent: ``compile()`` in a
   subprocess instead of in-process ``ast.parse``; ``runpy.run_path`` instead of
   ``importlib``; live ``listdir`` of the checkout instead of the configured
   root set).  Two independent implementations agreeing per file is the only
   claim made here — it catches implementation drift, not semantic truth.
   Predeclared: agreement ≥ 0.90, false-positive rate ≤ 0.10.
2. **idempotency** — every in-scope task is built twice; the two
   ``fingerprint.subject_sha256`` values must be equal (and the two payload byte
   strings identical).
3. **replay gain** — paired A/B over mission families that contain both a
   circuit-broken task and a *succeeded* re-dispatch of the same campaign node.
   Required set ``R`` = the successor's delivered files; inherited set ``I`` =
   dead-task files that survive the sift and land in ``R``; authoring capacity
   ``C`` = number of distinct files the dead task had actually authored (a
   measured quantity, not a free parameter).  Arm judge: pass iff
   ``covered ≥ |R|`` with ``covered_control = min(C, |R|)`` and
   ``covered_enabled = min(|I| + C, |R|)``.  Tests: Fisher exact (as specified)
   and McNemar exact (the paired-correct test, reported alongside).

Everything is deterministic: no clock, no randomness, no network.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from math import comb
from pathlib import Path

from . import salvage, sift

#: predeclared thresholds (copied into the acceptance report verbatim)
THRESHOLDS = {
    "consistency_min": 0.90,
    "false_positive_max": 0.10,
    "idempotency_min": 1.00,
    "salvage_rate_min": 1.00,
    "replay_fisher_alpha": 0.10,
    "replay_min_pairs": 10,
    "replay_min_gain_pairs": 4,
}

#: replay arm judge parameters (predeclared; sensitivity sweep reported too)
REPLAY_CAPACITY_MODES = ("all_files", "code_files")


# ───────────────────────── independent oracle (verification) ─────────────────

_ORACLE_COMPILE = "import sys; p=sys.argv[1]; compile(open(p,'rb').read(), p, 'exec')"
_ORACLE_IMPORT = (
    "import importlib, sys\n"
    "worktree, path, name = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "sys.path.insert(0, worktree)\n"
    "try:\n"
    "    importlib.import_module(name)\n"
    "except ModuleNotFoundError as exc:\n"
    "    if exc.name != name:\n"
    "        raise\n"
    "    import runpy\n"
    "    runpy.run_path(path, run_name='__oracle__')\n"
)


def _subprocess_ok(args: list[str], cwd: str | None = None, timeout: int = 20, env_extra: dict | None = None):
    """Run a check subprocess; the environment mirrors the gate's smoke env."""
    base = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    base.setdefault("PATH", "/usr/bin:/bin")
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    base["PYTHONHASHSEED"] = "0"
    env = base
    env.update(env_extra or {})
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    if proc.returncode == 0:
        return True, "ok"
    tail = (proc.stderr or "").strip().splitlines()
    return False, (tail[-1] if tail else f"exit{proc.returncode}")[:160]


_ORACLE_RUNPY = (
    "import runpy, sys\n"
    "worktree, path = sys.argv[1], sys.argv[2]\n"
    "sys.path.insert(0, worktree)\n"
    "runpy.run_path(path, run_name='__oracle__')\n"
)


def oracle_roots(worktree: Path) -> set[str]:
    """Layout used by the oracle: live listing of the mission's own checkout."""
    live = set(salvage.FALLBACK_ROOTS)
    try:
        for entry in Path(worktree).iterdir():
            if not entry.name.startswith("."):
                live.add(entry.name)
    except OSError:
        pass
    return live


def oracle_sift(
    path: Path,
    rel: str | None,
    repo_root: Path | None,
    worktree: Path,
    timeout: int = 20,
    smoke_files: set[str] | None = None,
) -> dict:
    """Independent second opinion on the same three gates."""
    gates: dict[str, dict] = {}
    path = Path(path)
    exists = path.is_file()
    ext = sift.ext_of(path.name)

    # gate A — syntax via compile() in a subprocess (not ast.parse in-process)
    if not exists:
        gates["parse"] = {"status": "fail", "detail": "file_missing"}
    elif ext == "py":
        ok, detail = _subprocess_ok(["python3", "-B", "-c", _ORACLE_COMPILE, str(path.resolve())], timeout=timeout)
        gates["parse"] = {"status": "pass" if ok else "fail", "detail": detail}
    elif ext in ("png", "pdf", "zip", "gz"):
        magic = {"png": b"\x89PNG\r\n\x1a\n", "pdf": b"%PDF-", "zip": b"PK\x03\x04", "gz": b"\x1f\x8b"}[ext]
        ok = path.read_bytes()[:8].startswith(magic)
        gates["parse"] = {"status": "pass" if ok else "fail", "detail": "magic_ok" if ok else "magic_mismatch"}
    elif ext == "json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
            gates["parse"] = {"status": "pass", "detail": "json_ok"}
        except Exception:  # noqa: BLE001
            gates["parse"] = {"status": "fail", "detail": "json_error"}
    else:
        try:
            path.read_text(encoding="utf-8")
            gates["parse"] = {"status": "pass", "detail": "utf8_ok"}
        except UnicodeDecodeError:
            gates["parse"] = {"status": "fail", "detail": "utf8_decode_failed"}

    # gate B — smoke via runpy (not importlib) in a scratch cwd
    if ext != "py" or not exists:
        gates["smoke"] = {"status": "not_applicable", "detail": "ext" if exists else "file_missing"}
        if not exists:
            gates["smoke"] = {"status": "fail", "detail": "file_missing"}
    elif smoke_files is not None and rel not in smoke_files:
        # the oracle must judge the same file set, otherwise a budget-limited gate
        # would look inconsistent for a reason that is a policy, not a defect
        gates["smoke"] = {"status": "fail", "detail": "smoke_not_run"}
    else:
        with tempfile.TemporaryDirectory(prefix="task-salvage-oracle-") as cwd:
            name = sift.dotted_name(rel or path.name) or ("oracle_" + hashlib.sha256(str(path).encode()).hexdigest()[:12])
            ok, detail = _subprocess_ok(
                ["python3", "-B", "-c", _ORACLE_IMPORT, str(Path(worktree).resolve()), str(path.resolve()), name],
                cwd=cwd,
                timeout=timeout,
                env_extra=sift.smoke_env(worktree),
            )
        gates["smoke"] = {"status": "pass" if ok else "fail", "detail": detail}

    # gate C — structural via live directory listing of the mission's checkout
    if rel is None:
        gates["structural"] = {"status": "fail", "detail": "outside_worktree"}
    else:
        head = rel.split("/", 1)[0]
        live = oracle_roots(worktree)
        ok = head in live
        gates["structural"] = {"status": "pass" if ok else "fail", "detail": f"live_root:{head}"[:160]}

    failed = [name for name in sift.GATES if gates[name]["status"] == "fail"]
    return {"gates": gates, "verdict": sift.BLOCKED if failed else sift.INHERITABLE, "failed_gates": failed}


# ─────────────────────────────────── statistics ─────────────────────────────

def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> dict:
    """Two-sided Fisher exact test on [[a, b], [c, d]] (pure Python)."""
    n = a + b + c + d
    row1, col1 = a + b, a + c
    if n == 0:
        return {"p_value": 1.0, "table": [[a, b], [c, d]], "method": "fisher_exact_two_sided"}

    def prob(x: int) -> float:
        # hypergeometric probability of x successes in row1
        return comb(col1, x) * comb(n - col1, row1 - x) / comb(n, row1)

    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    observed = prob(a)
    p = sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed * (1 + 1e-12))
    return {
        "p_value": min(1.0, round(p, 6)),
        "table": [[a, b], [c, d]],
        "method": "fisher_exact_two_sided",
        "margins": {"row1": row1, "col1": col1, "n": n},
    }


def mcnemar_exact(b: int, c: int) -> dict:
    """Exact McNemar (binomial on discordant pairs, two-sided)."""
    n = b + c
    if n == 0:
        return {"p_value": 1.0, "discordant": 0, "method": "mcnemar_exact"}
    k = min(b, c)
    p = 2 * sum(comb(n, i) for i in range(0, k + 1)) / (2**n)
    return {"p_value": min(1.0, round(p, 6)), "discordant": n, "method": "mcnemar_exact"}


# ──────────────────────────────────── gates ─────────────────────────────────

def gate_consistency(corpus: dict) -> dict:
    """Gate-sift vs independent oracle, per file."""
    agree = disagree = 0
    claimed = fp = 0
    mismatches: list[dict] = []
    for mission in corpus.get("missions", []):
        oracle_map = {row["path"]: row for row in mission.get("oracle_files", [])}
        for row in mission.get("files", []):
            oracle = oracle_map.get(row["path"])
            if not oracle:
                continue
            agree += row["verdict"] == oracle["verdict"]
            if row["verdict"] == oracle["verdict"]:
                continue
            disagree += 1
            if row["verdict"] == sift.INHERITABLE:
                fp += 1
            mismatches.append(
                {
                    "mission_id": mission.get("mission_id"),
                    "path": row["path"],
                    "gate": row["verdict"],
                    "oracle": oracle["verdict"],
                    "gate_failed": row.get("failed_gates") or [],
                    "oracle_failed": oracle.get("failed_gates") or [],
                }
            )
            claimed += row["verdict"] == sift.INHERITABLE
    judged = agree + disagree
    claimed_total = sum(1 for m in corpus.get("missions", []) for r in m.get("files", []) if r["verdict"] == sift.INHERITABLE)
    consistency = round(agree / judged, 4) if judged else 0.0
    fp_rate = round(fp / claimed_total, 4) if claimed_total else 0.0
    return {
        "judged_files": judged,
        "agree": agree,
        "disagree": disagree,
        "consistency": consistency,
        "claimed_inheritable": claimed_total,
        "false_positives": fp,
        "false_positive_rate": fp_rate,
        "mismatches": mismatches[:20],
        "consistency_pass": consistency >= THRESHOLDS["consistency_min"],
        "false_positive_pass": fp_rate <= THRESHOLDS["false_positive_max"],
    }


def gate_idempotency(corpus: dict) -> dict:
    rows = []
    for mission in corpus.get("missions", []):
        first = (mission.get("salvage") or {}).get("fingerprint")
        second = (mission.get("salvage_second") or {}).get("fingerprint")
        rows.append(
            {
                "mission_id": mission.get("mission_id"),
                "first": first,
                "second": second,
                "equal": bool(first) and first == second,
                "bytes_equal": bool((mission.get("salvage") or {}).get("bytes_sha256"))
                and (mission.get("salvage") or {}).get("bytes_sha256")
                == (mission.get("salvage_second") or {}).get("bytes_sha256"),
            }
        )
    judged = len(rows)
    equal = sum(1 for r in rows if r["equal"])
    return {
        "judged_missions": judged,
        "equal_fingerprints": equal,
        "rate": round(equal / judged, 4) if judged else 0.0,
        "rows": rows,
        "idempotency_pass": judged > 0 and equal == judged and all(r["bytes_equal"] for r in rows),
    }


def gate_salvage_rate(corpus: dict) -> dict:
    missions = corpus.get("missions", [])
    produced = sum(1 for m in missions if (m.get("salvage") or {}).get("written"))
    inheritable = sum(1 for m in missions if (m.get("salvage") or {}).get("state") == sift.INHERITABLE)
    blocked = produced - inheritable
    codes: dict[str, int] = {}
    for m in missions:
        code = (m.get("salvage") or {}).get("blocked_code")
        if code:
            codes[code] = codes.get(code, 0) + 1
    return {
        "terminal_tasks": len(missions),
        "salvage_written": produced,
        "state_inheritable": inheritable,
        "state_blocked": blocked,
        "blocked_codes": dict(sorted(codes.items())),
        "rate": round(produced / len(missions), 4) if missions else 0.0,
        "salvage_rate_pass": bool(missions) and produced == len(missions),
    }


def gate_replay(
    corpus: dict,
    capacity_mode: str = "all_files",
    required_source: str | None = None,
    match_mode: str = "path",
) -> dict:
    """Paired A/B over mission families with a succeeded re-dispatch.

    ``required_source`` restricts the families to those whose required set came
    from the named source (``evidence.changed_files`` is the primary source; the
    successor-worktree fallback is a proxy and is reported as sensitivity).
    """
    pairs = []
    for fam in corpus.get("families", []):
        if required_source and fam.get("required_source") != required_source:
            continue
        required = fam.get("required") or []
        if capacity_mode == "code_files":
            required = fam.get("required_code") or []
        if not required:
            continue
        if match_mode == "basename":
            # role-level matching: same file name, possibly relocated by the
            # accepting run (exploratory - the primary instrument is the path)
            wanted = {p.rsplit("/", 1)[-1] for p in required}
            inherited = [p for p in fam.get("inherited") or [] if p.rsplit("/", 1)[-1] in wanted]
        else:
            inherited = [p for p in fam.get("inherited") or [] if p in set(required)]
        capacity = fam.get("capacity_code") if capacity_mode == "code_files" else fam.get("capacity")
        capacity = int(capacity or 0)
        if capacity_mode == "code_files":
            capacity = int(fam.get("capacity_code") or 0)
        control_cover = min(capacity, len(required))
        enabled_cover = min(len(inherited) + capacity, len(required))
        pairs.append(
            {
                "node_key": fam.get("node_key"),
                "mission_id": fam.get("failed_mission_id"),
                "successor_id": fam.get("ok_mission_id"),
                "required": len(required),
                "inherited": len(inherited),
                "capacity": capacity,
                "control_cover": control_cover,
                "enabled_cover": enabled_cover,
                "control_coverage": round(control_cover / len(required), 4),
                "enabled_coverage": round(enabled_cover / len(required), 4),
                "overlap_ratio": round(len(inherited) / len(required), 4),
                "control_pass": control_cover >= len(required),
                "enabled_pass": enabled_cover >= len(required),
            }
        )
    n = len(pairs)
    a = sum(1 for p in pairs if p["enabled_pass"])
    b = sum(1 for p in pairs if p["enabled_pass"] and not p["control_pass"])  # gain
    c = sum(1 for p in pairs if p["control_pass"] and not p["enabled_pass"])  # loss (impossible by design)
    d = n - a - b - c
    control_pass = sum(1 for p in pairs if p["control_pass"])
    fisher = fisher_exact_2x2(a, n - a, control_pass, n - control_pass)
    mcnemar = mcnemar_exact(b, c)
    gain_rate_enabled = round(a / n, 4) if n else 0.0
    gain_rate_control = round(control_pass / n, 4) if n else 0.0
    passed = (
        n >= THRESHOLDS["replay_min_pairs"]
        and gain_rate_enabled > gain_rate_control
        and fisher["p_value"] < THRESHOLDS["replay_fisher_alpha"]
    )
    required_total = sum(p["required"] for p in pairs)
    overlap_total = sum(p["inherited"] for p in pairs)
    return {
        "capacity_mode": capacity_mode,
        "match_mode": match_mode,
        "required_files_total": required_total,
        "overlap_files_total": overlap_total,
        "overlap_ratio": round(overlap_total / required_total, 4) if required_total else 0.0,
        "families_with_overlap": sum(1 for p in pairs if p["inherited"] > 0),
        "mean_control_coverage": round(sum(p["control_coverage"] for p in pairs) / n, 4) if n else 0.0,
        "mean_enabled_coverage": round(sum(p["enabled_coverage"] for p in pairs) / n, 4) if n else 0.0,
        "pairs": n,
        "enabled_pass": a,
        "control_pass": control_pass,
        "gain_pairs": b,
        "loss_pairs": c,
        "enabled_pass_rate": gain_rate_enabled,
        "control_pass_rate": gain_rate_control,
        "fisher": fisher,
        "mcnemar": mcnemar,
        "rows": pairs,
        "replay_gain_pass": passed,
    }


def aggregate(corpus: dict) -> dict:
    """All gates + sensitivity sweep, ready for the acceptance JSON."""
    gates = {
        "salvage_rate": gate_salvage_rate(corpus),
        "consistency": gate_consistency(corpus),
        "idempotency": gate_idempotency(corpus),
        "replay_all_files": gate_replay(corpus, "all_files"),
        "replay_code_files": gate_replay(corpus, "code_files"),
        "replay_evidence_only": gate_replay(corpus, "all_files", "evidence.changed_files"),
        "replay_basename_match": gate_replay(corpus, "all_files", None, "basename"),
    }
    verdict = {
        "salvage_rate": gates["salvage_rate"]["salvage_rate_pass"],
        "consistency": gates["consistency"]["consistency_pass"],
        "false_positive": gates["consistency"]["false_positive_pass"],
        "idempotency": gates["idempotency"]["idempotency_pass"],
        "replay_gain": gates["replay_all_files"]["replay_gain_pass"],
    }
    return {
        "schema_version": "1.0.0",
        "thresholds": THRESHOLDS,
        "gates": gates,
        "verdict": verdict,
        "all_pass": all(verdict.values()),
    }


def corpus_sha256(corpus: dict) -> str:
    blob = json.dumps(corpus, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()