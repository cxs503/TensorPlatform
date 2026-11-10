"""Unit and dimensional bookkeeping for cross-solver interface contracts.

Mechanics only.  The unit table (spellings, scale factors, dimension vectors,
field-name suffixes) is injected from a JSON document -- see
``config/xsolver/units_si.json`` -- so nothing physical is hard-coded here.

A dimension is a canonical tuple of ``(base_dimension, exponent)`` pairs; two
units are *compatible* when their canonical vectors are equal.  This is the only
thing an interface contract needs in order to answer the question that actually
bites in a solver chain: "does this quantity reach the consumer arm in the unit
the consumer declares?"
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

Dim = Tuple[Tuple[str, float], ...]


# ---------------------------------------------------------------------------
# dimension algebra
# ---------------------------------------------------------------------------
def canonical_dim(dims: Optional[Mapping[str, float]]) -> Dim:
    """Sort a dimension mapping into a hashable canonical vector."""
    if not dims:
        return ()
    return tuple(sorted((str(k), float(v)) for k, v in dims.items() if abs(float(v)) > 1e-12))


def dim_mul(a: Dim, b: Dim) -> Dim:
    acc: Dict[str, float] = dict(a)
    for k, v in b:
        acc[k] = acc.get(k, 0.0) + v
    return canonical_dim(acc)


def dim_div(a: Dim, b: Dim) -> Dim:
    return dim_mul(a, canonical_dim({k: -v for k, v in b}))


def dim_pow(a: Dim, p: float) -> Dim:
    return canonical_dim({k: v * p for k, v in a})


def dim_str(a: Dim) -> str:
    if not a:
        return "1"
    return " ".join(f"{k}^{v:g}" for k, v in a)


def _split_exponent(token: str) -> Tuple[str, float]:
    """``"m2" -> ("m", 2)`` / ``"s" -> ("s", 1)``; exponent must be an integer tail."""
    i = len(token)
    while i > 0 and token[i - 1].isdigit():
        i -= 1
    if i == len(token) or i == 0:
        return token, 1.0
    return token[:i], float(token[i:])


class UnitTable:
    """Parsed unit table with composite-form fallback.

    Accepted spellings, in order of preference: exact table key, ``A_per_B``
    (ratio), then ``A_B`` / ``A_Bn`` (product, ``n`` = integer exponent).
    """

    def __init__(self, spec: Mapping[str, Any]) -> None:
        self._units: Dict[str, Dict[str, Any]] = {}
        for key, entry in (spec.get("units") or {}).items():
            self._units[str(key)] = {
                "scale": float(entry["scale"]),
                "dims": canonical_dim(entry.get("dims") or {}),
            }
        self._quantities: Dict[str, Dim] = {
            str(k): canonical_dim(v) for k, v in (spec.get("quantity_dimensions") or {}).items()
        }
        self._suffixes: Dict[str, str] = {
            str(k): str(v) for k, v in (spec.get("field_suffix_units") or {}).items()
        }
        # case-insensitive view: field names in the wild write ``_MPa`` and ``_hz``
        self._suffixes_ci: Dict[str, str] = {k.lower(): v for k, v in self._suffixes.items()}
        self.base_dimensions = tuple(spec.get("base_dimensions") or ())

    # -- loading ---------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path | str) -> "UnitTable":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- lookup ----------------------------------------------------------
    def parse(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return ``{"scale", "dims", "token", "form"}`` or ``None``."""
        if token is None:
            return None
        text = str(token).strip()
        if not text:
            return None
        if text in self._units:
            entry = self._units[text]
            return {"scale": entry["scale"], "dims": entry["dims"], "token": text, "form": "table"}

        parts = [p for p in text.split("_") if p]
        if len(parts) == 3 and parts[1] == "per":
            num, den = self.parse(parts[0]), self.parse(parts[2])
            if num and den:
                return {
                    "scale": num["scale"] / den["scale"],
                    "dims": dim_div(num["dims"], den["dims"]),
                    "token": text,
                    "form": "ratio",
                }
        if len(parts) >= 2:
            acc = {"scale": 1.0, "dims": ()}
            ok = True
            for part in parts:
                base, exponent = _split_exponent(part)
                unit = self.parse(base)
                if unit is None:
                    ok = False
                    break
                acc = {
                    "scale": acc["scale"] * unit["scale"] ** exponent,
                    "dims": dim_mul(acc["dims"], dim_pow(unit["dims"], exponent)),
                }
            if ok:
                return {"scale": acc["scale"], "dims": acc["dims"], "token": text, "form": "product"}
        return None

    def quantity_dim(self, quantity: Optional[str]) -> Optional[Dim]:
        if not quantity:
            return None
        return self._quantities.get(str(quantity))

    def field_suffix_unit(self, field_name: str) -> Optional[str]:
        """Longest declared suffix of ``field_name`` (``a_b_MPa`` -> ``MPa``)."""
        parts = [p for p in str(field_name).split("_") if p]
        for width in (2, 1):
            if len(parts) >= width:
                candidate = "_".join(parts[-width:]).lower()
                if candidate in self._suffixes_ci:
                    return self._suffixes_ci[candidate]
        return None

    def scale_ratio(self, from_unit: str, to_unit: str) -> Optional[float]:
        """Multiplier converting a value expressed in ``from_unit`` into ``to_unit``."""
        a, b = self.parse(from_unit), self.parse(to_unit)
        if not a or not b or a["dims"] != b["dims"]:
            return None
        return a["scale"] / b["scale"]

    def compatible(self, unit_a: str, unit_b: str) -> bool:
        a, b = self.parse(unit_a), self.parse(unit_b)
        return bool(a and b and a["dims"] == b["dims"])


DEFAULT_UNITS_PATH = Path(__file__).resolve().parents[4] / "config" / "xsolver" / "units_si.json"


def load_default_table(path: Optional[Path | str] = None) -> UnitTable:
    return UnitTable.from_file(path or DEFAULT_UNITS_PATH)