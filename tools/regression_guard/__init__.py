"""Cross-mission regression guard (campaign node p380w2-n01).

Public surface::

    TaskLedger            injected dependency ledger (artifacts->features->tasks+DAG)
    RepoGraph             static intra-repo import graph
    ImpactAnalyzer        changed paths -> affected capabilities/tasks/tests
    RegressionGuard       plan + execute + compare + structured report
    ScenarioSuite         machine-checkable detection / false-trigger measurement

Design constraints honoured here (TensorPlatform iron law): no domain
vocabulary, no hard-coded paths, no assumption that the guarded code lives in
this repo — everything enters through the ledger, the manifest and the config.
"""
from .model import (
    GUARD_VERSION,
    LEDGER_SCHEMA,
    REPORT_SCHEMA,
    Change,
    ChangeManifest,
    Task,
    TaskLedger,
    canonical_json,
    sha256_obj,
)
from .impact import ImpactAnalyzer, Impact, changes_from_git
from .scanner import RepoGraph, path_matches
from .guard import GuardConfig, RegressionGuard, write_report
from .runner import FakeRunner, PytestRunner, TestOutcome
from .cache import ResultCache
from .scenarios import Scenario, ScenarioSuite, evaluate_scenarios

__all__ = [
    "GUARD_VERSION", "LEDGER_SCHEMA", "REPORT_SCHEMA",
    "Change", "ChangeManifest", "Task", "TaskLedger", "canonical_json", "sha256_obj",
    "ImpactAnalyzer", "Impact", "changes_from_git",
    "RepoGraph", "path_matches",
    "GuardConfig", "RegressionGuard", "write_report",
    "FakeRunner", "PytestRunner", "TestOutcome", "ResultCache",
    "Scenario", "ScenarioSuite", "evaluate_scenarios",
]