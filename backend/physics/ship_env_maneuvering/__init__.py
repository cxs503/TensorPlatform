"""Wind-current-ice coupled ship manoeuvrability (mission W3-N08).

Public API
----------
``data``    reference hull, OCIMF-style coefficient anchors, ITTC-57 friction
``loads``   wind / current / ice load models plus independent reference models
``mmg``     3-DOF MMG manoeuvring model, turning-circle and zigzag simulators
``validation`` acceptance-criteria harness (criteria (a)-(d) of mission W3-N08)
``imo_criteria`` statutory minimum-manoeuvrability audit, IMO Res. MSC.137(76)
               (verification-debt closure, tp-ver-8 / mission 1652)
"""

from . import data, imo_criteria, loads, mmg  # noqa: F401

__all__ = ["data", "loads", "mmg", "imo_criteria"]

__version__ = "1.0.0"