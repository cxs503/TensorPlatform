"""Crash-bundle forensics: field extraction, self-verification, tamper detection, liveness.

Two layers are tested separately on purpose:

* the *assembly* logic with a synthetic gdb transcript -- deterministic, no ptrace,
  so a CI x86 runner without privileges can still exercise every field rule;
* one *live* SIGSEGV through ptrace + gdb, which is the only way to prove that the
  registers and symbolized frames are real machine state rather than parsed fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.survival import forensics_bundle as fb

GDB_TEXT = """\
Thread 1 (Thread 0x7f1c2a8b1740 (LWP 4242) "python3"):
#0  0x00007f1c2a1c4d0f in ?? ()
#1  0x00007f1c2a1c4d0f in PyObject_GetAttr ()
Mapped address spaces:

          Start Addr           End Addr       Size     Offset  objfile
            0x400000            0x52a000   0x12a000        0x0  /usr/bin/python3
      0x7f1c29000000      0x7f1c2a000000 0x1000000        0x0
      0x7ffe5f000000      0x7ffe5f021000   0x21000        0x0  [stack]
Thread 2 (Thread 0x7f1c2900c640 (LWP 4243) "python3"):
#0  0x00007f1c2bb0e6d2 in __libc_poll ()
rax            0x0                 0
rbx            0x7f1c2a000010      139758874939408
rcx            0x7f1c2a1c4d0f      139758873873679
rdx            0x0                 0
rsi            0x1                 1
rdi            0x7ffe5f020000      140727843356672
rbp            0x7ffe5f020010      0x7ffe5f020010
rsp            0x7ffe5f020008      0x7ffe5f020008
rip            0x7f1c2a1c4d0f      0x7f1c2a1c4d0f <PyObject_GetAttr+47>
rflags         0x10202             [ IF RF ]
"""


def _synthetic_report(tmp_path: Path) -> dict:
    stackcore_path = tmp_path / "stackcore.json"
    stackcore_path.write_text(json.dumps({"frames": [{"address": "0x7f1", "function": "PyObject_GetAttr",
                                                     "file": "Objects/object.c", "line": 1234}]}))
    (tmp_path / "work" / "checkpoints").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "checkpoints" / "ckpt_0003.json").write_text(
        json.dumps({"step": 3, "digest": "a" * 64}))
    return {
        "job_id": "synthetic",
        "term_signal": 11,
        "signal_name": "SIGSEGV",
        "failure_mode": "native_segfault",
        "exit_code": -11,
        "classification_reasons": ["fatal signal 11"],
        "core": {"status": "stackcore_only", "path": str(stackcore_path), "n_native_frames": 2,
                 "native_bt": [{"address": "0x7f1", "function": "PyObject_GetAttr"}]},
        "child": {"resumed_from_step": 0, "result_digest": "b" * 64,
                  "final_checkpoint_digest": "a" * 64},
        "artifacts": {"checkpoints": str(tmp_path / "work" / "checkpoints")},
    }


def test_parse_registers_extracts_at_least_five_fields() -> None:
    regs = fb.parse_registers(GDB_TEXT)
    assert len(regs) >= 5
    assert regs["rip"].startswith("0x7f1c")
    assert regs["rsp"].startswith("0x7ffe")
    assert regs["rflags"] == "0x10202"
    assert "Thread" not in regs and "python3" not in regs


def test_bundle_holds_every_required_artifact(tmp_path: Path) -> None:
    report = _synthetic_report(tmp_path)
    bundle = fb.build_bundle(report, {"threads_and_backtrace": GDB_TEXT,
                                      "frames": report["core"]["native_bt"],
                                      "siginfo": {"si_signo": 11, "si_code": 1}},
                             tmp_path / "out", cfg={"steps": 10},
                             ckpt_dir=report["artifacts"]["checkpoints"], stamp="unit")
    bdir = Path(bundle["bundle_dir"])
    for name in ("siginfo.json", "registers.json", "stack.txt", "mappings.txt",
                 "manifest.json", "SHA256SUMS", "digest.json", "stackcore.json"):
        assert (bdir / name).exists(), name
    regs = json.loads((bdir / "registers.json").read_text())
    assert regs["sufficient"] is True and regs["n_fields"] >= 5
    assert "PyObject_GetAttr" in (bdir / "stack.txt").read_text()
    assert "0x400000" in (bdir / "mappings.txt").read_text()
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert manifest["checkpoint_chain"]["n_checkpoints"] == 1
    assert manifest["digest"] == bundle["manifest_digest"]


def test_bundle_digest_is_location_independent(tmp_path: Path) -> None:
    report = _synthetic_report(tmp_path)
    bundle = fb.build_bundle(report, {"threads_and_backtrace": GDB_TEXT,
                                      "frames": report["core"]["native_bt"], "siginfo": {}},
                             tmp_path / "out", stamp="loc")
    src = Path(bundle["bundle_dir"])
    dst = tmp_path / "elsewhere" / "copy"
    dst.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(src, dst)
    a = fb.verify_bundle(src)
    b = fb.verify_bundle(dst)
    assert a["ok"] and b["ok"]
    assert a["bundle_digest"] == b["bundle_digest"] == bundle["bundle_digest"]


def test_tampered_bundle_is_rejected(tmp_path: Path) -> None:
    report = _synthetic_report(tmp_path)
    bundle = fb.build_bundle(report, {"threads_and_backtrace": GDB_TEXT,
                                      "frames": report["core"]["native_bt"], "siginfo": {}},
                             tmp_path / "out", stamp="tamper")
    bdir = Path(bundle["bundle_dir"])
    stack = bdir / "stack.txt"
    stack.write_text(stack.read_text().replace("PyObject_GetAttr", "PyObject_GetAttribute"))
    check = fb.verify_bundle(bdir)
    assert check["ok"] is False
    assert check["mismatched"] == ["stack.txt"]


def test_missing_file_is_reported(tmp_path: Path) -> None:
    report = _synthetic_report(tmp_path)
    bundle = fb.build_bundle(report, {"threads_and_backtrace": GDB_TEXT,
                                      "frames": report["core"]["native_bt"], "siginfo": {}},
                             tmp_path / "out", stamp="missing")
    bdir = Path(bundle["bundle_dir"])
    (bdir / "mappings.txt").unlink()
    check = fb.verify_bundle(bdir)
    assert check["ok"] is False and check["missing"] == ["mappings.txt"]


def test_registers_below_five_are_reported_as_insufficient(tmp_path: Path) -> None:
    """Honest degradation: if gdb cannot deliver registers we say so, we do not invent them."""
    report = _synthetic_report(tmp_path)
    bundle = fb.build_bundle(report, {"threads_and_backtrace": "rip 0x1\nrsp 0x2\n",
                                      "frames": [], "siginfo": {}}, tmp_path / "out", stamp="short")
    regs = json.loads((Path(bundle["bundle_dir"]) / "registers.json").read_text())
    assert regs["n_fields"] == 2 and regs["sufficient"] is False


def test_live_segv_produces_symbolized_stack_and_registers(tmp_path: Path) -> None:
    """A1 liveness: a real SIGSEGV under ptrace yields real registers, stack and a digest."""
    from tools.longrun import guardian

    if guardian.find_gdb() is None:  # pragma: no cover - environmental
        pytest.skip("gdb not available")
    bundle = fb.dump_crash(
        workdir=tmp_path / "run", outroot=tmp_path, crash_mode="segv", at_step=6,
        steps=12, checkpoint_every=3, stamp="live",
        cfg={"job": {"grid": 16}, "steps": 12, "checkpoint_every": 3,
             "forensics": {"stack_interval_s": 0.5}},
    )
    bdir = Path(bundle["bundle_dir"])
    regs = json.loads((bdir / "registers.json").read_text())
    assert regs["n_fields"] >= 5, regs
    assert bundle["failure_mode"] == "native_segfault"
    assert bundle["signal"] == 11
    assert bundle["frame_lines"] >= 1
    assert fb.verify_bundle(bdir)["ok"] is True
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert manifest["numpy"] and manifest["python"]["version"]