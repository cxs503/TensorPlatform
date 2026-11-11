"""Cross-architecture benchmark normalisation (platform mechanism, zero domain semantics).

Layers
------
1. `measure`  : measure a numerical timing kernel on a host -> schema observations
2. `alpha`    : alpha_arch = T_arch / T_ref (geometric mean of matched ratios,
                cluster-bootstrap CI over workloads)
3. `mixed_effects` : linear mixed-effects fit log T = x.beta + u_host + eps (REML)
4. `injection`/`coverage` : Monte-Carlo validation against known ground truth
5. `criteria` : machine-computed acceptance criteria (a)-(e)
6. `figures`  : report figures

Everything except the timing kernel is a pure function of observation records.
"""
from . import alpha, criteria, coverage, figures, injection, measure, mixed_effects  # noqa: F401

__all__ = ["alpha", "criteria", "coverage", "figures", "injection", "measure", "mixed_effects"]