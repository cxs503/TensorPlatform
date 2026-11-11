"""环境锁：构建/校验/完整性/本机缺口（blocked 清偿物）。"""
from tools.repro_settlement.envlock import (LOCK_SCHEMA, build_lock, completeness,
                                            integrity, live_gap, resolvable, verify_lock)

RUNTIME = {"implementation": "CPython", "python": "3.11.8", "system": "linux",
           "machine": "loongarch64", "packages": {"numpy": "1.24.0", "scipy": "1.11.0"}}


def _lock(**kw):
    return build_lock("c1", pins=["numpy", "scipy"], solver_commit="deadbeef", runtime=RUNTIME, **kw)


def test_lock_has_schema_and_sha256():
    lk = _lock()
    assert lk["schema"] == LOCK_SCHEMA and len(lk["sha256"]) == 64


def test_lock_body_carries_python_arch_commit_pins():
    body = _lock()["body"]
    assert body["runtime"]["python"] == "3.11.8"
    assert body["runtime"]["machine"] == "loongarch64"
    assert body["solver"]["commit"] == "deadbeef"
    assert body["packages"] == {"numpy": "1.24.0", "scipy": "1.11.0"}


def test_integrity_true_for_fresh_lock():
    assert integrity(_lock()) is True


def test_integrity_false_after_body_tamper():
    lk = _lock()
    lk["body"]["packages"]["numpy"] = "9.9.9"
    assert integrity(lk) is False


def test_verify_detects_sha_mismatch():
    lk = _lock()
    lk["sha256"] = "0" * 64
    assert verify_lock(lk)["status"] == "broken"
    assert verify_lock(lk)["reason"] == "sha256_mismatch"


def test_verify_exact_against_declared_target():
    assert verify_lock(_lock())["status"] == "exact"


def test_verify_patch_drift_on_patch_bump():
    rt = dict(RUNTIME, packages={"numpy": "1.24.9", "scipy": "1.11.0"})
    assert verify_lock(_lock(), rt)["status"] == "patch_drift"


def test_verify_broken_on_minor_bump():
    rt = dict(RUNTIME, packages={"numpy": "2.0.0", "scipy": "1.11.0"})
    assert verify_lock(_lock(), rt)["status"] == "broken"


def test_verify_broken_on_missing_dependency():
    rt = dict(RUNTIME, packages={"numpy": None, "scipy": "1.11.0"})
    res = verify_lock(_lock(), rt)
    assert res["status"] == "broken" and res["missing"] == ["numpy"]


def test_verify_broken_on_python_minor_mismatch():
    rt = dict(RUNTIME, python="3.12.1")
    assert verify_lock(_lock(), rt)["reason"] == "python_minor_mismatch"


def test_completeness_flags_missing_fields():
    lk = {"body": {"runtime": {}, "solver": {}, "packages": {}}, "sha256": "x"}
    assert set(completeness(lk)) == {"python_missing", "arch_missing",
                                     "solver_commit_missing", "pins_missing"}


def test_completeness_ok_for_fresh_lock():
    assert completeness(_lock()) == []


def test_live_gap_reports_only_absent_pins():
    gap = live_gap(_lock())
    assert isinstance(gap, list) and set(gap) <= {"numpy", "scipy"}


def test_resolvable_true_for_declared_target():
    assert resolvable(_lock()) is True
