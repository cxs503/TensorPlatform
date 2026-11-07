"""Cross-ISA floating-point reproducibility protocol (ULP + canonical quantization).

Two-layer judging model
-----------------------
1. **Canonical quantization** ("does the result survive at 12 significant
   decimal digits?").  Every quantity is mapped to a canonical string via
   ``format(x, '.12g')`` (NaN / +-Inf / signed zero normalised), and the digest
   is taken over those strings.  This is the *semantic* equality layer: it is
   scale invariant and immune to last-bit noise.
2. **ULP gate** ("how far apart, in units in the last place?").  The distance
   between two IEEE-754 doubles measured in monotone integer key space, so
   ``ulp_distance(a, b)`` is the number of representable doubles between them.

Both layers must pass: quantization alone cannot see a 1e-13 perturbation that
sits below the 12th significant digit, and the ULP gate alone cannot tell a
1e-300 wobble of a near-zero value from a physical discrepancy.  See
``docs/REPRODUCIBILITY_CROSS_ISA.md`` for the measured gate value ``k``.

Nothing in this module depends on numpy; it is stdlib-only so that it can run
unchanged on any node of the cluster.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from . import spec as _spec

DEFAULT_DIGITS = 12

#: Sentinel returned by :func:`ulp_distance` when two values are not both
#: finite (e.g. one is NaN and the other is 1.0).  Larger than any real distance
#: on the 64-bit monotone key line (which is bounded by 2**64 - 1).
ULP_UNBOUNDED = (1 << 64) - 1

SIGN_BIT = 1 << 63
MASK63 = (1 << 63) - 1


class ReproViolation(AssertionError):
    """Raised when a candidate run fails the reproducibility protocol."""


# --------------------------------------------------------------------------- #
# hex <-> double plumbing (%a round-trip; the C kernels emit exactly this)
# --------------------------------------------------------------------------- #
def decode_hex(token: str) -> float:
    """Decode a C ``%a`` token (or ``nan``/``inf``/``-inf``) into a float."""
    if not isinstance(token, str):
        raise TypeError("hex token must be a string, got %r" % (token,))
    t = token.strip()
    low = t.lower()
    if low in ("nan", "+nan", "-nan"):
        return math.nan
    if low in ("inf", "+inf", "infinity", "+infinity"):
        return math.inf
    if low in ("-inf", "-infinity"):
        return -math.inf
    try:
        return float.fromhex(t)
    except (ValueError, OverflowError) as exc:  # pragma: no cover - defensive
        raise ValueError("not a hex float token: %r" % (token,)) from exc


def encode_hex(x: float) -> str:
    """Inverse of :func:`decode_hex` (exact, C-compatible ``%a`` form)."""
    if isinstance(x, str):
        raise TypeError("encode_hex expects a float, got str %r" % (x,))
    x = float(x)
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return float.hex(x)


def _bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def monotone_key(x: float) -> int:
    """Map a double onto a monotone integer line (Gustafson-style).

    ``+0.0`` and ``-0.0`` map to the same key 0, consecutive representable
    doubles are consecutive integers, so ``|key(a) - key(b)|`` is the ULP
    distance between ``a`` and ``b`` including across zero and across signs.
    """
    b = _bits(x)
    if b & SIGN_BIT:
        return -(b & MASK63)
    return b


def ulp_distance(a: float, b: float) -> int:
    """Number of representable doubles between ``a`` and ``b``.

    * finite vs finite      -> exact integer distance (0 iff bit-identical or
      +-0.0 pair)
    * matching non-finites   -> 0 (NaN/NaN or +Inf/+Inf or -Inf/-Inf)
    * non-finite mismatch    -> :data:`ULP_UNBOUNDED`
    """
    a = float(a)
    b = float(b)
    fa, fb = math.isfinite(a), math.isfinite(b)
    if not fa or not fb:
        if math.isnan(a) and math.isnan(b):
            return 0
        if math.isinf(a) and math.isinf(b) and (a > 0) == (b > 0):
            return 0
        return ULP_UNBOUNDED
    return abs(monotone_key(a) - monotone_key(b))


# --------------------------------------------------------------------------- #
# canonical quantization (12 significant decimal digits by default)
# --------------------------------------------------------------------------- #
def quantize(x: float, digits: int = DEFAULT_DIGITS) -> str:
    """Canonical string key at ``digits`` significant decimal digits.

    Mapping is *many-to-one by construction*: two values whose 12-digit
    representations agree are declared semantically identical.  Signed zero
    collapses to ``'0'``; NaN/Inf get their own canonical tokens.
    """
    if digits < 1:
        raise ValueError("digits must be >= 1")
    x = float(x)
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    if x == 0.0:
        return "0"
    return format(x, ".%dg" % digits)


def quantize_value(x: float, digits: int = DEFAULT_DIGITS) -> float:
    """Quantized *value* (grid point) rather than its string key."""
    q = quantize(x, digits)
    if q in ("nan", "inf", "-inf"):
        return float(q)
    return float(q)


# --------------------------------------------------------------------------- #
# digests
# --------------------------------------------------------------------------- #
def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def bit_digest(values: Sequence[float]) -> str:
    """Naive bit digest: SHA-256 over little-endian IEEE-754 payload bytes.

    This is the gate that *fails* across architectures/compiler flags; it is
    kept in the artefact precisely so that the failure is measurable.
    """
    buf = bytearray()
    for v in values:
        buf += struct.pack("<d", float(v))
    return _sha256(bytes(buf))


def quant_digest(values: Sequence[float], digits: int = DEFAULT_DIGITS) -> str:
    """Protocol digest: SHA-256 over canonical quantized strings."""
    return _sha256("\x1f".join(quantize(v, digits) for v in values).encode("ascii"))


# --------------------------------------------------------------------------- #
# record loading / digest emission
# --------------------------------------------------------------------------- #
def load_record(obj: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalise a kernel JSON record into ``{bench, meta, fields, scalars, traj}``.

    All hex tokens become floats.  The input is not mutated.
    """
    if not isinstance(obj, Mapping):
        raise TypeError("record must be a mapping")
    if "bench" not in obj:
        raise ValueError("record is missing 'bench'")
    rec: Dict[str, Any] = {
        "bench": str(obj["bench"]),
        "meta": dict(obj.get("meta") or {}),
        "fields": {},
        "scalars": {},
        "traj": [],
        "data": {},
    }
    for name, blob in (obj.get("fields") or {}).items():
        rec["fields"][str(name)] = [decode_hex(t) for t in blob["values"]]
    for name, blob in (obj.get("data") or {}).items():
        rec["data"][str(name)] = [decode_hex(t) for t in blob["values"]]
    for name, token in (obj.get("scalars") or {}).items():
        rec["scalars"][str(name)] = decode_hex(token)
    for pt in obj.get("traj") or []:
        rec["traj"].append(
            {"step": int(pt["step"]), "probe": [decode_hex(t) for t in pt["probe"]]}
        )
    return rec


def _vec_digest(values: Sequence[float], digits: int) -> Dict[str, Any]:
    return {
        "n": len(values),
        "bit_digest": bit_digest(values),
        "quant_digest": quant_digest(values, digits),
        "nonfinite": sum(1 for v in values if not math.isfinite(v)),
    }


def digest_record(rec: Mapping[str, Any], digits: Optional[int] = None) -> Dict[str, Any]:
    """The ``--emit-digest`` payload: digests only, no raw values."""
    if digits is None:
        digits = _spec.digits_for(rec["bench"])
    out: Dict[str, Any] = {
        "bench": rec["bench"],
        "meta": rec.get("meta", {}),
        "protocol": {"digits": digits, "hash": "sha256"},
        "fields": {k: dict(_vec_digest(v, digits),
                           **{"class": _spec.class_of(rec["bench"], k)})
                   for k, v in rec.get("fields", {}).items()},
        "scalars": {k: {"hex": encode_hex(v), "quant": quantize(v, digits),
                        "class": _spec.class_of(rec["bench"], k)}
                    for k, v in rec.get("scalars", {}).items()},
        "traj": [
            {"step": pt["step"], **_vec_digest(pt["probe"], digits)}
            for pt in rec.get("traj", [])
        ],
    }
    all_vals = [v for k in sorted(rec.get("fields", {})) for v in rec["fields"][k]]
    obs_vals = [v for k in sorted(rec.get("fields", {}))
                if _spec.class_of(rec["bench"], k) == _spec.OBSERVABLE
                for v in rec["fields"][k]]
    obs_vals += [v for k in sorted(rec.get("scalars", {}))
                 if _spec.class_of(rec["bench"], k) == _spec.OBSERVABLE
                 for v in [rec["scalars"][k]]]
    out["record_digest"] = {
        "bit": bit_digest(all_vals),
        "quant": quant_digest(obs_vals, digits),
        "quant_observables": quant_digest(obs_vals, digits),
        "quant_all": quant_digest(all_vals, digits),
        "n_observables": len(obs_vals),
        "n_all": len(all_vals),
    }
    return out


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #
def _compare_vectors(ref: Sequence[float], cur: Sequence[float], label: str,
                     digits: int = DEFAULT_DIGITS) -> Dict[str, Any]:
    if len(ref) != len(cur):
        return {
            "label": label,
            "ok": False,
            "reason": "length mismatch %d != %d" % (len(ref), len(cur)),
            "max_ulp": ULP_UNBOUNDED,
            "quant_equal": False,
            "bit_equal": False,
        }
    worst = 0
    worst_i = -1
    nonfinite_mismatch = 0
    nan_pairs = 0
    max_rel = 0.0
    max_abs = 0.0
    for i, (a, b) in enumerate(zip(ref, cur)):
        if math.isnan(a) and math.isnan(b):
            nan_pairs += 1
            continue
        d = ulp_distance(a, b)
        if d >= ULP_UNBOUNDED:
            nonfinite_mismatch += 1
        else:
            if math.isfinite(a) and math.isfinite(b):
                max_abs = max(max_abs, abs(b - a))
                if a != 0.0:
                    max_rel = max(max_rel, abs(b - a) / abs(a))
        if d > worst:
            worst, worst_i = d, i
    quant_eq = quant_digest(ref, digits) == quant_digest(cur, digits)
    bit_eq = bit_digest(ref) == bit_digest(cur)
    return {
        "label": label,
        "n": len(ref),
        "ok": quant_eq and worst < ULP_UNBOUNDED,
        "bit_equal": bit_eq,
        "quant_equal": quant_eq,
        "max_ulp": worst,
        "argmax_index": worst_i,
        "ref_at_argmax": encode_hex(ref[worst_i]) if worst_i >= 0 else None,
        "cur_at_argmax": encode_hex(cur[worst_i]) if worst_i >= 0 else None,
        "max_rel_diff": max_rel,
        "max_abs_diff": max_abs,
        "nan_pairs": nan_pairs,
        "nonfinite_mismatch": nonfinite_mismatch,
    }


def compare_records(ref: Mapping[str, Any], cur: Mapping[str, Any],
                    digits: Optional[int] = None) -> Dict[str, Any]:
    """Field-by-field comparison of two kernel records.

    Quantities are judged according to their declared class
    (:mod:`tools.repro_digest.spec`):

    * ``observable`` -- ``quant_digest`` equality at ``digits`` significant
      digits **and** ``max_ulp <= k``;
    * ``diagnostic`` -- absolute drift ``<= diag_tol`` (cancelling quantities
      have no meaningful ULP scale); its ULP is reported for information.

    Returns a JSON-serialisable report; numeric divergence never raises here
    (that is :func:`assert_reproducible`'s job), only structural breakage such
    as a missing benchmark field does.
    """
    if ref["bench"] != cur["bench"]:
        raise ValueError("bench mismatch: %r vs %r" % (ref["bench"], cur["bench"]))
    if set(ref["fields"]) != set(cur["fields"]):
        raise ValueError("field set mismatch: %r vs %r"
                         % (sorted(ref["fields"]), sorted(cur["fields"])))
    if set(ref["scalars"]) != set(cur["scalars"]):
        raise ValueError("scalar set mismatch")
    bench = ref["bench"]
    if digits is None:
        digits = _spec.digits_for(bench)

    fields = {k: _compare_vectors(ref["fields"][k], cur["fields"][k], k, digits)
              for k in sorted(ref["fields"])}
    scalars = {k: _compare_vectors([ref["scalars"][k]], [cur["scalars"][k]], k, digits)
               for k in sorted(ref["scalars"])}

    for bucket in (fields, scalars):
        for name, row in bucket.items():
            cls = _spec.class_of(bench, name)
            row["class"] = cls
            if cls == _spec.DIAGNOSTIC:
                tol = _spec.diag_tol(bench, name)
                row["diag_tol"] = tol
                row["diag_ok"] = row["max_abs_diff"] <= tol

    all_rows = list(fields.values()) + list(scalars.values())
    obs = [v for v in all_rows if v["class"] == _spec.OBSERVABLE]
    diag = [v for v in all_rows if v["class"] == _spec.DIAGNOSTIC]

    first_div = _first_divergence_step(ref, cur)
    report = {
        "bench": bench,
        "digits": digits,
        "fields": fields,
        "scalars": scalars,
        "n_observables": len(obs),
        "n_diagnostics": len(diag),
        "max_ulp": max([v["max_ulp"] for v in obs] + [0]),
        "obs_quant_equal": all(v["quant_equal"] for v in obs),
        "obs_bit_equal": all(v["bit_equal"] for v in obs),
        "obs_equal_rate": (sum(1 for v in obs if v["quant_equal"]) / len(obs)) if obs else 1.0,
        "diag_ok": all(v["diag_ok"] for v in diag),
        "quant_digest_equal": all(v["quant_equal"] for v in all_rows),
        "bit_digest_equal": all(v["bit_equal"] for v in all_rows),
        "fields_bit_equal_rate": (sum(1 for v in fields.values() if v["bit_equal"]) / len(fields)) if fields else 1.0,
        "fields_quant_equal_rate": (sum(1 for v in fields.values() if v["quant_equal"]) / len(fields)) if fields else 1.0,
        "obs_names_quant_mismatch": [v["label"] for v in obs if not v["quant_equal"]],
        "diag_names_out_of_tol": [v["label"] for v in diag if not v["diag_ok"]],
        "first_divergence_step": first_div,
        "traj_steps_compared": len(set(p["step"] for p in ref.get("traj", [])) &
                                   set(p["step"] for p in cur.get("traj", []))),
        "ref_meta": dict(ref.get("meta", {})),
        "cur_meta": dict(cur.get("meta", {})),
    }
    return report


def _first_divergence_step(ref: Mapping[str, Any], cur: Mapping[str, Any]):
    ref_traj = {pt["step"]: pt["probe"] for pt in ref.get("traj", [])}
    cur_traj = {pt["step"]: pt["probe"] for pt in cur.get("traj", [])}
    for step in sorted(set(ref_traj) & set(cur_traj)):
        if bit_digest(ref_traj[step]) != bit_digest(cur_traj[step]):
            return step
    return None


def assert_reproducible(ref: Mapping[str, Any], cur: Mapping[str, Any],
                        ulp_gate: int, digits: Optional[int] = None) -> Dict[str, Any]:
    """Protocol gate used by pytest: raise :class:`ReproViolation` on failure.

    Acceptance requires *all three* rails:

    * ``obs_quant_equal`` -- every observable agrees after canonical
      quantization to ``digits`` significant decimal digits;
    * ``max_ulp <= ulp_gate`` -- no observable drifted further than the declared
      bound ``k`` (measured, written into ``artifacts/cross_isa_repro.json``);
    * ``diag_ok`` -- every cancelling diagnostic stays inside its declared
      absolute tolerance.

    Returns the comparison report on success (useful for diagnostics and for
    tests that assert on the margin).
    """
    if ulp_gate < 0:
        raise ValueError("ulp_gate must be >= 0")
    if digits is None:
        digits = _spec.digits_for(ref["bench"])
    n_values = sum(len(v) for v in ref.get("fields", {}).values()) + len(ref.get("scalars", {}))
    if n_values == 0:
        raise ReproViolation("empty record: nothing to compare (0 field values, 0 scalars)")
    report = compare_records(ref, cur, digits)
    problems: List[str] = []
    if report["obs_names_quant_mismatch"]:
        problems.append("quantized digest mismatch (digits=%d) on observables %s"
                        % (digits, report["obs_names_quant_mismatch"]))
    if report["max_ulp"] > ulp_gate:
        obs = [v for v in list(report["fields"].values()) + list(report["scalars"].values())
               if v["class"] == _spec.OBSERVABLE]
        worst = max(obs, key=lambda v: v["max_ulp"])
        problems.append("max_ulp=%d exceeds gate k=%d (%s @ index %s, ref=%s cur=%s)"
                        % (report["max_ulp"], ulp_gate, worst["label"],
                           worst.get("argmax_index"), worst.get("ref_at_argmax"),
                           worst.get("cur_at_argmax")))
    if not report["diag_ok"]:
        bad = [v for v in list(report["fields"].values()) + list(report["scalars"].values())
               if v["class"] == _spec.DIAGNOSTIC and not v["diag_ok"]]
        problems.append("diagnostic absolute tolerance exceeded: %s"
                        % [(v["label"], v["max_abs_diff"], v["diag_tol"]) for v in bad])
    if problems:
        raise ReproViolation("; ".join(problems) + " | first_divergence_step=%s"
                             % report["first_divergence_step"])
    return report


# --------------------------------------------------------------------------- #
# negative-control helper
# --------------------------------------------------------------------------- #
def _first_observable(rec, bucket: str, mapping: Mapping[str, Any]) -> str:
    """First observable quantity in ``mapping`` (diagnostics are skipped: the
    negative control must hit a *gated* quantity to be meaningful)."""
    for k in sorted(mapping):
        if _spec.class_of(rec.get("bench", ""), k) == _spec.OBSERVABLE:
            return k
    return sorted(mapping)[0]


def inject_relative_perturbation(rec: Mapping[str, Any], rel: float = 1e-9,
                                 which: str = "scalar",
                                 name: str | None = None,
                                 index: int = 0) -> Dict[str, Any]:
    """Copy ``rec`` with one quantity nudged by ``rel`` relative (default 1e-9).

    Used as the protocol's negative control: a perturbation this size must be
    *rejected*, both by the ULP gate (1e-9 relative ~ 4.5e6 ULP near 1.0) and
    by 12-digit quantization.
    """
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in rec.items()}
    out["fields"] = {k: list(v) for k, v in rec.get("fields", {}).items()}
    out["scalars"] = dict(rec.get("scalars", {}))
    out["traj"] = [{"step": pt["step"], "probe": list(pt["probe"])} for pt in rec.get("traj", [])]
    out["meta"] = dict(rec.get("meta", {}))
    if which == "scalar":
        key = name or _first_observable(rec, "scalars", out["scalars"])
        out["scalars"][key] = out["scalars"][key] * (1.0 + rel)
    elif which == "field":
        key = name or _first_observable(rec, "fields", out["fields"])
        out["fields"][key][index] = out["fields"][key][index] * (1.0 + rel)
    else:
        raise ValueError("which must be 'scalar' or 'field'")
    return out