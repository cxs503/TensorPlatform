"""计算调度放置策略引擎（owner 2026-11-13 计算调度令）。

职责边界（刻意做窄）：
  本模块只做**放置决策**（local / cluster / queue）+ 节点选择 + 违例判定；
  不执行任何计算、不写库、不含任何域语义（仓名/算例/物理词条都不进代码）。
  执行面见 ``cli.py``（submit / run-local），账本见 ``meter.py``。

策略数据外置：``config/compute_policy.v1.json``
  - 优先级：rework(返修) > mainline(主线) > self_driven(自驱)
  - 每优先级一份本机 CPU 秒预算（本机帽）+ 重计算阈值
  - 节点权重（同一负载率下权重高的节点优先吃活）
  - 集群不可用时的行为：排队（默认）或降级本机（记违例）

设计要点（为什么这么定）：
  1. 决策是纯函数：同样的请求 + 同样的观测 → 同样的决策，可被测试钉死；
  2. 「重计算」由**估算 CPU 秒**判定而不是「repo 名」判定——零域语义；
  3. 本机帽是**滚动小时预算**而不是并发数，避免长尾单长期占控制节点；
  4. 集群无容量 → 返回 ``queue``（HPC 语义：排队等待），绝不默认偷偷回本机；
     只有策略显式配置 ``on_unavailable=local`` 才降级，且必须记违例。
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable

PRIORITY_ORDER = ("rework", "mainline", "self_driven")  # 索引 0 = 最高优先
FALLBACK_CLASS = "self_driven"

PLACEMENT_LOCAL = "local"
PLACEMENT_CLUSTER = "cluster"
PLACEMENT_QUEUE = "queue"
PLACEMENTS = (PLACEMENT_LOCAL, PLACEMENT_CLUSTER, PLACEMENT_QUEUE)

DEFAULT_POLICY_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "config" / "compute_policy.v1.json"
)

# 违例码（看板「违例数」按 code 汇总；blocking 才会拦门）
V_HEAVY_ON_CONTROL = "heavy_compute_on_control_node"
V_LOCAL_CAP_OVERFLOW = "local_cap_overflow"
V_CLUSTER_DOWN_LOCAL = "cluster_unavailable_local_fallback"
V_LOCAL_SHARE_OVER_TARGET = "local_share_over_target"
V_UNGOVERNED = "ungoverned_compute"          # 无 policy_version 的账本行
V_QUEUE_HOL = "queue_head_of_line_stall"     # 队头阻塞
V_STALE_RUNNING = "stale_running_rows"       # 孤儿 running 行封死准入


class PolicyError(ValueError):
    """策略配置非法（启动即炸，不带着坏策略跑）。"""


def _need(d: dict, key: str, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise PolicyError(f"{where} 缺字段 {key!r}")
    return d[key]


def _num(v: Any, where: str, *, lo: float | None = None, hi: float | None = None) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise PolicyError(f"{where} 不是数值: {v!r}") from None
    if lo is not None and f < lo:
        raise PolicyError(f"{where} 应 >= {lo}: {f}")
    if hi is not None and f > hi:
        raise PolicyError(f"{where} 应 <= {hi}: {f}")
    return f


@dataclass(frozen=True)
class PriorityClass:
    name: str
    rank: int
    local_cpu_seconds_per_hour: float
    heavy_threshold_cpu_seconds: float
    queue_weight: float


@dataclass(frozen=True)
class NodeSpec:
    id: str
    host: str
    cores: int
    weight: float
    max_jobs: int
    load_cap: float


@dataclass
class Policy:
    version: str
    priority_classes: dict[str, PriorityClass]
    local: dict
    cluster: dict
    metering: dict
    raw: dict = field(default_factory=dict)

    # ── 便捷访问 ────────────────────────────────────────────────────
    def cls(self, name: str | None) -> PriorityClass:
        if name in self.priority_classes:
            return self.priority_classes[name]
        return self.priority_classes[FALLBACK_CLASS]

    @property
    def nodes(self) -> list[NodeSpec]:
        return [
            NodeSpec(
                id=n["id"],
                host=n.get("host", ""),
                cores=int(n["cores"]),
                weight=float(n["weight"]),
                max_jobs=int(n["max_jobs"]),
                load_cap=float(n["load_cap"]),
            )
            for n in self.cluster["nodes"]
        ]

    @property
    def local_share_target(self) -> float:
        return float(self.local["local_share_target"])

    @property
    def local_load_cap(self) -> float:
        return float(self.local["load_cap"])

    @property
    def on_unavailable(self) -> str:
        return str(self.cluster["on_unavailable"])

    @property
    def control_node(self) -> str:
        return str(self.local["control_node"])

    @property
    def stale_after_factor(self) -> float:
        return float(self.cluster.get("stale_after_factor", 1.5))

    @property
    def shard_node_share(self) -> int:
        return int(self.cluster["shard_node_share"])


def load_policy(path: str | pathlib.Path | None = None) -> Policy:
    p = pathlib.Path(path) if path is not None else DEFAULT_POLICY_PATH
    try:
        raw = json.loads(pathlib.Path(p).read_text())
    except FileNotFoundError:
        raise PolicyError(f"策略文件不存在: {p}") from None
    except json.JSONDecodeError as e:
        raise PolicyError(f"策略文件不是合法 JSON: {p}: {e}") from None

    version = str(_need(raw, "version", "policy"))
    pcs_raw = _need(raw, "priority_classes", "policy")
    classes: dict[str, PriorityClass] = {}
    for name in PRIORITY_ORDER:
        c = _need(pcs_raw, name, f"priority_classes.{name}")
        where = f"priority_classes.{name}"
        classes[name] = PriorityClass(
            name=name,
            rank=int(_num(_need(c, "rank", where), f"{where}.rank", lo=0)),
            local_cpu_seconds_per_hour=_num(
                _need(c, "local_cpu_seconds_per_hour", where),
                f"{where}.local_cpu_seconds_per_hour", lo=0),
            heavy_threshold_cpu_seconds=_num(
                _need(c, "heavy_threshold_cpu_seconds", where),
                f"{where}.heavy_threshold_cpu_seconds", lo=0),
            queue_weight=_num(_need(c, "queue_weight", where),
                              f"{where}.queue_weight", lo=0, hi=1),
        )
    ranks = [classes[n].rank for n in PRIORITY_ORDER]
    if not all(ranks[i] > ranks[i + 1] for i in range(len(ranks) - 1)):
        raise PolicyError(
            f"优先级序必须是 {'>'.join(PRIORITY_ORDER)}（返修>主线>自驱），实测 rank={ranks}")

    local = _need(raw, "local", "policy")
    _num(_need(local, "local_share_target", "local"), "local.local_share_target",
         lo=0.0, hi=1.0)
    _num(_need(local, "load_cap", "local"), "local.load_cap", lo=0.0, hi=1.0)
    _num(_need(local, "light_cpu_seconds_ceiling", "local"),
         "local.light_cpu_seconds_ceiling", lo=0)

    cluster = _need(raw, "cluster", "policy")
    nodes_raw = _need(cluster, "nodes", "cluster")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise PolicyError("cluster.nodes 必须是非空列表")
    seen: set[str] = set()
    for i, n in enumerate(nodes_raw):
        where = f"cluster.nodes[{i}]"
        nid = str(_need(n, "id", where))
        if nid in seen:
            raise PolicyError(f"cluster.nodes 节点 id 重复: {nid}")
        seen.add(nid)
        _num(_need(n, "cores", where), f"{where}.cores", lo=1)
        _num(_need(n, "weight", where), f"{where}.weight", lo=1e-9)
        _num(_need(n, "max_jobs", where), f"{where}.max_jobs", lo=1)
        _num(_need(n, "load_cap", where), f"{where}.load_cap", lo=0.0, hi=1.0)
    on_unavail = str(_need(cluster, "on_unavailable", "cluster"))
    if on_unavail not in (PLACEMENT_QUEUE, PLACEMENT_LOCAL):
        raise PolicyError(
            f"cluster.on_unavailable 只能是 {PLACEMENT_QUEUE!r} 或 {PLACEMENT_LOCAL!r}: {on_unavail!r}")
    _num(_need(cluster, "shard_node_share", "cluster"), "cluster.shard_node_share", lo=1)

    return Policy(
        version=version,
        priority_classes=classes,
        local=dict(local),
        cluster=dict(cluster),
        metering=dict(raw.get("metering") or {}),
        raw=raw,
    )


# ── 节点选择 ──────────────────────────────────────────────────────────
@dataclass
class Decision:
    placement: str
    priority_class: str
    rank: int
    queue_priority: float
    node: str | None
    est_cpu_seconds: float
    reason: str
    policy_version: str
    violations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "placement": self.placement,
            "priority_class": self.priority_class,
            "rank": self.rank,
            "queue_priority": self.queue_priority,
            "node": self.node,
            "est_cpu_seconds": self.est_cpu_seconds,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "violations": list(self.violations),
        }


def _node_view(policy: Policy, cluster_nodes: Iterable[dict] | None) -> list[dict]:
    """把外部节点观测补齐成统一视图。

    ``cluster_nodes=None`` = 离线静态视图（节点全部视为空闲可用），
    只用于离线决策/单测；真实提交通道必须给实时观测。
    """
    if cluster_nodes is None:
        return [
            {"id": n.id, "ok": True, "loadr": 0.0, "running_jobs": 0,
             "used_cores": 0, "cores": n.cores, "weight": n.weight,
             "max_jobs": n.max_jobs, "load_cap": n.load_cap}
            for n in policy.nodes
        ]
    specs = {n.id: n for n in policy.nodes}
    out = []
    for obs in cluster_nodes:
        spec = specs.get(str(obs.get("id")))
        if spec is None:
            continue  # 未登记的节点不参与放置（防手滑绕开权重配置）
        out.append({
            "id": spec.id,
            "ok": bool(obs.get("ok", True)),
            "loadr": float(obs.get("loadr", 0.0) or 0.0),
            "running_jobs": int(obs.get("running_jobs", 0) or 0),
            "used_cores": int(obs.get("used_cores", 0) or 0),
            "cores": spec.cores,
            "weight": spec.weight,
            "max_jobs": spec.max_jobs,
            "load_cap": spec.load_cap,
        })
    return out


def node_score(node: dict) -> float:
    """越小越优先：负载率 / 权重。权重高=更能吃活=同负载下分数更低。"""
    return float(node["loadr"]) / max(float(node["weight"]), 1e-9)


def pick_node(policy: Policy, cluster_nodes: Iterable[dict] | None, cores: int,
              *, exclude: Iterable[str] = ()) -> tuple[str | None, str]:
    """容量准入 + 权重选点。返回 (node_id|None, 原因)。"""
    excl = set(exclude)
    best, reason = None, "no_capacity"
    best_score = float("inf")
    for n in sorted(_node_view(policy, cluster_nodes), key=lambda x: x["id"]):
        if n["id"] in excl:
            continue
        if not n["ok"]:
            reason = "all_nodes_down"
            continue
        if n["loadr"] >= n["load_cap"]:
            reason = "node_load_cap"
            continue
        if n["running_jobs"] >= n["max_jobs"]:
            reason = "node_job_cap"
            continue
        if n["cores"] - n["used_cores"] < int(cores):
            reason = "node_cores_short"
            continue
        s = node_score(n)
        if s < best_score:
            best, best_score, reason = n["id"], s, "ok"
    return best, reason


def decide(
    policy: Policy,
    request: dict,
    *,
    local_usage_cpu_seconds: float = 0.0,
    local_load_ratio: float = 0.0,
    cluster_nodes: Iterable[dict] | None = None,
    forced_placement: str | None = None,
) -> Decision:
    """放置决策（纯函数）。

    request: {mission_id, repo, est_cpu_seconds, priority, cores, cmd_kind}
    local_usage_cpu_seconds: 该优先级类在滚动 1h 内**已经**烧在本机的 CPU 秒
    local_load_ratio: 控制节点 load/cores（用于负载帽）
    cluster_nodes: 实时节点观测（见 _node_view）
    forced_placement: 仅实验/返修用；强制 local 且属重计算 → 记 blocking 违例
    """
    cls = policy.cls((request or {}).get("priority"))
    est = float((request or {}).get("est_cpu_seconds") or 0.0)
    cores = int((request or {}).get("cores") or 1)
    violations: list[dict] = []

    heavy = est >= cls.heavy_threshold_cpu_seconds
    load_high = float(local_load_ratio) >= policy.local_load_cap
    cap_left = cls.local_cpu_seconds_per_hour - float(local_usage_cpu_seconds)
    cap_exceeded = est > cap_left

    if heavy:
        want, reason = PLACEMENT_CLUSTER, f"est_cpu_seconds={est:g}>=heavy_threshold={cls.heavy_threshold_cpu_seconds:g}"
    elif load_high:
        want, reason = PLACEMENT_CLUSTER, f"control_load={local_load_ratio:.2f}>=cap={policy.local_load_cap:.2f}"
    elif cap_exceeded:
        want, reason = PLACEMENT_CLUSTER, (
            f"local_hour_budget_left={max(cap_left, 0):g}<est={est:g}（class={cls.name}）")
    else:
        want, reason = PLACEMENT_LOCAL, (
            f"light: est={est:g}<heavy={cls.heavy_threshold_cpu_seconds:g} "
            f"且本机小时预算余 {cap_left:g}≥est")

    node: str | None = None
    if want == PLACEMENT_CLUSTER:
        node, node_reason = pick_node(policy, cluster_nodes, cores)
        if node is None:
            if policy.on_unavailable == PLACEMENT_LOCAL:
                want = PLACEMENT_LOCAL
                reason = f"cluster_unavailable({node_reason}) → 策略降级本机"
                violations.append({
                    "code": V_CLUSTER_DOWN_LOCAL, "severity": "warn",
                    "detail": f"集群无容量（{node_reason}）时降级本机，class={cls.name}",
                })
            else:
                want = PLACEMENT_QUEUE
                reason = f"cluster_unavailable({node_reason}) → 排队等待"

    if forced_placement is not None:
        if forced_placement not in PLACEMENTS:
            raise PolicyError(f"forced_placement 非法: {forced_placement!r}")
        if forced_placement != want:
            violations.append({
                "code": V_HEAVY_ON_CONTROL if (heavy and forced_placement == PLACEMENT_LOCAL)
                else V_LOCAL_CAP_OVERFLOW,
                "severity": "blocking" if (heavy and forced_placement == PLACEMENT_LOCAL) else "warn",
                "detail": (f"强制 {forced_placement} 覆盖策略建议 {want}："
                           f"est={est:g} 阈值={cls.heavy_threshold_cpu_seconds:g} class={cls.name}"),
            })
        want = forced_placement
        reason = f"forced:{forced_placement}（策略建议见违例）"
        node = node if want == PLACEMENT_CLUSTER else None

    return Decision(
        placement=want,
        priority_class=cls.name,
        rank=cls.rank,
        queue_priority=cls.queue_weight,
        node=node,
        est_cpu_seconds=est,
        reason=reason,
        policy_version=policy.version,
        violations=violations,
    )


def order_queue(policy: Policy, rows: Iterable[dict]) -> list[dict]:
    """队列派发序（替换 cluster_queue 的纯 FIFO）：优先级 rank 降序，同级 FIFO。

    rows: [{id, priority, submitted_at}]
    输出带 order_index / rank；cluster_queue 侧按此序逐条尝试即可消队头阻塞。
    """
    def key(r: dict) -> tuple:
        cls = policy.cls(r.get("priority"))
        return (-cls.rank, str(r.get("submitted_at") or ""), int(r.get("id") or 0))

    out = []
    for i, r in enumerate(sorted(rows, key=key)):
        cls = policy.cls(r.get("priority"))
        out.append({**r, "order_index": i, "rank": cls.rank, "priority_class": cls.name})
    return out