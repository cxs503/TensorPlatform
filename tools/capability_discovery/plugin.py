"""运行期插件：把 pytest 的**真实结局**采下来，供定级使用。

用法（求解器仓内）
------------------
    # PYTHONPATH 含本仓根
    pytest -q -p tools.capability_discovery.plugin tests/ \
        --capability-outcomes artifacts/capability_discovery/outcomes.json

或设环境变量：``CAPABILITY_DISCOVERY_OUT=<path>``。

采集内容
--------
* 每个测试节点收集到的 capability marker 元数据（可直接作为发现源）；
* 该节点的运行结局：``passed`` / ``failed`` / ``error`` / ``skipped`` /
  ``xfailed`` / ``xpassed``（区分 xfail 与 skip 是定级的前提）。

插件对未打 marker 的测试零影响：不拦截、不跳过、不改写结果。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

OUTCOME_ENV = "CAPABILITY_DISCOVERY_OUT"
OPTION_NAME = "--capability-outcomes"
SCHEMA = "capability_discovery.outcomes.v1"


def _marker_meta(marker: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"args": [], "kwargs": {}}
    try:
        meta["args"] = [a for a in marker.args if isinstance(a, (str, int, float, bool))]
        meta["kwargs"] = {
            k: v
            for k, v in marker.kwargs.items()
            if isinstance(v, (str, int, float, bool, list, tuple, dict)) and k != "id"
        }
        if marker.kwargs.get("id") is not None:
            meta["kwargs"]["id"] = marker.kwargs["id"]
    except Exception:  # pragma: no cover - marker 实现异常不应打断测试
        pass
    return meta


def pytest_addoption(parser):  # noqa: D103
    group = parser.getgroup("capability-discovery")
    group.addoption(
        OPTION_NAME,
        action="store",
        default=None,
        help="写入能力发现运行结局 JSON 的路径（也可用环境变量 %s）" % OUTCOME_ENV,
    )


_STATE: Dict[str, Any] = {"store": None}


def pytest_configure(config):  # noqa: D103
    path = config.getoption(OPTION_NAME) or os.environ.get(OUTCOME_ENV)
    _STATE["store"] = {
        "path": path,
        "tests": {},
        "markers": {},
    }


def pytest_unconfigure(config):  # noqa: D103
    _STATE["store"] = None


def pytest_collection_modifyitems(session, config, items):  # noqa: D103
    store = _STATE.get("store")
    if store is None:
        return
    for item in items:
        try:
            marks = [_marker_meta(m) for m in item.iter_markers(name="capability")]
        except Exception:  # pragma: no cover
            marks = []
        if marks:
            store["markers"][item.nodeid] = marks


def pytest_runtest_logreport(report):  # noqa: D103
    store = _STATE.get("store")
    if store is None:
        return
    nodeid = getattr(report, "nodeid", "")
    state = _state_of(report)
    if state is None:
        return
    entry = store["tests"].setdefault(nodeid, {"nodeid": nodeid, "outcome": state, "durations": []})
    # 结局按矛盾度优先级覆盖：error > failed > passed > ...
    precedence = ["error", "failed", "passed", "xpassed", "xfailed", "skipped"]
    if precedence.index(state) < precedence.index(entry["outcome"]):
        entry["outcome"] = state
    entry["durations"].append({"when": report.when, "duration": round(getattr(report, "duration", 0.0), 4)})
    if getattr(report, "wasxfail", None):
        entry["wasxfail"] = str(report.wasxfail)[:200]


def _state_of(report: Any) -> str | None:
    when = getattr(report, "when", "")
    if when == "setup":
        if getattr(report, "failed", False):
            return "error"
        if getattr(report, "skipped", False):
            return "xfailed" if getattr(report, "wasxfail", None) else "skipped"
        return None
    if when == "call":
        if getattr(report, "passed", False):
            return "xpassed" if getattr(report, "wasxfail", None) else "passed"
        if getattr(report, "failed", False):
            return "failed"
        if getattr(report, "skipped", False):
            return "xfailed" if getattr(report, "wasxfail", None) else "skipped"
        return None
    if when == "teardown" and getattr(report, "failed", False):
        return "error"
    return None


def pytest_sessionfinish(session, exitstatus):  # noqa: D103
    config = session.config
    store = _STATE.get("store")
    if not store or not store.get("path"):
        return
    tests: List[Dict[str, Any]] = []
    for nodeid in sorted(store["tests"]):
        entry = dict(store["tests"][nodeid])
        entry["markers"] = store["markers"].get(nodeid, [])
        tests.append(entry)
    doc = {
        "schema": SCHEMA,
        "session": {
            "exitstatus": int(exitstatus),
            "rootdir": str(getattr(config, "rootdir", "")),
            "collected": len(store["markers"]) or len(tests),
        },
        "tests": tests,
    }
    path = Path(store["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")


# ── 消费侧辅助 ───────────────────────────────────────────────────────────────
def load_outcomes(path: Path | str) -> Dict[str, str]:
    """读运行结局文件 → {nodeid: state}（供 pipeline.run 使用）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"运行结局文件 schema 不匹配: {doc.get('schema')!r}")
    return {t["nodeid"]: t["outcome"] for t in doc.get("tests", []) if t.get("outcome")}


def load_outcome_records(path: Path | str) -> List[Dict[str, Any]]:
    """读运行结局文件 → 原始测试记录（含 marker 元数据，可作纯运行期发现源）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"运行结局文件 schema 不匹配: {doc.get('schema')!r}")
    return list(doc.get("tests", []))