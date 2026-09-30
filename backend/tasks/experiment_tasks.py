"""Celery entrypoint for durable model-factory experiments."""
import logging

from database.config import SessionLocal
from tasks.celery_app import celery_app
from utils.experiment_metrics import log_experiment_event


@celery_app.task(bind=True, name="experiment.execute_job", acks_late=True, reject_on_worker_lost=True, soft_time_limit=3600, time_limit=3660)
def execute_experiment_job(self, job_id: int, organization_id: int):
    from services.experiment_runtime import (
        execute_persisted_experiment,
        experiment_kind,
        mark_experiment_failed,
        serialize_experiment,
    )
    db = SessionLocal()
    try:
        job = execute_persisted_experiment(db, int(job_id), int(organization_id), task_id=str(self.request.id or ""))
        log_experiment_event(
            "celery_delivery_finished", kind=experiment_kind(job),
            job_id=job.id, organization_id=job.organization_id,
            status=getattr(job.status, "value", job.status),
        )
        return serialize_experiment(job)
    except Exception as exc:
        db.rollback()
        try:
            job = mark_experiment_failed(db, int(job_id), int(organization_id), exc)
            log_experiment_event(
                "celery_delivery_failed", kind=experiment_kind(job),
                job_id=job.id, organization_id=job.organization_id,
                status=getattr(job.status, "value", job.status),
                error_type=type(exc).__name__, level=logging.ERROR,
            )
            return serialize_experiment(job)
        except Exception:
            db.rollback()
            log_experiment_event(
                "celery_failure_persistence_failed", kind="other",
                job_id=int(job_id), organization_id=int(organization_id),
                error_type=type(exc).__name__, level=logging.ERROR,
            )
            raise
    finally: db.close()
