"""Safe expression registry for metric recompute.

Whitelist-based expression parser — NO eval(), NO exec().
Supports:
  - Arithmetic: +, -, *, /, **
  - Functions: abs, sqrt, log, exp, min, max, pow
  - Variables: references to other fields in the metric row (e.g., numerator, denominator)
  - Literals: int, float

Security: only whitelisted tokens allowed; anything else raises SafeExprError.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Set


class SafeExprError(ValueError):
    """Raised when an expression contains disallowed tokens or fails to parse."""
    pass


# Whitelisted function names → Python implementations
_SAFE_FUNCTIONS: Dict[str, Callable[..., float]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "min": min,
    "max": max,
    "pow": pow,
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
}

# Token pattern: identifiers, numbers, operators, parens, commas, whitespace
# IMPORTANT: ** must be matched before * to avoid splitting into two tokens
_TOKEN_RE = re.compile(
    r"""
    \s+                       |  # whitespace (skip)
    (?P<POW>\*\*)             |  # power (must come before OP)
    (?P<NUMBER>               # integer or float
        \d+\.\d+              # 1.23
        | \d+\.               # 1.
        | \.\d+               # .5
        | \d+                 # 42
    )                         |
    (?P<IDENT>[A-Za-z_]\w*)   |  # identifier
    (?P<OP>[+\-*/%,])         |  # operator or comma
    (?P<LPAREN>\()            |
    (?P<RPAREN>\))
    """,
    re.VERBOSE,
)


@dataclass
class ExprRegistry:
    """Registry of named recompute expressions.

    Expressions are registered at startup from config (JSON/YAML).
    Each expression is a string referencing row fields as variables.
    """

    _expressions: Dict[str, str] = field(default_factory=dict)
    _allowed_vars: Dict[str, Set[str]] = field(default_factory=dict)

    def register(self, name: str, expr: str, allowed_vars: Set[str] | None = None) -> None:
        """Register a recompute expression.

        Args:
            name: metric name (e.g., "repro_rate")
            expr: expression string (e.g., "reproduced / total")
            allowed_vars: optional whitelist of variable names; if None, auto-detected
        """
        # Validate expression syntax at registration time
        self._validate(expr)
        self._expressions[name] = expr
        if allowed_vars is not None:
            self._allowed_vars[name] = allowed_vars
        else:
            self._allowed_vars[name] = self._extract_vars(expr)

    def _validate(self, expr: str) -> None:
        """Validate expression contains only whitelisted tokens."""
        pos = 0
        while pos < len(expr):
            m = _TOKEN_RE.match(expr, pos)
            if not m:
                raise SafeExprError(
                    f"Invalid token at position {pos} in expression: {expr!r}"
                )
            # Check for unknown function calls
            if m.lastgroup == "IDENT":
                ident = m.group()
                # Look ahead for '(' to detect function call
                rest = expr[m.end():].lstrip()
                if rest.startswith("(") and ident not in _SAFE_FUNCTIONS:
                    raise SafeExprError(f"Unknown function: {ident!r}")
            pos = m.end()

    def _extract_vars(self, expr: str) -> Set[str]:
        """Extract variable names (identifiers that are not function names)."""
        vars_found: Set[str] = set()
        pos = 0
        while pos < len(expr):
            m = _TOKEN_RE.match(expr, pos)
            if m and m.group("IDENT"):
                ident = m.group("IDENT")
                if ident not in _SAFE_FUNCTIONS:
                    vars_found.add(ident)
            pos = m.end()
        return vars_found

    def evaluate(self, name: str, row: Dict[str, Any]) -> float:
        """Evaluate a registered expression against a metric row.

        Args:
            name: metric name
            row: dict of field values (e.g., {"reproduced": 19, "total": 20})

        Returns:
            Computed value as float

        Raises:
            SafeExprError: if expression not registered or evaluation fails
            KeyError: if required variable missing from row
        """
        if name not in self._expressions:
            raise SafeExprError(f"Expression not registered: {name!r}")

        expr = self._expressions[name]
        allowed = self._allowed_vars.get(name, set())

        # Build variable bindings
        bindings: Dict[str, float] = {}
        for var in allowed:
            if var not in row:
                raise KeyError(f"Missing variable {var!r} for expression {name!r}")
            val = row[var]
            if val is None:
                raise SafeExprError(f"Variable {var!r} is None")
            try:
                bindings[var] = float(val)
            except (TypeError, ValueError) as e:
                raise SafeExprError(f"Variable {var!r} not numeric: {val!r}") from e

        return self._eval_expr(expr, bindings)

    def _eval_expr(self, expr: str, bindings: Dict[str, float]) -> float:
        """Recursive descent parser for arithmetic expressions.

        Grammar:
            expr   → term (('+' | '-') term)*
            term   → power (('*' | '/' | '%') power)*
            power  → unary ('**' unary)*
            unary  → ('-' | '+') unary | atom
            atom   → NUMBER | IDENT | IDENT '(' args ')' | '(' expr ')'
            args   → expr (',' expr)*
        """
        tokens = self._tokenize(expr)
        pos = [0]  # mutable index

        def peek() -> str | None:
            if pos[0] < len(tokens):
                return tokens[pos[0]][0]
            return None

        def peek_val() -> str | None:
            if pos[0] < len(tokens):
                return tokens[pos[0]][1]
            return None

        def consume(expected_type: str | None = None) -> tuple[str, str]:
            if pos[0] >= len(tokens):
                raise SafeExprError(f"Unexpected end of expression: {expr!r}")
            tok = tokens[pos[0]]
            if expected_type and tok[0] != expected_type:
                raise SafeExprError(f"Expected {expected_type}, got {tok[0]}: {expr!r}")
            pos[0] += 1
            return tok

        def parse_expr() -> float:
            result = parse_term()
            while peek() == "OP" and peek_val() in ("+", "-"):
                op = consume()[1]
                right = parse_term()
                result = result + right if op == "+" else result - right
            return result

        def parse_term() -> float:
            result = parse_power()
            while peek() == "OP" and peek_val() in ("*", "/", "%"):
                op = consume()[1]
                right = parse_power()
                if op == "*":
                    result = result * right
                elif op == "/":
                    if right == 0:
                        raise SafeExprError("Division by zero")
                    result = result / right
                else:  # %
                    if right == 0:
                        raise SafeExprError("Modulo by zero")
                    result = result % right
            return result

        def parse_power() -> float:
            base = parse_unary()
            if peek() == "POW":
                consume()
                exp = parse_unary()
                try:
                    return base ** exp
                except OverflowError:
                    raise SafeExprError(f"Overflow in {base}**{exp}")
            return base

        def parse_unary() -> float:
            if peek() == "OP" and peek_val() in ("+", "-"):
                op = consume()[1]
                val = parse_unary()
                return -val if op == "-" else val
            return parse_atom()

        def parse_atom() -> float:
            tok_type = peek()
            if tok_type is None:
                raise SafeExprError(f"Unexpected end of expression: {expr!r}")

            if tok_type == "NUMBER":
                consume()
                return float(tokens[pos[0] - 1][1])

            if tok_type == "IDENT":
                consume()
                ident = tokens[pos[0] - 1][1]
                # Function call?
                if peek() == "LPAREN":
                    consume()  # (
                    args = []
                    if peek() != "RPAREN":
                        args.append(parse_expr())
                        while peek() == "OP" and peek_val() == ",":
                            consume()
                            args.append(parse_expr())
                    if peek() != "RPAREN":
                        raise SafeExprError(f"Expected ')' in function call: {expr!r}")
                    consume()  # )
                    if ident not in _SAFE_FUNCTIONS:
                        raise SafeExprError(f"Unknown function: {ident!r}")
                    try:
                        return float(_SAFE_FUNCTIONS[ident](*args))
                    except Exception as e:
                        raise SafeExprError(f"Function {ident!r} failed: {e}")
                else:
                    # Variable
                    if ident not in bindings:
                        raise KeyError(f"Unknown variable: {ident!r}")
                    return bindings[ident]

            if tok_type == "LPAREN":
                consume()
                val = parse_expr()
                if peek() != "RPAREN":
                    raise SafeExprError(f"Expected ')': {expr!r}")
                consume()
                return val

            raise SafeExprError(f"Unexpected token: {tok_type}")

        result = parse_expr()
        if pos[0] < len(tokens):
            raise SafeExprError(f"Trailing tokens in expression: {expr!r}")
        return result

    def _tokenize(self, expr: str) -> list[tuple[str, str]]:
        """Tokenize expression into (type, value) pairs."""
        tokens = []
        pos = 0
        while pos < len(expr):
            m = _TOKEN_RE.match(expr, pos)
            if not m:
                raise SafeExprError(f"Invalid token at position {pos}: {expr!r}")
            if m.lastgroup and m.lastgroup != "WS":
                tokens.append((m.lastgroup, m.group()))
            pos = m.end()
        return tokens

    def get_expression(self, name: str) -> str | None:
        """Get registered expression by name."""
        return self._expressions.get(name)

    def list_expressions(self) -> Dict[str, str]:
        """List all registered expressions."""
        return dict(self._expressions)
