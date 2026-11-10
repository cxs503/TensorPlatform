"""Artifact construction and the ``--gate`` ingest mode (fail-closed).

An artifact is the *only* thing downstream consumers (evidence chain, ledger,
report) need to look at, so it must be self-verifying:

``self_sha256``
    Canonical digest of everything except itself.  Editing any recorded number,
    threshold or verdict invalidates it.

``payload_digest``
    Canonical digest of the generator payload.  Replay compares this digest —
    not floats printed with finite precision — so "replay consistent" means
    bit-identical numbers.

``generator.sha256``
    Content hash of the generator script that produced the numbers.  If the
    script changes, the artifact is *stale* and must be regenerated.

No timestamps live inside the artifact on purpose: two identical runs therefore
produce byte-identical artifacts, which is what makes "replay consistent" a
one-line file comparison instead of a fuzzy numeric comparison.  Wall-clock
information is recorded in the run record instead.

``python -m tools.evidence_pipeline.artifact --gate <artifact.json>`` runs every
check below and exits non-zero on the first class of failure; nothing is
"repaired" or "warned through".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tools.coupling.provenance import canonical_digest

from . import ARTIFACT_SCHEMA, PipelineError
from .config import BenchmarkConfig, ROOT, load_config
from .execution import execute_generator, validate_payload
from .gate import GateOutcome, summarise

__all__ = [
    "sha256_file",
    "build_artifact",
    "artifact_digest",
    "verify_artifact",
    "write_artifact",
    "main",
]

REQUIRED_KEYS = (
    "schema",
    "node",
    "benchmark",
    "config",
    "generator",
    "params",
    "payload",
    "payload_digest",
    "gates",
    "verdict",
    "provenance",
    "self_sha256",
)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_digest(body: Mapping[str, Any]) -> str:
    return canonical_digest({k: v for k, v in body.items() if k != "self_sha256"})


def build_artifact(
    cfg: BenchmarkConfig,
    payload: Mapping[str, Any],
    outcomes: Sequence[GateOutcome],
    provenance: Mapping[str, Any],
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the artifact for one judged run (status derived, not asserted)."""
    verdict = summarise(outcomes)
    status = "pass" if verdict["passed"] else "fail"
    body: Dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "node": cfg.node,
        "benchmark": cfg.source,
        "config": {
            "path": str(config_path or cfg.source),
            "digest": cfg.digest(),
        },
        "generator": {
            "path": cfg.generator,
            "sha256": sha256_file(cfg.generator_path),
        },
        "params": dict(cfg.params),
        "payload": dict(payload),
        "payload_digest": canonical_digest(payload),
        "gates": [o.to_dict() for o in outcomes],
        "verdict": verdict,
        "status": status,
        "provenance": dict(provenance),
    }
    body["self_sha256"] = artifact_digest(body)
    return body


def write_artifact(artifact: Mapping[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- gate
def _check(cid: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"id": cid, "passed": bool(passed), "detail": detail}


def verify_artifact(
    artifact_path: str | Path,
    config_path: Optional[str] = None,
    replay: bool = True,
) -> Dict[str, Any]:
    """Run the ingest gate. Returns a report; ``passed`` is the conjunction."""
    path = Path(artifact_path)
    checks: List[Dict[str, Any]] = []

    if not path.is_file():
        return {
            "passed": False,
            "artifact": str(path),
            "checks": [_check("A0_readable", False, f"artifact not found: {path}")],
        }
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "passed": False,
            "artifact": str(path),
            "checks": [_check("A0_readable", False, f"invalid JSON: {exc}")],
        }

    missing = [k for k in REQUIRED_KEYS if k not in artifact]
    checks.append(
        _check("A1_schema", not missing, "missing keys: " + ", ".join(missing) if missing else "all required keys present")
    )
    checks.append(
        _check(
            "A2_schema_version",
            artifact.get("schema") == ARTIFACT_SCHEMA,
            f"schema={artifact.get('schema')!r} expected {ARTIFACT_SCHEMA!r}",
        )
    )

    recorded_self = artifact.get("self_sha256")
    recomputed = artifact_digest(artifact)
    checks.append(
        _check(
            "A3_self_digest",
            recorded_self == recomputed,
            f"recorded={recorded_self} recomputed={recomputed}",
        )
    )

    checks.append(
        _check(
            "A4_status_pass",
            artifact.get("status") == "pass",
            f"status={artifact.get('status')!r} (a failed run must never be ingested)",
        )
    )

    verdict = artifact.get("verdict") or {}
    gates = artifact.get("gates") or []
    gates_consistent = bool(verdict.get("passed")) == all(
        bool(g.get("passed")) for g in gates
    ) and bool(gates) and bool(verdict.get("passed")) == (artifact.get("status") == "pass")
    checks.append(
        _check(
            "A5_verdict_consistency",
            gates_consistent,
            f"status={artifact.get('status')!r} verdict.passed={verdict.get('passed')!r} "
            f"gates_passed={[g.get('passed') for g in gates]}",
        )
    )

    payload = artifact.get("payload")
    try:
        payload_digest = canonical_digest(payload)
    except Exception as exc:  # pragma: no cover - defensive
        payload_digest = f"error:{exc}"
    checks.append(
        _check(
            "A6_payload_digest",
            payload_digest == artifact.get("payload_digest"),
            f"recorded={artifact.get('payload_digest')} recomputed={payload_digest}",
        )
    )

    # ---- generator identity -------------------------------------------------
    gen_rel = (artifact.get("generator") or {}).get("path")
    gen_path = Path(gen_rel) if gen_rel else None
    if gen_path is not None and not gen_path.is_absolute():
        gen_path = ROOT / gen_path
    if gen_path is None or not gen_path.is_file():
        checks.append(_check("A7_generator_identity", False, f"generator missing: {gen_path}"))
        gen_sha = None
    else:
        gen_sha = sha256_file(gen_path)
        recorded_sha = (artifact.get("generator") or {}).get("sha256")
        checks.append(
            _check(
                "A7_generator_identity",
                gen_sha == recorded_sha,
                f"recorded={recorded_sha} current={gen_sha}",
            )
        )

    # ---- configuration identity --------------------------------------------
    cfg_path = config_path or (artifact.get("config") or {}).get("path")
    cfg: Optional[BenchmarkConfig] = None
    try:
        if cfg_path is None:
            raise PipelineError("no configuration path recorded")
        cfg = load_config(cfg_path)
        recorded_cfg = (artifact.get("config") or {}).get("digest")
        checks.append(
            _check(
                "A8_config_identity",
                cfg.digest() == recorded_cfg,
                f"recorded={recorded_cfg} current={cfg.digest()}",
            )
        )
    except PipelineError as exc:
        checks.append(_check("A8_config_identity", False, f"config unavailable: {exc}"))

    # ---- replay consistency -------------------------------------------------
    if replay:
        if cfg is None:
            checks.append(_check("A9_replay", False, "replay impossible: config unavailable"))
        else:
            node_match = cfg.node == artifact.get("node")
            checks.append(
                _check("A9a_node_match", node_match, f"config node={cfg.node!r} artifact node={artifact.get('node')!r}")
            )
            try:
                result = execute_generator(cfg)
            except PipelineError as exc:
                checks.append(_check("A9_replay", False, f"execution failed: {exc}"))
                result = None
            if result is not None:
                if not result.ok:
                    checks.append(
                        _check(
                            "A9_replay",
                            False,
                            f"generator rc={result.returncode} timed_out={result.timed_out} "
                            f"stderr={result.stderr.strip()[:300]!r}",
                        )
                    )
                else:
                    errors = validate_payload(result.payload, cfg)
                    replayed_digest = canonical_digest(result.payload)
                    checks.append(
                        _check(
                            "A9_replay",
                            not errors and replayed_digest == artifact.get("payload_digest"),
                            (
                                f"payload contract errors {errors}"
                                if errors
                                else f"recorded={artifact.get('payload_digest')} replayed={replayed_digest}"
                            ),
                        )
                    )
    else:  # pragma: no cover - used by the report to show the check is meaningful
        checks.append(_check("A9_replay", True, "skipped (--no-replay)"))

    passed = all(c["passed"] for c in checks)
    return {
        "passed": passed,
        "artifact": str(path),
        "node": artifact.get("node"),
        "status": artifact.get("status"),
        "checks": checks,
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c["passed"]),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="evidence-pipeline artifact ingest gate")
    ap.add_argument("--gate", required=True, metavar="ARTIFACT", help="artifact JSON to verify")
    ap.add_argument("--config", default=None, help="benchmark config (default: recorded path)")
    ap.add_argument("--no-replay", action="store_true", help="skip generator replay (audit only)")
    ap.add_argument("--json", default=None, help="write the verification report here")
    args = ap.parse_args(argv)

    report = verify_artifact(args.gate, config_path=args.config, replay=not args.no_replay)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())