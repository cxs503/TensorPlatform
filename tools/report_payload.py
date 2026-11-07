#!/usr/bin/env python3
"""报告工装：独占写盘 + 自证（sha256/bytes/mission_id）+ 提交前本地复算。

为什么需要它（历史事故）
------------------------
报告写错单号是**静默**事故：旧工装只做 ``json.dump(open(path, "w"))``，
后果有三层——(1) 路径由调用方自由命名，两个智能体撞同一路径时后者**覆盖**前者，
前者的报告正文凭空消失；(2) 文件名不含 mission_id，事后无法从文件反查归属；
(3) 提交前没人核对「磁盘上的字节 == 我打算提交的字节」，编码/截断/换行替换
都会让落库内容与作者意图分叉。

本模块把这三层都变成**结构性**保证：

* ``write_report_payload(path, mission_id, payload)``
    - ``payload["mission_id"]`` 必须等于入参 ``mission_id``（防错单）；
    - 目标文件名必须**含 mission_id 令牌**（``m5496`` / ``5496``），否则拒写；
    - 写入是**独占**的：目标已存在 → 抛 ``PayloadExists``（默认绝不覆盖）；
    - 原子的：先写同目录临时文件再 ``os.link`` 到目标（link 天然 EEXIST 语义）；
    - 返回**自证**：``{"path", "mission_id", "sha256", "bytes"}``，并且立刻从磁盘
      回读复算 sha256 —— 不一致即抛 ``SelfProofError``（写完就验，不留到提交时）。
* ``preflight(path, mission_id, proof=None)``
    - 提交前本地复算：字节数/sha256/内嵌 mission_id 三项全等才返回载荷，
      否则抛 ``PayloadDrift``（客户端 fail-fast，把 403/422 挡在服务端之前）。
* ``validate_figure_png(name, png_base64)``
    - 直接复用服务端 ``backend/services/report_ownership.py`` 的判据（同一份实现），
      于是「本地绿 / 服务端红」不可能发生。

判据
----
* 覆盖写入被拒（``PayloadExists``）；
* 文件名不含 mission_id 被拒（``PayloadNameNotExclusive``）；
* payload mission_id 与入参不符被拒（``PayloadMissionMismatch``）；
* 自证 sha256 == 回读 sha256；篡改文件后 ``preflight`` 必抛 ``PayloadDrift``。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = [
    "PayloadDrift",
    "PayloadError",
    "PayloadExists",
    "PayloadMissionMismatch",
    "PayloadNameNotExclusive",
    "SelfProofError",
    "open_ownership_module",
    "preflight",
    "validate_figure_png",
    "write_report_payload",
]

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_PATH = ROOT / "backend" / "services" / "report_ownership.py"


# ── 错误族 ──────────────────────────────────────────────────────────────────
class PayloadError(ValueError):
    """工装拒绝写入/复算失败（客户端 fail-fast 的统一错误基类）。"""


class PayloadExists(PayloadError):
    """目标文件已存在——报告文件是独占的，绝不静默覆盖他人成果。"""


class PayloadNameNotExclusive(PayloadError):
    """文件名不含 mission_id 令牌：无法从路径反查归属。"""


class PayloadMissionMismatch(PayloadError):
    """payload 内嵌的 mission_id 与入参不一致（写错单）。"""


class SelfProofError(PayloadError):
    """写完回读复算不一致（磁盘字节 != 内存字节）。"""


class PayloadDrift(PayloadError):
    """提交前复算发现文件被改动/截断（与自证不符）。"""


# ── 复用服务端判据（单一真值，避免本地/服务端两套规则漂移） ──────────────
_OWNERSHIP_MOD: Any = None


def open_ownership_module(path: Path | None = None):
    """按文件路径 import 服务端判据模块（纯 stdlib，无需 DB/psycopg2）。"""
    global _OWNERSHIP_MOD
    if _OWNERSHIP_MOD is not None and path is None:
        return _OWNERSHIP_MOD
    if path is None:
        # 优先走正常 import：类身份与服务端一致（except FigureRejected 才能对上）
        try:
            import sys as _sys

            backend = str(ROOT / "backend")
            if backend not in _sys.path:
                _sys.path.insert(0, backend)
            from services import report_ownership as _mod  # noqa: PLC0415

            _OWNERSHIP_MOD = _mod
            return _mod
        except Exception:  # 无 backend 环境（纯工装容器）→ 退回按路径加载
            pass
    src = Path(path or OWNERSHIP_PATH)
    spec = importlib.util.spec_from_file_location("_report_ownership_shared", src)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御
        raise PayloadError(f"cannot load ownership module at {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if path is None:
        _OWNERSHIP_MOD = mod
    return mod


def validate_figure_png(name: str, png_base64: str) -> bytes:
    """与服务端同一份实现：PNG 八字节魔数 + IEND 段。"""
    return open_ownership_module().validate_png_payload(name, png_base64)


# ── 文件名独占性 ────────────────────────────────────────────────────────────
def mission_token_pattern(mission_id: int) -> re.Pattern[str]:
    """文件名里允许出现的 mission_id 令牌形态：``m5496`` / ``5496``（数字边界）。"""
    return re.compile(rf"(?<!\d)(?:m)?{int(mission_id)}(?!\d)")


def require_exclusive_name(path: str | Path, mission_id: int) -> Path:
    p = Path(path)
    if not mission_token_pattern(mission_id).search(p.name):
        raise PayloadNameNotExclusive(
            f"{p.name}: 文件名必须含 mission_id 令牌 {int(mission_id)}"
            f"（建议 {p.stem}_m{int(mission_id)}{p.suffix}）——"
            f"否则事后无法从文件反查归属，且不同单会撞同一路径互相覆盖"
        )
    return p


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _serialize(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2,
                      sort_keys=False).encode("utf-8")


# ── 主工装 ──────────────────────────────────────────────────────────────────
def write_report_payload(
    path: str | Path,
    mission_id: int,
    payload: dict,
    *,
    replace: bool = False,
    fsync: bool = True,
) -> dict[str, Any]:
    """独占写盘 + 返回自证；任何一项不满足即抛错，绝不写。

    ``replace=True`` 只允许同一个 mission 的**自己的**文件被刷新（仍然要求
    文件名含 mission_id，仍然做写完复算）。
    """
    if not isinstance(payload, dict):
        raise PayloadError("payload 必须是 dict（报告 JSON 体）")
    if "mission_id" not in payload:
        raise PayloadMissionMismatch(
            "payload 缺少 mission_id 字段：报告体必须自带单号，服务端归属门据此对账")
    if int(payload["mission_id"]) != int(mission_id):
        raise PayloadMissionMismatch(
            f"payload.mission_id={payload['mission_id']} 与入参 {int(mission_id)} 不一致"
            "（写错单是静默事故，必须在写盘前拦下）")

    dest = require_exclusive_name(path, mission_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not replace:
        raise PayloadExists(
            f"{dest} 已存在：报告文件独占，拒绝覆盖（如需刷新本单文件请显式 replace=True）")

    raw = _serialize(payload)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp",
                                    dir=str(dest.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        if replace:
            os.replace(tmp, dest)          # 原子刷新本单文件
        else:
            os.link(tmp, dest)             # 原子且独占：目标存在则 FileExistsError
            tmp.unlink()
    except FileExistsError as exc:
        raise PayloadExists(f"{dest} 已存在（并发写者先到）：拒绝覆盖") from exc
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    proof = {
        "path": str(dest),
        "mission_id": int(mission_id),
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    on_disk = dest.read_bytes()
    if sha256_bytes(on_disk) != proof["sha256"] or len(on_disk) != proof["bytes"]:
        raise SelfProofError(
            f"{dest}: 写完回读复算不一致（磁盘 {len(on_disk)}B/"
            f"{sha256_bytes(on_disk)[:12]} != 内存 {proof['bytes']}B/{proof['sha256'][:12]}）")
    proof["verified"] = True
    return proof


def preflight(path: str | Path, mission_id: int,
              proof: dict | None = None) -> dict[str, Any]:
    """提交前本地复算：把服务端可能拒的东西在客户端先拒掉。

    返回 ``{"payload", "proof"}``：proof 为**现场重算**的值（不信任传进来的）。
    """
    p = Path(path)
    if not p.is_file():
        raise PayloadDrift(f"{p} 不存在：提交前复算失败（文件没写成功或写到了别的路径）")
    raw = p.read_bytes()
    live = {"path": str(p), "sha256": sha256_bytes(raw), "bytes": len(raw)}
    if proof is not None:
        for key in ("sha256", "bytes"):
            if key in proof and proof[key] != live[key]:
                raise PayloadDrift(
                    f"{p}: {key} 漂移（自证 {proof[key]} != 现场 {live[key]}）——"
                    "文件被改动/截断，拒绝提交")
        if "mission_id" in proof and int(proof["mission_id"]) != int(mission_id):
            raise PayloadMissionMismatch(
                f"自证 mission_id={proof['mission_id']} 与本次提交 {int(mission_id)} 不一致")
    require_exclusive_name(p, mission_id)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadDrift(f"{p}: 不是合法 UTF-8 JSON（{type(exc).__name__}）") from exc
    if int(payload.get("mission_id", -1)) != int(mission_id):
        raise PayloadMissionMismatch(
            f"{p}: payload.mission_id={payload.get('mission_id')} != {int(mission_id)}")
    live["mission_id"] = int(mission_id)
    live["verified"] = True
    return {"payload": payload, "proof": live}


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 由测试轻覆盖
    import argparse

    ap = argparse.ArgumentParser(description="报告载荷工装：独占写盘 + 自证 + 提交前复算")
    ap.add_argument("--path", required=True)
    ap.add_argument("--mission-id", type=int, required=True)
    ap.add_argument("--payload", required=True, help="来源 JSON 文件（须自带 mission_id）")
    ap.add_argument("--replace", action="store_true")
    a = ap.parse_args(argv)
    payload = json.loads(Path(a.payload).read_text(encoding="utf-8"))
    proof = write_report_payload(a.path, a.mission_id, payload, replace=a.replace)
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())