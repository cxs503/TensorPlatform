"""Repo-level pytest config: register the reproducible-digest fixture plugin.

The plugin exposes ``assert_reproducible`` / ``repro_gate_k`` / ``ulp_protocol``
so that any digest-producing benchmark in this repo can assert the cross-ISA
reproducibility protocol in one line (see docs/REPRODUCIBILITY_CROSS_ISA.md).
"""

pytest_plugins = ["tools.repro_digest.pytest_plugin"]
