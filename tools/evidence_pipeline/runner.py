"""The evidence-pipeline orchestrator (domain-neutral).

One call runs the whole chain, in this order and no other:

1. **execute** — ``generators/<node>.py`` in a subprocess, deterministic env;
2. **validate** — payload contract (schema/node/metrics/fields);
3. **judge** — every gate of the benchmark configuration, in declaration order;
4. **artifact** — build the self-verifying artifact (status derived from gates);
5. **ingest gate** — ``artifact.verify_artifact``: schema, self digest, status,
   generator identity, configuration identity, payload digest and a full
   generator **replay**; any failure rejects the artifact (fail-closed);
6. **regression** — compare against the committed baseline; on drift re-run the
   generator ``regression.reruns`` times and classify (see
   :mod:`tools.evidence_pipeline.regression`);
7. **provenance** — an append-only, hash-chained ledger over all of the above,
   whose ``chain_valid`` flag is recomputed before the run record is written.

Exit codes are the machine-readable contract:

===  ==========================================================
0    pass (gates pass, artifact ingested, regression stable)
2    a gate failed (artifact written with status ``fail``)
3    the ingest gate rejected the artifact
4    regression detected drift / non-determinism
5    the generator failed, timed out or violated the payload
6    a baseline re-blessing was requested without ``--accept-drift``
===  ==========================================================
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field as _dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tools.coupling.provenance import ExchangeLedger, canonical_digest

from . import RUN_SCHEMA, PipelineError
from .artifact import build_artifact, sha256_file, verify_artifact, write_artifact
from .config import ROOT, BenchmarkConfig, load_config
from .execution import ExecResult, execute_generator, validate_payload
from .gate import run_gates, summarise
from .regression import compare, decide

__all__ = ["RunResult", "run_benchmark", "EXIT_CODES", "main"]

EXIT_OK = 0
EXIT_GATE_FAIL = 2
EXIT_INGEST_REJECT = 3
EXIT_REGRESSION = 4
EXIT_GENERATOR_ERROR = 5
EXIT_BASELINE_REFUSED = 6

EXIT_CODES = {
    "pass": EXIT_OK,
    "gate_fail": EXIT_GATE_FAIL,
    "ingest_rejected": EXIT_INGEST_REJECT,
    "regression_fail": EXIT_REGRESSION,
    "generator_error": EXIT_GENERATOR_ERROR,
    "baseline_refused": EXIT_BASELINE_REFUSED,
}


@dataclass
class RunResult:
    node: str
    status: str
    exit_code: int
    artifact_path: Optional[str] = None
    record_path: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    verdict: Dict[str, Any] = _dc_field(default_factory=dict)
    ingest: Dict[str, Any] = _dc_field(default_factory=dict)
    regression: Dict[str, Any] = _dc_field(default_factory=dict)
    attempts: List[Dict[str, Any]] = _dc_field(default_factory=list)
    ledger: Optional[ExchangeLedger] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "status": self.status,
            "exit_code": self.exit_code,
            "artifact_path": self.artifact_path,
            "record_path": self.record_path,
            "verdict": self.verdict,
            "ingest_passed": self.ingest.get("passed"),
            "regression": self.regression,
            "attempts": self.attempts,
            "provenance_chain_valid": self.ledger.verify_chain() if self.ledger else None,
            "provenance_digest": self.ledger.digest if self.ledger else None,
        }


def _attempt_record(index: int, payload: Mapping[str, Any], duration_s: float, gates_passed: bool) -> Dict[str, Any]:
    return {
        "attempt": index,
        "payload_digest": canonical_digest(payload),
        "gate_passed": bool(gates_passed),
        "duration_s": round(float(duration_s), 6),
    }


def run_benchmark(
    cfg: BenchmarkConfig,
    config_path: Optional[str | Path] = None,
    out_dir: str | Path = "artifacts/P167W2-N11/runs",
    update_baseline: bool = False,
    accept_drift: bool = False,
    replay_ingest: bool = True,
) -> RunResult:
    """Run the full chain for one benchmark and persist artifact + run record."""
    out_dir = Path(out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"{cfg.node}.artifact.json"
    record_path = out_dir / f"{cfg.node}.run.json"
    config_path = str(config_path or cfg.source)

    prev_artifact_sha = sha256_file(artifact_path) if artifact_path.is_file() else None
    ledger = ExchangeLedger(name=f"evidence-pipeline::{cfg.node}")
    ledger.append(stage="config", config_digest=cfg.digest(), config_path=config_path,
                  generator=cfg.generator, generator_sha256=sha256_file(cfg.generator_path))
    started = datetime.now(timezone.utc).isoformat()

    # ---------------------------------------------------------------- 1. execute
    exec_result: ExecResult = execute_generator(cfg)
    ledger.append(
        stage="execute",
        returncode=exec_result.returncode,
        timed_out=exec_result.timed_out,
        duration_s=round(exec_result.duration_s, 6),
        payload_digest=canonical_digest(exec_result.payload) if exec_result.payload else None,
    )

    def _write_record(status: str, extra: Optional[Mapping[str, Any]] = None) -> Path:
        body: Dict[str, Any] = {
            "schema": RUN_SCHEMA,
            "node": cfg.node,
            "benchmark": config_path,
            "config_digest": cfg.digest(),
            "status": status,
            "exit_code": EXIT_CODES[status],
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "artifact_path": str(artifact_path) if artifact_path.is_file() else None,
            "provenance": ledger.to_dict(),
        }
        if extra:
            body.update({k: v for k, v in extra.items()})
        record_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return record_path

    if not exec_result.ok:
        ledger.append(stage="execute_failed", returncode=exec_result.returncode,
                      timed_out=exec_result.timed_out, stderr=exec_result.stderr.strip()[:500])
        _write_record("generator_error", {"stderr": exec_result.stderr.strip()[:2000]})
        return RunResult(node=cfg.node, status="generator_error",
                         exit_code=EXIT_GENERATOR_ERROR, record_path=str(record_path), ledger=ledger)

    payload = exec_result.payload or {}
    # --------------------------------------------------------------- 2. validate
    contract_errors = validate_payload(payload, cfg)
    ledger.append(stage="validate", errors=contract_errors, ok=not contract_errors)
    if contract_errors:
        _write_record("generator_error", {"contract_errors": contract_errors})
        return RunResult(node=cfg.node, status="generator_error",
                         exit_code=EXIT_GENERATOR_ERROR, payload=payload,
                         record_path=str(record_path), ledger=ledger)

    # ------------------------------------------------------------------ 3. judge
    outcomes = run_gates(cfg, payload)
    verdict = summarise(outcomes)
    ledger.append(
        stage="gates",
        verdict=verdict,
        gate_digests={o.id: canonical_digest(o.to_dict()) for o in outcomes},
    )
    attempts = [_attempt_record(0, payload, exec_result.duration_s, verdict["passed"])]

    # --------------------------------------------------------------- 4. artifact
    artifact = build_artifact(
        cfg,
        payload,
        outcomes,
        provenance={
            "config_digest": cfg.digest(),
            "generator_sha256": sha256_file(cfg.generator_path),
            "payload_digest": canonical_digest(payload),
            "gate_digests": {o.id: canonical_digest(o.to_dict()) for o in outcomes},
        },
        config_path=config_path,
    )
    write_artifact(artifact, artifact_path)
    ledger.append(stage="artifact", self_sha256=artifact["self_sha256"],
                  status=artifact["status"], prev_artifact_sha256=prev_artifact_sha)

    # ----------------------------------------------------------- 5. ingest gate
    ingest = verify_artifact(artifact_path, config_path=config_path, replay=replay_ingest)
    ledger.append(stage="ingest_gate", passed=ingest["passed"],
                  failed_checks=[c["id"] for c in ingest["checks"] if not c["passed"]])
    # A failed verdict is reported as ``gate_fail`` (the ingest gate is *expected*
    # to refuse such an artifact; that refusal is recorded as evidence that the
    # ingest gate is fail-closed rather than as a separate status).
    if not verdict["passed"]:
        _write_record("gate_fail", {"ingest": ingest, "verdict": verdict, "attempts": attempts})
        return RunResult(node=cfg.node, status="gate_fail", exit_code=EXIT_GATE_FAIL,
                         artifact_path=str(artifact_path), record_path=str(record_path),
                         payload=payload, verdict=verdict, ingest=ingest, attempts=attempts,
                         ledger=ledger)

    if not ingest["passed"]:
        _write_record("ingest_rejected", {"ingest": ingest, "verdict": verdict, "attempts": attempts})
        return RunResult(node=cfg.node, status="ingest_rejected", exit_code=EXIT_INGEST_REJECT,
                         artifact_path=str(artifact_path), record_path=str(record_path),
                         payload=payload, verdict=verdict, ingest=ingest, attempts=attempts,
                         ledger=ledger)

    # ------------------------------------------------------------- 6. regression
    reg_cfg = cfg.regression
    baseline_path = cfg.baseline_path
    baseline: Optional[Dict[str, Any]] = None
    if reg_cfg.enabled and str(baseline_path) not in ("", ".") and baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            baseline = None
    has_baseline = bool(baseline and isinstance(baseline.get("metrics"), dict))
    comparison = compare(
        payload.get("metrics", {}),
        (baseline or {}).get("metrics", {}),
        reg_cfg.tol,
        reg_cfg.floors,
    )

    if has_baseline and not comparison["within_tolerance"]:
        for i in range(reg_cfg.reruns):
            rerun = execute_generator(cfg)
            if not rerun.ok:
                attempts.append({"attempt": i + 1, "payload_digest": None, "gate_passed": False,
                                 "error": f"re-run failed rc={rerun.returncode}"})
                continue
            rerun_errors = validate_payload(rerun.payload, cfg)
            rerun_gates = summarise(run_gates(cfg, rerun.payload)) if not rerun_errors else {"passed": False}
            attempts.append(_attempt_record(i + 1, rerun.payload or {}, rerun.duration_s,
                                            bool(rerun_gates.get("passed"))))

    decision = decide(comparison, attempts, has_baseline)
    ledger.append(
        stage="regression",
        classification=decision["classification"],
        passed=decision["passed"],
        baseline_path=str(baseline_path) if has_baseline else None,
        max_rel_drift=comparison.get("max_rel_drift"),
        attempt_digests=[a.get("payload_digest") for a in attempts],
    )

    baseline_updated = False
    reblessed = (
        update_baseline
        and accept_drift
        and decision["classification"] == "drift_reproducible"
    )
    if update_baseline:
        if decision["classification"] == "drift_reproducible" and not accept_drift:
            _write_record(
                "baseline_refused",
                {"ingest": ingest, "verdict": verdict, "regression": {"comparison": comparison,
                                                                      "decision": decision},
                 "attempts": attempts,
                 "reason": "re-blessing a reproducible drift requires --accept-drift"},
            )
            return RunResult(node=cfg.node, status="baseline_refused",
                             exit_code=EXIT_BASELINE_REFUSED, artifact_path=str(artifact_path),
                             record_path=str(record_path), payload=payload, verdict=verdict,
                             ingest=ingest, regression={"comparison": comparison, "decision": decision},
                             attempts=attempts, ledger=ledger)
        if decision["passed"] or reblessed:
            baseline_doc = {
                "schema": "evidence-pipeline/baseline/1",
                "node": cfg.node,
                "config_digest": cfg.digest(),
                "generator_sha256": sha256_file(cfg.generator_path),
                "payload_digest": canonical_digest(payload),
                "artifact_self_sha256": artifact["self_sha256"],
                "metrics": payload.get("metrics", {}),
                "blessed_utc": datetime.now(timezone.utc).isoformat(),
                "replaces": (baseline or {}).get("artifact_self_sha256"),
            }
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps(baseline_doc, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
            baseline_updated = True
            ledger.append(stage="baseline_written", path=str(baseline_path),
                          payload_digest=baseline_doc["payload_digest"],
                          reblessed=reblessed)

    regression_block = {"comparison": comparison, "decision": decision,
                        "baseline_path": str(baseline_path) if has_baseline else None,
                        "baseline_updated": baseline_updated,
                        "reblessed": bool(reblessed)}
    if not decision["passed"] and not reblessed:
        _write_record("regression_fail", {"ingest": ingest, "verdict": verdict,
                                          "regression": regression_block, "attempts": attempts})
        return RunResult(node=cfg.node, status="regression_fail", exit_code=EXIT_REGRESSION,
                         artifact_path=str(artifact_path), record_path=str(record_path),
                         payload=payload, verdict=verdict, ingest=ingest,
                         regression=regression_block, attempts=attempts, ledger=ledger)

    chain_valid = ledger.verify_chain()
    _write_record(
        "pass",
        {
            "ingest": {"passed": ingest["passed"], "n_checks": ingest["n_checks"]},
            "verdict": verdict,
            "regression": regression_block,
            "attempts": attempts,
            "provenance_chain_valid": chain_valid,
        },
    )
    return RunResult(node=cfg.node, status="pass", exit_code=EXIT_OK,
                     artifact_path=str(artifact_path), record_path=str(record_path),
                     payload=payload, verdict=verdict, ingest=ingest,
                     regression=regression_block, attempts=attempts, ledger=ledger)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="evidence-pipeline runner")
    ap.add_argument("--config", required=True, help="benchmark configuration JSON")
    ap.add_argument("--out-dir", default="artifacts/P167W2-N11/runs")
    ap.add_argument("--update-baseline", action="store_true",
                    help="write the committed baseline when the run passes")
    ap.add_argument("--accept-drift", action="store_true",
                    help="allow re-blessing a reproducible drift (explicit human/campaign decision)")
    ap.add_argument("--no-replay-ingest", action="store_true",
                    help="skip the replay check of the ingest gate (audit only)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except PipelineError as exc:
        print(json.dumps({"status": "config_error", "error": str(exc)}, indent=2))
        return 5

    result = run_benchmark(
        cfg,
        config_path=args.config,
        out_dir=args.out_dir,
        update_baseline=args.update_baseline,
        accept_drift=args.accept_drift,
        replay_ingest=not args.no_replay_ingest,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())