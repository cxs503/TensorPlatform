"""pytest 夹具：把 backend/ 与仓库根塞进 sys.path，并缓存一次全链结果。

本目录自洽可跑::

    PYTHONPATH=backend python3 -m pytest backend/green_navigation/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]         # worktree 根
_BACKEND = _ROOT / "backend"
for p in (str(_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def chain_result():
    """一次性跑完全链（3 算例 + 判据），供验收类测试复用。"""
    from green_navigation import chain

    return chain.run_all()


@pytest.fixture(scope="session")
def sfoc_model():
    from green_navigation import chain

    return chain.calibrate_sfoc()["model"]


@pytest.fixture(scope="session")
def cases():
    from green_navigation import chain

    return {c["case_id"]: c for c in chain.load_cases()}


@pytest.fixture()
def vlcc(cases):
    from green_navigation import chain

    case = cases["KVLCC2_VLCC"]
    geometry, prop, train = chain._ship_parts(case)
    return case, geometry, prop, train