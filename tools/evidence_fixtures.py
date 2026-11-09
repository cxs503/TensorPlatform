#!/usr/bin/env python3
"""Synthetic-domain fixture generator for the evidence grading engine.

Thirty contracts with known labels, built from an abstract scalar pipeline over
nouns ``alpha`` / ``beta`` / ``gamma``. No domain vocabulary, no physical
quantities: the generator only needs "a number that two paths can compute".

Bucket design (labels fixed by construction, written to ``labels.json``)
-----------------------------------------------------------------------
=======  ===  ==========================================================
label    n    construction
=======  ===  ==========================================================
REAL      8   anchor file + 1-2 independently sourced paths + residual
              recomputable + predeclared tolerance + fail-able negative
              control + provenance digest
MOCK      8   numbers, but not recomputable/supportable:
              3 x inline literal anchor      -> HARDCODED_INLINE
              2 x no repro_cmd              -> NO_REPRO_CMD
              2 x no anchor                 -> NO_ANCHOR
              1 x anchor sha256 mismatch    -> ANCHOR_NOT_RECOMPUTABLE
STUB      6   interface artifact only, no number -> INTERFACE_ONLY
WITHHELD  8   2 x anchor present but no residual -> NO_RESIDUAL
              6 x "numbers look complete, but the two paths are the same
                  source copy"                 -> SAME_SOURCE_COPY
=======  ===  ==========================================================

The 6 traps are the point of the exercise: they carry anchor, residual, two
paths, a fail-able control, a repro command and a valid provenance digest, so
every naive "count the fields" checker calls them REAL. Two of them even rename
the second path's method; four are byte-identical copies. Independence is
decided by provenance (source sha256 / ref / derived_from), not by the label.

Determinism: every number derives from ``random.Random(seed + index)`` and the
case order is a seeded shuffle of opaque ids (``case-01``..``case-30``), so the
fixture tree is byte-reproducible and the file names leak no label.
"""
from __future__ import annotations

import hashlib
import json
import random
import struct
from datetime import datetime, timezone
from pathlib import Path

import evidence_grade as EG  # sibling module (tools/)

SCHEMA_VERSION = "evidence-contract/1.0"
FIXTURE_VERSION = "synthetic-fixtures/1.0"
DEFAULT_SEED = 20261109
NOUNS = ("alpha", "beta", "gamma")
VERBS = ("calc", "solve", "fit")
TOLERANCE_LIMIT = 1e-5  # routes agree to float32 rounding
#: true when the fixture lane carries an injected-fault negative control
CONTROL_FILE = "control.json"


# ── numeric core (abstract: no units, no physical meaning) ──────────────────
def _method_a(x) -> float:
    """Path A: straightforward double-precision evaluation."""
    return x[0] * 1.7 + x[1] * x[1] * 0.01 - x[2] / 3.0


def _method_b(x) -> float:
    """Path B: same algebra, single-precision intermediates (a genuine second route)."""
    f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]  # noqa: E731
    return (f32(f32(x[0]) * f32(1.7))
            + f32(f32(f32(x[1]) * f32(x[1])) * f32(0.01))
            - f32(f32(x[2]) / f32(3.0)))


def _vector(index: int, seed: int) -> list[float]:
    rng = random.Random(seed + 1000 * index)
    return [round(rng.uniform(0.8, 4.2), 6) for _ in range(3)]


# ── filesystem helpers ──────────────────────────────────────────────────────
def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode()


class _CaseWriter:
    def __init__(self, case_dir: Path):
        self.dir = case_dir
        self.files: dict[str, bytes] = {}

    def write(self, name: str, payload) -> str:
        raw = payload if isinstance(payload, bytes) else _dump(payload)
        (self.dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_bytes(raw)
        self.files[name] = raw
        return _sha(raw)

    def copy_of(self, src: str, dst: str) -> str:
        raw = self.files[src]
        return self.write(dst, raw)


def _artifact(aid: str, kind: str, ref: str, digest: str | None, **extra) -> dict:
    src = {"kind": "file", "ref": ref}
    if digest:
        src["sha256"] = digest
    art = {"id": aid, "kind": kind, "source": src}
    art.update(extra)
    return art


def _base_contract(noun: str, verb: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "capability_id": f"cap.{noun}.{verb}",
        "claim": {
            "kind": "numeric",
            "statement": f"the {noun} pipeline reproduces a documented scalar "
                         f"within a predeclared tolerance",
            "tolerance": {"metric": "rel_abs_diff", "limit": TOLERANCE_LIMIT},
        },
        "repro_cmd": "python3 tools/evidence_grade.py grade --contract "
                     "<case>/contract.json",
    }


# ── case builders ───────────────────────────────────────────────────────────
def _build_core(w: _CaseWriter, noun: str, verb: str, index: int) -> tuple[dict, float, float]:
    """Write anchor + two independent paths + a fail-able control."""
    # both routes must evaluate the SAME quantity from the SAME inputs; only the
    # arithmetic (precision) differs, which is exactly what a residual measures
    x = _vector(index, DEFAULT_SEED)
    pa = _method_a(x)
    pb = _method_b(x)
    pa_hash = w.write("path-a.json", {"method": "method_a", "value": pa, "inputs": x})
    pb_hash = w.write("path-b.json", {"method": "method_b", "value": pb, "inputs": x})

    # the anchor is the neutral consensus of both routes, so the residual is a
    # genuine (non-zero) disagreement rather than a self-comparison
    anchor_value = 0.5 * (pa + pb)
    a_value, a_hash = anchor_value, w.write("anchor.json",
                                            {"method": "consensus", "value": anchor_value,
                                             "inputs": x})
    best = min((pa, "path-a.json", pa_hash), (pb, "path-b.json", pb_hash),
               key=lambda t: abs(t[0] - a_value))
    residual = abs(a_value - best[0]) / abs(a_value) if a_value else abs(a_value - best[0])
    res_hash = w.write("residual.json", {"method": "rel_abs_diff", "value": residual})
    ctrl_hash = w.write(CONTROL_FILE, {"outcome": "fail",
                                       "value": 250.0,
                                       "metric": "injected_rel_offset",
                                       "detail": "injected offset exceeds the predeclared limit"})

    arts = {
        "anchor": _artifact("anchor", "anchor", "anchor.json", a_hash,
                            value=a_value, method="method_a"),
        "path_a": _artifact("path-a", "independent_path", "path-a.json", pa_hash,
                            value=pa, method="method_a"),
        "path_b": _artifact("path-b", "independent_path", "path-b.json", pb_hash,
                            value=pb, method="method_b"),
        "residual": _artifact("residual", "residual", "residual.json", res_hash,
                              value=residual, method="rel_abs_diff"),
        "control": _artifact("control", "negative_control", CONTROL_FILE, ctrl_hash,
                             expectation="must_fail", outcome="fail"),
    }
    return arts, a_value, residual


def _finalize(contract: dict) -> dict:
    contract["provenance"] = {"sha256": EG.provenance_digest(contract["artifacts"]),
                              "tool": "evidence_fixtures/v1"}
    return contract


def _real(w: _CaseWriter, noun: str, verb: str, index: int, single_path: bool) -> dict:
    arts, _, _ = _build_core(w, noun, verb, index)
    c = _base_contract(noun, verb)
    chosen = [arts["anchor"], arts["path_a"]]
    if not single_path:
        chosen.append(arts["path_b"])
    c["artifacts"] = chosen + [arts["residual"], arts["control"]]
    return _finalize(c)


def _mock_inline(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    x = _vector(index, DEFAULT_SEED)
    value = round(_method_a(x), 6)
    arts, _, _ = _build_core(w, noun, verb, index)
    c = _base_contract(noun, verb)
    inline_anchor = {"id": "anchor", "kind": "anchor",
                     "source": {"kind": "inline", "ref": repr(value)},
                     "value": value, "method": "method_a"}
    c["artifacts"] = [inline_anchor, arts["path_a"], arts["path_b"],
                      arts["residual"], arts["control"]]
    return _finalize(c)


def _mock_missing_repro(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    c = _real(w, noun, verb, index, single_path=False)
    c.pop("repro_cmd", None)
    return _finalize(c)


def _mock_no_anchor(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    arts, _, _ = _build_core(w, noun, verb, index)
    c = _base_contract(noun, verb)
    c["artifacts"] = [arts["path_a"], arts["path_b"], arts["residual"], arts["control"]]
    return _finalize(c)


def _mock_bad_hash(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    arts, _, _ = _build_core(w, noun, verb, index)
    arts["anchor"]["source"]["sha256"] = "0" * 64
    c = _base_contract(noun, verb)
    c["artifacts"] = [arts["anchor"], arts["path_a"], arts["path_b"],
                      arts["residual"], arts["control"]]
    return _finalize(c)


def _stub(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    w.write("interface.txt", {"entry_point": f"{noun}.{verb}(x) -> scalar",
                              "implemented": False})
    return {
        "schema_version": SCHEMA_VERSION,
        "capability_id": f"cap.{noun}.{verb}",
        "claim": {"kind": "interface_only",
                  "statement": f"{noun} entry point is declared; no numeric claim"},
        "artifacts": [{
            "id": "api",
            "kind": "interface",
            "source": {"kind": "inline", "ref": f"{noun}.{verb}(x) -> scalar"},
            "notes": "surface only: no number, no path, no control",
        }],
        "repro_cmd": "python3 tools/evidence_grade.py grade --contract "
                     "<case>/contract.json",
    }


def _withheld_no_residual(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    arts, _, _ = _build_core(w, noun, verb, index)
    c = _base_contract(noun, verb)
    c["artifacts"] = [arts["anchor"], arts["path_a"], arts["path_b"], arts["control"]]
    return _finalize(c)


def _trap_same_bytes(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    """Two 'independent' paths, second is a byte-identical copy of the first."""
    arts, _, _ = _build_core(w, noun, verb, index)
    copy_hash = w.copy_of("path-a.json", "path-b.json")
    arts["path_b"] = _artifact("path-b", "independent_path", "path-b.json", copy_hash,
                               value=arts["path_a"]["value"], method="independent_beta")
    c = _base_contract(noun, verb)
    c["artifacts"] = [arts["anchor"], arts["path_a"], arts["path_b"],
                      arts["residual"], arts["control"]]
    return _finalize(c)


def _trap_declared_derivation(w: _CaseWriter, noun: str, verb: str, index: int) -> dict:
    """Second path has its own bytes but declares derivation from the first."""
    arts, _, _ = _build_core(w, noun, verb, index)
    arts["path_b"]["derived_from"] = ["path-a"]
    c = _base_contract(noun, verb)
    c["artifacts"] = [arts["anchor"], arts["path_a"], arts["path_b"],
                      arts["residual"], arts["control"]]
    return _finalize(c)


# ── assembly ────────────────────────────────────────────────────────────────
#: (label, expected reason code, builder, count)
LANES = (
    ("REAL", "ALL_GATES_PASS", "real", 8),
    ("MOCK", "HARDCODED_INLINE", "mock_inline", 3),
    ("MOCK", "NO_REPRO_CMD", "mock_missing_repro", 2),
    ("MOCK", "NO_ANCHOR", "mock_no_anchor", 2),
    ("MOCK", "ANCHOR_NOT_RECOMPUTABLE", "mock_bad_hash", 1),
    ("STUB", "INTERFACE_ONLY", "stub", 6),
    ("WITHHELD", "NO_RESIDUAL", "withheld_no_residual", 2),
    ("WITHHELD", "SAME_SOURCE_COPY", "trap_same_bytes", 4),
    ("WITHHELD", "SAME_SOURCE_COPY", "trap_declared_derivation", 2),
)

_BUILDERS = {
    "real": _real,
    "mock_inline": _mock_inline,
    "mock_missing_repro": _mock_missing_repro,
    "mock_no_anchor": _mock_no_anchor,
    "mock_bad_hash": _mock_bad_hash,
    "stub": _stub,
    "withheld_no_residual": _withheld_no_residual,
    "trap_same_bytes": _trap_same_bytes,
    "trap_declared_derivation": _trap_declared_derivation,
}


def build_cases(seed: int | None = None, root: Path | None = None) -> list[dict]:
    """Return the 30 hand-labelled cases as in-memory dicts (deterministic).

    ``root`` is the fixture root directory; when given, artifact files are
    materialised under ``root/<case_id>/``.
    """
    seed = DEFAULT_SEED if seed is None else int(seed)
    spec: list[tuple[str, str, str]] = []
    for label, code, builder, count in LANES:
        spec.extend([(label, code, builder)] * count)

    order = list(range(len(spec)))
    random.Random(seed).shuffle(order)
    cases: list[dict] = []
    for slot, idx in enumerate(order):
        label, code, builder = spec[idx]
        case_id = f"case-{slot + 1:02d}"
        case_dir = Path(root) / case_id if root is not None else Path(".") / case_id
        if root is not None:
            case_dir.mkdir(parents=True, exist_ok=True)
        w = _CaseWriter(case_dir)
        if builder == "real":
            contract = _real(w, NOUNS[idx % 3], VERBS[idx % 3], idx, single_path=(idx % 4 == 0))
        else:
            contract = _BUILDERS[builder](w, NOUNS[idx % 3], VERBS[idx % 3], idx)
        case = {"case_id": case_id,
                "expected_grade": label,
                "expected_reason_code": code,
                "builder": builder,
                "contract": contract,
                "files": sorted(w.files)}
        # capability ids must be unique per case so reconciliation has a 1:1 key
        contract["capability_id"] = f"cap.{NOUNS[idx % 3]}.{VERBS[idx % 3]}.{case_id}"
        if root is not None:
            (case_dir / "contract.json").write_bytes(_dump(contract))
        case["capability_id"] = contract["capability_id"]
        cases.append(case)
    return cases


def write_fixtures(out_dir, seed: int | None = None) -> dict:
    """Materialise the fixture tree; return its manifest (counts + hashes)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = DEFAULT_SEED if seed is None else int(seed)
    cases = build_cases(seed=seed, root=out_dir)

    labels = {c["case_id"]: {
        "grade": c["expected_grade"],
        "reason_code": c["expected_reason_code"],
        "builder": c["builder"],
        "note": "label fixed by construction in tools/evidence_fixtures.py LANES",
    } for c in cases}
    (out_dir / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")

    counts: dict[str, int] = {}
    for c in cases:
        counts[c["expected_grade"]] = counts.get(c["expected_grade"], 0) + 1
    traps = [c["case_id"] for c in cases if c["expected_reason_code"] == "SAME_SOURCE_COPY"]
    manifest = {
        "schema_version": FIXTURE_VERSION,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "trap_cases": traps,
        "case_capability": {c["capability_id"]: c["case_id"] for c in cases},
        "capability_case": {c["case_id"]: c["capability_id"] for c in cases},
        "cases": {c["case_id"]: {
            "expected_grade": c["expected_grade"],
            "expected_reason_code": c["expected_reason_code"],
            "builder": c["builder"],
            "contract_sha256": _sha(_dump(c["contract"])),
        } for c in cases},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


if __name__ == "__main__":
    here = Path(__file__).resolve().parents[1] / "artifacts" / "evidence_grade" / "fixtures"
    print(json.dumps(write_fixtures(here)["counts"], ensure_ascii=False, indent=2))