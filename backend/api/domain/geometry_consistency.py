"""
Hull-geometry consistency gate (Cb / L / B / T / displacement identity).

Why this exists
---------------
Platform accuracy rule: **geometry must be validated (Cb/L/B) before any
resistance/stability number is compared against a benchmark**.  Historically the
benchmark harness fed hand-typed presets into the Holtrop route without ever
checking the geometric identity

        Cb == volume_displacement / (L * B * T)          (merchant hulls)

so a wrong displacement (or a beam/draft copy-pasted from a diameter) silently
shifted every error percentage reported downstream.

This module is deliberately dependency-free (stdlib only) and read-only: it does
*not* recompute resistance, it only certifies that a geometry description is
self-consistent (or explains precisely how it is not).

Verdict levels
--------------
PASS            |dev| <= tol_pct                       -> geometry usable
WARN            tol_pct < |dev| <= warn_pct            -> usable, document it
FAIL            |dev| > warn_pct                       -> geometry unusable
INVALID         the parameterisation itself does not represent the body
                (e.g. an axisymmetric hull described as a box with
                 beam == draft == displacement == Cb)

Reference densities: seawater 1025 kg/m3 (ISO 19962 / ITTC), fresh water
1000 kg/m3.  Callers must state the unit of the displacement; the unit is part of
the check, not an afterthought.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

RHO_SEAWATER = 1025.0
RHO_FRESHWATER = 1000.0

PASS, WARN, FAIL, INVALID = "PASS", "WARN", "FAIL", "INVALID"

#: default acceptance band (percent of stated Cb)
DEFAULT_TOL_PCT = 2.0
DEFAULT_WARN_PCT = 5.0


# ---------------------------------------------------------------------------
# unit handling
# ---------------------------------------------------------------------------

def displacement_to_volume(
    value: float,
    unit: str,
    rho: float = RHO_SEAWATER,
) -> float:
    """Convert a displacement to a volume in m3.

    unit: "m3" | "t" (tonnes, mass) | "kg" (kilograms, mass) | "l" (litres).
    """
    if value is None or value <= 0:
        raise ValueError(f"displacement must be positive, got {value!r}")
    u = (unit or "").strip().lower()
    if u in ("m3", "m^3", "m³"):
        return float(value)
    if u in ("t", "tonne", "tonnes", "mt"):
        return float(value) * 1000.0 / rho
    if u in ("kg",):
        return float(value) / rho
    if u in ("l", "litre", "liter"):
        return float(value) / 1000.0
    raise ValueError(f"unknown displacement unit {unit!r}")


# ---------------------------------------------------------------------------
# result container
# ---------------------------------------------------------------------------

@dataclass
class GeometryCheck:
    label: str
    scheme: str
    verdict: str
    volume_m3: Optional[float] = None
    implied_cb: Optional[float] = None
    stated_cb: Optional[float] = None
    rel_dev_pct: Optional[float] = None
    values: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "scheme": self.scheme,
            "verdict": self.verdict,
            "volume_m3": None if self.volume_m3 is None else round(self.volume_m3, 6),
            "implied_cb": None if self.implied_cb is None else round(self.implied_cb, 5),
            "stated_cb": None if self.stated_cb is None else round(self.stated_cb, 5),
            "rel_dev_pct": None if self.rel_dev_pct is None else round(self.rel_dev_pct, 3),
            "values": self.values,
            "note": self.note,
        }


def _verdict(dev_pct: Optional[float], tol_pct: float, warn_pct: float) -> str:
    if dev_pct is None:
        return INVALID
    a = abs(dev_pct)
    if a <= tol_pct:
        return PASS
    if a <= warn_pct:
        return WARN
    return FAIL


# ---------------------------------------------------------------------------
# merchant-hull identity
# ---------------------------------------------------------------------------

def check_merchant_hull(
    label: str,
    length: float,
    beam: float,
    draft: float,
    cb: float,
    displacement: float,
    displacement_unit: str = "m3",
    rho: float = RHO_SEAWATER,
    tol_pct: float = DEFAULT_TOL_PCT,
    warn_pct: float = DEFAULT_WARN_PCT,
) -> GeometryCheck:
    """Check Cb == volume / (L * B * T) for a conventional (monohull) form.

    A degenerate parameterisation (repeated identical constants that cannot be a
    hull: beam == draft == displacement, cb == beam, ...) is reported as INVALID
    rather than as a numerical deviation, because a percentage would legitimise
    a meaningless input.
    """
    values = {
        "length_m": length,
        "beam_m": beam,
        "draft_m": draft,
        "cb_stated": cb,
        "displacement": displacement,
        "displacement_unit": displacement_unit,
        "rho_kg_m3": rho,
    }
    if length <= 0 or beam <= 0 or draft <= 0:
        return GeometryCheck(label, "merchant_hull", INVALID, values=values,
                             note="non-positive principal dimension")
    if not (0.0 < cb <= 1.0):
        return GeometryCheck(label, "merchant_hull", INVALID, values=values,
                             note=f"stated Cb={cb} outside (0, 1]")

    # degenerate / copy-paste preset detection
    degenerate = (
        abs(beam - draft) < 1e-9 and abs(displacement - beam) < 1e-9
        and abs(cb - beam) < 1e-9
    )
    if degenerate:
        return GeometryCheck(
            label, "merchant_hull", INVALID, values=values,
            note=("degenerate preset: beam == draft == displacement == cb "
                  f"== {beam}; the parameter set is a placeholder, not a hull"),
        )

    try:
        vol = displacement_to_volume(displacement, displacement_unit, rho)
    except ValueError as exc:
        return GeometryCheck(label, "merchant_hull", INVALID, values=values,
                             note=str(exc))

    lb = length * beam * draft
    implied = vol / lb
    dev = (implied - cb) / cb * 100.0
    verdict = _verdict(dev, tol_pct, warn_pct)
    note = ""
    if verdict != PASS:
        note = (
            f"volume from displacement ({vol:.6g} m3) over L*B*T ({lb:.6g} m3) "
            f"gives Cb={implied:.4f} vs stated Cb={cb:.4f} ({dev:+.2f}%)"
        )
    return GeometryCheck(label, "merchant_hull", verdict, volume_m3=vol,
                         implied_cb=implied, stated_cb=cb, rel_dev_pct=dev,
                         values=values, note=note)


# ---------------------------------------------------------------------------
# axisymmetric (body of revolution) identity
# ---------------------------------------------------------------------------

def check_body_of_revolution(
    label: str,
    length: float,
    diameter: float,
    volume_m3: Optional[float] = None,
    displacement: Optional[float] = None,
    displacement_unit: str = "m3",
    rho: float = RHO_FRESHWATER,
    wetted_surface_m2: Optional[float] = None,
    expected_volumetric_coef: Optional[float] = None,
    tol_pct: float = DEFAULT_TOL_PCT,
    warn_pct: float = DEFAULT_WARN_PCT,
) -> GeometryCheck:
    """Check the volumetric coefficient of a body of revolution.

    Cv = volume / (pi/4 * D^2 * L).  A submarine-like hull has Cv ~ 0.6-0.9;
    the identity is the axisymmetric counterpart of the merchant-hull Cb check
    and it is the only meaningful geometry gate when there is no waterline beam.

    The optional wetted-surface plausibility test uses the analytical result that
    a streamlined body of revolution has a wetted area between a cylinder side
    area (pi*D*L, lower bound for a very slender body) and 4/pi times that for a
    sphere-equivalent.  It catches diameter/wetted-area mismatches such as the
    common 2x error of quoting the surface of a full hull.
    """
    a_ref = 3.141592653589793 / 4.0 * diameter ** 2 * length
    values = {
        "length_m": length,
        "diameter_m": diameter,
        "reference_area_piD2_4L_m3": a_ref,
        "wetted_surface_m2": wetted_surface_m2,
    }
    if length <= 0 or diameter <= 0:
        return GeometryCheck(label, "body_of_revolution", INVALID, values=values,
                             note="non-positive principal dimension")

    vol = volume_m3
    if vol is None and displacement is not None:
        vol = displacement_to_volume(displacement, displacement_unit, rho)

    if vol is None:
        # Surface-only gate: many published axisymmetric benchmarks (e.g. DARPA
        # SUBOFF) quote the wetted surface but not the displaced volume.  The
        # surface ratio S/(pi*D*L) is then the only geometry evidence available
        # and it is checked on its own rather than skipped.
        if wetted_surface_m2 is None:
            return GeometryCheck(label, "body_of_revolution", INVALID, values=values,
                                 note="neither volume, displacement nor wetted surface supplied")
        s_ref = wetted_surface_m2 / (3.141592653589793 * diameter * length)
        values["S_over_piDL"] = s_ref
        ok = 0.30 <= s_ref <= 1.60
        return GeometryCheck(
            label, "body_of_revolution", PASS if ok else FAIL, values=values,
            note=("surface-only check: S/(pi*D*L)=%.3f %s band [0.30, 1.60]; "
                  "displaced volume not declared, Cv unverified" % (s_ref, "inside" if ok else "outside")),
        )

    cv = vol / a_ref
    values["volumetric_coef"] = cv
    dev = None
    verdict = PASS
    note = ""
    if expected_volumetric_coef is not None:
        dev = (cv - expected_volumetric_coef) / expected_volumetric_coef * 100.0
        verdict = _verdict(dev, tol_pct, warn_pct)
        if verdict != PASS:
            note = (f"Cv={cv:.4f} vs expected {expected_volumetric_coef:.4f} "
                    f"({dev:+.2f}%)")
    else:
        # plausibility band for a streamlined body of revolution
        if not (0.40 <= cv <= 1.00):
            verdict = FAIL
            note = f"Cv={cv:.4f} outside plausible band [0.40, 1.00]"

    if wetted_surface_m2 is not None and verdict != INVALID:
        s_ref = wetted_surface_m2 / (3.141592653589793 * diameter * length)
        values["S_over_piDL"] = s_ref
        if not (0.30 <= s_ref <= 1.60):
            verdict = FAIL
            note += (f"; wetted surface {wetted_surface_m2} m2 -> S/(pi*D*L)="
                     f"{s_ref:.3f} outside [0.30, 1.60]")

    return GeometryCheck(label, "body_of_revolution", verdict, volume_m3=vol,
                         implied_cb=cv, stated_cb=expected_volumetric_coef,
                         rel_dev_pct=dev, values=values, note=note)


# ---------------------------------------------------------------------------
# ship-type aware dispatch for benchmark datasets
# ---------------------------------------------------------------------------

#: dataset geometry keys -> (unit, scheme)
_UNIT_KEYS = (
    ("displacement_t", "t"),
    ("displacement_kg", "kg"),
    ("displacement_m3", "m3"),
    ("displacement", "m3"),
)


def check_dataset_geometry(
    case_id: str,
    ship_type: str,
    geometry: Dict[str, Any],
    rho: float = RHO_SEAWATER,
) -> GeometryCheck:
    """Gate one geometry record from ``benchmark_reference.json``.

    ``scheme`` is inferred: a record with ``diameter_m`` and no ``beam_m`` is a
    body of revolution; anything else is treated as a merchant hull.  Merchant
    hulls additionally require a stated ``cb`` -- without it there is nothing to
    check against and the record is INVALID (silence is not a pass).
    """
    g = dict(geometry or {})
    label = f"{case_id}:{ship_type}"

    if "diameter_m" in g and "beam_m" not in g:
        disp, unit = None, "m3"
        for key, u in _UNIT_KEYS:
            if key in g:
                disp, unit = g[key], u
                break
        return check_body_of_revolution(
            label,
            length=float(g.get("length_m", 0.0)),
            diameter=float(g["diameter_m"]),
            volume_m3=float(g["volume_m3"]) if "volume_m3" in g else None,
            displacement=None if disp is None else float(disp),
            displacement_unit=unit,
            rho=rho,
            wetted_surface_m2=(float(g["wetted_surface_m2"])
                               if "wetted_surface_m2" in g else None),
            expected_volumetric_coef=g.get("cv"),
        )

    disp, unit = None, "m3"
    for key, u in _UNIT_KEYS:
        if key in g:
            disp, unit = g[key], u
            break
    if disp is None or "cb" not in g:
        return GeometryCheck(
            label, "merchant_hull", INVALID,
            values=dict(g),
            note="record lacks a stated Cb and/or displacement: geometry unverified",
        )
    return check_merchant_hull(
        label,
        length=float(g.get("length_m", 0.0)),
        beam=float(g.get("beam_m", 0.0)),
        draft=float(g.get("draft_m", 0.0)),
        cb=float(g["cb"]),
        displacement=float(disp),
        displacement_unit=unit,
        rho=rho,
    )


def check_preset(
    ship_type: str,
    preset: Dict[str, Any],
    rho: float = RHO_SEAWATER,
) -> GeometryCheck:
    """Gate one hand-typed preset (``SHIP_GEOMETRY`` style: length/beam/draft/cb/
    displacement, unit implied by the harness).

    The preset itself may carry ``displacement_unit`` and ``geometry_status``;
    a preset that declares ``geometry_status`` other than "VERIFIED" is reported
    as INVALID so that an acknowledged approximation can never be mistaken for a
    verified geometry.
    """
    label = f"preset:{ship_type}"
    status = str(preset.get("geometry_status", "VERIFIED")).upper()
    unit = str(preset.get("displacement_unit", "m3"))
    check = check_merchant_hull(
        label,
        length=float(preset.get("length", 0.0)),
        beam=float(preset.get("beam", 0.0)),
        draft=float(preset.get("draft", 0.0)),
        cb=float(preset.get("cb", 0.0)),
        displacement=float(preset.get("displacement", 0.0)),
        displacement_unit=unit,
        rho=rho,
    )
    if status != "VERIFIED":
        check.values["geometry_status"] = status
        check.verdict = INVALID
        check.note = (check.note + "; " if check.note else "") + (
            f"preset self-declares geometry_status={status}"
        )
    return check


# ---------------------------------------------------------------------------
# batch audit
# ---------------------------------------------------------------------------

def summarise(checks: Iterable[GeometryCheck]) -> Dict[str, Any]:
    checks = list(checks)
    counts = {k: 0 for k in (PASS, WARN, FAIL, INVALID)}
    for c in checks:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1
    worst = None
    for c in checks:
        if c.rel_dev_pct is None:
            continue
        if worst is None or abs(c.rel_dev_pct) > abs(worst.rel_dev_pct):
            worst = c
    return {
        "total": len(checks),
        "counts": counts,
        "max_abs_dev_pct": None if worst is None else round(abs(worst.rel_dev_pct), 3),
        "max_abs_dev_label": None if worst is None else worst.label,
        "gate_passed": counts[FAIL] == 0 and counts[INVALID] == 0,
    }


def audit_dataset(
    dataset: Dict[str, Any],
    presets: Optional[Dict[str, Dict[str, Any]]] = None,
    rho: float = RHO_SEAWATER,
) -> Dict[str, Any]:
    """Audit a benchmark dataset (and optional presets) -> machine-readable report."""
    checks: List[GeometryCheck] = []
    seen = set()
    for case in dataset.get("cases", []):
        key = (case.get("ship_type"), repr(sorted((case.get("geometry") or {}).items())))
        if key in seen:            # identical geometry reused across cases
            continue
        seen.add(key)
        checks.append(check_dataset_geometry(
            case.get("case_id", "?"), case.get("ship_type", "?"),
            case.get("geometry") or {}, rho=rho,
        ))
    preset_checks: List[GeometryCheck] = []
    for name, preset in (presets or {}).items():
        preset_checks.append(check_preset(name, preset, rho=rho))
    return {
        "dataset": dataset.get("description", ""),
        "dataset_version": dataset.get("version", ""),
        "rho_kg_m3": rho,
        "tolerance_pct": {"pass": DEFAULT_TOL_PCT, "warn": DEFAULT_WARN_PCT},
        "dataset_geometry": [c.as_dict() for c in checks],
        "presets": [c.as_dict() for c in preset_checks],
        "dataset_summary": summarise(checks),
        "preset_summary": summarise(preset_checks),
    }