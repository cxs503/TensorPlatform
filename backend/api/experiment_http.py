"""HTTP boundary for the model-factory experiment runtime."""

from fastapi import HTTPException

from services.experiment_runtime import (
    ExperimentAccessDenied,
    ExperimentCapacityError,
    ExperimentNotFound,
    ExperimentRuntimeError,
    ExperimentValidationError,
)


def audit_actor(current_user: dict) -> str:
    """Return the authenticated principal identifier used by audit events."""
    return str((current_user or {}).get("id") or (current_user or {}).get("username") or "system/unknown")


def experiment_call(function, *args, **kwargs):
    """Translate stable runtime errors without leaking internal exceptions."""
    try:
        return function(*args, **kwargs)
    except (ExperimentNotFound, ExperimentAccessDenied) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExperimentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExperimentCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ExperimentRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["audit_actor", "experiment_call"]
