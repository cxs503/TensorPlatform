"""Audited copy of the serving B4-70 open-water model, plus its declared reference dataset.

Why a copy exists
-----------------
The ship-performance platform serves propeller open water from
``platforms/shared/methods/wageningen_b.py``.  To *grade* that model instead of
reading its documentation, the arithmetic is transcribed here verbatim and
kept behind a single function, so that

* :func:`serving_wageningen_b` can be fed to :func:`methods.propulsion.open_water.audit_open_water_curve`
  (gate suite) and to residual comparisons against
  :func:`load_reference_dataset`;
* any divergence between this copy and the upstream file is a *provenance*
  problem that is visible in one place (see :data:`SERVING_PROVENANCE`).

Provenance
----------
``serving_wageningen_b`` reproduces, line for line, the function
``open_water_coefficients`` of the upstream file at the commit recorded in
:data:`SERVING_PROVENANCE` (read-only audit; the upstream tree is outside the
TensorPlatform worktree and is **not** modified by this module).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict

__all__ = ["SERVING_PROVENANCE", "serving_wageningen_b", "load_reference_dataset", "DATA_DIR"]

DATA_DIR = Path(__file__).resolve().parent / "data"
DATASET_PATH = DATA_DIR / "b4_70_reference_dataset.json"

SERVING_PROVENANCE = {
    "upstream_repo": "ship-performance-platform (serving API :8001)",
    "upstream_path": "platforms/shared/methods/wageningen_b.py",
    "upstream_commit": "38841cf",
    "transcribed_function": "open_water_coefficients()",
    "declared_metadata": {
        "description": "Wageningen B-series B4-70 open-water engineering approximation",
        "validation_status": "verified",
        "references": ["Troost, Wageningen B-series open-water propeller data"],
        "input_schema": {"J": "0.0 to 1.2", "P_D": "0.6 to 1.4", "Ae_Ao": "0.4 to 0.8",
                         "Z": "3, 4, or 5", "n": "positive rps"},
    },
    "dataset_source": "platforms/../backend/data/test_data/propulsion_wageningen.json",
}

RHO_SEAWATER = 1025.0


def serving_wageningen_b(J: float, P_D: float = 1.0, Ae_Ao: float = 0.7, Z: int = 4,
                         rho: float = RHO_SEAWATER) -> Dict[str, float]:
    """Serving B4-70 approximation (verbatim transcription, incl. its clamps).

    The clamps are part of the model under test: ``KT`` floored at 0, ``KQ``
    floored at ``1e-6``, ``eta_0`` clipped to ``[0, 0.95]``.
    """
    if not 0.0 <= J <= 1.2:
        raise ValueError("J must be within the applicable range 0.0 to 1.2")
    if not 0.6 <= P_D <= 1.4:
        raise ValueError("P_D must be within the applicable range 0.6 to 1.4")
    if not 0.4 <= Ae_Ao <= 0.8:
        raise ValueError("Ae_Ao must be within the applicable range 0.4 to 0.8")
    if Z not in (3, 4, 5):
        raise ValueError("Z must be 3, 4, or 5 for this B-series approximation")

    pitch = P_D - 0.8
    area = Ae_Ao - 0.55
    blades = Z - 4
    kt_raw = 0.320 - 0.280 * J - 0.010 * J * J + 0.42 * pitch + 0.10 * area + 0.008 * blades
    kq_raw = 0.052 - 0.038 * J + 0.001 * J * J + 0.055 * pitch + 0.012 * area + 0.002 * blades
    KT = max(0.0, kt_raw)
    KQ = max(1.0e-6, kq_raw)
    eta_0 = J * KT / (2.0 * math.pi * KQ)
    return {"J": J, "KT": KT, "KQ": KQ, "eta_0": max(0.0, min(eta_0, 0.95))}


def load_reference_dataset() -> Dict[str, object]:
    """Load the platform's declared B4-70 open-water reference dataset.

    The file is a vendored copy of the serving platform's benchmark JSON
    (``backend/data/test_data/propulsion_wageningen.json``); the vendored copy
    adds a ``_provenance`` block and is never edited otherwise.
    """
    with open(DATASET_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)