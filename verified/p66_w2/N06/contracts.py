"""Cross-module interface contracts for the P66 multi-physics coupling gate.

P66 couples three modules of the ice-navigation multi-physics chain:

==============  ==========================================================  ==========================
module          payload                                                     producer
==============  ==========================================================  ==========================
P66-N03         ice *abrasion-field conditions* on the hull ice belt:        TensorDEM (abrasion-field
                per-patch contact pressure ``p_ice_Pa``, slip velocity      conditions) -- **not merged
                ``v_slip_ms``, normal approach ``u_n_ms``, local plating    in main**, see below
                angle ``alpha_rad``, patch measures ``s_m``/``area_m2``
P66-N04         full-ship *resistance chain*: ``R_crush``, ``R_break``,      TensorPhysics
                ``R_submersion``, ``R_ice``, ``R_ow``, ``R_total`` on an    (``tensorphysics.ice.resistance``
                ``(h, V)`` grid + validity-domain membership                -- merged in main)
P66-N05         spatio-temporal *abrasion-field evolution*: per-patch        TensorDEM / TensorPhysics
                depth ``depth_m``, wear rate ``dh_dt_ms``, ``mass_loss_kg``  -- **not merged in main**
==============  ==========================================================  ==========================

Upstream availability (checked in this gate, recorded as ``upstream_status``)
---------------------------------------------------------------------------
``ice.resistance`` (the N04 numerical half) is part of ``main``.  The DEM-side
producers N03/N05 are *not* present in ``main`` as of this node, so the gate
consumes **contract-conforming reference producers**
(:mod:`tensorphysics.campaign.p66_gate.producers`); they are labelled
``REFERENCE-PRODUCER`` everywhere, they are analytic closures (never solver
kernels), and when the real DEM producer lands it must deliver the same
payload -- the gate code does not change.  Every check in this module is
written against the payload only, so the substitution is a plan-level, not a
code-level, change.

Two independent classes of machine check
----------------------------------------
1. **Schema** -- the repository's dependency-free JSON-Schema subset
   (:func:`tensorphysics.campaign.schema.validate`), with
   ``additionalProperties: false`` everywhere, so a silent extra field is a
   contract break rather than a tolerated extension.
2. **Dimension / scale** -- every numeric field carries a declared unit string
   (``units`` map).  :func:`parse_unit` returns the SI base-dimension vector
   *and* the scale relative to SI, so ``mm``, ``km``, ``kn``, ``h`` (hour) and
   ``t`` (tonne) are rejected: a producer that silently switches to knots and
   millimetres is exactly the defect class this check exists for.  Derived
   identities (wear rate ``= k_w p v / H``) are checked dimensionally as well.

Nothing here imports numpy or a solver: the module is pure validation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping

from ..schema import SchemaError, payload_digest, validate

# ---------------------------------------------------------------------------
# interface contract names
# ---------------------------------------------------------------------------

SCHEMA_N03 = "p66_abrasion_field_conditions.v1"
SCHEMA_N04 = "p66_resistance_chain.v1"
SCHEMA_N05 = "p66_abrasion_evolution.v1"
SCHEMA_EXCHANGE = "p66_interface_exchange.v1"

CONTRACT_N03 = "abrasion_field_conditions_n03_v1"
CONTRACT_N04 = "resistance_chain_n04_v1"
CONTRACT_N05 = "abrasion_evolution_n05_v1"
CONTRACT_EXCHANGE = "interface_exchange_n03_n05_v1"

UPSTREAM = {
    CONTRACT_N03: {
        "repo": "TensorDEM",
        "node": "P66-W2-N03",
        "in_main": False,
        "status": "REFERENCE-PRODUCER (contract frozen, upstream module not merged in main)",
    },
    CONTRACT_N04: {
        "repo": "TensorPhysics",
        "node": "P66-W2-N04",
        "in_main": True,
        "status": "MERGED (tensorphysics.ice.resistance)",
    },
    CONTRACT_N05: {
        "repo": "TensorDEM",
        "node": "P66-W2-N05",
        "in_main": False,
        "status": "REFERENCE-PRODUCER (contract frozen, upstream module not merged in main)",
    },
}

# ---------------------------------------------------------------------------
# SI dimension algebra
# ---------------------------------------------------------------------------

DIM_NAMES = ("kg", "m", "s", "K", "A", "mol", "cd")
DIMENSIONLESS = (0, 0, 0, 0, 0, 0, 0)


class UnitError(ValueError):
    """A declared unit string is unknown, malformed, or dimensionally wrong."""


def _dim(*pairs: tuple[str, int]) -> tuple[int, ...]:
    out = [0] * len(DIM_NAMES)
    for name, exponent in pairs:
        out[DIM_NAMES.index(name)] += exponent
    return tuple(out)


#: unit symbol -> (SI base dimension vector, scale relative to SI)
_UNITS: dict[str, tuple[tuple[int, ...], float]] = {
    "-": (DIMENSIONLESS, 1.0),
    "1": (DIMENSIONLESS, 1.0),
    "": (DIMENSIONLESS, 1.0),
    "rad": (DIMENSIONLESS, 1.0),
    "deg": (DIMENSIONLESS, 1.0),
    "kg": (_dim(("kg", 1)), 1.0),
    "g": (_dim(("kg", 1)), 1e-3),
    "m": (_dim(("m", 1)), 1.0),
    "s": (_dim(("s", 1)), 1.0),
    "K": (_dim(("K", 1)), 1.0),
    "N": (_dim(("kg", 1), ("m", 1), ("s", -2)), 1.0),
    "Pa": (_dim(("kg", 1), ("m", -1), ("s", -2)), 1.0),
    "J": (_dim(("kg", 1), ("m", 2), ("s", -2)), 1.0),
    "W": (_dim(("kg", 1), ("m", 2), ("s", -3)), 1.0),
    "Hz": (_dim(("s", -1)), 1.0),
    "t": (_dim(("kg", 1)), 1e3),  # tonne: dimensionally kg, wrong scale
    "kn": (_dim(("m", 1), ("s", -1)), 1852.0 / 3600.0),
    "min": (_dim(("s", 1)), 60.0),
    "h": (_dim(("s", 1)), 3600.0),
}

_PREFIXES = {
    "n": 1e-9,
    "u": 1e-6,
    "\u00b5": 1e-6,
    "m": 1e-3,
    "c": 1e-2,
    "d": 1e-1,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}


def _lookup(name: str) -> tuple[tuple[int, ...], float]:
    if name in _UNITS:
        return _UNITS[name]
    prefix, rest = name[:1], name[1:]
    if prefix in _PREFIXES and rest in _UNITS:
        dims, scale = _UNITS[rest]
        return dims, scale * _PREFIXES[prefix]
    raise UnitError(f"unknown unit symbol {name!r}")


def _split_terms(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a unit string into numerator and denominator terms.

    ``kg/m^3`` -> (``('kg',)``, ``('m^3',)``); ``kg/m^2/s`` -> (``('kg',)``,
    ``('m^2', 's')``) -- a second ``/`` continues the denominator, which is the
    SI reading of the string (``/`` binds left to right, so ``a/b/c = a/(b c)``).
    """
    cleaned = text.strip().replace("(", "").replace(")", "")
    if cleaned in _UNITS:
        return (cleaned,), ()

    def terms(part: str) -> tuple[str, ...]:
        part = part.strip()
        if part in ("", "1"):
            return ()
        return tuple(tok.strip() for tok in part.split("*") if tok.strip())

    parts = cleaned.split("/")
    denominator: tuple[str, ...] = ()
    for part in parts[1:]:
        denominator += terms(part)
    return terms(parts[0]), denominator


def _term_power(term: str) -> tuple[str, int]:
    for separator in ("**", "^"):
        if separator in term:
            symbol, _, exponent = term.partition(separator)
            try:
                return symbol.strip(), int(exponent)
            except ValueError as exc:  # pragma: no cover - defensive
                raise UnitError(f"bad exponent in {term!r}") from exc
    return term, 1


@lru_cache(maxsize=512)
def parse_unit(text: str) -> tuple[tuple[int, ...], float]:
    """Return ``(SI dimension vector, scale to SI)`` for a unit string.

    ``parse_unit('mm')`` -> ((0, 1, 0, 0, 0, 0, 0), 1e-3); the dimension is
    right but the scale is not, which is precisely how a millimetre payload is
    caught.
    """
    if not isinstance(text, str):
        raise UnitError(f"unit must be a string, got {type(text).__name__}")
    dims = [0] * len(DIM_NAMES)
    scale = 1.0
    numerator, denominator = _split_terms(text)
    for sign, terms in ((+1, numerator), (-1, denominator)):
        for term in terms:
            symbol, power = _term_power(term)
            term_dim, term_scale = _lookup(symbol)
            for index, exponent in enumerate(term_dim):
                dims[index] += sign * power * exponent
            scale *= term_scale ** (sign * power)
    return tuple(dims), scale


@lru_cache(maxsize=512)
def unit_ok(declared: str, expected: str) -> tuple[bool, str]:
    """Check that ``declared`` is dimensionally *and* numerically SI-identical."""
    try:
        declared_dims, declared_scale = parse_unit(declared)
    except UnitError as exc:
        return False, str(exc)
    expected_dims, expected_scale = parse_unit(expected)
    if declared_dims != expected_dims:
        readable = ",".join(
            f"{name}^{exp}" for name, exp in zip(DIM_NAMES, declared_dims) if exp
        ) or "1"
        want = ",".join(
            f"{name}^{exp}" for name, exp in zip(DIM_NAMES, expected_dims) if exp
        ) or "1"
        return False, f"dimension {readable} != expected {want}"
    if not math.isclose(declared_scale, expected_scale, rel_tol=1e-12):
        return False, (
            f"scale {declared_scale:.6g} != SI {expected_scale:.6g} "
            f"(non-SI unit accepted by the producer)"
        )
    return True, "ok"


# ---------------------------------------------------------------------------
# payload schemas (closed subset; additionalProperties always false)
# ---------------------------------------------------------------------------

_NUM = {"type": "number"}
_NUM_ARRAY = {"type": "array", "items": _NUM, "minItems": 1}
_SHIP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "L_pp_m", "B_m", "T_m", "bow_angle_deg", "flare_deg"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "L_pp_m": {"type": "number", "exclusiveMinimum": 0.0},
        "B_m": {"type": "number", "exclusiveMinimum": 0.0},
        "T_m": {"type": "number", "exclusiveMinimum": 0.0},
        "bow_angle_deg": {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 90.0},
        "flare_deg": {"type": "number", "minimum": 0.0, "exclusiveMaximum": 90.0},
    },
}

SCHEMA_N03_DEF: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "producer", "ship", "belt", "ice", "contact_area_ratio", "samples", "units"],
    "properties": {
        "schema": {"const": SCHEMA_N03},
        "producer": {"type": "string", "minLength": 1},
        "ship": _SHIP,
        "belt": {
            "type": "object",
            "additionalProperties": False,
            "required": ["length_m", "height_m", "n_patches"],
            "properties": {
                "length_m": {"type": "number", "exclusiveMinimum": 0.0},
                "height_m": {"type": "number", "exclusiveMinimum": 0.0},
                "n_patches": {"type": "integer", "minimum": 2},
            },
        },
        "ice": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rho_ice_kg_m3", "sigma_c_Pa", "sigma_f_Pa", "mu_hull_ice"],
            "properties": {
                "rho_ice_kg_m3": {"type": "number", "exclusiveMinimum": 0.0},
                "sigma_c_Pa": {"type": "number", "exclusiveMinimum": 0.0},
                "sigma_f_Pa": {"type": "number", "exclusiveMinimum": 0.0},
                "mu_hull_ice": {"type": "number", "minimum": 0.0, "exclusiveMaximum": 1.0},
            },
        },
        "contact_area_ratio": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
        "samples": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "h_m", "V_ms", "s_m", "area_m2", "p_ice_Pa",
                    "v_slip_ms", "u_n_ms", "alpha_rad",
                ],
                "properties": {
                    "h_m": {"type": "number", "exclusiveMinimum": 0.0},
                    "V_ms": {"type": "number", "exclusiveMinimum": 0.0},
                    "s_m": _NUM_ARRAY,
                    "area_m2": _NUM_ARRAY,
                    "p_ice_Pa": _NUM_ARRAY,
                    "v_slip_ms": _NUM_ARRAY,
                    "u_n_ms": _NUM_ARRAY,
                    "alpha_rad": _NUM_ARRAY,
                },
            },
        },
        "units": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

SCHEMA_N04_DEF: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "producer", "ship", "method", "samples", "units"],
    "properties": {
        "schema": {"const": SCHEMA_N04},
        "producer": {"type": "string", "minLength": 1},
        "ship": _SHIP,
        "method": {"type": "string", "minLength": 1},
        "samples": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "h_m", "V_ms", "R_crush_N", "R_break_N", "R_submersion_N",
                    "R_ice_N", "R_ow_N", "R_total_N", "in_domain", "domain_note",
                ],
                "properties": {
                    "h_m": {"type": "number", "exclusiveMinimum": 0.0},
                    "V_ms": {"type": "number", "exclusiveMinimum": 0.0},
                    "R_crush_N": {"type": "number", "minimum": 0.0},
                    "R_break_N": {"type": "number", "minimum": 0.0},
                    "R_submersion_N": {"type": "number", "minimum": 0.0},
                    "R_ice_N": {"type": "number", "minimum": 0.0},
                    "R_ow_N": {"type": "number", "minimum": 0.0},
                    "R_total_N": {"type": "number", "minimum": 0.0},
                    "in_domain": {"type": "boolean"},
                    "domain_note": {"type": "string"},
                },
            },
        },
        "units": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_SCALAR_ARRAY = ["depth_m", "dh_dt_ms", "area_m2"]

SCHEMA_N05_DEF: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "producer", "wear", "samples", "units"],
    "properties": {
        "schema": {"const": SCHEMA_N05},
        "producer": {"type": "string", "minLength": 1},
        "wear": {
            "type": "object",
            "additionalProperties": False,
            "required": ["k_w", "H_hull_Pa", "duty_factor", "duration_s"],
            "properties": {
                "k_w": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
                "H_hull_Pa": {"type": "number", "exclusiveMinimum": 0.0},
                "duty_factor": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
                "duration_s": {"type": "number", "exclusiveMinimum": 0.0},
            },
        },
        "samples": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["h_m", "V_ms", "depth_m", "dh_dt_ms", "area_m2",
                             "mean_depth_m", "mass_loss_kg"],
                "properties": {
                    "h_m": {"type": "number", "exclusiveMinimum": 0.0},
                    "V_ms": {"type": "number", "exclusiveMinimum": 0.0},
                    "depth_m": _NUM_ARRAY,
                    "dh_dt_ms": _NUM_ARRAY,
                    "area_m2": _NUM_ARRAY,
                    "mean_depth_m": {"type": "number", "minimum": 0.0},
                    "mass_loss_kg": {"type": "number", "minimum": 0.0},
                },
            },
        },
        "units": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

SCHEMAS = {
    CONTRACT_N03: SCHEMA_N03_DEF,
    CONTRACT_N04: SCHEMA_N04_DEF,
    CONTRACT_N05: SCHEMA_N05_DEF,
}

EXPECTED_UNITS: dict[str, dict[str, str]] = {
    CONTRACT_N03: {
        "L_pp_m": "m", "B_m": "m", "T_m": "m", "bow_angle_deg": "-", "flare_deg": "-",
        "length_m": "m", "height_m": "m", "n_patches": "-",
        "rho_ice_kg_m3": "kg/m^3", "sigma_c_Pa": "Pa", "sigma_f_Pa": "Pa",
        "mu_hull_ice": "-", "contact_area_ratio": "-",
        "h_m": "m", "V_ms": "m/s", "s_m": "m", "area_m2": "m^2",
        "p_ice_Pa": "Pa", "v_slip_ms": "m/s", "u_n_ms": "m/s", "alpha_rad": "rad",
    },
    CONTRACT_N04: {
        "L_pp_m": "m", "B_m": "m", "T_m": "m", "bow_angle_deg": "-", "flare_deg": "-",
        "h_m": "m", "V_ms": "m/s", "R_crush_N": "N", "R_break_N": "N",
        "R_submersion_N": "N", "R_ice_N": "N", "R_ow_N": "N", "R_total_N": "N",
    },
    CONTRACT_N05: {
        "h_m": "m", "V_ms": "m/s", "depth_m": "m", "dh_dt_ms": "m/s",
        "area_m2": "m^2", "mean_depth_m": "m", "mass_loss_kg": "kg",
        "k_w": "-", "H_hull_Pa": "Pa", "duty_factor": "-", "duration_s": "s",
    },
}

#: physical plausibility bands (silent unit/dimension conversions that survive
#: the unit map still show up here); each entry is (lower, upper) in SI.
MAGNITUDE_BANDS = {
    "h_m": (1e-2, 5.0),
    "V_ms": (1e-2, 15.0),
    "p_ice_Pa": (1e2, 5e7),
    "v_slip_ms": (1e-3, 15.0),
    "u_n_ms": (0.0, 15.0),
    "dh_dt_ms": (0.0, 1e-3),
    "depth_m": (0.0, 0.5),
    "R_total_N": (1.0, 1e8),
}

#: dimensional identities that a producer must not break
#: name -> (left factors, right terms, human form)
DIMENSIONAL_IDENTITIES = {
    "wear_rate_law": {
        "left": {"k_w": "-", "p_ice_Pa": "Pa", "v_slip_ms": "m/s"},
        "right": {"H_hull_Pa": "Pa", "dh_dt_ms": "m/s"},
        "law": "dh/dt = k_w * p * v_slip / H_hull",
    },
    "mass_loss_law": {
        "left": {"rho_steel_kg_m3": "kg/m^3", "depth_m": "m", "area_m2": "m^2"},
        "right": {"mass_loss_kg": "kg"},
        "law": "mass_loss = rho_steel * depth * area",
    },
    "interface_mass_flux": {
        "left": {"rho_ice_kg_m3": "kg/m^3", "u_n_ms": "m/s", "area_m2": "m^2"},
        "right": {"mass_flux_kg_s": "kg/s"},
        "law": "mass_flux = rho_ice * u_n * area",
    },
}


@dataclass
class ContractCheck:
    check_id: str
    ok: bool
    detail: str
    value: float | None = None
    limit: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "ok": bool(self.ok),
            "detail": self.detail,
            "value": self.value,
            "limit": self.limit,
        }


def _product_dimension(units: Mapping[str, str], names: Iterable[str]) -> tuple[tuple[int, ...], float]:
    dims = [0] * len(DIM_NAMES)
    scale = 1.0
    for name in names:
        term_dims, term_scale = parse_unit(units[name])
        for index, exponent in enumerate(term_dims):
            dims[index] += exponent
        scale *= term_scale
    return tuple(dims), scale


def check_identity(identity: Mapping[str, Any], units: Mapping[str, str]) -> ContractCheck:
    """Check ``prod(left) == prod(right)`` dimensionally (names give direction)."""
    left = identity["left"]
    right = identity["right"]
    law = identity["law"]
    left_dims, left_scale = _product_dimension(units, left)
    # the right side is a quotient of the named terms except the result term
    right_names = list(right)
    result_name = [n for n in right_names if "Pa" in n or n in units and n.endswith(("dh_dt_ms", "mass_loss_kg", "mass_flux_kg_s"))]
    if not result_name:
        return ContractCheck(f"identity:{law}", False, "identity has no result term")
    divisor = [n for n in right_names if n != result_name[0]]
    divisor_dims, divisor_scale = _product_dimension(units, divisor)
    result_dims, result_scale = parse_unit(units[result_name[0]])
    combined = [left_dims[i] - divisor_dims[i] for i in range(len(DIM_NAMES))]
    combined_scale = left_scale / divisor_scale
    ok = tuple(combined) == result_dims and math.isclose(combined_scale, result_scale, rel_tol=1e-12)
    detail = (
        f"{law}: {'ok' if ok else 'dimension mismatch'}"
        if ok
        else f"{law}: left/divisor {combined} scale {combined_scale:.6g} != result {result_dims} scale {result_scale:.6g}"
    )
    return ContractCheck(f"identity:{result_name[0]}", ok, detail)


def validate_payload(contract: str, payload: Mapping[str, Any], *,
                     check_identities: bool = True) -> list[ContractCheck]:
    """Schema + unit + magnitude + dimensional-identity checks for one payload."""
    checks: list[ContractCheck] = []
    schema = SCHEMAS[contract]
    try:
        validate(schema, payload)
        checks.append(ContractCheck(f"{contract}:schema", True, "schema conforming"))
    except SchemaError as exc:
        checks.append(ContractCheck(f"{contract}:schema", False, str(exc)))
        return checks  # nothing else is meaningful on a malformed payload

    units = payload.get("units", {})
    expected = EXPECTED_UNITS[contract]
    missing_units = sorted(name for name in expected if name not in units)
    if missing_units:
        checks.append(
            ContractCheck(f"{contract}:units_present", False,
                          f"missing unit declarations: {missing_units}")
        )
    else:
        checks.append(
            ContractCheck(f"{contract}:units_present", True,
                          f"{len(expected)} unit declarations present")
        )
    for name, want in expected.items():
        if name not in units:
            continue
        ok, detail = unit_ok(units[name], want)
        checks.append(ContractCheck(f"{contract}:unit:{name}", ok,
                                    f"{units[name]!r} vs {want!r}: {detail}"))

    if check_identities:
        for key, identity in DIMENSIONAL_IDENTITIES.items():
            needed = set(identity["left"]) | set(identity["right"])
            if not needed.issubset(set(units) | {"rho_steel_kg_m3", "mass_flux_kg_s", "mass_loss_kg"}):
                continue
            if not needed.issubset(set(units)):
                continue
            checks.append(check_identity(identity, units))

    return checks


def check_magnitudes(contract: str, values: Mapping[str, float]) -> list[ContractCheck]:
    out: list[ContractCheck] = []
    for name, (low, high) in MAGNITUDE_BANDS.items():
        if name not in values:
            continue
        value = float(values[name])
        ok = low <= value <= high
        out.append(ContractCheck(
            f"{contract}:band:{name}", ok,
            f"{value:.6g} in [{low:g}, {high:g}]", value=value, limit=high,
        ))
    return out


def sample_keys(payload: Mapping[str, Any]) -> list[tuple[float, float]]:
    """``(h, V)`` keys of a payload, in declared order."""
    return [(round(float(s["h_m"]), 9), round(float(s["V_ms"]), 9)) for s in payload["samples"]]


def check_parameter_consistency(n03: Mapping[str, Any], n04: Mapping[str, Any],
                                n05: Mapping[str, Any]) -> ContractCheck:
    """N03/N04/N05 must be evaluated on the *same* ``(h, V)`` samples."""
    keys03 = sample_keys(n03)
    keys04 = sample_keys(n04)
    keys05 = sample_keys(n05)
    same_records = len(keys03) == len(keys04) == len(keys05)
    same_values = same_records and keys03 == keys04 and keys03 == keys05
    overlap = len(set(keys03) & set(keys04) & set(keys05))
    total = max(len(keys03), len(keys04), len(keys05), 1)
    ok = same_values
    detail = (
        f"{total} samples, {overlap}/{total} shared (h,V) keys, "
        f"order-identical={same_values}"
    )
    return ContractCheck("cross_module:(h,V)_key_consistency", ok, detail,
                         value=overlap / total, limit=1.0)


@dataclass
class ContractReport:
    checks: list[ContractCheck] = field(default_factory=list)
    digests: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def n_checks(self) -> int:
        return len(self.checks)

    @property
    def n_pass(self) -> int:
        return sum(1 for check in self.checks if check.ok)

    @property
    def n_fail(self) -> int:
        return sum(1 for check in self.checks if not check.ok)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "n_checks": len(self.checks),
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "digests": dict(self.digests),
            "checks": [check.as_dict() for check in self.checks],
        }


def validate_cross_module(n03: Mapping[str, Any], n04: Mapping[str, Any],
                          n05: Mapping[str, Any]) -> ContractReport:
    """Full contract validation across the three module payloads."""
    report = ContractReport()
    for contract, payload in ((CONTRACT_N03, n03), (CONTRACT_N04, n04), (CONTRACT_N05, n05)):
        report.digests[contract] = payload_digest(payload)
        report.checks.extend(validate_payload(contract, payload))

    # magnitude bands on the first sample of each payload (a silent conversion
    # such as knots -> m/s keeps all units valid but moves the value)
    first03 = n03["samples"][0]
    report.checks.extend(check_magnitudes(CONTRACT_N03, {
        "h_m": first03["h_m"], "V_ms": first03["V_ms"],
        "p_ice_Pa": max(first03["p_ice_Pa"]), "v_slip_ms": max(first03["v_slip_ms"]),
        "u_n_ms": max(first03["u_n_ms"]),
    }))
    first04 = n04["samples"][0]
    report.checks.extend(check_magnitudes(CONTRACT_N04, {
        "h_m": first04["h_m"], "V_ms": first04["V_ms"], "R_total_N": first04["R_total_N"],
    }))
    first05 = n05["samples"][0]
    report.checks.extend(check_magnitudes(CONTRACT_N05, {
        "h_m": first05["h_m"], "V_ms": first05["V_ms"],
        "dh_dt_ms": max(first05["dh_dt_ms"]), "depth_m": max(first05["depth_m"]),
    }))
    report.checks.append(check_parameter_consistency(n03, n04, n05))
    return report