#!/usr/bin/env python3
"""CAS 提交：写入前比对期望前置哈希，不符即拒绝并记 lost-update 事件。

为什么需要 CAS（静默覆写归因）
------------------------------
租约只保证「同一时刻只有一个写者」，但**持有者的读与写之间有时间差**：
进程被 TTL 回收后复活、外部工具直接改文件、或影子模式的旁路写，都会让
「我读到的版本」≠「我写之前的版本」。没有 CAS 时这类覆写是**静默**的：
后写者的内容覆盖先写者，无人报错、事后无痕。CAS 把每次覆写变成一条带
``expected`` / ``actual`` 哈希的台账事件（可归因到人/租约/时刻）。

临界区
------
``commit/<key>.lock`` 的 ``flock`` 覆盖「校验 + 落盘」整段（跨进程有效，
进程被杀内核自动释放），因此 CAS 判定与 ``os.replace`` 之间没有窗口。
"""
from __future__ import annotations

import fcntl
import hashlib
import os

from . import model


def content_sha(data: bytes) -> str:
    """内容摘要（算法由 :func:`tools.write_lease.model.set_hash_algo` 决定）。"""
    return model.content_digest(data)


def read_state(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        return {"exists": True, "data": data, "sha": content_sha(data), "bytes": len(data)}
    except FileNotFoundError:
        return {"exists": False, "data": b"", "sha": content_sha(b""), "bytes": 0}


class CasWriter:
    def __init__(self, registry):
        self.registry = registry
        self._locks: dict[str, int] = {}      # 常驻临界区 fd（每键一个）
        self._dirs: set[str] = set()          # 已确认存在的目录（避免每次 makedirs）

    def _lock_path(self, key: str) -> str:
        d = os.path.join(self.registry.root, "commit")
        if d not in self._dirs:
            os.makedirs(d, exist_ok=True)
            self._dirs.add(d)
        return os.path.join(d, f"{key}.lock")

    def _lock_fd(self, key: str) -> int:
        fd = self._locks.get(key)
        if fd is None:
            fd = os.open(self._lock_path(key), os.O_CREAT | os.O_RDWR, 0o644)
            self._locks[key] = fd
        return fd

    def _atomic_write(self, path: str, data: bytes) -> None:
        d = os.path.dirname(os.path.abspath(path)) or "."
        if d not in self._dirs:
            os.makedirs(d, exist_ok=True)
            self._dirs.add(d)
        tmp = f"{path}.tmp.{os.getpid()}.{abs(hash(data)) & 0xFFFFFF}"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def commit(self, path: str, new_bytes: bytes, expected_sha: str | None,
               lease_id: str | None = None, holder: str | None = None,
               repo: str = "repo-x", enforce_lease: bool = True,
               resource: str | None = None, lease_fd: int | None = None,
               lease_resource: str | None = None) -> dict:
        """校验 + 落盘。``expected_sha=None`` 表示不校验哈希（仅建文件场景）。

        ``path`` 是落盘用的真实路径，``resource`` 是**资源标识**（仓内相对路径，
        与 ``acquire`` 用的是同一把钥匙）；两者分离是为了让键与工作目录绝对路径无关。
        """
        from .registry import LeaseRevoked, LostUpdate

        resource = path if resource is None else resource
        key = model.resource_key(repo, resource)               # 临界区键：文件级
        lease_key = model.resource_key(repo, lease_resource if lease_resource is not None
                                       else resource)          # 授权键：可为 scope 级
        lock_fd = self._lock_fd(key)
        if True:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                if enforce_lease:
                    if lease_id is None:
                        raise ValueError("enforce_lease=True 需要 lease_id")
                    if not self.registry.holds(lease_id, lease_key, fd=lease_fd):
                        self.registry.ledger.append(
                            model.CAS_REJECT, path=resource, repo=repo, lease_id=lease_id,
                            holder=holder, reason="lease_not_holder", expected=expected_sha,
                            actual=None)
                        raise LeaseRevoked(f"lease {lease_id} is not the active holder of {resource!r}")
                if expected_sha is None:
                    # 盲写（不比对前置版本）：不必读旧内容、不必算旧哈希
                    st = {"sha": None, "bytes": os.path.getsize(path) if os.path.exists(path) else 0}
                else:
                    st = read_state(path)
                if expected_sha is not None and st["sha"] != expected_sha:
                    self.registry.ledger.append(
                        model.CAS_REJECT, path=resource, repo=repo, lease_id=lease_id,
                        holder=holder, reason="hash_mismatch", expected=expected_sha,
                        actual=st["sha"], bytes_before=st["bytes"])
                    raise LostUpdate(
                        f"lost update on {resource!r}: expected {expected_sha[:12]} actual {st['sha'][:12]}")
                self._atomic_write(path, new_bytes)
                new_sha = content_sha(new_bytes)   # 内容寻址：一次哈希（CAS 归因必需）
                self.registry.ledger.append(
                    model.CAS_COMMIT, path=resource, repo=repo, lease_id=lease_id, holder=holder,
                    prev_sha=st["sha"], new_sha=new_sha, prev_bytes=st["bytes"],
                    new_bytes=len(new_bytes))
                return {"path": resource, "repo": repo, "key": key, "prev_sha": st["sha"],
                        "sha": new_sha, "bytes": len(new_bytes), "ok": True}
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def plain_write(self, path: str, new_bytes: bytes, repo: str = "repo-x",
                    resource: str | None = None) -> dict:
        """无租约直写（吞吐基线 / 影子模式旁路），仍做原子替换。"""
        self._atomic_write(path, new_bytes)
        sha = content_sha(new_bytes)
        return {"path": path if resource is None else resource, "sha": sha,
                "bytes": len(new_bytes), "ok": True}


def lost_update_events(ledger) -> list[dict]:
    return [e for e in ledger.read() if e.get("kind") == model.CAS_REJECT]


def dump_ledger_stats(ledger) -> dict:
    """台账统计（报告用，全部程序化注入）。"""
    counts: dict[str, int] = {}
    for ev in ledger.read():
        counts[ev.get("kind", "?")] = counts.get(ev.get("kind", "?"), 0) + 1
    return {"events": sum(counts.values()), "by_kind": dict(sorted(counts.items())),
            "digest": model.digest(counts)}