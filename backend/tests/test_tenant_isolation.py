"""Fail-closed gates for core multi-tenant resources (no production writes)."""

import os
os.environ.setdefault("WORKSPACE_ROOT", "/tmp/foundation-test-workspaces")

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.deps import require_permission
from api.tenant_access import (
    assert_organization_access,
    create_organization_id,
    normalized_role,
    require_dataset_access,
    require_deployment_access,
    require_model_access,
    require_training_job_access,
    scope_query,
)
from api.workspace import get_user_id


ADMIN = {"id": 1, "role": "ADMIN", "organization_id": 1}
ENGINEER = {"id": 2, "role": "ENGINEER", "organization_id": 10}
VIEWER = {"id": 3, "role": "VIEWER", "organization_id": 10}


class QuerySpy:
    def __init__(self):
        self.filters = []

    def filter(self, expression):
        self.filters.append(expression)
        return self


class DBRow:
    def __init__(self, organization_id):
        self.organization_id = organization_id

    def execute(self, statement, params):
        return SimpleNamespace(fetchone=lambda: (self.organization_id,))


def test_roles_are_case_insensitive_and_enum_safe():
    assert normalized_role(ADMIN) == "admin"
    assert normalized_role({"role": "UserRole.ENGINEER"}) == "engineer"


def test_admin_query_is_unrestricted_but_user_query_is_scoped():
    admin_query = QuerySpy()
    user_query = QuerySpy()
    assert scope_query(admin_query, SimpleNamespace(__eq__=lambda *_: True), ADMIN) is admin_query
    scope_query(user_query, 10, ENGINEER)
    assert admin_query.filters == []
    assert len(user_query.filters) == 1


def test_non_admin_cannot_spoof_organization_on_create():
    assert create_organization_id(None, ENGINEER) == 10
    assert create_organization_id(10, ENGINEER) == 10
    with pytest.raises(HTTPException) as exc:
        create_organization_id(11, ENGINEER)
    assert exc.value.status_code == 403
    assert create_organization_id(11, ADMIN) == 11


def test_foreign_object_is_hidden_with_404():
    assert_organization_access(10, ENGINEER)
    assert_organization_access(99, ADMIN)
    with pytest.raises(HTTPException) as exc:
        assert_organization_access(11, ENGINEER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate,resource_id",
    [
        (require_dataset_access, 7),
        (require_model_access, 8),
        (require_deployment_access, 9),
        (require_training_job_access, "train_abc"),
    ],
)
async def test_all_core_object_gates_reject_foreign_tenant(gate, resource_id):
    with pytest.raises(HTTPException) as exc:
        parameter = "job_id" if isinstance(resource_id, str) else (
            "dep_id" if gate is require_deployment_access else
            "model_id" if gate is require_model_access else "dataset_id"
        )
        await gate(**{parameter: resource_id}, db=DBRow(11), user=ENGINEER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_rbac_write_permissions_match_legacy_roles_case_insensitively():
    assert await require_permission("datasets:write")(ENGINEER) == ENGINEER
    assert await require_permission("deployment:write")(ADMIN) == ADMIN
    with pytest.raises(HTTPException) as exc:
        await require_permission("models:write")(VIEWER)
    assert exc.value.status_code == 403


def test_workspace_id_comes_from_authenticated_user():
    assert get_user_id(ENGINEER) == "2"
