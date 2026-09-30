"""Contract tests for the shared platform metadata facts."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.dashboard import get_dashboard_statistics
from api.deployment import list_deployments
from api.models import get_model, list_models


class QueryStub:
    def __init__(self, rows=None, total=0, scalar_values=None):
        self.rows = rows or []
        self.total = total
        self.scalar_values = scalar_values

    def filter(self, *args): return self
    def join(self, *args): return self
    def order_by(self, *args): return self
    def offset(self, value): return self
    def limit(self, value): return self
    def all(self): return self.rows
    def first(self): return self.rows[0] if self.rows else None
    def count(self): return self.total
    def scalar(self): return next(self.scalar_values)


def test_model_list_reports_database_total_and_real_deployments():
    models = [
        SimpleNamespace(id=7, to_dict=lambda: {"id": 7, "name": "deployed"}),
        SimpleNamespace(id=8, to_dict=lambda: {"id": 8, "name": "draft"}),
    ]

    class DB:
        def query(self, model): return QueryStub(models, total=101)
        def execute(self, statement):
            sql = str(statement)
            if "DISTINCT ON (model_id)" in sql:
                return SimpleNamespace(fetchall=lambda: [(7, 71, "v3", "run-7", "9")])
            if "deployment_facts" in sql:
                return SimpleNamespace(fetchall=lambda: [(7, 2, 3)])
            if "referenced_model_id" in sql:
                return SimpleNamespace(fetchall=lambda: [(7, 1)])
            return SimpleNamespace(fetchall=lambda: [(7, 2)])
        def rollback(self): pass

    result = list_models(db=DB(), current_user={"role": "ADMIN", "organization_id": 1})
    assert result["total"] == 101
    assert result["items"][0]["is_deployed"] is True
    assert result["items"][0]["deployment_count"] == 2
    assert result["items"][0]["deployment_record_count"] == 3
    assert result["items"][0]["training_job_count"] == 1
    assert result["items"][0]["latest_mlflow_run_id"] == "run-7"
    assert result["items"][0]["latest_version"]["mlflow_model_version"] == "9"
    assert result["items"][1]["is_deployed"] is False


def test_model_detail_exposes_latest_mlflow_run_and_version():
    now = datetime(2026, 7, 17)
    model = SimpleNamespace(id=7, to_dict=lambda: {
        "id": 7, "name": "model-a", "mlflow_name": "foundation_model_7"
    })

    class DB:
        def query(self, model_type): return QueryStub([model], total=1)
        def execute(self, statement, params):
            return SimpleNamespace(fetchone=lambda: (71, "v3", "run-7", "9", True, now))

    result = get_model(7, db=DB())
    assert result["latest_mlflow_run_id"] == "run-7"
    assert result["latest_version"]["id"] == 71
    assert result["latest_version"]["mlflow_model_version"] == "9"


def test_dashboard_deployed_count_comes_from_deployments():
    scalar_values = iter([3, 2, 37, 101, 56, 4, 50, 1, 12.5, 2])

    class DB:
        def query(self, model): return QueryStub(scalar_values=scalar_values)
        def execute(self, statement): return SimpleNamespace(one=lambda: (12, 12, 10))
        def rollback(self): pass

    result = get_dashboard_statistics(db=DB())
    assert result["models"] == {"total": 101, "deployed": 10}
    assert result["deployments"] == {"total": 12, "active": 12}


def test_deployment_list_contract_exposes_strategy_and_active_count():
    now = datetime(2026, 7, 17)

    class DB:
        def execute(self, statement):
            return SimpleNamespace(fetchall=lambda: [
                (1, 7, "endpoint-a", "1.0", "RUNNING", {"strategy": "canary"}, now, now,
                 71, "model-a", "foundation_model_7", "1.0", "run-7", "9"),
                (2, 8, "endpoint-b", "2.0", "stopped", {}, now, now,
                 None, "model-b", None, None, None, None),
            ])

    result = list_deployments(db=DB(), current_user={"role": "ADMIN", "organization_id": 1})
    assert result["total"] == 2
    assert result["active"] == 1
    assert result["items"][0]["strategy"] == "canary"
    assert result["items"][0]["is_active"] is True
    assert result["items"][0]["model_version_id"] == 71
    assert result["items"][0]["mlflow_run_id"] == "run-7"
    assert result["items"][1]["model_version"] == "2.0"
