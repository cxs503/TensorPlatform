"""计算计量账本：``cpu_seconds × placement`` 入库 + 按单/按节点/按窗口聚合。

一单烧了多少、烧在哪（本机 99 还是集群哪个节点）→ 只认这张账本（``compute_ledger``）。
两个写入面：
  1. **集群侧回填**（``ingest_cluster_jobs``）：读既有 ``cluster_jobs`` 账本，
     ``cpu_seconds = 有效核数 × (finished_at-started_at)``，按 ``source_ref='cj<id>'``
     幂等覆盖（running 行会随后被终态行刷新）。
  2. **本机执行**（``cli.py run-local``）：执行完按进程实测 CPU 秒写入。

存储双实现（同一契约）：
  - ``SqlStore``：PostgreSQL ``compute_ledger``（生产账本，psql 直连，无 ORM 依赖）
  - ``JsonlStore``：JSONL 文件（测试/离线/单机复现，零依赖）
两条路都按 ``source_ref`` 幂等，重复 ingest 不翻倍。
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import subprocess
from typing import Any, Iterable

PLACEMENTS = ("local", "cluster")

RECORD_KEYS = (
    "source_ref", "mission_id", "repo", "cmd_kind", "placement", "node",
    "priority_class", "priority_rank", "cores", "cpu_seconds", "wall_seconds",
    "exit_code", "policy_version", "decision_reason", "started_at", "finished_at",
)

PSQL = ("runuser", "-u", "postgres", "--", "psql")
PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


class MeterError(ValueError):
    """账本记录非法。"""


# ── 记录构造与校验 ────────────────────────────────────────────────────
def make_record(
    *,
    source_ref: str,
    placement: str,
    cpu_seconds: float,
    mission_id: int | None = None,
    repo: str | None = None,
    cmd_kind: str | None = None,
    node: str | None = None,
    priority_class: str | None = None,
    priority_rank: int | None = None,
    cores: int | None = None,
    wall_seconds: float | None = None,
    exit_code: int | None = None,
    policy_version: str | None = None,
    decision_reason: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict:
    if placement not in PLACEMENTS:
        raise MeterError(f"placement 只能是 {PLACEMENTS}: {placement!r}")
    if not source_ref:
        raise MeterError("source_ref 不可为空（幂等键）")
    cpu = float(cpu_seconds)
    if cpu < 0 or cpu != cpu:  # NaN 也拦
        raise MeterError(f"cpu_seconds 非法: {cpu_seconds!r}")
    wall = None if wall_seconds is None else float(wall_seconds)
    if wall is not None and (wall < 0 or wall != wall):
        raise MeterError(f"wall_seconds 非法: {wall_seconds!r}")
    return {
        "source_ref": str(source_ref),
        "mission_id": None if mission_id in (None, "") else int(mission_id),
        "repo": repo, "cmd_kind": cmd_kind,
        "placement": placement,
        "node": node,
        "priority_class": priority_class,
        "priority_rank": None if priority_rank is None else int(priority_rank),
        "cores": None if cores is None else int(cores),
        "cpu_seconds": round(cpu, 3),
        "wall_seconds": None if wall is None else round(wall, 3),
        "exit_code": None if exit_code is None else int(exit_code),
        "policy_version": policy_version,
        "decision_reason": decision_reason,
        # 空串=缺失（cluster_jobs 里有 '' 而不是 NULL 的历史行）
        "started_at": None if started_at in (None, "") else str(started_at),
        "finished_at": None if finished_at in (None, "") else str(finished_at),
    }


def merge_records(old: dict, new: dict) -> dict:
    """幂等覆盖的合并语义：新行为空字段时不抹掉旧的治理字段。

    典型场景：submit 时先写占位行（policy_version / decision_reason / mission_id 齐全、
    cpu_seconds=0），随后 ingest 用 cluster_jobs 实测值刷新同一 source_ref——
    实测行没有 policy_version，若直接覆盖就会把「这单是谁批的、为什么放这」抹掉。
    """
    out = dict(new)
    for k in RECORD_KEYS:
        if k == "source_ref":
            continue
        if out.get(k) in (None, "") and old.get(k) not in (None, ""):
            out[k] = old[k]
    return out


def cpu_seconds_from_job(*, cores: int, wall_seconds: float,
                         shard_nodes: int = 1, shard_node_share: int = 2) -> float:
    """集群作业 CPU 秒 = 有效核数 × 墙钟。

    run 型：有效核数 = 请求 cores（未填按 1）。
    shard 型：作业撒满 ``shard_nodes`` 个节点，每节点记账 ``shard_node_share`` 核。
    （coarse 但可审计：真实核占用率未采样，账本记的是「租赁核·秒」。）
    """
    eff = int(shard_nodes) * int(shard_node_share) if int(shard_nodes) > 1 else max(int(cores or 1), 1)
    return max(float(eff), 1.0) * max(float(wall_seconds), 0.0)


# ── 存储实现 ──────────────────────────────────────────────────────────
class JsonlStore:
    """JSONL 账本（幂等键 source_ref；后写覆盖前写）。"""

    kind = "jsonl"

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, record: dict) -> bool:
        rec = make_record(**{k: record.get(k) for k in RECORD_KEYS})
        rows = {r["source_ref"]: r for r in self.records()}
        is_new = rec["source_ref"] not in rows
        rows[rec["source_ref"]] = merge_records(rows[rec["source_ref"]], rec) if not is_new else rec
        self.path.write_text(
            "".join(json.dumps(rows[k], ensure_ascii=False, sort_keys=True) + "\n"
                    for k in sorted(rows)))
        return is_new

    def records(self, *, days: float | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text().splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
        if days is None:
            return out
        cut = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=float(days))
        keep = []
        for r in out:
            ts = r.get("finished_at") or r.get("started_at")
            keep.append(r) if ts in (None, "") else (_ts(ts) >= cut and keep.append(r))
        return keep


def _sql_lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


class SqlStore:
    """PostgreSQL 账本（表 ``compute_ledger``，schema 见 ``schema.sql``）。"""

    kind = "sql"

    def __init__(self, db: str = "tensorplatform", *, runner=None):
        self.db = db
        self._runner = runner or self._psql

    # -- 底层 --------------------------------------------------------
    def _psql(self, sql: str) -> list[list[str]]:
        r = subprocess.run(list(PSQL) + ["-d", self.db, "-tA", "-F", "\x01", "-R", "\x02",
                                         "-v", "ON_ERROR_STOP=1", "-c", sql],
                           capture_output=True, text=True, timeout=180, env=PGENV)
        if r.returncode != 0:
            raise RuntimeError("psql: " + (r.stderr or "").strip()[:400])
        return [rec.split("\x01") for rec in r.stdout.strip().split("\x02") if rec.strip()]

    def ensure_schema(self) -> None:
        ddl = (pathlib.Path(__file__).parent / "schema.sql").read_text()
        self._psql(ddl)

    # -- 契约 --------------------------------------------------------
    def add(self, record: dict) -> bool:
        rec = make_record(**{k: record.get(k) for k in RECORD_KEYS})
        cols = ", ".join(RECORD_KEYS)
        vals = ", ".join(_sql_lit(rec[k]) for k in RECORD_KEYS)
        upd = ", ".join(
            f"{k}=coalesce(EXCLUDED.{k}, compute_ledger.{k})"
            for k in RECORD_KEYS if k != "source_ref")
        rows = self._psql(
            f"INSERT INTO compute_ledger ({cols}) VALUES ({vals}) "
            f"ON CONFLICT (source_ref) DO UPDATE SET {upd}, ingested_at=now() "
            f"RETURNING (xmax = 0)::int")
        return bool(rows and rows[0][0] == "1")

    def records(self, *, days: float | None = None) -> list[dict]:
        where = ""
        if days is not None:
            where = (f"WHERE coalesce(finished_at, started_at, ingested_at) "
                     f">= now() - interval '{float(days)} days'")
        rows = self._psql(
            f"SELECT {', '.join(RECORD_KEYS)}, ingested_at FROM compute_ledger {where} "
            f"ORDER BY id")
        out = []
        for r in rows:
            rec = dict(zip(RECORD_KEYS + ("ingested_at",), r))
            for k in ("mission_id", "priority_rank", "cores"):
                rec[k] = None if rec.get(k) in (None, "") else int(rec[k])
            for k in ("cpu_seconds", "wall_seconds"):
                rec[k] = None if rec.get(k) in (None, "") else float(rec[k])
            rec["exit_code"] = None if rec.get("exit_code") in (None, "") else int(rec["exit_code"])
            out.append(rec)
        return out

    def count(self) -> int:
        rows = self._psql("SELECT count(*) FROM compute_ledger")
        return int(rows[0][0]) if rows else 0


class UnionStore:
    """多个账本叠加读（写入只落第一个）——用于「历史集群账 + 本次实验账」联合出数。

    例：``UnionStore([SqlStore(), JsonlStore(ab_treatment.jsonl)])``
    读：按 source_ref 去重后合并（同一幂等键以靠前 store 为准）。
    """

    kind = "union"

    def __init__(self, stores: list):
        if not stores:
            raise MeterError("UnionStore 至少要一个 store")
        self.stores = list(stores)

    def ensure_schema(self) -> None:
        for st in self.stores:
            if hasattr(st, "ensure_schema"):
                st.ensure_schema()

    def add(self, record: dict) -> bool:
        return self.stores[0].add(record)

    def records(self, **kw) -> list[dict]:
        seen: dict[str, dict] = {}
        for st in self.stores:
            try:
                rows = st.records(**kw)
            except TypeError:
                rows = st.records()
            for r in rows:
                seen.setdefault(r.get("source_ref"), r)
        return list(seen.values())


# ── 集群账本回填 ──────────────────────────────────────────────────────
CLUSTER_COLS = ("id", "mode", "cores", "shards", "mid", "repo", "state", "node",
                "submitted_at", "started_at", "finished_at", "timeout")


def fetch_cluster_rows(db: str = "tensorplatform", *, days: float | None = None,
                       psql=None) -> list[dict]:
    """读集群调度账本 ``cluster_jobs``（原样返回，供计量与看板共用）。"""
    run = psql or SqlStore(db)._psql
    where = ""
    if days is not None:
        where = f"WHERE submitted_at >= now() - interval '{float(days)} days'"
    rows = run(f"SELECT {', '.join(CLUSTER_COLS)} FROM cluster_jobs {where} ORDER BY id")
    out = []
    for r in rows:
        d = dict(zip(CLUSTER_COLS, r))
        for k in ("id", "cores", "shards", "mid", "timeout"):
            d[k] = None if d.get(k) in (None, "") else int(d[k])
        out.append(d)
    return out


TERMINAL = ("succeeded", "failed", "killed")


WALL_CAP_FACTOR = 1.1        # 未终态作业的墙钟记账上限 = timeout × 1.1
WALL_CAP_GRACE_S = 900.0     # 终态作业的宽限（调度器超时杀单有 600s 余量）


def cluster_row_to_record(row: dict, *, policy=None, shard_nodes: int = 2,
                          shard_node_share: int = 2) -> dict | None:
    """cluster_jobs 行 → 账本记录（无 started_at = 从没跑过，不入账）。

    墙钟**必须封顶**：孤儿 running 行（worker 掉了但 state 仍 running）会把
    ``now-started_at`` 记成几天 CPU 秒——2026-11-13 实测 cj26 因此虚报 1.19e6 秒。
    规则：未终态按 ``started_at + timeout×1.1`` 封顶；终态按 ``timeout+900s`` 封顶。
    """
    if not row.get("started_at"):
        return None
    start = _ts(row["started_at"])
    end = _ts(row["finished_at"]) if row.get("finished_at") else None
    timeout = float(row.get("timeout") or 7200)
    capped = None
    if end is None:
        end = _dt.datetime.now(_dt.timezone.utc)      # running：记到此刻（终态会刷新）
        capped = end
        limit = start + _dt.timedelta(seconds=timeout * WALL_CAP_FACTOR)
        if end > limit:
            end, capped = limit, "wall_capped_by_timeout"
    wall = max((end - start).total_seconds(), 0.0)
    hard = timeout + WALL_CAP_GRACE_S
    if wall > hard:
        wall, capped = hard, "wall_capped_by_timeout"
    is_shard = row.get("mode") == "shard"
    cpu = cpu_seconds_from_job(
        cores=row.get("cores") or 1, wall_seconds=wall,
        shard_nodes=shard_nodes if is_shard else 1,
        shard_node_share=shard_node_share)
    cls_name = None
    if policy is not None:
        cls_name = getattr(policy, "default_priority_name", None)
    return make_record(
        source_ref=f"cj{row['id']}",
        mission_id=row.get("mid"),
        repo=row.get("repo"),
        cmd_kind="shard" if is_shard else "run",
        placement="cluster",
        node=row.get("node") or ("shards" if is_shard else None),
        priority_class=cls_name,
        priority_rank=None,
        cores=(shard_nodes * shard_node_share) if is_shard else (row.get("cores") or 1),
        cpu_seconds=cpu,
        wall_seconds=wall,
        exit_code=0 if row.get("state") == "succeeded" else None,
        policy_version=None,
        decision_reason=("backfill:cluster_jobs" if not capped else
                         f"backfill:cluster_jobs;{capped}"),
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
    )


_TZ_SHORT = re.compile(r"([+-]\d{2})$")


def _ts(v: Any) -> _dt.datetime:
    """解析 psql 输出的时间戳（兼容 ``+00`` 这种无冒号时区偏移）。"""
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)
    s = str(v).strip().replace(" ", "T")
    s = _TZ_SHORT.sub(r"\1:00", s)
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        raise MeterError(f"时间戳无法解析: {v!r}") from None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def ingest_cluster_jobs(store, *, days: float | None = None, db: str = "tensorplatform",
                        shard_nodes: int = 2, shard_node_share: int = 2,
                        rows: list[dict] | None = None) -> dict:
    """把 cluster_jobs 回填进账本（幂等）。返回 {seen, ingested, new, cpu_seconds}。"""
    src = rows if rows is not None else fetch_cluster_rows(db, days=days)
    seen = ingested = new = 0
    total = 0.0
    for row in src:
        seen += 1
        rec = cluster_row_to_record(row, shard_nodes=shard_nodes,
                                   shard_node_share=shard_node_share)
        if rec is None:
            continue
        if store.add(rec):
            new += 1
        ingested += 1
        total += rec["cpu_seconds"]
    return {"seen": seen, "ingested": ingested, "new": new,
            "cpu_seconds": round(total, 1)}


def audit_running(cluster_rows: Iterable[dict], *, now: _dt.datetime | None = None,
                  factor: float = 1.5) -> dict:
    """对账 ``state='running'`` 行是否还活着：age > timeout×factor 视为孤儿。

    为什么需要：``cluster_queue`` 的准入按 DB 里 running 行数算并发帽，
    孤儿行（worker 掉了但没人 reconcile）会把节点永久封死——2026-11-13 实测
    20 条 11-09 起的 shard 行仍标 running，双节点 4/4 并发帽占满 → 580 单排队零派发，
    而节点实际 load 只有 0.32/0.44。计量侧必须能把这类行认出来并扣掉。
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    live, stale = [], []
    for r in cluster_rows:
        if r.get("state") != "running":
            continue
        start = r.get("started_at")
        if not start:
            continue
        age = (now - _ts(start)).total_seconds()
        timeout = float(r.get("timeout") or 7200)
        item = {"id": r.get("id"), "node": r.get("node"), "age_seconds": round(age, 1),
                "timeout": timeout, "cores": r.get("cores")}
        (stale if age > timeout * float(factor) else live).append(item)
    return {"running": len(live) + len(stale), "live": len(live), "stale": stale,
            "live_by_node": _count_nodes(live), "by_node": _count_nodes(live + stale)}


def _count_nodes(items: Iterable[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = str(it.get("node") or "-")
        out[k] = out.get(k, 0) + 1
    return out


# ── 聚合 ──────────────────────────────────────────────────────────────
def _pct(values: list[float], p: float) -> float | None:
    """最近秩百分位（不插值，避免小样本上的假平滑）。"""
    if not values:
        return None
    v = sorted(values)
    import math
    k = max(1, math.ceil(float(p) / 100.0 * len(v))) - 1     # 最近秩（nearest-rank）
    return v[min(k, len(v) - 1)]


def percentile(values: Iterable[float], p: float) -> float | None:
    return _pct([float(x) for x in values], p)


def summarize(records: Iterable[dict]) -> dict:
    """按 placement / node / mission / priority 汇总 cpu_seconds。"""
    recs = list(records)
    by_pl: dict[str, dict] = {p: {"cpu_seconds": 0.0, "jobs": 0} for p in PLACEMENTS}
    by_node: dict[str, dict] = {}
    by_prio: dict[str, dict] = {}
    for r in recs:
        p = r.get("placement")
        if p not in by_pl:
            continue
        cpu = float(r.get("cpu_seconds") or 0.0)
        by_pl[p]["cpu_seconds"] += cpu
        by_pl[p]["jobs"] += 1
        nd = str(r.get("node") or "-")
        by_node.setdefault(nd, {"cpu_seconds": 0.0, "jobs": 0, "placement": p})
        by_node[nd]["cpu_seconds"] += cpu
        by_node[nd]["jobs"] += 1
        pc = str(r.get("priority_class") or "unclassified")
        by_prio.setdefault(pc, {"cpu_seconds": 0.0, "jobs": 0})
        by_prio[pc]["cpu_seconds"] += cpu
        by_prio[pc]["jobs"] += 1
    total = sum(v["cpu_seconds"] for v in by_pl.values())
    for d in (by_pl, by_node, by_prio):
        for v in d.values():
            v["cpu_seconds"] = round(v["cpu_seconds"], 1)
            v["cpu_hours"] = round(v["cpu_seconds"] / 3600.0, 4)
            v["share"] = round(v["cpu_seconds"] / total, 4) if total else 0.0
    return {
        "total_cpu_seconds": round(total, 1),
        "total_cpu_hours": round(total / 3600.0, 4),
        "jobs": len(recs),
        "by_placement": by_pl,
        "local_share": by_pl["local"]["share"],
        "by_node": dict(sorted(by_node.items(), key=lambda kv: -kv[1]["cpu_seconds"])),
        "by_priority": by_prio,
    }


def per_mission(records: Iterable[dict]) -> list[dict]:
    """每单烧了多少、烧在哪（验收①）。"""
    agg: dict[Any, dict] = {}
    for r in records:
        mid = r.get("mission_id")
        key = mid if mid is not None else "unassigned"
        a = agg.setdefault(key, {"mission_id": mid, "cpu_seconds": 0.0, "jobs": 0,
                                 "local_cpu_seconds": 0.0, "cluster_cpu_seconds": 0.0,
                                 "nodes": [], "repos": [], "priorities": []})
        cpu = float(r.get("cpu_seconds") or 0.0)
        a["cpu_seconds"] += cpu
        a["jobs"] += 1
        a["local_cpu_seconds" if r.get("placement") == "local" else "cluster_cpu_seconds"] += cpu
        for k, f in (("nodes", "node"), ("repos", "repo"), ("priorities", "priority_class")):
            v = r.get(f)
            if v and v not in a[k]:
                a[k].append(v)
    out = []
    for a in agg.values():
        a["cpu_seconds"] = round(a["cpu_seconds"], 1)
        a["cpu_hours"] = round(a["cpu_seconds"] / 3600.0, 4)
        a["local_cpu_seconds"] = round(a["local_cpu_seconds"], 1)
        a["cluster_cpu_seconds"] = round(a["cluster_cpu_seconds"], 1)
        a["local_share"] = round(a["local_cpu_seconds"] / a["cpu_seconds"], 4) if a["cpu_seconds"] else 0.0
        out.append(a)
    out.sort(key=lambda x: -x["cpu_seconds"])
    return out


def estimate_cpu_seconds(records: Iterable[dict], *, repo: str | None = None,
                         cmd_kind: str | None = None, default: float = 0.0,
                         use_p90: bool = True) -> tuple[float, str]:
    """从历史账本估这一单会烧多少 CPU 秒（放置决策的输入）。

    优先同 repo 同 cmd_kind；退化到同 repo；再退化到 default。
    返回 (est, basis) —— basis 是可审计的估计依据，禁止静默瞎猜。
    """
    recs = [r for r in records if float(r.get("cpu_seconds") or 0) > 0]
    tiers = [
        ([r for r in recs if repo and r.get("repo") == repo
          and (cmd_kind is None or r.get("cmd_kind") == cmd_kind)],
         f"history:repo={repo},cmd_kind={cmd_kind}"),
        ([r for r in recs if repo and r.get("repo") == repo], f"history:repo={repo}"),
    ]
    for pool, basis in tiers:
        if len(pool) >= 2:
            vals = [float(r["cpu_seconds"]) for r in pool]
            return round(_pct(vals, 90 if use_p90 else 50) or 0.0, 1), f"{basis},n={len(pool)}"
    return float(default), "default:no_history"