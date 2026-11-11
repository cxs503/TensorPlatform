"""治理成熟化测试的公共夹具：把仓库根与 backend/ 放进 sys.path，起一个小 app。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(ROOT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TOOL_DIRS = ("tools/pulse", "tools/chronicle", "tools/worker_portrait")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """可篡改的最小仓库副本：契约文件 + 度量工具源码（用于负控演练）。"""
    dst = tmp_path / "repo"
    (dst / "contracts").mkdir(parents=True)
    shutil.copy2(ROOT / "contracts" / "metrics_emission.v1.json",
                 dst / "contracts" / "metrics_emission.v1.json")
    shutil.copytree(ROOT / "contracts" / "coupling", dst / "contracts" / "coupling")
    shutil.copytree(BACKEND / "contracts", dst / "backend" / "contracts")
    for rel in TOOL_DIRS:
        shutil.copytree(ROOT / rel, dst / rel)
    return dst


@pytest.fixture(scope="session")
def api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.governance_maturity_integration import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)