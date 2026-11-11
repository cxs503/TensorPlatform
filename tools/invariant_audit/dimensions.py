#!/usr/bin/env python3
"""Dimension algebra over *injected* base symbols.

A dimension is an exponent vector over the base symbols declared by the audit
plan (``base_dimensions``).  No base symbol is hard-coded: ``length``,
``mass``, ``time`` ... are just strings that arrive from configuration, so the
mechanism is reusable for any unit system (SI, CGS, or a synthetic one).

Grammar accepted by :meth:`Dimension.parse`::

    expr     := term (('*' | '/') term)*
    term     := symbol ['**' exponent]
    exponent := integer | 'p/q'

Examples: ``"mass*length*time**-2"``, ``"length/time"``, ``"mass**1/2"``.

Rational exponents are kept exact via :class:`fractions.Fraction`, so
``length**2 * length**-2`` compares equal to the dimensionless unit with no
floating-point fuzz.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, Iterable, Mapping, Optional

DimensionSpec = Any  # Mapping[str, number] | str | Dimension


class DimensionError(ValueError):
    """Malformed dimension expression or unknown base symbol (fail-closed)."""


_TERM_RE = re.compile(
    r"\s*(?P<op>[*/]?)\s*"
    r"(?P<symbol>[A-Za-z_][A-Za-z_0-9]*)"
    r"(?:\s*\*\*\s*(?P<exp>-?\d+(?:\s*/\s*\d+)?))?"
)


def _fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):  # bool is an int; reject the confusion early
        raise DimensionError(f"dimension exponent must be numeric, got {value!r}")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if value != int(value):
            raise DimensionError(f"non-integer exponent {value!r} is not supported")
        return Fraction(int(value))
    if isinstance(value, str):
        try:
            return Fraction(value.strip().replace(" ", ""))
        except (ValueError, ZeroDivisionError) as exc:
            raise DimensionError(f"cannot parse exponent {value!r}") from exc
    raise DimensionError(f"dimension exponent must be numeric, got {value!r}")


def _as_dimension(value: Any) -> "Dimension":
    if isinstance(value, Dimension):
        return value
    return Dimension.parse(value)


class Dimension:
    """Exact exponent vector over named base symbols."""

    __slots__ = ("_exp",)

    def __init__(self, exponents: Optional[Mapping[str, Any]] = None) -> None:
        exp: Dict[str, Fraction] = {}
        for symbol, power in (exponents or {}).items():
            symbol = str(symbol)
            value = _fraction(power)
            if value != 0:
                exp[symbol] = value
        self._exp: Dict[str, Fraction] = exp

    # -- constructors ------------------------------------------------------
    @classmethod
    def unit(cls) -> "Dimension":
        return cls({})

    @classmethod
    def base(cls, symbol: str) -> "Dimension":
        return cls({str(symbol): Fraction(1)})

    @classmethod
    def parse(cls, spec: DimensionSpec,
              symbols: Optional[Iterable[str]] = None) -> "Dimension":
        """Parse a dimension from a mapping, an expression string, or a Dimension.

        When ``symbols`` is given the expression is validated against that
        symbol set: an unknown symbol raises :class:`DimensionError` instead of
        silently inventing a new axis (typo protection).
        """
        known = {str(s) for s in symbols} if symbols is not None else None
        if isinstance(spec, Dimension):
            dim = spec
        elif isinstance(spec, Mapping):
            dim = cls({str(k): v for k, v in spec.items()})
        elif isinstance(spec, str):
            dim = cls._parse_expression(spec)
        else:
            raise DimensionError(f"unsupported dimension specification {spec!r}")
        if known is not None:
            unknown = sorted(set(dim._exp) - known)
            if unknown:
                raise DimensionError(
                    f"dimension {dim.to_expr()!r} uses undeclared base symbols "
                    f"{unknown}; declared: {sorted(known)}")
        return dim

    @classmethod
    def _parse_expression(cls, expression: str) -> "Dimension":
        text = expression.strip()
        if not text:
            raise DimensionError("empty dimension expression")
        if text in {"1", "dimensionless", "-"}:
            return cls.unit()
        exponents: Dict[str, Fraction] = {}
        pos = 0
        first = True
        while pos < len(text):
            match = _TERM_RE.match(text, pos)
            if not match or match.end() == pos:
                raise DimensionError(
                    f"cannot parse dimension expression {expression!r} at offset {pos}")
            op = match.group("op")
            if first and op == "/":
                raise DimensionError(
                    f"dimension expression may not start with '/': {expression!r}")
            sign = -1 if op == "/" else 1
            power = match.group("exp")
            value = sign * (_fraction(power) if power is not None else Fraction(1))
            symbol = match.group("symbol")
            exponents[symbol] = exponents.get(symbol, Fraction(0)) + value
            first = False
            pos = match.end()
            if pos < len(text) and text[pos] not in "*/":
                raise DimensionError(
                    f"cannot parse dimension expression {expression!r} at offset {pos}")
        return cls(exponents)

    # -- algebra -----------------------------------------------------------
    def __mul__(self, other: DimensionSpec) -> "Dimension":
        other = _as_dimension(other)
        out = dict(self._exp)
        for symbol, power in other._exp.items():
            out[symbol] = out.get(symbol, Fraction(0)) + power
        return Dimension(out)

    def __truediv__(self, other: DimensionSpec) -> "Dimension":
        other = _as_dimension(other)
        out = dict(self._exp)
        for symbol, power in other._exp.items():
            out[symbol] = out.get(symbol, Fraction(0)) - power
        return Dimension(out)

    def __pow__(self, power: Any) -> "Dimension":
        exponent = _fraction(power)
        return Dimension({s: p * exponent for s, p in self._exp.items()})

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Dimension):
            try:
                other = _as_dimension(other)
            except DimensionError:
                return NotImplemented
        return self._exp == other._exp

    def __hash__(self) -> int:
        return hash(tuple(sorted((s, p) for s, p in self._exp.items())))

    def __repr__(self) -> str:
        return f"Dimension({self.to_expr()})"

    # -- presentation ------------------------------------------------------
    def is_dimensionless(self) -> bool:
        return not self._exp

    def to_dict(self) -> Dict[str, str]:
        return {s: str(p) for s, p in sorted(self._exp.items())}

    def to_expr(self) -> str:
        if not self._exp:
            return "1"
        parts = []
        for symbol, power in sorted(self._exp.items()):
            if power == 1:
                parts.append(symbol)
            else:
                parts.append(f"{symbol}**{power}")
        return "*".join(parts)


def dimension_table(base_dimensions: Iterable[str],
                   declared: Optional[Mapping[str, DimensionSpec]] = None
                   ) -> Dict[str, Dimension]:
    """Build a symbol -> Dimension table.

    Base symbols map to themselves; declared quantities are parsed on top, so a
    declaration may be written in terms of base symbols or previously declared
    quantities.
    """
    table: Dict[str, Dimension] = {}
    for symbol in base_dimensions:
        table[str(symbol)] = Dimension.base(str(symbol))
    base = set(table)
    for name, spec in (declared or {}).items():
        try:
            table[str(name)] = Dimension.parse(spec, symbols=set(table) | base)
        except DimensionError:
            raise
    return table