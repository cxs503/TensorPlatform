"""Ice-region multi-physics digital twin (W3-N10).

Public surface
--------------
``SurrogateRegistry``   five physics surrogates behind one ``predict`` contract
``ExtendedKalmanFilter`` 3-state estimator fusing sensors with model predictions
``OnlinePredictor``    1 h / 6 h / 24 h performance envelope forecasting
``DigitalTwinOrchestrator``  production loop (ingest -> estimate -> forecast)
"""
from .dt_orchestrator import DigitalTwinOrchestrator, get_orchestrator
from .online_predictor import EnvironmentForecast, OnlinePredictor, VoyagePlan
from .state_estimator import (ExtendedKalmanFilter, Measurement, STATE_NAMES,
                              convergence_study)
from .surrogate_registry import (PCESurrogate, Surrogate, SurrogateRegistry,
                                 get_registry)
from .types import (API_VERSION, DT_VERSION, SCHEMA_VERSION, DigitalTwinError,
                    GeometryValidationError, HullGeometry, InputValidationError,
                    SurrogateExecutionError, SurrogateNotFound)

__all__ = [
    "DigitalTwinOrchestrator", "get_orchestrator",
    "EnvironmentForecast", "OnlinePredictor", "VoyagePlan",
    "ExtendedKalmanFilter", "Measurement", "STATE_NAMES", "convergence_study",
    "PCESurrogate", "Surrogate", "SurrogateRegistry", "get_registry",
    "API_VERSION", "DT_VERSION", "SCHEMA_VERSION", "DigitalTwinError",
    "GeometryValidationError", "HullGeometry", "InputValidationError",
    "SurrogateExecutionError", "SurrogateNotFound",
]