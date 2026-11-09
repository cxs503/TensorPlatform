"""Shadow-mode zero-side-effect regression for P92W3-N10.

Produces ``runs/p92w3-n10/shadow_golden.json``: a fixed case set (synthetic edge
cases + a seeded sample of real corpus payloads) with the verdicts of both
classifiers, plus the invariants that make "shadow" mean something:

* two consecutive evaluations hash to the same bytes (deterministic),
* the classification modules import no I/O (no db/network/file-write tokens),
* classifying a corpus never mutates the input payloads.

Run: python3 tools/p92w3_n10/shadow_golden.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.domain import delivery_state as ds  # noqa: E402
from api.domain.delivery_state import assess_attested_delivery, assess_delivery  # noqa: E402

OUT = ROOT / "runs/p92w3-n10"
CORPUS = OUT / "corpus/missions_succeeded.json"
SEED = 20261109
N_REAL = 40

SYNTHETIC = [
    None,
    {},
    {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done"},
    {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done", "fail_stage": ""},
    {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done",
     "fail_stage": "budget_exhausted+forced_finish"},
    {"changed_files": ["a.py"], "checks": "", "summary": "done"},
    {"changed_files": ["a.py"], "summary": "done"},
    {"changed_files": ["a.py"], "checks": "未运行"},
    {"checks": "12 passed", "summary": "done"},
    {"on_disk_changes": ["b.py"], "checks": "3/3 passed", "headline": "ok"},
    {"on_disk_changes": ["b.py"], "checks": [{"cmd": "pytest", "exit_code": 0}],
     "headline": "ok"},
    {"changed_files": ["a.py"], "checks": "gate A \u2705 | gate B \u2705",
     "summary": "d", "fail_stage": "wall_exhausted+forced_finish"},
]

IO_TOKENS = ("sqlalchemy", "urllib", "requests", "httpx", "psycopg", "open(",
             "write_text", "write_bytes", "subprocess")


def _payload(record: dict):
    value = record.get("result")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def build_cases() -> list[dict]:
    cases = [{"kind": "synthetic", "id": None, "result": payload} for payload in SYNTHETIC]
    if CORPUS.exists():
        records = sorted(json.loads(CORPUS.read_text(encoding="utf-8")),
                         key=lambda r: r.get("id") or 0)
        rng = random.Random(SEED)
        for record in rng.sample(records, min(N_REAL, len(records))):
            cases.append({"kind": "real", "id": record.get("id"),
                          "result": _payload(record)})
    for case in cases:
        case["state"] = assess_attested_delivery(case["result"]).state
        case["artifact_state"] = assess_delivery(case["result"]).state
    return cases


def purity_report() -> dict:
    findings = {}
    for module in (ds,):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        findings[pathlib.Path(module.__file__).name] = sorted({
            token for token in IO_TOKENS if re.search(re.escape(token), source)
        })
    return findings


def main() -> None:
    cases_a = build_cases()
    cases_b = build_cases()
    digest_a = hashlib.sha256(json.dumps(cases_a, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    digest_b = hashlib.sha256(json.dumps(cases_b, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    # mutate-check: classification must not touch the caller's payloads
    before = json.dumps(cases_a, sort_keys=True, ensure_ascii=False)
    for case in cases_a:
        assess_attested_delivery(case["result"])
    after = json.dumps(cases_a, sort_keys=True, ensure_ascii=False)

    payload = {
        "seed": SEED,
        "n_cases": len(cases_a),
        "n_real": sum(1 for c in cases_a if c["kind"] == "real"),
        "digest_run_1": digest_a,
        "digest_run_2": digest_b,
        "deterministic": digest_a == digest_b,
        "input_mutated": before != after,
        "io_tokens_in_classifier": purity_report(),
        "state_counts": {
            "attested": {s: sum(1 for c in cases_a if c["state"] == s)
                         for s in ("delivered_complete", "delivered_partial", "suspect_undeclared")},
            "artifact": {s: sum(1 for c in cases_a if c["artifact_state"] == s)
                         for s in ("delivered_complete", "delivered_partial", "suspect_undeclared")},
        },
        "cases": cases_a,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shadow_golden.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in
                      ("n_cases", "n_real", "deterministic", "input_mutated",
                       "io_tokens_in_classifier", "state_counts")}, ensure_ascii=False, indent=2))
    if not payload["deterministic"] or payload["input_mutated"]:
        raise SystemExit("shadow golden violated: non-deterministic or mutating")


if __name__ == "__main__":
    main()