"""Portable factory identity and project configuration, shared with workers."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class FactoryProfile:
    name: str
    state_root: Path
    projects: dict[str, dict]
    roles: dict[str, dict]

    @property
    def project_names(self) -> tuple[str, ...]:
        return tuple(self.projects)

    def public(self) -> dict:
        return {
            "name": self.name,
            "projects": [{"id": key, "name": value.get("name", key)}
                         for key, value in self.projects.items()],
            "roles": [{"id": key, "name": value["name"],
                       "model_configured": bool(os.getenv(value["model_env"], "").strip())}
                      for key, value in self.roles.items()],
            "execution": {"acceptance_required": True, "isolation": "host-process",
                          "autonomous_llm_configured": bool(os.getenv("LLM_BASE_URL")
                                                            and os.getenv("LLM_API_KEY"))},
        }


@lru_cache(maxsize=4)
def _load(filename: str) -> FactoryProfile:
    path = Path(filename).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("factory profile requires schema_version=1")
    projects = data.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("factory profile requires at least one project")
    resolved = {}
    for key, item in projects.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,119}", key):
            raise ValueError(f"invalid project id: {key!r}")
        root = (path.parent / item["path"]).resolve()
        resolved[key] = {**item, "path": str(root)}
    roles = data.get("roles", {})
    for role in roles.values():
        if not isinstance(role.get("name"), str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]*", role.get("model_env", "")
        ):
            raise ValueError("roles require a name and a model_env variable name")
    return FactoryProfile(name=data["name"],
                          state_root=(path.parent / data["state_root"]).resolve(),
                          projects=resolved, roles=roles)


def get_factory_profile() -> FactoryProfile | None:
    filename = os.getenv("FACTORY_PROFILE", "")
    return _load(filename) if filename else None


def validate_projects(repo: str, co_repos: list[str] | None) -> None:
    from fastapi import HTTPException

    profile = get_factory_profile()
    repos = [repo] + (co_repos or [])
    if len(set(repos)) != len(repos):
        raise HTTPException(422, "project list contains duplicates")
    if profile and any(name not in profile.projects for name in repos):
        raise HTTPException(422, "project is outside the configured factory workspace")


def validate_base_ref(value: str) -> None:
    from fastapi import HTTPException

    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", value)
            or ".." in value or "//" in value):
        raise HTTPException(422, "base_ref must be a branch, tag or commit without command options")


def require_factory_agent(user: dict) -> None:
    from fastapi import HTTPException

    if get_factory_profile() and str(user.get("role", "")).upper() != "AGENT":
        raise HTTPException(403, "a dedicated AGENT account is required")
