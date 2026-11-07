"""pytest plugin: the ``assert_reproducible`` contract for cross-ISA runs.

Consumers (any repo with a digest-producing kernel) add::

    # conftest.py
    pytest_plugins = ["tools.repro_digest.pytest_plugin"]

and then write::

    def test_rerun_h5(assert_reproducible, repro_gate_k):
        ref = load("ref.json"); cur = load("rerun.json")
        assert_reproducible(ref, cur, ulp_gate=repro_gate_k)

Fixtures
--------
``ulp_protocol``        the :mod:`tools.repro_digest.ulp` module itself
``assert_reproducible`` callable(``ref``, ``cur``, ``ulp_gate=k``, ``digits=12``)
``repro_gate_k``        the gate ``k`` declared in ``artifacts/cross_isa_repro.json``
``repro_protocol``      dict with ``k``, ``digits`` and provenance (machine table)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from tools.repro_digest import ulp

ARTIFACT = Path("artifacts/cross_isa_repro.json")

#: conservative fallback if the artefact is not present (e.g. fresh checkout);
#: tests that need the *declared* gate should use ``repro_gate_k`` instead.
FALLBACK_K = 1024


def _load_protocol(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else ARTIFACT
    if not p.exists():
        return {"k": FALLBACK_K, "digits": ulp.DEFAULT_DIGITS, "source": "fallback"}
    data = json.loads(p.read_text())
    proto = data.get("protocol", {})
    return {
        "k": int(proto.get("ulp_gate_k", FALLBACK_K)),
        "digits": int(proto.get("quantization", {}).get("digits", ulp.DEFAULT_DIGITS)),
        "source": str(p),
        "machines": data.get("machines", []),
        "determinism_floor": data.get("determinism_floor", {}),
    }


@pytest.fixture(scope="session")
def ulp_protocol():
    """The protocol implementation module (quantization + ULP + comparison)."""
    return ulp


@pytest.fixture(scope="session")
def repro_protocol() -> Dict[str, Any]:
    """Declared protocol parameters, read from the measured artefact."""
    return _load_protocol()


@pytest.fixture(scope="session")
def repro_gate_k(repro_protocol) -> int:
    """The declared ULP gate ``k`` (conservative upper bound, artifact-backed)."""
    return repro_protocol["k"]


@pytest.fixture(scope="session")
def assert_reproducible():
    """Protocol gate as a fixture: ``assert_reproducible(ref, cur, ulp_gate=k)``."""
    return ulp.assert_reproducible