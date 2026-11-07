"""Build + run the H1/H2/H5 digest-producing kernels, emit digest JSON.

Usage (all paths are relative to the repo root)::

    python3 -m tools.repro_digest.emit_digest --bench h1 --emit-digest
    python3 -m tools.repro_digest.emit_digest --bench h2 --flags="-O3 -ffp-contract=off"
    python3 -m tools.repro_digest.emit_digest --bench h5 --values-out /tmp/h5.json

The kernels are plain C11 with no dependencies beyond libm; they print one JSON
object with *exact* hex floats (``%a``).  This module is the only place that
turns those values into digests (see :mod:`tools.repro_digest.ulp`), so the
"computed" layer stays independent of the "judged" layer.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
CSRC = HERE / "csrc"

#: short bench key -> C source stem
BENCHES: Dict[str, str] = {
    "h1": "h1_channel60",
    "h2": "h2_powerlaw",
    "h5": "h5_calibration",
}
BENCH_ORDER: List[str] = ["h1", "h2", "h5"]

#: CPU-model flags that differ per architecture (same *role* in the matrix)
MARCH_PROFILES: Dict[str, List[str]] = {
    "loongarch64": ["-march=la464", "-march=la664"],
    "x86_64": ["-march=x86-64-v2", "-march=x86-64-v3"],
    "AMD64": ["-march=x86-64-v2", "-march=x86-64-v3"],
    "aarch64": ["-mcpu=cortex-a72", "-mcpu=neoverse-n2"],
}


def march_flags() -> List[str]:
    """CPU-model flag candidates for the host ISA (empty if unknown)."""
    return MARCH_PROFILES.get(platform.machine(), [])


def cc_name() -> str:
    return os.environ.get("REPRO_CC", "gcc")


def cc_version(cc: Optional[str] = None) -> str:
    cc = cc or cc_name()
    try:
        out = subprocess.run([cc, "--version"], capture_output=True, text=True,
                             timeout=30).stdout.splitlines()[0]
    except Exception:  # pragma: no cover - toolchain missing
        return "unknown"
    return out.strip()


def libc_version() -> str:
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        lib.gnu_get_libc_version.restype = ctypes.c_char_p
        return lib.gnu_get_libc_version().decode()
    except Exception:  # pragma: no cover - non-glibc
        return platform.libc_ver()[1] or "unknown"


def build(bench: str, flags: str = "", outdir: Optional[Path] = None,
          cc: Optional[str] = None, variant: str = "base") -> Path:
    """Compile one kernel; returns the binary path."""
    if bench not in BENCHES:
        raise KeyError("unknown bench %r (choose from %s)" % (bench, sorted(BENCHES)))
    cc = cc or cc_name()
    outdir = Path(outdir) if outdir else Path(tempfile.mkdtemp(prefix="repro_kernels_"))
    outdir.mkdir(parents=True, exist_ok=True)
    stem = BENCHES[bench]
    binary = outdir / ("%s_%s" % (stem, variant))
    cmd = [cc, "-std=c11", "-Wall", "-Wextra"]
    cmd += (flags.split() if flags else [])
    cmd += [
        '-DBUILD_ARCH="%s"' % platform.machine(),
        '-DBUILD_CC="%s"' % cc_version(cc).replace('"', ''),
        '-DBUILD_FLAGS="%s"' % flags.replace('"', ''),
        '-DBUILD_VARIANT="%s"' % variant,
        "-I", str(CSRC),
        str(CSRC / (stem + ".c")), "-o", str(binary), "-lm",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("compile failed: %s\n%s" % (" ".join(cmd), proc.stderr[-2000:]))
    return binary


def run_kernel(bench: str, flags: str = "", value_out: Optional[Path] = None,
               variant: str = "base", bindir: Optional[Path] = None) -> Dict:
    """Build + execute one kernel and return the parsed record (hex tokens)."""
    binary = build(bench, flags, outdir=bindir, variant=variant)
    proc = subprocess.run([str(binary)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("kernel %s failed rc=%d: %s"
                           % (bench, proc.returncode, proc.stderr[-2000:]))
    if value_out is not None:
        Path(value_out).parent.mkdir(parents=True, exist_ok=True)
        Path(value_out).write_text(proc.stdout)
    return json.loads(proc.stdout)


def emit_digest(bench: str, flags: str = "", variant: str = "base",
                value_out: Optional[Path] = None) -> Dict:
    """Run a kernel and return its digest payload (``--emit-digest``)."""
    from . import ulp

    raw = run_kernel(bench, flags, value_out=value_out, variant=variant)
    rec = ulp.load_record(raw)
    payload = ulp.digest_record(rec)
    payload["meta"] = dict(payload.get("meta") or {})
    payload["meta"]["libc"] = libc_version()
    payload["meta"]["hostname"] = platform.node()
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", default="h1", choices=BENCH_ORDER + ["all"])
    ap.add_argument("--flags", default="", help="extra compiler flags, e.g. '-O3 -ffp-contract=off'")
    ap.add_argument("--variant", default="base", help="label written into the record meta")
    ap.add_argument("--emit-digest", action="store_true", help="print digest JSON (no raw values)")
    ap.add_argument("--values-out", default=None, help="also write the raw hex values JSON here")
    ap.add_argument("--digest-of", default=None,
                    help="compute a digest for an existing raw values JSON instead of running")
    argv = list(sys.argv[1:] if argv is None else argv)
    # allow "--flags -O3" (argparse would otherwise read -O3 as an option)
    fixed = []
    for i, tok in enumerate(argv):
        if fixed and fixed[-1] == "--flags" and tok.startswith("-"):
            fixed[-1] = "--flags=" + tok
        else:
            fixed.append(tok)
    args = ap.parse_args(fixed)

    from . import ulp

    benches = BENCH_ORDER if args.bench == "all" else [args.bench]
    payloads = {}
    if args.digest_of:
        rec = ulp.load_record(json.loads(Path(args.digest_of).read_text()))
        payloads[rec["bench"]] = ulp.digest_record(rec)
    else:
        for bench in benches:
            payloads[bench] = emit_digest(
                bench, args.flags, variant=args.variant,
                value_out=Path(args.values_out) if args.values_out and len(benches) == 1 else None)
    out = payloads[benches[0]] if len(benches) == 1 else payloads
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())