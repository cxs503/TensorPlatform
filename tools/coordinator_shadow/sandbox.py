#!/usr/bin/env python3
"""Fail-closed sandbox for the SR1 maturation engine (IN28-N06).

纪律
----
成熟化引擎的一切产物（回填库、报告、图、日志）只能落在**沙箱根目录之内**。任何一次
写到沙箱外的尝试都必须**被拒绝且不留副作用**（fail-closed），并且被记进审计日志——
"没有越界写"这句话要有机器证据，而不是靠自觉。

三层门
------
1. ``resolve()``：``os.path.realpath`` 解析后再做 ``commonpath`` 比较，符号链接、
   ``..`` 穿越、绝对路径越界一律在**打开文件之前**拒绝；
2. ``write_bytes()/write_json()``：只经 ``_atomic_write``（临时文件 + ``os.replace``），
   半途失败不会留下半个文件；
3. ``audit``：每一次尝试都留一条 ``{path, allowed, reason}``，``committed_outside`` 恒为 0
   才算自检通过。

自检（``self_check``）自带负控：三种典型逃逸（``..``、绝对路径、符号链接）必须全部被拒，
否则 `fail_closed=False`——一个"从不拒绝"的门等于没有门。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA = "coordinator-shadow-sandbox/1"


class SandboxViolation(RuntimeError):
    """写到沙箱外（fail-closed：拒绝执行，不留副作用）。"""


def _real(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.fspath(path))


@dataclass
class SandboxGuard:
    """限制写入根集合；每次尝试都审计。

    ``root`` 是引擎私有沙箱；``extra_roots`` 是**显式声明**的第二个写面（本单只用于仓库内的
    ``artifacts/`` 产物目录）。声明之外的任何路径一律 fail-closed。
    """

    root: str
    extra_roots: tuple[str, ...] = ()
    audit: list[dict[str, Any]] = field(default_factory=list)
    _roots_real: tuple[str, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        roots = [_real(self.root)] + [_real(p) for p in self.extra_roots]
        for r in roots:
            os.makedirs(r, exist_ok=True)
        self._roots_real = tuple(dict.fromkeys(roots))

    @property
    def _root_real(self) -> str:
        return self._roots_real[0]

    # -- 判定 -----------------------------------------------------------
    def allows(self, path: str | os.PathLike[str]) -> tuple[bool, str]:
        target = _real(path)
        for root in self._roots_real:
            try:
                if os.path.commonpath([target, root]) == root:
                    return True, f"inside {root}"
            except ValueError:  # 不同盘符/不可比路径
                continue
        return False, f"outside declared write surface {list(self._roots_real)}"

    def resolve(self, path: str | os.PathLike[str]) -> str:
        ok, reason = self.allows(path)
        self.audit.append({"path": os.fspath(path), "allowed": ok, "reason": reason,
                           "op": "resolve"})
        if not ok:
            raise SandboxViolation(f"拒写沙箱外路径：{os.fspath(path)!r}（{reason}）")
        return _real(path)

    # -- 写入 -----------------------------------------------------------
    def _atomic_write(self, path: str | os.PathLike[str], blob: bytes) -> str:
        target = self.resolve(path)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", prefix=".sbx-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self.audit.append({"path": target, "allowed": True, "reason": "committed", "op": "write"})
        return target

    def write_bytes(self, path: str | os.PathLike[str], blob: bytes) -> str:
        return self._atomic_write(path, blob)

    def write_json(self, path: str | os.PathLike[str], obj: Any) -> str:
        blob = (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        return self._atomic_write(path, blob)

    # -- 审计 -----------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        attempted = [a for a in self.audit if a["op"] != "write"]
        rejected = [a for a in attempted if not a["allowed"]]
        committed_outside = [
            a for a in self.audit if a["op"] == "write" and self.allows(a["path"])[0] is False
        ]
        return {
            "schema": SCHEMA,
            "root": self._root_real,
            "roots": list(self._roots_real),
            "attempts": len(attempted),
            "rejected": len(rejected),
            "rejected_paths": [a["path"] for a in rejected][:20],
            "committed_inside": sum(1 for a in self.audit if a["op"] == "write"),
            "committed_outside": len(committed_outside),
            "fail_closed": len(committed_outside) == 0,
        }


def self_check(root: str, extra_roots: Iterable[str] = ()) -> dict[str, Any]:
    """负控自检：三种逃逸必须全部被拒，且沙箱内写成功 1 个文件。"""
    guard = SandboxGuard(root, extra_roots=tuple(extra_roots))
    outside = tempfile.mkdtemp(prefix="in28-outside-")
    escape_cases: list[tuple[str, str]] = [
        ("dotdot", os.path.join(root, "..", "..", "escape_dotdot.json")),
        ("absolute", os.path.join(outside, "escape_absolute.json")),
        ("symlink", os.path.join(root, "escape_link.json")),
    ]
    link = os.path.join(root, "escape_link.json")
    try:  # 造一个指向沙箱外的符号链接（若已有先删掉）
        if os.path.islink(link) or os.path.exists(link):
            os.unlink(link)
        os.symlink(outside, link)
    except OSError:
        escape_cases = [c for c in escape_cases if c[0] != "symlink"]

    results: list[dict[str, Any]] = []
    for name, path in escape_cases:
        try:
            guard.write_json(path, {"should": "never land"})
            results.append({"case": name, "path": path, "blocked": False})
        except SandboxViolation as exc:
            results.append({"case": name, "path": path, "blocked": True, "reason": str(exc)})
    ok_path = guard.write_json(os.path.join(root, "selfcheck_ok.json"),
                              {"schema": SCHEMA, "selfcheck": True})
    try:
        os.unlink(link)
    except OSError:
        pass
    import shutil
    shutil.rmtree(outside, ignore_errors=True)
    summary = guard.summary()
    all_blocked = all(r["blocked"] for r in results)
    return {
        "schema": SCHEMA,
        "root": os.path.realpath(root),
        "roots": list(guard._roots_real),
        "cases": results,
        "escape_cases": len(results),
        "escape_blocked": sum(1 for r in results if r["blocked"]),
        "inside_write": ok_path,
        "outside_committed": summary["committed_outside"],
        "fail_closed": bool(all_blocked and summary["committed_outside"] == 0),
        "production_writes": summary["committed_outside"],
    }


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="fail-closed sandbox self-check")
    ap.add_argument("--root", default="runs/in28_n06/sandbox")
    ap.add_argument("--out", default="artifacts/coordinator_shadow/failclosed_selfcheck.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    report = self_check(args.root)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: report[k] for k in ("escape_blocked", "escape_cases", "fail_closed",
                                             "production_writes")}, ensure_ascii=False))
    print(f"written {args.out}")
    return 0 if report["fail_closed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())