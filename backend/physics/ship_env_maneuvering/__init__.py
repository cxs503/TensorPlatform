"""Wind-current-ice coupled ship manoeuvrability (mission W3-N08).

Public API
----------
``data``    reference hull, OCIMF-style coefficient anchors, ITTC-57 friction
``loads``   wind / current / ice load models plus independent reference models
``mmg``     3-DOF MMG manoeuvring model, turning-circle and zigzag simulators
``validation`` acceptance-criteria harness (criteria (a)-(d) of mission W3-N08)
"""

from . import data, loads, mmg  # noqa: F401

__all__ = ["data", "loads", "mmg"]

__version__ = "1.0.0"