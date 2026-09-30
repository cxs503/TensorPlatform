"""
Persistent platform scheduler.

Schedules use China Standard Time for Cron evaluation.  Actual work is executed
through the same application services as the manual API routes, never by
returning a placeholder success message.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import croniter

SCHEDULE_DIR = Path(os.getenv("SCHEDULER_STORAGE_DIR", "/app/uploads/schedules"))
SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULE_FILE = SCHEDULE_DIR / "schedules.json"
EXECUTION_FILE = SCHEDULE_DIR / "executions.json"
PLATFORM_TIMEZONE = ZoneInfo(os.getenv("PLATFORM_TIMEZONE", "Asia/Shanghai"))
MAX_EXECUTION_HISTORY = 100


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    BATCH_INFERENCE = "batch_inference"
    MODEL_TRAINING = "model_training"


def _now() -> datetime:
    return datetime.now(PLATFORM_TIMEZONE)


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return {"message": str(value)[:1000]}


class Schedule:
    def __init__(self, name: str, task_type: str, cron_expression: str, config: Dict[str, Any], enabled: bool = True):
        self.id = f"schedule_{uuid.uuid4().hex[:8]}"
        self.name = name
        self.task_type = task_type
        self.cron_expression = cron_expression
        self.config = dict(config or {})
        self.enabled = enabled
        self.created_at = _now().isoformat()
        self.last_run: Optional[str] = None
        self.next_run = self._calculate_next_run()

    def _calculate_next_run(self) -> Optional[str]:
        try:
            return croniter.croniter(self.cron_expression, _now()).get_next(datetime).isoformat()
        except Exception:
            return None

    def to_dict(self, include_owner: bool = False) -> dict:
        config = dict(self.config)
        if not include_owner:
            config.pop("_scheduler_owner", None)
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "cron_expression": self.cron_expression,
            "config": config,
            "enabled": self.enabled,
            "status": "active" if self.enabled else "paused",
            "created_at": self.created_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
        }


class TaskExecution:
    def __init__(self, schedule_id: str, execution_id: Optional[str] = None):
        self.schedule_id = schedule_id
        self.execution_id = execution_id or f"exec_{uuid.uuid4().hex[:8]}"
        self.status: TaskStatus = TaskStatus.PENDING
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self.result: Any = None
        self.error: Optional[str] = None

    @classmethod
    def from_dict(cls, value: dict) -> "TaskExecution":
        item = cls(str(value.get("schedule_id") or ""), str(value.get("execution_id") or ""))
        try:
            item.status = TaskStatus(str(value.get("status") or TaskStatus.FAILED))
        except ValueError:
            item.status = TaskStatus.FAILED
        item.start_time = value.get("start_time")
        item.end_time = value.get("end_time")
        item.result = value.get("result")
        item.error = value.get("error")
        return item

    def to_dict(self) -> dict:
        return {
            "schedule_id": self.schedule_id,
            "execution_id": self.execution_id,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "result": _json_safe(self.result),
            "error": self.error,
        }


class SchedulerService:
    def __init__(self):
        self.schedules: Dict[str, Schedule] = {}
        self.executions: Dict[str, List[TaskExecution]] = {}
        self._running: set[str] = set()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        with self._lock:
            if SCHEDULE_FILE.exists():
                try:
                    for value in json.loads(SCHEDULE_FILE.read_text(encoding="utf-8")):
                        schedule = Schedule(
                            value["name"], value["task_type"], value["cron_expression"],
                            value.get("config") or {}, bool(value.get("enabled", True)),
                        )
                        schedule.id = value.get("id") or schedule.id
                        schedule.created_at = value.get("created_at") or schedule.created_at
                        schedule.last_run = value.get("last_run")
                        # Recalculate after restart in the current business timezone.
                        schedule.next_run = schedule._calculate_next_run()
                        self.schedules[schedule.id] = schedule
                except Exception:
                    self.schedules = {}
            if EXECUTION_FILE.exists():
                try:
                    raw = json.loads(EXECUTION_FILE.read_text(encoding="utf-8"))
                    for schedule_id, records in (raw or {}).items():
                        loaded = [TaskExecution.from_dict(record) for record in records if isinstance(record, dict)]
                        for record in loaded:
                            if record.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                                record.status = TaskStatus.FAILED
                                record.end_time = _now().isoformat()
                                record.error = "调度服务重启，未完成的执行已终止"
                        self.executions[schedule_id] = loaded[-MAX_EXECUTION_HISTORY:]
                except Exception:
                    self.executions = {}
            self._save_locked()

    def _save_locked(self) -> None:
        SCHEDULE_FILE.write_text(
            json.dumps([item.to_dict(include_owner=True) for item in self.schedules.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        EXECUTION_FILE.write_text(
            json.dumps(
                {key: [item.to_dict() for item in values[-MAX_EXECUTION_HISTORY:]] for key, values in self.executions.items()},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def create_schedule(self, name: str, task_type: str, cron_expression: str, config: Dict[str, Any]) -> Schedule:
        if task_type not in {item.value for item in TaskType}:
            raise ValueError("仅支持批量推理、流水线和模型训练定时任务")
        if not croniter.croniter.is_valid(cron_expression):
            raise ValueError("Cron 表达式无效，应为“分 时 日 月 周”五段格式")
        schedule = Schedule(name.strip() or "定时任务", task_type, cron_expression, config)
        with self._lock:
            self.schedules[schedule.id] = schedule
            self.executions.setdefault(schedule.id, [])
            self._save_locked()
        return schedule

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        with self._lock:
            return self.schedules.get(schedule_id)

    def list_schedules(self, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self.schedules.values())
            if enabled is not None:
                items = [item for item in items if item.enabled == enabled]
            return [item.to_dict() for item in sorted(items, key=lambda item: item.created_at, reverse=True)]

    def update_schedule(self, schedule_id: str, updates: Dict[str, Any]) -> bool:
        with self._lock:
            schedule = self.schedules.get(schedule_id)
            if not schedule:
                return False
            if "name" in updates:
                schedule.name = str(updates["name"]).strip() or schedule.name
            if "cron_expression" in updates:
                cron = str(updates["cron_expression"])
                if not croniter.croniter.is_valid(cron):
                    raise ValueError("Cron 表达式无效，应为“分 时 日 月 周”五段格式")
                schedule.cron_expression = cron
            if "config" in updates and isinstance(updates["config"], dict):
                owner = schedule.config.get("_scheduler_owner")
                schedule.config = dict(updates["config"])
                if owner:
                    schedule.config["_scheduler_owner"] = owner
            if "enabled" in updates:
                schedule.enabled = bool(updates["enabled"])
            schedule.next_run = schedule._calculate_next_run() if schedule.enabled else None
            self._save_locked()
            return True

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            if schedule_id not in self.schedules:
                return False
            if schedule_id in self._running:
                raise ValueError("任务正在执行，不能删除")
            self.schedules.pop(schedule_id, None)
            self.executions.pop(schedule_id, None)
            self._save_locked()
            return True

    def trigger_schedule(self, schedule_id: str) -> Dict[str, Any]:
        with self._lock:
            schedule = self.schedules.get(schedule_id)
            if not schedule:
                return {"error": "Schedule not found"}
            if schedule_id in self._running:
                return {"error": "任务正在执行，请等待当前执行完成"}
            execution = TaskExecution(schedule_id)
            execution.status = TaskStatus.RUNNING
            execution.start_time = _now().isoformat()
            self.executions.setdefault(schedule_id, []).append(execution)
            self.executions[schedule_id] = self.executions[schedule_id][-MAX_EXECUTION_HISTORY:]
            self._running.add(schedule_id)
            self._save_locked()
        threading.Thread(target=self._run_execution, args=(schedule_id, execution), daemon=True).start()
        return execution.to_dict()

    # Backward-compatible name used by the existing API.
    def execute_schedule(self, schedule_id: str) -> Dict[str, Any]:
        return self.trigger_schedule(schedule_id)

    def _run_execution(self, schedule_id: str, execution: TaskExecution) -> None:
        try:
            schedule = self.get_schedule(schedule_id)
            if not schedule:
                raise RuntimeError("定时任务已删除")
            execution.result = self._execute_task(schedule)
            execution.status = TaskStatus.COMPLETED
        except Exception as exc:
            execution.status = TaskStatus.FAILED
            execution.error = str(exc)[:4000]
        finally:
            execution.end_time = _now().isoformat()
            with self._lock:
                schedule = self.schedules.get(schedule_id)
                if schedule:
                    schedule.last_run = execution.start_time
                    schedule.next_run = schedule._calculate_next_run() if schedule.enabled else None
                self._running.discard(schedule_id)
                self._save_locked()

    def _owner(self, schedule: Schedule) -> dict:
        owner = schedule.config.get("_scheduler_owner")
        if not isinstance(owner, dict) or owner.get("organization_id") is None:
            raise RuntimeError("该任务缺少创建者组织信息；请删除后重新创建")
        return owner

    def _execute_task(self, schedule: Schedule) -> Dict[str, Any]:
        owner = self._owner(schedule)
        from database.config import SessionLocal

        with SessionLocal() as db:
            if schedule.task_type == TaskType.BATCH_INFERENCE.value:
                model_id = int(schedule.config.get("model_id"))
                dataset_id = int(schedule.config.get("dataset_id"))
                from api.inference import batch_inference
                result = batch_inference(
                    model_id,
                    {"dataset_id": dataset_id, "name": schedule.name, "output_format": schedule.config.get("output_format", "json")},
                    db,
                    owner,
                )
                return {
                    "kind": "batch_inference",
                    "inference_log_id": result.get("id"),
                    "model_id": result.get("model_id"),
                    "dataset_id": result.get("dataset_id"),
                    "count": result.get("count"),
                    "inference_time_ms": result.get("inference_time_ms"),
                    "drift_count": result.get("drift_count"),
                }
            if schedule.task_type == TaskType.MODEL_TRAINING.value:
                from api.real_training import DatasetJobTrainingRequest, create_training_job_from_dataset
                allowed = {
                    "name", "task_type", "model_type", "dataset_id", "target_column",
                    "feature_columns", "hyperparams", "hyperparameter_source",
                    "algorithm_id", "resume_algorithm_id", "source_execution_mode", "model_folder_id",
                }
                payload = {key: value for key, value in schedule.config.items() if key in allowed}
                request = DatasetJobTrainingRequest(**payload)
                result = create_training_job_from_dataset(request, db, owner)
                return {
                    "kind": "model_training",
                    "job_id": result.get("job_id"),
                    "dataset_id": result.get("dataset_id"),
                    "status": result.get("status"),
                }
        raise RuntimeError("未知任务类型")

    def get_executions(self, schedule_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            records = self.executions.get(schedule_id, [])
            return [record.to_dict() for record in records[-max(1, min(limit, MAX_EXECUTION_HISTORY)):]][::-1]

    def dispatch_due_schedules(self) -> int:
        now = _now()
        due: list[str] = []
        with self._lock:
            for schedule in self.schedules.values():
                if not schedule.enabled or schedule.id in self._running or not schedule.next_run:
                    continue
                try:
                    if datetime.fromisoformat(schedule.next_run) <= now:
                        due.append(schedule.id)
                except ValueError:
                    schedule.next_run = schedule._calculate_next_run()
            self._save_locked()
        for schedule_id in due:
            self.trigger_schedule(schedule_id)
        return len(due)


_scheduler_service: Optional[SchedulerService] = None


def get_scheduler_service() -> SchedulerService:
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
