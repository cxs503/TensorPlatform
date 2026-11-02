"""Hull-girder structural strength verification package (discipline: 结构).

Fills the one remaining void of the nine ship-performance disciplines on this
platform: no structural-strength endpoint or module existed (all probed
``/api/v1/*structure*`` paths return 404), so 总纵强度 had zero evidence.

Contents
--------
``section``   exact section properties (rectangle / Green-theorem / closed form)
``smith``     Smith progressive-collapse ultimate bending moment
``loads``     IACS UR S11 wave bending moments + girder stress
``benchmarks`` machine-checkable verification harness (CLI; writes artifacts)

Run the harness::

    python3 -m tools.verify.hull_girder.benchmarks --write

Evidence grade of what this package provides: REAL for the analytic identities it
is verified against (they are re-derivable in closed form); WITHHELD for absolute
accuracy against model-scale collapse tests, which are not available on this host.
"""

from . import loads, section, smith  # noqa: F401

__all__ = ["section", "smith", "loads"]
__version__ = "1.0.0"