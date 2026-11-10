#!/usr/bin/env python3
"""写所有权注册中心 v2：flock 为权威 + 记录板为元数据 + 等待图死锁打破。

存储布局（纯文件系统，跨进程共享）
--------------------------------
``locks/<key>.lock``  768 字节记录板（单文件、原地 ``pwrite``，无 rename）：
  * ``[0:512)``   持有者记录 JSON（lease_id / holder / mode / ttl / heartbeat / pid / epoch）
  * ``[512:768)`` 撤销标记 JSON（只有打破者写，与持有者心跳区分离，互不覆盖）
``ledger.jsonl``  append-only 事件台账（等待边 / 死锁 / 撤销 / 回收 / CAS）

为什么这样分层（设计取舍）
--------------------------
1. **权威是 ``flock``，不是 JSON**：互斥由内核保证（同一 ``open file description`` 才是同一
   持有者，因此每个租约每个键各开一个 fd），进程被 SIGKILL 时内核自动释放 → 崩溃回收是
   **瞬时**的，不依赖 TTL。JSON 记录板只是「谁持有」的元数据（advisory），写入撕裂的最坏
   后果是失去身份信息（记 ``record_corrupt``），**不会**破坏 ≤1 持有者不变量。
2. **记录板原地覆写而不是 tmp+rename**：写租约在热路径上（每次写操作一次），rename 流程
   每次约 0.17ms，而 ``pwrite`` 一次约 0.01ms。这是吞吐判据（D3 ≤10%）能否成立的关键。
3. **TTL + 心跳** 覆盖另一类故障：持有者**没死但卡住**（内核锁不会释放）。此时打破者写撤销
   标记，持有者下一次心跳即感知并让出。局限见 README（卡死进程的 flock 不可被偷）。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import random
import threading
import time

from . import model, waitgraph
from .ledger import Ledger

REC_OFF, REC_SIZE = 0, 512
MARK_OFF, MARK_SIZE = 512, 256


class LeaseError(RuntimeError):
    pass


class LeaseTimeout(LeaseError):
    pass


class LeaseRevoked(LeaseError):
    pass


class LostUpdate(LeaseError):
    pass


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def _write_json(path: str, obj) -> None:
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True)
        fh.flush()
    os.replace(tmp, path)


def _pad(blob: bytes, size: int) -> bytes:
    if len(blob) > size:
        raise ValueError(f"record too large: {len(blob)} > {size}")
    return blob + b" " * (size - len(blob))


class Lease:
    """一张已获得的租约：每个资源键持有一个 fd（内核锁跟着 fd 走）。"""

    def __init__(self, registry: "LeaseRegistry", lease_id: str, holder: str,
                 keys: list[str], paths: list[str], mode: str, ttl: float,
                 repo: str = "repo-x"):
        self.registry = registry
        self.repo = repo
        self.lease_id = lease_id
        self.holder = holder
        self.keys = list(keys)
        self.paths = list(paths)
        self.mode = mode
        self.ttl = ttl
        self.fds: dict[str, int] = {}
        self.revoked = False
        self.released = False
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    # ---- 心跳 ----
    def start_heartbeat(self, interval: float = 0.05) -> "Lease":
        interval = max(0.005, float(interval))

        def loop():
            while not self._hb_stop.wait(interval):
                try:
                    self.registry.heartbeat(self.lease_id)
                except LeaseRevoked:
                    self.revoked = True
                    return
                except Exception:
                    return

        self._hb_thread = threading.Thread(target=loop, name=f"hb-{self.lease_id}", daemon=True)
        self._hb_thread.start()
        return self

    def stop_heartbeat(self) -> None:
        self._hb_stop.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=1.0)
            self._hb_thread = None

    def release(self) -> None:
        if self.released:
            return
        self.stop_heartbeat()
        self.registry.release(self.lease_id)
        self.released = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def as_dict(self) -> dict:
        return {"lease_id": self.lease_id, "holder": self.holder, "keys": list(self.keys),
                "paths": list(self.paths), "mode": self.mode, "ttl": self.ttl}


class LeaseRegistry:
    def __init__(self, root, default_ttl: float = 1.0, wait_timeout: float = 5.0,
                 poll: float = 0.0005, ledger: Ledger | None = None, clock=time.time,
                 cross_process: bool = True, ext_scan_interval: float = 0.05):
        self.root = str(root)
        os.makedirs(os.path.join(self.root, "locks"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "commit"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "meta"), exist_ok=True)
        self.default_ttl = float(default_ttl)
        self.wait_timeout = float(wait_timeout)
        self.poll = float(poll)
        self.clock = clock
        self.ledger = ledger or Ledger(os.path.join(self.root, "ledger.jsonl"))
        self.cross_process = bool(cross_process)
        self.ext_scan_interval = float(ext_scan_interval)
        self._edges: dict[tuple[str, str], dict] = {}
        self._edge_lock = threading.Lock()
        self._lease_keys: dict[str, list[str]] = {}
        self._seen_cycles: set[tuple[frozenset, str]] = set()
        self._fds: dict[tuple[str, str], int] = {}
        self._last_ext_scan = 0.0
        self._lock = threading.Lock()
        self.stats = {"acquires": 0, "waits": 0, "deadlocks": 0, "revoked": 0,
                      "reclaimed": 0, "reentrant": 0, "record_corrupt": 0}

    @contextlib.contextmanager
    def _meta_lock(self, key: str):
        """单键元数据临界区（``flock`` 短临界区；进程崩溃内核自动释放）。"""
        path = os.path.join(self.root, "meta", f"{key}.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # ---------------- 记录板 ----------------
    def _lock_file(self, key: str) -> str:
        return os.path.join(self.root, "locks", f"{key}.lock")

    def _open(self, key: str) -> int:
        return os.open(self._lock_file(key), os.O_CREAT | os.O_RDWR, 0o644)

    def _readers_dir(self, key: str) -> str:
        return os.path.join(self.root, "locks", f"{key}.readers")

    def _register_reader(self, key: str, lease_id: str, holder: str, ttl: float, now: float) -> None:
        d = self._readers_dir(key)
        os.makedirs(d, exist_ok=True)
        _write_json(os.path.join(d, f"{lease_id}.json"),
                    {"lease_id": lease_id, "holder": holder, "ttl": float(ttl),
                     "heartbeat_at": now, "created_at": now, "pid": os.getpid()})

    def _readers(self, key: str, now: float | None = None) -> list[dict]:
        d = self._readers_dir(key)
        now = self.clock() if now is None else now
        out = []
        try:
            names = sorted(os.listdir(d))
        except FileNotFoundError:
            return []
        for name in names:
            if not name.endswith(".json"):
                continue
            rec = _read_json(os.path.join(d, name))
            if not rec:
                continue
            if now > rec.get("heartbeat_at", 0.0) + rec.get("ttl", 0.0):
                continue                       # 过期读者：不计入（reap 会清）
            out.append(rec)
        return out

    def _unregister_reader(self, key: str, lease_id: str) -> bool:
        path = os.path.join(self._readers_dir(key), f"{lease_id}.json")
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False

    def _read_record(self, key: str, fd: int | None = None) -> dict | None:
        own = fd is None
        fd = self._open(key) if own else fd
        try:
            blob = os.pread(fd, REC_SIZE, REC_OFF).strip()
        finally:
            if own:
                os.close(fd)
        if not blob:
            return None
        try:
            return json.loads(blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            with self._lock:
                self.stats["record_corrupt"] += 1
            return None

    def _write_record(self, key: str, rec: dict, fd: int | None = None) -> None:
        own = fd is None
        fd = self._open(key) if own else fd
        try:
            os.pwrite(fd, _pad(json.dumps(rec, sort_keys=True).encode("utf-8"), REC_SIZE), REC_OFF)
        finally:
            if own:
                os.close(fd)

    def _read_mark(self, key: str, fd: int | None = None) -> dict | None:
        own = fd is None
        fd = self._open(key) if own else fd
        try:
            blob = os.pread(fd, MARK_SIZE, MARK_OFF).strip()
        finally:
            if own:
                os.close(fd)
        if not blob:
            return None
        try:
            return json.loads(blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_mark(self, key: str, mark: dict) -> None:
        fd = self._open(key)
        try:
            os.pwrite(fd, _pad(json.dumps(mark, sort_keys=True).encode("utf-8"), MARK_SIZE), MARK_OFF)
        finally:
            os.close(fd)

    # ---------------- 兼容性 ----------------
    @staticmethod
    def _record_active(rec: dict | None, now: float) -> bool:
        if not rec or rec.get("released"):
            return False
        return now <= rec.get("heartbeat_at", rec.get("created_at", 0.0)) + rec.get("ttl", 0.0)

    def _try_lock(self, key: str, mode: str) -> int | None:
        fd = self._open(key)
        flag = fcntl.LOCK_EX if mode == model.RW else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, flag | fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
            return None

    # ---------------- 台账 / 等待边 ----------------
    def _edge_set(self, waiter: str, key: str, blocker: str, holder, blocker_holder,
                  wait_timeout: float) -> None:
        """登记等待边。同一 (waiter, key, blocker) 只在**首次**落台账（否则每轮自旋都写一行，
        台账会被等待者灌满、审计价值下降且拖慢热路径）。"""
        now = self.clock()
        with self._edge_lock:
            prev = self._edges.get((waiter, key))
            self._edges[(waiter, key)] = {
                "waiter": waiter, "blocker": blocker, "key": key, "holder": holder,
                "blocker_holder": blocker_holder,
                "since": (prev or {}).get("since", now), "deadline": now + wait_timeout}
            duplicate = bool(prev) and prev.get("blocker") == blocker
        if not duplicate:
            self.ledger.append(model.WAIT_START, waiter=waiter, blocker=blocker, key=key,
                               holder=holder, blocker_holder=blocker_holder,
                               wait_timeout=wait_timeout)

    def _edge_clear(self, waiter: str, key: str, why: str) -> None:
        with self._edge_lock:
            gone = self._edges.pop((waiter, key), None)
        if gone is not None:
            self.ledger.append(model.WAIT_END, waiter=waiter, key=key, why=why,
                               blocker=gone.get("blocker"))

    def clear_edges(self, waiter: str, why: str = "done") -> None:
        with self._edge_lock:
            keys = [k for k in self._edges if k[0] == waiter]
        for _, key in keys:
            self._edge_clear(waiter, key, why)

    def _external_edges(self, now: float) -> list[dict]:
        if not self.cross_process or now - self._last_ext_scan < self.ext_scan_interval:
            return []
        self._last_ext_scan = now
        try:
            with open(self.ledger.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 262144))
                blob = fh.read().decode("utf-8", "ignore")
        except FileNotFoundError:
            return []
        out = []
        for line in blob.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("pid") != os.getpid():
                out.append(ev)
        return out

    def local_graph(self) -> dict[str, list[str]]:
        with self._edge_lock:
            graph: dict[str, set[str]] = {}
            for edge in self._edges.values():
                graph.setdefault(edge["waiter"], set()).add(edge["blocker"])
        return {k: sorted(v) for k, v in graph.items()}

    def graph_snapshot(self) -> dict:
        graph = self.local_graph()
        for ev in self._external_edges(self.clock()):
            if ev.get("kind") == model.WAIT_START:
                graph.setdefault(ev["waiter"], [])
        return {"graph": graph, "digest": model.digest(graph),
                "edges": len(sum(graph.values(), []))}

    # ---------------- 到期 / 撤销 ----------------
    def lease_expiry(self, lease_id: str) -> float:
        keys = self._lease_keys.get(lease_id)
        if keys is None:
            return float("inf")
        best = float("inf")
        for key in keys:
            rec = self._read_record(key)
            if rec and rec.get("lease_id") == lease_id:
                best = min(best, rec.get("heartbeat_at", 0.0) + rec.get("ttl", 0.0))
        return best

    def holds(self, lease_id: str, key: str, fd: int | None = None) -> bool:
        """该租约是否仍是资源键的活跃持有者。

        ``fd`` 是**该键自己的**租约 fd（热路径单次 pread）；调用方给的 fd 若不属于该键
        会被忽略（退回慢路径），避免"拿 A 键的 fd 去证明 B 键"的越权。
        """
        own_fd = self._fds.get((lease_id, key))
        # 只信本注册中心记着的「该键的 fd」；外来 fd 一律不采信（否则可用 A 键的 fd 冒充 B 键）
        fd = own_fd if own_fd is not None else None
        if fd is not None:
            blob = os.pread(fd, REC_SIZE + MARK_SIZE, REC_OFF)
            rec_blob, mark_blob = blob[:REC_SIZE].strip(), blob[REC_SIZE:].strip()
            try:
                rec = json.loads(rec_blob.decode("utf-8")) if rec_blob else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                rec = None
            try:
                mark = json.loads(mark_blob.decode("utf-8")) if mark_blob else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                mark = None
            if not rec or rec.get("lease_id") != lease_id:
                return False
            if mark and mark.get("lease_id") == lease_id:
                return False
            return self._record_active(rec, self.clock())
        return self._holds_slow(lease_id, key)

    def _holds_slow(self, lease_id: str, key: str) -> bool:
        rec = self._read_record(key)
        if not rec or rec.get("lease_id") != lease_id:
            return False
        mark = self._read_mark(key)
        if mark and mark.get("lease_id") == lease_id:
            return False
        return self._record_active(rec, self.clock())

    def _keys_held_by(self, lease_id: str) -> list[str]:
        """该租约当前持有的资源键（内存索引 + 记录板扫描，跨进程也成立）。"""
        keys = list(self._lease_keys.get(lease_id, []))
        try:
            names = os.listdir(os.path.join(self.root, "locks"))
        except FileNotFoundError:
            return keys
        for name in names:
            if not name.endswith(".lock"):
                continue
            key = name[:-5]
            if key in keys:
                continue
            rec = self._read_record(key)
            if rec and rec.get("lease_id") == lease_id and not rec.get("released"):
                keys.append(key)
        return keys

    def is_revoked(self, lease_id: str) -> bool:
        keys = self._lease_keys.get(lease_id, [])
        for key in keys:
            mark = self._read_mark(key)
            if mark and mark.get("lease_id") == lease_id:
                return True
        return False

    def revoke(self, lease_id: str, reason: str, by: str | None = None,
               keys: list[str] | None = None) -> dict:
        """撤销：写撤销标记（持有者下次心跳即感知），并记台账。"""
        ks = list(keys if keys is not None else self._lease_keys.get(lease_id, []))
        now = self.clock()
        for key in ks:
            self._write_mark(key, {"lease_id": lease_id, "at": now, "reason": reason, "by": by})
        with self._lock:
            self.stats["revoked"] += 1
        return self.ledger.append(model.REVOKE, lease_id=lease_id, reason=reason, by=by,
                                  keys=ks, at=now)

    # ---------------- 回收 ----------------
    def reap(self, now: float | None = None) -> list[dict]:
        """两路回收：① 内核锁已释放（进程死）→ 立刻回收；② TTL 到期+心跳缺失 → 撤销。"""
        now = self.clock() if now is None else now
        out = []
        locks_dir = os.path.join(self.root, "locks")
        for name in sorted(os.listdir(locks_dir)):
            if not name.endswith(".lock"):
                continue
            key = name[:-5]
            rec = self._read_record(key)
            if not rec or rec.get("released"):
                continue
            fd = self._open(key)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    free = True
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    free = False
            finally:
                os.close(fd)
            reason = None
            if free:
                reason = "holder_gone"          # 进程已死，内核已释放
                self._write_record(key, {"lease_id": rec.get("lease_id"), "holder": rec.get("holder"),
                                         "mode": rec.get("mode"), "ttl": rec.get("ttl"),
                                         "created_at": rec.get("created_at"),
                                         "heartbeat_at": rec.get("heartbeat_at"),
                                         "released": True, "epoch": int(rec.get("epoch", 0)) + 1})
            elif now > rec.get("heartbeat_at", 0.0) + rec.get("ttl", 0.0):
                reason = "heartbeat_missing"    # 活着但卡住 → 撤销
                self._write_mark(key, {"lease_id": rec.get("lease_id"), "at": now,
                                       "reason": reason, "by": "reaper"})
            if reason:
                ev = self.ledger.append(model.RECLAIM, lease_id=rec.get("lease_id"),
                                        holder=rec.get("holder"), key=key, reason=reason,
                                        age=max(0.0, now - rec.get("heartbeat_at", now)))
                out.append({**rec, "key": key, "reason": reason, "event": ev})
        if out:
            with self._lock:
                self.stats["reclaimed"] += len(out)
        return out

    # ---------------- 获得租约 ----------------
    def acquire(self, paths, holder: str, ttl: float | None = None, mode: str = model.RW,
                repo: str = "repo-x", wait_timeout: float | None = None,
                heartbeat: bool = False, scope: str | None = None) -> Lease:
        ttl = self.default_ttl if ttl is None else float(ttl)
        wait_timeout = self.wait_timeout if wait_timeout is None else float(wait_timeout)
        lease_id = model.new_lease_id()
        keys = [model.resource_key(repo, p) for p in paths]
        self._lease_keys[lease_id] = keys
        lease = Lease(self, lease_id, holder, keys, [model.norm_path(p) for p in paths],
                      mode, ttl, repo=repo)
        try:
            for key in keys:
                fd = self._claim(lease, key, ttl, mode, wait_timeout)
                lease.fds[key] = fd
                self._fds[(lease_id, key)] = fd
                if scope is not None:
                    # 层级授权：拿到文件键后必须让位给活跃的 scope 租约（否则回滚释放）
                    self._await_scope_free(lease, scope, repo, deadlines=[wait_timeout])
        except BaseException:
            for key, fd in lease.fds.items():
                self._unlock(key, fd, lease_id, why="rollback")
                self._fds.pop((lease_id, key), None)
            lease.fds.clear()
            self._lease_keys.pop(lease_id, None)
            raise
        self.ledger.append(model.ACQUIRE, lease_id=lease_id, holder=holder, keys=keys,
                           mode=mode, ttl=ttl, paths=lease.paths, repo=repo)
        with self._lock:
            self.stats["acquires"] += 1
        if heartbeat:
            lease.start_heartbeat(interval=max(0.01, ttl / 4.0))
        return lease

    def extend(self, lease: Lease, paths, ttl: float | None = None) -> Lease:
        """租约扩容：把新路径并入同一租约（已持键 → 自环重入授予，不重复上锁）。

        若扩容过程中租约被撤销（死锁 victim）→ **立即释放**已持的锁再抛错，
        避免调用方忘了释放导致 flock 泄漏到进程退出。
        """
        try:
            return self._extend(lease, paths, ttl)
        except (LeaseRevoked, LeaseTimeout):
            self.release(lease.lease_id, why="extend_rollback")
            raise

    def _extend(self, lease: Lease, paths, ttl: float | None = None) -> Lease:
        for p in paths:
            key = model.resource_key(lease.repo, p)
            fd = self._claim(lease, key, ttl or lease.ttl, lease.mode, self.wait_timeout)
            if key not in lease.fds:
                lease.fds[key] = fd
            if key not in lease.keys:
                lease.keys.append(key)
            self._lease_keys.setdefault(lease.lease_id, [])
            if key not in self._lease_keys[lease.lease_id]:
                self._lease_keys[lease.lease_id].append(key)
            if model.norm_path(p) not in lease.paths:
                lease.paths.append(model.norm_path(p))
        return lease

    def _claim(self, lease: Lease, key: str, ttl: float, mode: str, wait_timeout: float) -> int:
        lease_id = lease.lease_id
        if key in lease.fds:
            # 自环：本租约已持该键却又来请求（重复路径 / 扩容含已持键）
            self._edge_set(lease_id, key, lease_id, lease.holder, lease.holder, 0.05)
            self._record_deadlock(lease_id, [lease_id], lease_id, by=lease_id, reason="self_loop")
            self._edge_clear(lease_id, key, "reentrant_grant")
            with self._lock:
                self.stats["reentrant"] += 1
            return lease.fds[key]
        deadline = self.clock() + wait_timeout
        spins, pause, first = 0, max(1e-5, self.poll / 8.0), True
        since = self.clock()
        last_check = 0.0
        last_probe = 0.0
        while True:
            now = self.clock()
            fd = self._try_lock(key, mode)
            if fd is not None:
                if mode == model.R:
                    # 共享读：SH flock 已经授予；身份登记到 side 文件（不覆盖 rw 记录板）
                    self._register_reader(key, lease_id, lease.holder, ttl, now)
                    self._edge_clear(lease_id, key, "acquired")
                    if not first:
                        self.ledger.append(model.ACQUIRE_KEY, lease_id=lease_id,
                                           holder=lease.holder, key=key, mode=mode, waited=True)
                    return fd
                rec = self._read_record(key, fd)
                mark = self._read_mark(key, fd)
                if rec and mark and mark.get("lease_id") == rec.get("lease_id"):
                    pass                      # 前任被撤销后消失：标记随新租约作废
                if rec and rec.get("lease_id") == lease_id and not rec.get("released"):
                    self._edge_clear(lease_id, key, "reentrant")
                    with self._lock:
                        self.stats["reentrant"] += 1
                    self._record_deadlock(lease_id, [lease_id], lease_id, by=lease_id,
                                          reason="reentrant_self_edge")
                    return fd
                self._write_record(key, {
                    "lease_id": lease_id, "holder": lease.holder, "mode": mode, "ttl": ttl,
                    "created_at": now, "heartbeat_at": now, "pid": os.getpid(),
                    "released": False, "epoch": int((rec or {}).get("epoch", 0)) + 1,
                }, fd)
                self._edge_clear(lease_id, key, "acquired")
                if not first:
                    self.ledger.append(model.ACQUIRE_KEY, lease_id=lease_id, holder=lease.holder,
                                       key=key, mode=mode, waited=True)
                return fd
            # 冲突：读持有者身份，登记等待边
            rec = self._read_record(key)
            if rec is None:
                # 竞争窗口：对方刚拿到锁还没写记录板
                if now > deadline:
                    self._edge_clear(lease_id, key, "timeout")
                    raise LeaseTimeout(f"lease timeout on {key!r} after {wait_timeout:.3f}s")
                time.sleep(self.poll)
                continue
            blocker = rec.get("lease_id")
            if blocker == lease_id:
                # 自环：自己等自己（同一租约对同一键的重复请求）
                self._edge_set(lease_id, key, lease_id, lease.holder, rec.get("holder"),
                               max(0.01, deadline - now))
                self._record_deadlock(lease_id, [lease_id], lease_id, by=lease_id,
                                      reason="self_loop")
                self._edge_clear(lease_id, key, "self_grant")
                with self._lock:
                    self.stats["reentrant"] += 1
                continue
            if first:
                with self._lock:
                    self.stats["waits"] += 1
                first = False
            # 等待边只登记一次（同一 blocker）；阻塞者变了才更新台账
            self._edge_set(lease_id, key, blocker, lease.holder, rec.get("holder"),
                           max(0.01, min(wait_timeout, deadline - now)))
            if now > deadline:
                self._edge_clear(lease_id, key, "timeout")
                raise LeaseTimeout(f"lease timeout on {key!r} after {wait_timeout:.3f}s")
            spins += 1
            waited_ms = (now - since) * 1e3
            # 每轮自旋只做「抢锁 + 读记录板」两次系统调用；撤销检查与死锁检测限流，
            # 否则 40 路自旋会把 CPU 全耗在 open/pread 上（实测吞吐掉 30 倍）。
            if spins % 32 == 0 and self.is_revoked(lease_id):
                self._edge_clear(lease_id, key, "revoked")
                raise LeaseRevoked(f"lease {lease_id} revoked while waiting")
            fresh = rec if (now - last_probe) < 0.0015 else self._read_record(key)
            if fresh is not rec:
                last_probe = now
            mark = self._read_mark(key) if spins % 16 == 0 else None
            if (fresh and fresh.get("lease_id") == blocker and not fresh.get("released")
                    and now > fresh.get("heartbeat_at", 0.0) + fresh.get("ttl", 0.0)
                    and not (mark and mark.get("lease_id") == blocker)):
                # 持有者心跳过期（活着但卡住）→ 撤销并记回收（同一租约只撤一次）
                self.revoke(blocker, reason="heartbeat_missing", by=lease_id, keys=[key])
                self.ledger.append(model.RECLAIM, lease_id=blocker, holder=fresh.get("holder"),
                                   key=key, reason="heartbeat_missing",
                                   age=max(0.0, now - fresh.get("heartbeat_at", now)))
            # 死锁只可能在「我之外还有等待边」时成立；且只在等了 >3ms 才检，且限流 2ms
            if len(self._edges) >= 2 and waited_ms > 3.0 and (now - last_check) > 0.002:
                last_check = now
                self._detect_and_break(lease_id)
            time.sleep(pause * (0.5 + random.random()))
            pause = min(0.0008, pause * 1.2)

    def _await_scope_free(self, lease: Lease, scope: str, repo: str, deadlines: list) -> None:
        """层级租约：文件级租约必须让位给活跃的 scope（worktree/repo 级）租约。"""
        scope_key = model.resource_key(repo, scope)
        if scope_key in lease.fds:
            return
        deadline = self.clock() + float(deadlines[0])
        pause = max(1e-5, self.poll / 4.0)
        while True:
            rec = self._read_record(scope_key)
            now = self.clock()
            if not self._record_active(rec, now) or rec.get("lease_id") == lease.lease_id:
                mark = self._read_mark(scope_key)
                if not (mark and mark.get("lease_id") == (rec or {}).get("lease_id")):
                    return
            self._edge_set(lease.lease_id, scope_key, rec.get("lease_id", "unknown"),
                           lease.holder, rec.get("holder"), max(0.01, deadline - now))
            if now > deadline:
                self._edge_clear(lease.lease_id, scope_key, "timeout")
                raise LeaseTimeout(f"scope {scope!r} held by {rec.get('holder')!r}")
            if len(self._edges) >= 2:
                self._detect_and_break(lease.lease_id)
            time.sleep(pause)
            pause = min(0.002, pause * 1.6)

    def _record_deadlock(self, waiter: str, cycle: list[str], victim: str, by: str,
                         reason: str = "wait_for_cycle") -> None:
        with self._lock:
            self.stats["deadlocks"] += 1
        self.ledger.append(model.DEADLOCK, waiter=waiter, cycle=sorted(cycle),
                           cycle_class=waitgraph.classify(sorted(cycle)), victim=victim, by=by,
                           rule=("reentrant_grant" if waitgraph.is_self_cycle(cycle)
                                 else "earliest_ttl_expiry"), trigger=reason)

    def _detect_and_break(self, waiter: str) -> dict | None:
        """检环 + 打破。打破后**不再重复处理**同一环（避免撤销风暴）。"""
        graph = self.local_graph()
        hit = waitgraph.detect(graph, waiter=waiter, expiry_of=self.lease_expiry)
        if hit is None:
            return None
        cycle, victim = hit["cycle"], hit["resolution"]["victim"]
        breaker = min(cycle)                  # 确定性打破者：环内 lease_id 最小者
        sig = (frozenset(cycle), breaker)
        fresh = sig not in self._seen_cycles
        if fresh:
            self._seen_cycles.add(sig)
            self._record_deadlock(waiter, cycle, victim, by=breaker,
                                  reason=f"cycle_{waitgraph.classify(cycle)}")
        if waitgraph.is_self_cycle(cycle) and victim == waiter:
            return hit
        if breaker != waiter:
            # 我不是打破者：不动手（避免对称双杀把整个环都撤销）
            return hit
        with self._edge_lock:
            ks = [e["key"] for e in self._edges.values() if e["waiter"] == victim]
        # 撤销标记必须写在受害者**真正持有的键**上：受害者只在自持键上检查撤销，
        # 只标它「正在等的键」会让它靠 TTL 到期才发现（实测慢到 ~1×TTL）。
        for key in self._keys_held_by(victim):
            if key not in ks:
                ks.append(key)
        ks = ks or self._lease_keys.get(victim, [])
        if not any((self._read_mark(k) or {}).get("lease_id") == victim for k in ks):
            self.revoke(victim, reason="deadlock_victim", by=waiter, keys=ks or None)
        if victim == waiter:
            # 自己就是 TTL 最早到期者 → 自我牺牲（本地立即让出，不等下一轮心跳）
            raise LeaseRevoked(f"lease {waiter} sacrificed to break a {len(cycle)}-cycle")
        return hit

    # ---------------- 心跳 / 释放 ----------------
    def heartbeat(self, lease_id: str) -> float:
        keys = self._lease_keys.get(lease_id)
        if keys is None:
            raise LeaseRevoked(f"lease {lease_id} revoked or released")
        now = self.clock()
        for key in keys:
            mark = self._read_mark(key)
            if mark and mark.get("lease_id") == lease_id:
                raise LeaseRevoked(f"lease {lease_id} revoked ({mark.get('reason')})")
            reader = os.path.join(self._readers_dir(key), f"{lease_id}.json")
            if os.path.exists(reader):
                ent = _read_json(reader) or {}
                ent["heartbeat_at"] = now
                _write_json(reader, ent)
                continue
            rec = self._read_record(key)
            if rec and rec.get("lease_id") == lease_id:
                rec["heartbeat_at"] = now
                self._write_record(key, rec)
        return now

    def _unlock(self, key: str, fd: int, lease_id: str, why: str = "release") -> int:
        if self._unregister_reader(key, lease_id):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            self._edge_clear(lease_id, key, why)
            self.ledger.append(model.RELEASE, lease_id=lease_id, key=key, why=why, mode=model.R)
            return 1
        rec = self._read_record(key, fd)
        released = 0
        if rec and rec.get("lease_id") == lease_id:
            rec["released"] = True
            rec["heartbeat_at"] = self.clock()
            self._write_record(key, rec, fd)
            released = 1
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        self._edge_clear(lease_id, key, why)
        if released:
            self.ledger.append(model.RELEASE, lease_id=lease_id, key=key, why=why)
        return released

    def release_key(self, lease_id: str, key: str, why: str = "release") -> int:
        """释放单个资源键（多键租约的部分释放）。"""
        keys = self._lease_keys.get(lease_id)
        if keys and key in keys:
            keys.remove(key)
        fd = self._fds.pop((lease_id, key), None)
        return self._unlock(key, fd, lease_id, why=why) if fd is not None else 0

    def release(self, lease_id: str, why: str = "release") -> int:
        keys = self._lease_keys.pop(lease_id, [])
        removed = 0
        for key in keys:
            fd = self._fds.pop((lease_id, key), None)
            if fd is not None:
                removed += self._unlock(key, fd, lease_id, why=why)
        self.clear_edges(lease_id, why=why)
        return removed

    # ---------------- 不变量 ----------------
    def scan_invariant(self) -> dict:
        now = self.clock()
        violations, checked, max_rw = [], 0, 0
        locks_dir = os.path.join(self.root, "locks")
        for name in sorted(os.listdir(locks_dir)):
            if not name.endswith(".lock"):
                continue
            key = name[:-5]
            rec = self._read_record(key)
            if not rec or rec.get("released"):
                continue
            checked += 1
            mark = self._read_mark(key)
            if mark and mark.get("lease_id") == rec.get("lease_id"):
                continue
            if rec.get("mode") == model.RW and self._record_active(rec, now):
                max_rw = max(max_rw, 1)
                # 内核锁是否真在该持有者手里：抢一下探测（拿到说明记录板撒谎）
                fd = self._try_lock(key, model.RW)
                if fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    violations.append({"key": key, "lease_id": rec.get("lease_id"),
                                       "reason": "record_active_but_lock_free"})
        return {"checked_keys": checked, "violations": violations, "max_rw_holders": max_rw,
                "ok": not violations}

    def holder_count(self, key: str) -> int:
        """该键的活跃持有者数（``rw`` 持有者 + 共享读者）。"""
        now = self.clock()
        count = 0
        rec = self._read_record(key)
        if rec and not rec.get("released"):
            mark = self._read_mark(key)
            if not (mark and mark.get("lease_id") == rec.get("lease_id")):
                count += 1 if self._record_active(rec, now) else 0
        return count + len(self._readers(key, now))