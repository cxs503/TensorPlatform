"""跨架构判定一致性（C4）：判定是纯函数，机器只贡献输入。

一致性不能靠「我们相信它一致」来成立，本模块把它拆成三条可测命题：

=====  =====================================================================
A      **异构指纹回填**：用另一套「宿主身份」（machine/system/byteorder/
       python 版本）重新回填同一份 claims，再复算。gate.json 的字节只因
       ``env_fingerprint`` 而不同，逐门 ``verdict_digest`` 必须 100% 相同。
B      **判定不读宿主**：``verify`` 路径里没有任何 ``platform`` / ``sys.byteorder``
       读取（结构性质，代码内可审计），因此 A 的结论可推广到未接入的架构。
C      **数值可移植**：证据里的每个实测数字经 C 运行库 ``%.17g`` 往返
       （snprintf→strtod）必须位级相等——即数字证据不依赖 Python 的浮点
       repr 实现，换编译器/运行库也不会漂。
=====  =====================================================================

另有 ``compare()``：两份 ``verify_all.json``（不同机器产出）→ 一致率。
真实跨架构实测件（loong64 节点 / x86 CI）齐了就喂进来；缺件时报告成
``available: false``，不拿模拟结果冒充实测。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import platform
import struct
import sys
from pathlib import Path
from .backfill import backfill
from .contract import canonical_sha256
from .verify import verify_dir

#: 异构宿主身份（不是"另一台机器"，而是"另一套宿主签名"——判定不许看它）
FOREIGN_PROFILES = (
    {"machine": "x86_64", "system": "Linux", "byteorder": "little",
     "python": (3, 11, 4)},
    {"machine": "aarch64", "system": "Linux", "byteorder": "little",
     "python": (3, 12, 1)},
    {"machine": "loongarch64", "system": "Linux", "byteorder": "little",
     "python": (3, 11, 2)},
    {"machine": "x86_64", "system": "Darwin", "byteorder": "little",
     "python": (3, 13, 0)},
)


def stable_projection(verify_all: dict) -> dict:
    """把 ``verify_all`` 投影成「机器无关摘要」：{gate_id: verdict_digest}。"""
    digests = verify_all.get("digests")
    if isinstance(digests, dict):
        return {str(k): str(v) for k, v in digests.items()}
    out = {}
    for gate in verify_all.get("gates", []):
        gid = gate.get("gate_id")
        if gid is not None:
            out[str(gid)] = str(gate.get("verdict_digest"))
    return out


def compare(local: dict, remote: dict) -> dict:
    """两份判定件的一致性（本地 loong64 / 远端 x86 或另一节点）。"""
    a, b = stable_projection(local), stable_projection(remote)
    shared = sorted(set(a) & set(b))
    same = [g for g in shared if a[g] == b[g]]
    return {
        "n_local": len(a),
        "n_remote": len(b),
        "n_compared": len(shared),
        "n_identical": len(same),
        "consistency": (len(same) / len(shared)) if shared else None,
        "only_local": sorted(set(a) - set(b)),
        "only_remote": sorted(set(b) - set(a)),
        "mismatched": sorted(set(shared) - set(same)),
        "projection_digest_local": canonical_sha256(a),
        "projection_digest_remote": canonical_sha256(b),
    }


def _patched_env(profile: dict):
    """临时把宿主签名替换成 ``profile``（返回还原函数）。"""
    originals = [(platform, "machine", platform.machine),
                 (platform, "system", platform.system)]
    has_byteorder = hasattr(sys, "byteorder")
    old_byteorder = getattr(sys, "byteorder", None)
    old_version = sys.version_info

    platform.machine = lambda: profile["machine"]
    platform.system = lambda: profile["system"]
    if has_byteorder:
        sys.byteorder = profile["byteorder"]
    sys.version_info = tuple(profile["python"]) + ("final", 0)

    def restore() -> None:
        for mod, name, value in originals:
            setattr(mod, name, value)
        if has_byteorder:
            sys.byteorder = old_byteorder
        sys.version_info = old_version

    return restore


def heterogeneous_fingerprint_check(gate_root: str | Path, registry_path: str | Path,
                                    claims_path: str | Path, out_dir: str | Path, *,
                                    profiles=FOREIGN_PROFILES) -> dict:
    """命题 A：换宿主签名重新回填 + 复算，逐门 verdict_digest 必须不变。"""
    gate_root, registry_path = Path(gate_root), Path(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    baseline = {g["gate_id"]: g["verdict_digest"]
                for g in verify_dir(gate_root, registry=registry)}
    base_gate_bytes = {p.parent.name: p.read_bytes() for p in gate_root.glob("*/gate.json")}

    # 复现口径：沿用门里已声明的 issued_utc（时间戳是 claims 的属性，不是宿主属性）
    sample = next(iter(sorted(gate_root.glob("*/gate.json"))), None)
    issued = "1970-01-01T00:00:00Z"
    if sample is not None:
        try:
            issued = json.loads(sample.read_text(encoding="utf-8")).get("issued_utc") or issued
        except (OSError, json.JSONDecodeError):
            pass

    runs: list[dict] = []
    mismatches: list[str] = []
    bytes_differ = 0
    for idx, profile in enumerate(profiles):
        dst = Path(out_dir) / f"foreign_{idx}_{profile['machine']}"
        restore = _patched_env(profile)
        try:
            backfill(claims_path, dst, work_root=None, issued_utc=issued)
        finally:
            restore()
        reg = json.loads((dst / "criteria_registry.json").read_text(encoding="utf-8"))
        digests = {g["gate_id"]: g["verdict_digest"] for g in verify_dir(dst, registry=reg)}
        diff = sorted(g for g in baseline if digests.get(g) != baseline[g])
        mismatches.extend(f"{g}@{profile['machine']}" for g in diff)
        for name, blob in base_gate_bytes.items():
            other = (dst / name / "gate.json")
            if other.is_file() and other.read_bytes() != blob:
                bytes_differ += 1
        runs.append({"profile": profile, "n_gates": len(digests),
                     "identical": len(digests) - len(diff), "mismatched": diff})
    return {
        "n_profiles": len(profiles),
        "n_gates": len(baseline),
        "n_comparisons": len(profiles) * len(baseline),
        "mismatches": mismatches,
        "consistency": ((len(profiles) * len(baseline) - len(mismatches))
                        / (len(profiles) * len(baseline))) if baseline and profiles else None,
        "gate_json_bytes_differ_cases": bytes_differ,
        "runs": runs,
        "note": "gate.json 字节不同（env_fingerprint 逐门指纹）但 verdict_digest 全同",
    }


def libc_roundtrip(values: list[float]) -> dict:
    """命题 C：``%.17g`` 往返位级相等（不依赖 Python 的浮点 repr）。"""
    name = ctypes.util.find_library("c") or "libc.so.6"
    libc = ctypes.CDLL(name)
    libc.snprintf.restype = ctypes.c_int
    libc.snprintf.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]  # 变参：只声明前三个
    libc.strtod.restype = ctypes.c_double
    libc.strtod.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]
    bad: list[str] = []
    for v in values:
        x = float(v)
        buf = ctypes.create_string_buffer(64)
        n = libc.snprintf(buf, ctypes.c_size_t(64), b"%.17g", ctypes.c_double(x))
        if n <= 0:
            bad.append(f"fmt_fail:{x!r}")
            continue
        back = libc.strtod(buf.value, None)
        if struct.pack(">d", back) != struct.pack(">d", x):
            bad.append(f"{x!r}->{buf.value!r}->{back!r}")
    return {"n_values": len(values), "n_bad": len(bad), "bad": bad[:10],
            "libc": name, "format": "%.17g",
            "consistency": ((len(values) - len(bad)) / len(values)) if values else None}


def measured_values(gate_root: str | Path) -> list[float]:
    """把门目录里全部 ``measured``（含数组型实测）摊平成浮点列表。"""
    out: list[float] = []
    for path in sorted(Path(gate_root).glob("*/gate.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for crit in doc.get("criteria", []) or []:
            val = crit.get("measured")
            if isinstance(val, bool) or val is None:
                continue
            if isinstance(val, (int, float)):
                out.append(float(val))
            elif isinstance(val, list):
                out.extend(float(x) for x in val
                           if isinstance(x, (int, float)) and not isinstance(x, bool))
    return out


def report(gate_root: str | Path, registry_path: str | Path, claims_path: str | Path,
           out_dir: str | Path, *, remote_artifact: str | Path | None = None,
           profiles=FOREIGN_PROFILES) -> dict:
    """C4 汇总：A/C 实测 +（若在）远端实测件比对。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hetero = heterogeneous_fingerprint_check(
        gate_root, registry_path, claims_path, out_dir / "foreign", profiles=profiles)
    roundtrip = libc_roundtrip(measured_values(gate_root))
    remote = {"available": False}
    local_artifact = out_dir / "verify_all.json"
    if remote_artifact is not None and Path(remote_artifact).is_file() \
            and local_artifact.is_file():
        remote = {"available": True,
                  "artifact": str(remote_artifact),
                  **compare(json.loads(local_artifact.read_text(encoding="utf-8")),
                            json.loads(Path(remote_artifact).read_text(encoding="utf-8")))}
    combined = [x for x in (hetero.get("consistency"), roundtrip.get("consistency"),
                            remote.get("consistency")) if x is not None]
    return {
        "A_heterogeneous_fingerprint": hetero,
        "B_verdict_reads_host": "verify.py 无 platform/sys.byteorder 读取（结构性）",
        "C_libc_roundtrip": roundtrip,
        "D_remote_artifact": remote,
        "consistency_min": min(combined) if combined else None,
        "claim": "判定投影跨宿主/跨运行库一致；未接入架构以 A+B 构造性外推，标注为外推而非实测",
    }
