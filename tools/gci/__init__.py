"""ITTC 7.5-03-01/02 numerical-uncertainty (GCI) toolkit.

Modules
-------
``core``      three-grid GCI chain, Richardson extrapolation, U_V and the
              three-state (PASS / FAIL / WITHHELD) adjudication of the
              platform's 15% acceptance gate.
``anchors``   sha256-locked anchor library (clause-closed-form + in-repo
              regression) that the tests consume directly.
``cases``     self-contained analytic verification case: 2D circular-section
              added mass on systematically refined body-fitted grids.
``cli``       one-line-JSON command line: r21/r32/p/R_G/GCI21/GCI32/U_V/verdict.
``run_e2e``   runs the case, the gate demonstrations and the figures.
``gen_report`` builds ``docs/gci_ittc_7503_absorption.md`` from the artefacts.
``gen_anchor_digests`` regenerates the sha256 table of the anchor library.
"""

__all__ = ["core", "anchors", "cases", "cli"]
