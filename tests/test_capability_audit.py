"""能力审计工具回归测试（M-R16-04）。

验证 ``audit_capabilities`` 工具能正常返回 21 项能力的证据等级，
不再因 ``capabilities.json`` 路径缺失而抛出 [Errno 2]。

验收判据：
- audit_capabilities() 无 FileNotFoundError / Errno 2
- 返回 21 项能力，每项含 capability / evidence_level / coverage 字段
- 证据等级 ∈ {REAL, MOCK, STUB, WITHHELD, NOT_SUPPORTED}
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

# 确保 tools/ 可导入（tests/ 下 parents[1] 即 repo root）
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.audit.ledger_consumer_audit_tool import audit_capabilities  # noqa: E402
from tools.audit.ledger_paths import resolve_ledger  # noqa: E402

VALID_EVIDENCE_LEVELS = {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED", "UNKNOWN"}
_CAP_RE = re.compile(r"^(\S+)\s+自述\s+(\S+)\s+对账\s+(\S+)\s+(\S+)\s+域\s+(.+)$")


def _parse_audit_output(text: str) -> list[dict]:
    caps = []
    for line in text.strip().split("\n"):
        m = _CAP_RE.match(line)
        if m:
            caps.append({
                "capability": m.group(1),
                "claimed_level": m.group(2),
                "evidence_level": m.group(3),
                "divergence": m.group(4),
                "coverage": m.group(5).strip(),
            })
    return caps


class TestAuditCapabilitiesNoError:
    def test_no_file_not_found_error(self):
        try:
            result = audit_capabilities()
        except FileNotFoundError as exc:
            pytest.fail(f"audit_capabilities 抛出 FileNotFoundError: {exc}")
        except OSError as exc:
            if exc.errno == 2:
                pytest.fail(f"audit_capabilities 抛出 [Errno 2]: {exc}")
            raise
        assert isinstance(result, str) and len(result) > 0

    def test_no_unknown_degradation(self):
        result = audit_capabilities()
        assert "UNKNOWN" not in result.split("\n")[0]


class TestAuditCapabilitiesCount:
    def test_returns_21_capabilities(self):
        result = audit_capabilities()
        caps = _parse_audit_output(result)
        assert len(caps) == 21, f"期望 21 项能力，实际 {len(caps)} 项"

    def test_header_mentions_21(self):
        result = audit_capabilities()
        assert "21" in result.strip().split("\n")[0]


class TestAuditCapabilitiesFields:
    @pytest.fixture(scope="class")
    @classmethod
    def caps(cls):
        return _parse_audit_output(audit_capabilities())

    def test_each_has_capability_field(self, caps):
        for c in caps:
            assert "capability" in c and len(c["capability"]) > 0

    def test_each_has_evidence_level_field(self, caps):
        for c in caps:
            assert "evidence_level" in c
            assert c["evidence_level"] in VALID_EVIDENCE_LEVELS

    def test_each_has_coverage_field(self, caps):
        for c in caps:
            assert "coverage" in c and len(c["coverage"]) > 0


class TestLedgerResolver:
    def test_resolve_ledger_no_exception(self):
        ref = resolve_ledger()
        assert isinstance(ref, dict)
        assert "status" in ref and "capabilities" in ref

    def test_resolve_ledger_has_21_capabilities(self):
        ref = resolve_ledger()
        assert ref["capabilities"] == 21

    def test_resolve_ledger_has_path(self):
        ref = resolve_ledger()
        assert ref.get("path") or ref.get("path_rel")


class TestCapabilityLedgerFile:
    @pytest.fixture(scope="class")
    @classmethod
    def ledger_data(cls):
        lp = _REPO_ROOT / "artifacts/capability_ledger/capability_ledger_resolved.json"
        assert lp.is_file(), f"台账文件不存在: {lp}"
        return json.loads(lp.read_text(encoding="utf-8"))

    def test_ledger_has_capabilities_list(self, ledger_data):
        caps = ledger_data.get("capabilities")
        assert isinstance(caps, list) and len(caps) == 21

    def test_each_capability_has_id(self, ledger_data):
        for c in ledger_data["capabilities"]:
            assert "id" in c

    def test_each_capability_has_reconciled_level(self, ledger_data):
        for c in ledger_data["capabilities"]:
            assert "reconciled_level" in c
            assert c["reconciled_level"] in VALID_EVIDENCE_LEVELS

    def test_each_capability_has_domain(self, ledger_data):
        for c in ledger_data["capabilities"]:
            assert "domain" in c
