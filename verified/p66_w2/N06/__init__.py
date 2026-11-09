"""P66 multi-physics coupling integration gate (ice-navigation campaign, wave 2).

The gate cross-validates three modules of the ice-navigation multi-physics
chain and decides five machine criteria:

=================  ===========================================================  ============================
criterion          what is measured                                               module
=================  ===========================================================  ============================
(1) interface      mass / momentum / third-law residuals across the non-matching  :mod:`.conservation`
    conservation   DEM-patch <-> LBM-facet interface (tolerance 1e-6, with
                   negative controls proving the check can fail)
(2) parameter      fraction of the abrasion-field ``(h, V)`` envelope covered by   :mod:`.parameter_space`
    space overlap  the resistance chain's evaluable domain (>= 90 %)
(3) interface      schema + SI-dimension/scale + cross-module key consistency      :mod:`.contracts`
    contracts      for the N03/N04/N05 payloads and the exchange hand-off
(4) end-to-end     >= 5 scenarios through the whole chain, each with its own        :mod:`.scenarios`
    scenarios      numbers and verdict (plus one expected-refusal control)
(5) performance    coupled end-to-end wall time / sum of single-module times        :mod:`.perf`
                   < 1.5
=================  ===========================================================  ============================

:mod:`.gate` assembles the verdict; :mod:`.producers` holds the contract
conforming reference producers for the two DEM-side modules that are not
merged in ``main`` yet.
"""
from . import conservation, contracts, gate, parameter_space, perf, producers, scenarios

__all__ = ["contracts", "producers", "conservation", "parameter_space", "scenarios", "perf", "gate"]