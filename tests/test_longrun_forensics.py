"""Crash-forensics regression suite (mission m9082, node p148w2-n03).

Every assertion here is about *evidence*, not about the job: after a deliberate
crash the autopsy must be complete (100 % of the required fields), classified
correctly, and must carry a symbolized stack.  Domain-free by construction.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.longrun import forensics, guardian, supervise  # noqa: E402

EXPECTED_MODE = {
    "segv": "native_segfault",
    "abort": "native_abort",
    "exception": "python_exception",
    "memory_error": "python_memory_error",
    "sigkill": "killed_externally",
}

BASE_CFG = {
    "job": {"grid": 24},
    "steps": 20,
    "checkpoint_every": 5,
    "crash": {"at_step": 8},
    "forensics": {"stack_interval_s": 0.1, "memory_interval_s": 0.1},
}


@pytest.mark.parametrize("mode", sorted(EXPECTED_MODE))
def test_autopsy_is_complete_for_every_crash_mode(tmp_path, mode):
    """Deliberate crash -> forensic bundle completeness must be 100 % (all modes)."""
    cfg = json.loads(json.dumps(BASE_CFG))
    cfg["crash"]["mode"] = mode
    rep = supervise.supervise(cfg, workdir=tmp_path / mode, job_id=mode)

    assert rep["failure_mode"] == EXPECTED_MODE[mode], rep["classification_reasons"]
    assert rep["bundle_completeness"]["completeness"] == 1.0, rep["bundle_completeness"]
    assert rep["bundle_completeness"]["missing"] == []
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    assert bundle["failure_mode"] == EXPECTED_MODE[mode]
    assert bundle["classification_reasons"], "classification must explain itself"
    assert bundle["python_stack_snapshot"], "python stack snapshot must be recorded"
    assert bundle["state_journal_tail"], "state journal tail must be recorded"
    assert bundle["code_version"]["commit"], "code version must be pinned"
    # every crash left a hashed artifact and a result/forensic trail on disk
    assert Path(rep["bundle_path"]).exists()
    assert Path(bundle["artifacts"]["journal"]).exists()


def test_signal_crash_carries_siginfo_and_symbolized_native_stack(tmp_path):
    cfg = json.loads(json.dumps(BASE_CFG))
    cfg["crash"]["mode"] = "segv"
    rep = supervise.supervise(cfg, workdir=tmp_path / "segv", job_id="segv")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())

    assert bundle["term_signal"] == 11
    assert bundle["signal_name"] == "SIGSEGV"
    assert bundle["siginfo"]["si_code"] == 1, "SEGV_MAPERR for a null dereference"
    assert bundle["siginfo"]["si_code_name"] == "SEGV_MAPERR"
    assert bundle["siginfo"]["si_addr"] == 0
    assert bundle["exit_code"] == -11, "signalled processes report -signum"
    frames = bundle["native_bt"]
    assert len(frames) >= 3, frames
    assert any(f.get("symbol") for f in frames), "native stack must be symbolized"
    assert bundle["core_status"].startswith("stackcore")
    assert bundle["core_size"] > 0 and bundle["core_sha256"]
    core = json.loads(Path(bundle["core_path"]).read_text())
    assert core["schema"] == "longrun.stackcore/1"
    assert core["frames"], "stackcore must contain parsed frames"


def test_elf_core_is_captured_and_reopens(tmp_path):
    """A real ELF core (gcore) must be written, hashed and re-openable by gdb."""
    if not guardian.find_gdb():
        pytest.skip("gdb unavailable")
    prog = tmp_path / "tiny_crash.py"
    prog.write_text("import ctypes\nctypes.string_at(0)\n")
    res = guardian.run_guarded(
        sys.executable,
        [str(prog)],
        core_path=tmp_path / "core.tiny",
        full_core=True,
        max_core_bytes=512 << 20,
        timeout=180,
    )
    assert res.term_signal == 11
    assert res.core["core_status"] == "stackcore+elf", res.core.get("core_status")
    assert res.core["elf_core_retained"] is True
    assert res.core["elf_core_size"] > 0
    assert res.core["elf_core_sha256"]
    assert Path(res.core["elf_core_path"]).exists()
    assert res.core["core_reopen_ok"] is True, "core must be parseable standalone"
    assert res.core["core_reopen_bt"], "symbolized backtrace from the core file"


def test_oversize_elf_core_is_hashed_then_discarded(tmp_path):
    """4 GiB address spaces must not silently fill the disk: hash, record, delete."""
    if not guardian.find_gdb():
        pytest.skip("gdb unavailable")
    prog = tmp_path / "tiny_crash2.py"
    prog.write_text("import ctypes\nctypes.string_at(0)\n")
    res = guardian.run_guarded(
        sys.executable,
        [str(prog)],
        core_path=tmp_path / "core.big",
        full_core=True,
        max_core_bytes=1024,  # force the guard
        timeout=180,
    )
    assert res.core["elf_core_retained"] is False
    assert res.core["elf_core_size"] > 1024
    assert res.core["elf_core_sha256"], "size+sha256 kept as evidence even when discarded"
    assert res.core["core_status"].endswith("elf_core_oversize")
    assert not Path(tmp_path / "core.big").exists(), "oversized core must be removed"
    assert Path(res.core["core_path"]).exists(), "tier-1 stackcore is always kept"


def test_classification_matrix():
    cases = [
        ({"term_signal": 11}, "native_segfault"),
        ({"term_signal": 6}, "native_abort"),
        ({"term_signal": 7}, "native_bus_error"),
        ({"term_signal": 8}, "native_arith_fault"),
        ({"term_signal": 9, "oom_kill_delta": 1}, "memory_oom_kill"),
        ({"term_signal": 9, "oom_kill_delta": 0}, "killed_externally"),
        ({"exit_code": 70, "child_report": {"error_type": "MemoryError"}}, "python_memory_error"),
        ({"exit_code": 70, "child_report": {"error_type": "CheckpointCorruption"}}, "state_corruption"),
        ({"exit_code": 70, "child_report": {"error_type": "ValueError"}}, "python_exception"),
        ({"exit_code": 75, "child_report": {"stop_reason": "graceful_degraded_exit"}},
         "graceful_degraded_exit"),
        ({"exit_code": 0, "child_report": {"finished": True}}, "completed"),
    ]
    for evidence, expected in cases:
        got = forensics.classify_failure(evidence)["failure_mode"]
        assert got == expected, (evidence, got, expected)


def test_completeness_flags_missing_fields():
    bundle = {name: "x" for name in forensics.REQUIRED_FIELDS}
    bundle["failure_mode"] = "native_segfault"
    bundle["term_signal"] = 11
    assert forensics.completeness(bundle)["completeness"] == 1.0
    del bundle["python_stack_snapshot"]
    score = forensics.completeness(bundle)
    assert score["completeness"] < 1.0
    assert "python_stack_snapshot" in score["missing"]
    # a completed run is not expected to carry a terminating signal
    done = {name: "x" for name in forensics.REQUIRED_FIELDS}
    done["failure_mode"] = "completed"
    done["term_signal"] = None
    assert forensics.completeness(done)["completeness"] == 1.0


def test_journal_survives_signal_kill(tmp_path):
    """A killed process never writes result.json; the journal must still carry the digest."""
    cfg = json.loads(json.dumps(BASE_CFG))
    cfg["crash"]["mode"] = "sigkill"
    cfg["checkpoint_every"] = 3
    rep = supervise.supervise(cfg, workdir=tmp_path / "kill", job_id="kill")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    assert bundle["last_checkpoint_digest"], "checkpoint digest recovered from the journal"
    assert bundle["last_checkpoint_step"] == 6