"""跨求解器耦合接口契约 (coupling interface contracts) — 声明、加载、校验.

契约是什么
----------
每个求解器 (LBM/FVM/FEM/BEM/...) 对外的输入输出端口, 用一份 JSON 声明:

    {"name": "force_x", "role": "output", "unit": "kN", "tensor_order": 1,
     "frame": "hull_fixed", "required": true, "semantic": "x 向合力"}

契约层**不含任何领域公式**: 只声明"这个端口交换的是什么量纲/什么坐标系/几阶张量"。
各学科的字段由数据文件注入 (``contracts/coupling/v*/``), 平台核心只提供机制。

校验维度 (每条都能判负并报出字段名)
------------------------------------
1. ``unit`` 可解析, 且量纲与可选 ``declared_dim`` 完全一致;
2. 一阶及以上张量必须声明坐标系, 且坐标系必须已登记;
3. 端口名在契约内唯一, ``role`` 合法, ``version`` 为正整数;
4. 耦合边两端量纲一致; 跨坐标系边必须存在可达帧变换;
5. 可选的 π 群声明必须真无量纲 (Buckingham π, 见 buckingham.py)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .buckingham import ConsistencyReport, check_declared_groups
from .dimension import Dim, DimensionError, format_dim, parse_dim
from .frames import FrameError, FrameGraph
from .units import UnknownUnitError, dim_of_unit

SCHEMA_ID = "coupling-contract/v1"
MAP_SCHEMA_ID = "coupling-map/v1"
ROLES = ("input", "output")
TENSOR_ORDERS = (0, 1, 2)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Port:
    """契约端口: 一个交换字段的声明 (单位/坐标系/张量阶/语义)."""

    name: str
    role: str
    unit: str
    tensor_order: int = 0
    frame: Optional[str] = None
    required: bool = True
    semantic: str = ""
    declared_dim: Optional[str] = None

    @property
    def dim(self) -> Dim:
        return dim_of_unit(self.unit)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "unit": self.unit,
            "tensor_order": self.tensor_order,
            "frame": self.frame,
            "required": self.required,
            "semantic": self.semantic,
            "declared_dim": self.declared_dim,
            "resolved_dim": format_dim(self.dim),
        }


@dataclass(frozen=True)
class Contract:
    """一个求解器的对外耦合契约 (带版本号)."""

    contract_id: str
    version: int
    solver: str
    ports: Tuple[Port, ...]
    discipline: str = ""
    description: str = ""
    pi_groups: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    require_complete_pi: bool = False

    # -- 检索 --
    def port(self, name: str, role: Optional[str] = None) -> Port:
        for p in self.ports:
            if p.name == name and (role is None or p.role == role):
                return p
        raise KeyError(f"{self.contract_id} 无端口 {name!r}" + (f" ({role})" if role else ""))

    def by_role(self, role: str) -> Tuple[Port, ...]:
        return tuple(p for p in self.ports if p.role == role)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_ID,
            "contract_id": self.contract_id,
            "version": self.version,
            "solver": self.solver,
            "discipline": self.discipline,
            "description": self.description,
            "ports": [p.to_dict() for p in self.ports],
            "pi_groups": {k: dict(v) for k, v in self.pi_groups.items()},
            "require_complete_pi": self.require_complete_pi,
        }


@dataclass(frozen=True)
class Edge:
    """耦合边: 上游契约端口 -> 下游契约端口."""

    src_contract: str
    src_port: str
    dst_contract: str
    dst_port: str

    def to_dict(self) -> dict:
        return {
            "from": {"contract": self.src_contract, "port": self.src_port},
            "to": {"contract": self.dst_contract, "port": self.dst_port},
        }


# ---------------------------------------------------------------------------
# 违规记录
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Violation:
    """一条契约违规: 机器可判、字段可定位."""

    code: str
    message: str
    contract_id: str = ""
    port: str = ""
    edge: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "contract_id": self.contract_id,
            "port": self.port,
            "edge": self.edge,
        }

    def location(self) -> str:
        bits = [b for b in (self.contract_id, self.port or self.edge) if b]
        return ".".join(bits) if bits else "<registry>"


@dataclass(frozen=True)
class ValidationReport:
    """一份可落盘的校验报告."""

    violations: Tuple[Violation, ...] = ()
    contracts_checked: int = 0
    edges_checked: int = 0
    pi_reports: Mapping[str, ConsistencyReport] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(sorted({v.code for v in self.violations}))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "contracts_checked": self.contracts_checked,
            "edges_checked": self.edges_checked,
            "violation_count": len(self.violations),
            "codes": list(self.codes),
            "violations": [v.to_dict() for v in self.violations],
            "pi": {k: r.to_dict() for k, r in self.pi_reports.items()},
        }


# ---------------------------------------------------------------------------
# 加载 (JSON -> 数据模型), 结构错误以 Violation 形式返回而不抛异常
# ---------------------------------------------------------------------------
def contract_from_dict(payload: Mapping[str, object]) -> Contract:
    """由 JSON 字典构造契约; 结构性错误抛 :class:`DimensionError` (字段级消息)."""
    if not isinstance(payload, Mapping):
        raise DimensionError("契约必须是 JSON 对象")
    for key in ("contract_id", "version", "solver", "ports"):
        if key not in payload:
            raise DimensionError(f"契约缺少必填键: {key!r}")
    cid = payload["contract_id"]
    if not isinstance(cid, str) or not cid:
        raise DimensionError(f"contract_id 非法: {cid!r}")
    ver = payload["version"]
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        raise DimensionError(f"{cid}: version 必须是 >=1 的整数, 收到 {ver!r}")
    solver = payload["solver"]
    if not isinstance(solver, str) or not solver:
        raise DimensionError(f"{cid}: solver 非法: {solver!r}")
    raw_ports = payload["ports"]
    if not isinstance(raw_ports, Sequence) or isinstance(raw_ports, (str, bytes)):
        raise DimensionError(f"{cid}: ports 必须是数组")
    ports: List[Port] = []
    for item in raw_ports:
        if not isinstance(item, Mapping):
            raise DimensionError(f"{cid}: ports 元素必须是对象, 收到 {item!r}")
        try:
            ports.append(Port(
                name=str(item["name"]),
                role=str(item.get("role", "input")),
                unit=str(item["unit"]),
                tensor_order=int(item.get("tensor_order", 0)),
                frame=(None if item.get("frame") in (None, "", "n_a") else str(item["frame"])),
                required=bool(item.get("required", True)),
                semantic=str(item.get("semantic", "")),
                declared_dim=(None if item.get("declared_dim") in (None, "") else str(item["declared_dim"])),
            ))
        except KeyError as exc:
            raise DimensionError(f"{cid}: 端口缺少必填键 {exc.args[0]!r} in {item!r}") from exc
        except (TypeError, ValueError) as exc:
            raise DimensionError(f"{cid}: 端口字段类型错误 in {item!r}: {exc}") from exc
    pi_groups = payload.get("pi_groups", {}) or {}
    if not isinstance(pi_groups, Mapping):
        raise DimensionError(f"{cid}: pi_groups 必须是对象")
    return Contract(
        contract_id=cid,
        version=ver,
        solver=solver,
        ports=tuple(ports),
        discipline=str(payload.get("discipline", "")),
        description=str(payload.get("description", "")),
        pi_groups={str(k): dict(v) for k, v in pi_groups.items()},
        require_complete_pi=bool(payload.get("require_complete_pi", False)),
    )


def load_contract(path: str | Path) -> Contract:
    """从 JSON 文件加载单个契约."""
    p = Path(path)
    return contract_from_dict(json.loads(p.read_text(encoding="utf-8")))


def load_edges(path: str | Path) -> List[Edge]:
    """从 JSON 文件加载耦合边表."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or "edges" not in payload:
        raise DimensionError(f"边表文件缺少 'edges': {path}")
    edges: List[Edge] = []
    for item in payload["edges"]:
        try:
            edges.append(Edge(
                src_contract=str(item["from"]["contract"]),
                src_port=str(item["from"]["port"]),
                dst_contract=str(item["to"]["contract"]),
                dst_port=str(item["to"]["port"]),
            ))
        except (KeyError, TypeError) as exc:
            raise DimensionError(f"边声明结构错误: {item!r} ({exc})") from exc
    return edges


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_contract(contract: Contract, frames: Optional[FrameGraph] = None) -> Tuple[Violation, ...]:
    """单契约校验 (单位/量纲/坐标系/端口语义/π 群)."""
    out: List[Violation] = []
    cid = contract.contract_id
    seen: Dict[str, int] = {}
    for port in contract.ports:
        seen[port.name] = seen.get(port.name, 0) + 1
        if not port.name:
            out.append(Violation("EMPTY_PORT_NAME", "端口名为空", cid))
            continue
        if port.role not in ROLES:
            out.append(Violation("BAD_ROLE", f"role 必须是 {ROLES} 之一, 收到 {port.role!r}", cid, port.name))
        if port.tensor_order not in TENSOR_ORDERS:
            out.append(Violation(
                "BAD_TENSOR_ORDER",
                f"tensor_order 必须是 {TENSOR_ORDERS}, 收到 {port.tensor_order!r}",
                cid, port.name))
        try:
            resolved = port.dim
        except (UnknownUnitError, DimensionError) as exc:
            out.append(Violation("UNKNOWN_UNIT", f"单位不可解析: {exc}", cid, port.name))
            continue
        if port.declared_dim is not None:
            try:
                declared = parse_dim(port.declared_dim)
            except DimensionError as exc:
                out.append(Violation("BAD_DECLARED_DIM", f"declared_dim 不可解析: {exc}", cid, port.name))
                declared = None
            if declared is not None and declared != resolved:
                out.append(Violation(
                    "DIM_DECLARED_MISMATCH",
                    f"declared_dim={format_dim(declared)} 与单位 {port.unit!r} 推导出的 "
                    f"{format_dim(resolved)} 不一致",
                    cid, port.name))
        if port.tensor_order >= 1:
            if not port.frame:
                out.append(Violation(
                    "MISSING_FRAME",
                    f"tensor_order={port.tensor_order} 的端口必须声明坐标系",
                    cid, port.name))
            elif frames is not None and port.frame not in frames.frames:
                out.append(Violation(
                    "UNKNOWN_FRAME",
                    f"坐标系 {port.frame!r} 未登记 (已登记: {sorted(frames.frames)})",
                    cid, port.name))
        elif port.frame and frames is not None and port.frame not in frames.frames:
            out.append(Violation(
                "UNKNOWN_FRAME",
                f"坐标系 {port.frame!r} 未登记 (已登记: {sorted(frames.frames)})",
                cid, port.name))
    for name, count in seen.items():
        if count > 1:
            out.append(Violation("DUPLICATE_PORT", f"端口名重复 {count} 次", cid, name))
    if contract.pi_groups:
        try:
            rep = check_declared_groups(
                [(p.name, p.dim) for p in contract.ports],
                contract.pi_groups,
                require_complete=contract.require_complete_pi,
            )
        except (DimensionError, UnknownUnitError) as exc:
            out.append(Violation("PI_GROUP_INVALID", f"π 群声明无法评估: {exc}", cid))
        else:
            for chk in rep.declared:
                if not chk.ok:
                    out.append(Violation(
                        "PI_GROUP_NOT_DIMENSIONLESS",
                        f"π 群 {chk.name!r} 组合量纲 {chk.dim and format_dim(chk.dim)} != 1 "
                        f"({chk.reason})",
                        cid, chk.name))
            if rep.redundant:
                out.append(Violation(
                    "PI_GROUPS_REDUNDANT",
                    f"π 群声明线性相关: 声明 {sum(1 for c in rep.declared if c.ok)} 条, "
                    f"独立 {rep.declared_independent} 条",
                    cid))
            if rep.declared_independent > rep.pi.n_groups:
                out.append(Violation(
                    "PI_GROUPS_OVERCOMPLETE",
                    f"独立群 {rep.declared_independent} > n-rank = {rep.pi.n_groups}",
                    cid))
            if contract.require_complete_pi and not rep.complete:
                out.append(Violation(
                    "PI_GROUPS_INCOMPLETE",
                    f"要求完整群基: 声明 {rep.declared_independent} 条 != n-rank = {rep.pi.n_groups}",
                    cid))
    return tuple(out)


def validate_registry(
    contracts: Iterable[Contract],
    edges: Sequence[Edge] = (),
    frames: Optional[FrameGraph] = None,
) -> ValidationReport:
    """整包校验: 单契约 + 边 (量纲一致 / 坐标系可达)."""
    registry: Dict[str, Contract] = {}
    violations: List[Violation] = []
    pi_reports: Dict[str, ConsistencyReport] = {}
    for c in contracts:
        if c.contract_id in registry:
            violations.append(Violation(
                "DUPLICATE_CONTRACT", "契约 id 重复登记", c.contract_id))
            continue
        registry[c.contract_id] = c
        violations.extend(validate_contract(c, frames))
        if c.pi_groups:
            try:
                pi_reports[c.contract_id] = check_declared_groups(
                    [(p.name, p.dim) for p in c.ports], c.pi_groups,
                    require_complete=c.require_complete_pi)
            except (DimensionError, UnknownUnitError):
                pass
    for e in edges:
        label = f"{e.src_contract}.{e.src_port}->{e.dst_contract}.{e.dst_port}"
        for side, cid, port, role in (
            ("from", e.src_contract, e.src_port, "output"),
            ("to", e.dst_contract, e.dst_port, "input"),
        ):
            if cid not in registry:
                violations.append(Violation(
                    "EDGE_UNKNOWN_CONTRACT", f"边 {side} 指向未登记契约", cid, port, label))
                continue
            try:
                registry[cid].port(port, role)
            except KeyError:
                violations.append(Violation(
                    "EDGE_UNKNOWN_PORT",
                    f"边 {side} 端口 {port!r} 不存在或其 role 不是 {role!r}",
                    cid, port, label))
        src = registry.get(e.src_contract)
        dst = registry.get(e.dst_contract)
        if src is None or dst is None:
            continue
        try:
            ps, pd = src.port(e.src_port, "output"), dst.port(e.dst_port, "input")
        except KeyError:
            continue
        try:
            if ps.dim != pd.dim:
                violations.append(Violation(
                    "EDGE_DIM_MISMATCH",
                    f"量纲不匹配: {e.src_contract}.{e.src_port} 为 {format_dim(ps.dim)} "
                    f"({ps.unit}), 而 {e.dst_contract}.{e.dst_port} 为 {format_dim(pd.dim)} "
                    f"({pd.unit})",
                    e.src_contract, e.src_port, label))
        except (UnknownUnitError, DimensionError):
            continue
        if ps.frame and pd.frame and ps.frame != pd.frame and frames is not None:
            try:
                frames.path(ps.frame, pd.frame)
            except FrameError:
                violations.append(Violation(
                    "EDGE_FRAME_UNREACHABLE",
                    f"坐标系 {ps.frame!r} -> {pd.frame!r} 无可达变换",
                    e.src_contract, e.src_port, label))
        if ps.tensor_order != pd.tensor_order:
            violations.append(Violation(
                "EDGE_TENSOR_ORDER_MISMATCH",
                f"张量阶不一致: {ps.tensor_order} vs {pd.tensor_order}",
                e.src_contract, e.src_port, label))
    return ValidationReport(
        violations=tuple(violations),
        contracts_checked=len(registry),
        edges_checked=len(edges),
        pi_reports=pi_reports,
    )


def load_registry(directory: str | Path) -> Tuple[List[Contract], List[Violation]]:
    """加载目录下全部 ``*.json`` 契约; 解析失败转为 ``CONTRACT_PARSE_ERROR`` 违规."""
    contracts: List[Contract] = []
    violations: List[Violation] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            contracts.append(load_contract(path))
        except (DimensionError, UnknownUnitError, json.JSONDecodeError) as exc:
            violations.append(Violation(
                "CONTRACT_PARSE_ERROR", f"{path.name}: {exc}", str(path.name)))
    return contracts, violations


def contract_versions(contracts: Iterable[Contract]) -> Dict[str, List[int]]:
    """按 contract_id 汇总版本号 (版本管理自检用)."""
    out: Dict[str, List[int]] = {}
    for c in contracts:
        out.setdefault(c.contract_id, []).append(c.version)
    return {k: sorted(v) for k, v in out.items()}

def load_registry_layers(*directories: str | Path) -> Tuple[List[Contract], List[Violation]]:
    """按层加载契约目录, 同 id 保留最高版本 (用于 ``v1/`` + ``v2/`` 叠加的真实状态).

    * 同名同版本重复定义 -> ``DUPLICATE_CONTRACT_VERSION`` 违规 (两份事实源);
    * 高版本覆盖低版本时, 不视为冲突 (版本管理语义)。
    """
    chosen: Dict[str, Contract] = {}
    violations: List[Violation] = []
    for directory in directories:
        contracts, errs = load_registry(directory)
        violations.extend(errs)
        for c in contracts:
            prev = chosen.get(c.contract_id)
            if prev is None or c.version > prev.version:
                chosen[c.contract_id] = c
            elif c.version == prev.version:
                violations.append(Violation(
                    "DUPLICATE_CONTRACT_VERSION",
                    f"同一契约同一版本被重复定义 (v{c.version}), 出现于 {directory}",
                    c.contract_id))
    return [chosen[k] for k in sorted(chosen)], violations
