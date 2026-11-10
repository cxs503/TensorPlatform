#!/usr/bin/env python3
"""写入门：把租约 + CAS 装配成「读-改-写」原语，支持 shadow / enforcing 两种模式。

模式
----
* ``enforcing``：必须先持租约才能写；写前 CAS 校验；CAS 不符 → 拒绝写 + 台账事件。
* ``shadow``：租约机制全跑（acquire / 等待图 / 台账），但**不拦写**：
  本会被拦的写记 ``would_block`` 事件后放行，落盘内容与直写基线逐字节相同。
  灰度顺序：shadow 累计 ≥ ``promote_after`` 次真实写 → 才允许 ``promote()`` 到 enforcing。

为什么这样分
------------
治理机制上线最怕「一开就挡业务」。shadow 让机制先被真实流量压过、台账有底，
再切 enforcing；切换门槛是**计数**而不是拍脑袋（见 :func:`should_promote`）。
"""
from __future__ import annotations

import os

from . import model
from .cas import CasWriter, read_state
from .registry import LeaseRevoked, LeaseTimeout, LostUpdate, LeaseRegistry


class WriteLeaseGate:
    def __init__(self, repo_root: str, registry: LeaseRegistry, mode: str = "enforcing",
                 repo: str = "repo-x", ttl: float = 1.0, promote_after: int = 200):
        if mode not in ("shadow", "enforcing"):
            raise ValueError(f"unknown mode {mode!r}")
        self.repo_root = os.path.abspath(repo_root)
        self.registry = registry
        self.mode = mode
        self.repo = repo
        self.ttl = float(ttl)
        self.promote_after = int(promote_after)
        self.writer = CasWriter(registry)
        self.counters = {"writes": 0, "shadow_writes": 0, "would_block": 0,
                         "commits": 0, "cas_rejects": 0, "refused": 0}
        self.events: list[dict] = []

    # ---------------- 路径 ----------------
    def abs_path(self, rel: str) -> str:
        return os.path.join(self.repo_root, model.norm_path(rel))

    def _record(self, kind: str, **fields) -> dict:
        ev = {"kind": kind, "mode": self.mode, "t": model.now_ts(), **fields}
        self.events.append(ev)
        return ev

    # ---------------- 写原语 ----------------
    def write(self, rel: str, data: bytes, holder: str, expected_sha: str | None = None,
              ttl: float | None = None) -> dict:
        """一次写入。enforcing 下必须持租约；shadow 下失败不拦。"""
        ttl = self.ttl if ttl is None else float(ttl)
        path = self.abs_path(rel)
        if self.mode == "shadow":
            return self._shadow_write(rel, path, data, holder, expected_sha, ttl)
        lease = self.registry.acquire([rel], holder=holder, ttl=ttl, mode=model.RW, repo=self.repo)
        try:
            out = self.writer.commit(path, data, expected_sha, lease_id=lease.lease_id,
                                     holder=holder, repo=self.repo, enforce_lease=True,
                                     resource=rel, lease_fd=lease.fds.get(lease.keys[0]))
            self.counters["commits"] += 1
            self.counters["writes"] += 1
            return {**out, "lease_id": lease.lease_id, "mode": self.mode}
        finally:
            lease.release()

    def read_modify_write(self, rel: str, mutate, holder: str, ttl: float | None = None,
                          expected_sha: str | None = None, retries: int = 0) -> dict:
        """持租约的读-改-写：读 → 变换 → CAS 写。``mutate(bytes) -> bytes``。"""
        ttl = self.ttl if ttl is None else float(ttl)
        path = self.abs_path(rel)
        attempt = 0
        while True:
            lease = self.registry.acquire([rel], holder=holder, ttl=ttl, mode=model.RW, repo=self.repo)
            try:
                st = read_state(path)
                exp = st["sha"] if expected_sha is None else expected_sha
                new_bytes = mutate(st["data"])
                out = self.writer.commit(path, new_bytes, exp, lease_id=lease.lease_id,
                                         holder=holder, repo=self.repo, enforce_lease=True,
                                         resource=rel, lease_fd=lease.fds.get(lease.keys[0]))
                self.counters["commits"] += 1
                self.counters["writes"] += 1
                return {**out, "lease_id": lease.lease_id, "prev_sha": st["sha"],
                        "mode": self.mode}
            except LostUpdate:
                self.counters["cas_rejects"] += 1
                if attempt >= retries:
                    raise
                attempt += 1
            finally:
                lease.release()

    def increment_counter(self, rel: str, holder: str, ttl: float | None = None) -> dict:
        """读-改-写一个十进制计数器（D1 线性化探针）。"""
        def bump(data: bytes) -> bytes:
            try:
                val = int(data.decode("utf-8").strip() or "0")
            except ValueError:
                val = 0
            return str(val + 1).encode("utf-8")

        return self.read_modify_write(rel, bump, holder=holder, ttl=ttl)

    def _shadow_write(self, rel: str, path: str, data: bytes, holder: str,
                      expected_sha: str | None, ttl: float) -> dict:
        lease = None
        blocked = None
        try:
            lease = self.registry.acquire([rel], holder=holder, ttl=ttl, mode=model.RW,
                                          repo=self.repo, wait_timeout=max(0.05, ttl))
        except (LeaseTimeout, LeaseRevoked) as exc:
            blocked = type(exc).__name__
            self.counters["would_block"] += 1
            self.registry.ledger.append(model.WOULD_BLOCK, path=rel, holder=holder,
                                        reason=blocked, mode="shadow")
            self._record("would_block", path=rel, holder=holder, reason=blocked)
        try:
            st = read_state(path)
            if expected_sha is not None and st["sha"] != expected_sha:
                # 影子模式只归因不拦：记 lost-update 面，但仍按直写语义落盘
                self.counters["cas_rejects"] += 1
                self.registry.ledger.append(model.CAS_REJECT, path=rel, repo=self.repo,
                                            lease_id=(lease.lease_id if lease else None),
                                            holder=holder, reason="shadow_hash_mismatch",
                                            expected=expected_sha, actual=st["sha"])
            out = self.writer.plain_write(path, data, repo=self.repo, resource=rel)
            self.counters["shadow_writes"] += 1
            self.counters["writes"] += 1
            return {**out, "mode": self.mode, "would_block": blocked,
                    "lease_id": lease.lease_id if lease else None}
        finally:
            if lease is not None:
                lease.release()

        # ---------------- 会话（worktree/repo 级）租约 ----------------
    def session(self, holder: str, scope: str = ".", ttl: float | None = None,
                heartbeat: bool = True) -> "WriteSession":
        """开一个**worktree/repo 级**写会话：租约拿一次，之后每次写只付 CAS + 台账。

        这是治理的正常粒度（平台的一次任务=一个仓/工作树的一段连续写），
        也是吞吐判据 D3 判定的对象；per-write 租约（:meth:`write`）是更强但更贵的粒度，
        两者在 ``WriteSession.write`` 中共享同一 CAS 与台账语义。
        """
        ttl = self.ttl if ttl is None else float(ttl)
        lease = self.registry.acquire([scope], holder=holder, ttl=ttl, mode=model.RW,
                                      repo=self.repo, heartbeat=heartbeat,
                                      wait_timeout=max(2.0, ttl * 4))
        return WriteSession(self, lease, holder, scope)

    # ---------------- 灰度 ----------------
    def should_promote(self) -> bool:
        return self.mode == "shadow" and self.counters["shadow_writes"] >= self.promote_after

    def promote(self) -> dict:
        """shadow → enforcing（门槛：真实写次数达标）。"""
        allowed = self.should_promote()
        before = self.mode
        if allowed:
            self.mode = "enforcing"
        return {"from": before, "to": self.mode, "allowed": allowed,
                "shadow_writes": self.counters["shadow_writes"],
                "promote_after": self.promote_after}

    def stats(self) -> dict:
        return dict(self.counters)


def open_gate(workdir: str, repo: str = "repo-x", mode: str = "enforcing",
              ttl: float = 1.0, promote_after: int = 200) -> WriteLeaseGate:
    """便利入口：在 ``workdir`` 下建 ``.lease/`` 状态目录 + 仓库目录。"""
    root = os.path.join(workdir, ".lease")
    os.makedirs(root, exist_ok=True)
    repo_root = os.path.join(workdir, "wt", repo)
    os.makedirs(repo_root, exist_ok=True)
    reg = LeaseRegistry(root, default_ttl=ttl, wait_timeout=max(2.0, ttl * 4))
    return WriteLeaseGate(repo_root, reg, mode=mode, repo=repo, ttl=ttl,
                          promote_after=promote_after)

class WriteSession:
    """持有一张 scope 级租约的写会话（写前只校验 scope 授权 + CAS）。"""

    def __init__(self, gate: WriteLeaseGate, lease, holder: str, scope: str):
        self.gate = gate
        self.lease = lease
        self.holder = holder
        self.scope = scope
        self.closed = False

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    def write(self, rel: str, data: bytes, expected_sha: str | None = None) -> dict:
        path = self.gate.abs_path(rel)
        out = self.gate.writer.commit(
            path, data, expected_sha, lease_id=self.lease.lease_id, holder=self.holder,
            repo=self.gate.repo, enforce_lease=True, resource=rel,
            lease_fd=self.lease.fds.get(self.lease.keys[0]), lease_resource=self.scope)
        self.gate.counters["commits"] += 1
        self.gate.counters["writes"] += 1
        return {**out, "lease_id": self.lease.lease_id, "scope": self.scope}

    def increment_counter(self, rel: str) -> dict:
        from .cas import read_state
        path = self.gate.abs_path(rel)
        st = read_state(path)
        try:
            val = int(st["data"].decode("utf-8").strip() or "0")
        except ValueError:
            val = 0
        return self.write(rel, str(val + 1).encode("utf-8"), expected_sha=st["sha"])

    def close(self) -> None:
        if not self.closed:
            self.lease.release()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
