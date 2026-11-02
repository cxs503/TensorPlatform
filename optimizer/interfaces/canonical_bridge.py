"""Single bridge from the optimizer to the canonical Holtrop-Mennen (1984) kernel.

Rationale (report 1144): every downstream consumer of calm-water resistance in this
platform must read **one** implementation.  The canonical kernel lives at
``backend/api/holtrop1984.py`` (landed by agent/m1553, consumed by
``POST /calculations/resistance``).  The optimizer therefore *imports* it here and
never re-derives the coefficient chain (c7/iE/c1/c2/c5/m1/m4/lambda, 1+k, Ca).

Resolution order (deterministic, provenance recorded)
-----------------------------------------------------
1. worktree file ``backend/api/holtrop1984.py`` (normal case);
2. ``git show <ref>:backend/api/holtrop1984.py`` for refs in :data:`GIT_REFS`
   (used only on a branch where the canonical module is not merged yet -- the
   bytes loaded are byte-identical to the canonical file, nothing is re-implemented);
3. unavailable -> caller must fall back to the legacy chain and stamp
   ``evidence_level="MOCK"``.

Loading is cached per process; the module object is exposed via
:func:`canonical_kernel` so callers share one execution context.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
CANON_REL = "backend/api/holtrop1984.py"
GIT_REFS = ("agent/m1553", "main")

_CACHE: Optional[Dict[str, Any]] = None


def _exec_from_path(path: Path, tag: str) -> Any:
    spec = importlib.util.spec_from_file_location(tag, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load canonical kernel from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[tag] = mod          # dataclasses need the module registered
    spec.loader.exec_module(mod)    # type: ignore[union-attr]
    return mod


def canonical_kernel() -> Dict[str, Any]:
    """Return ``{"module": <mod|None>, "provenance": {...}, "available": bool}``."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    local = ROOT / CANON_REL
    if local.exists():
        mod = _exec_from_path(local, "_tp_canon_holtrop1984")
        _CACHE = {
            "module": mod,
            "available": True,
            "provenance": {
                "path": CANON_REL,
                "source": "worktree",
                "sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
            },
        }
        return _CACHE

    for ref in GIT_REFS:
        try:
            blob = subprocess.run(
                ["git", "show", f"{ref}:{CANON_REL}"],
                cwd=str(ROOT), capture_output=True, check=True, timeout=60,
            ).stdout
        except Exception:  # noqa: BLE001 - ref absent / git unavailable
            continue
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "holtrop1984.py"
            tmp.write_bytes(blob)
            mod = _exec_from_path(tmp, "_tp_canon_holtrop1984")
        _CACHE = {
            "module": mod,
            "available": True,
            "provenance": {
                "path": CANON_REL,
                "source": f"git:{ref}",
                "sha256": hashlib.sha256(blob).hexdigest(),
            },
        }
        return _CACHE

    _CACHE = {"module": None, "available": False,
              "provenance": {"path": CANON_REL, "source": "unavailable"}}
    return _CACHE


def reset_cache() -> None:
    """Drop the cached kernel (tests that simulate an unavailable canonical)."""
    global _CACHE
    _CACHE = None


def available() -> bool:
    return bool(canonical_kernel()["available"])


def provenance() -> Dict[str, Any]:
    return dict(canonical_kernel()["provenance"])


def estimate_cm(cb: float) -> float:
    """Cm estimate from the canonical kernel (never duplicated here)."""
    k = canonical_kernel()
    if not k["available"]:
        return min(0.999, max(0.90, 1.0 - 0.045 * (1.0 - cb)))
    return float(k["module"].estimate_cm(cb))


def estimate_cwp(cb: float) -> float:
    """Cwp estimate from the canonical kernel (Kerlen/Schneekluth)."""
    k = canonical_kernel()
    if not k["available"]:
        return (1.0 + 2.0 * cb) / 3.0
    return float(k["module"].estimate_cwp(cb))