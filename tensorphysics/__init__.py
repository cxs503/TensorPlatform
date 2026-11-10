"""TensorPhysics-side runtime primitives (domain-neutral).

Currently:

* :mod:`tensorphysics.clock_sync` —— coupling clock management for heterogeneous
  multi-solver runs (master/sub clocks, sub-cycling, coupling points, energy
  ledger, coupled-map stability, RunManifest extension).

Nothing in this package encodes a physical model: values are seconds and numbers,
and any physical meaning is supplied by the caller through artifacts.
"""

__all__ = ["clock_sync"]