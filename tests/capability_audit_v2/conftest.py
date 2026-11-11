import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = "tests/capability_audit_v2/fixtures/spec_exec.json"
LABELS = "tests/capability_audit_v2/fixtures/labels.json"
PATHS = "tests/capability_audit_v2/fixtures/upgrade_paths.json"

for p in (str(ROOT), str(FIXTURES)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def pipeline_result():
    """一次完整管线（含可执行证据），供只读断言复用，避免重复起子进程。"""
    from tools.capability_audit_v2 import cli

    return cli._run_once(SPEC, ROOT, LABELS, PATHS, None)