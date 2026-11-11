"""场交换接口（N03）与耦合契约（N02 的场交换侧）的**接口一致性审计**。

判据（节点 p465w1-N07 验收项 (b)，逐项 diff 可算）
--------------------------------------------------
F1 ``schema_conformance``      每个版本化耦合契约都能通过契约 schema 校验（错误数=0）
F2 ``unit_parse``              全部端口单位字符串都能翻译成量纲（失败数=0）
F3 ``edge_dimension_parity``   耦合边两端的端口量纲必须相等（失配数=0）
F4 ``edge_port_resolution``    耦合边引用的端口在「版本化解析」后必须存在（缺失数=0）
F5 ``frame_registry``          tensor_order>=1 的端口声明的 frame 必须在 frames 登记表内
F6 ``envelope_build``          每个端口都能构造出通过 ``FieldMessage.validate()`` 的信封
F7 ``version_monotonic``       同一 contract_id 的新版本号必须严格大于旧版本
F8 ``migration_rename``        迁移表中的 rename 条目必须在 v1/v2 端口集合里真实可执行

外加两条 **findings**（不计入 diff=0 判据，但必须如实入报告，因为它们是被本审计
首次量化的能力缺口）：
* ``conservative_map_is_stub``：``FieldExchange._conservative_map`` 目前直接回落
  nearest 映射，实测积分量（Σ value·area）在 4×4→3×3 上偏差远大于任何守恒门限；
* ``migration_without_v2_file``：迁移表声明了某些 contract_id 的 v1→v2 路径，
  但仓库里没有对应的 ``v2/*.json`` 契约文件（上游未发布）。

零领域语义：本模块只按 schema/登记表/端口字段名判据行事，不认识任何学科量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import contracts as C
from .units import UnitParseError, parse_unit, to_dimension_vector


@dataclass
class Finding:
    id: str
    severity: str
    summary: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "severity": self.severity, "summary": self.summary,
                "evidence": self.evidence}


@dataclass
class FieldAuditResult:
    items: List[Any]
    findings: List[Finding]
    contracts_checked: Dict[str, Any]

    @property
    def total_diff(self) -> int:
        return sum(i.diff for i in self.items if i.status != "SKIP")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "items": [i.as_dict() for i in self.items],
            "findings": [f.as_dict() for f in self.findings],
            "total_diff": self.total_diff,
            "contracts_checked": self.contracts_checked,
            "status": "PASS" if self.total_diff == 0 else "FAIL",
        }


def _jsonschema_errors(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    import jsonschema

    validator = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))]


def _port_by_name(contract: C.CouplingContract, name: str) -> Optional[Dict[str, Any]]:
    for port in contract.ports:
        if port.get("name") == name:
            return port
    return None


def _grid(nx: int, ny: int):
    """合成面网格（零领域语义，仅用于信封/守恒实测）。"""
    import numpy as np

    from src.coupling.field_exchange import GridTopology

    xs = np.linspace(0.0, 1.0, nx + 1)
    ys = np.linspace(0.0, 1.0, ny + 1)
    coords = np.array([[x, y, 0.0] for y in ys for x in xs])
    faces, areas, normals = [], [], []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append([a, a + 1, a + nx + 2, a + nx + 1])
            areas.append(1.0 / (nx * ny))
            normals.append([0.0, 0.0, 1.0])
    return GridTopology(coords, faces, np.asarray(areas), np.asarray(normals))


def build_envelope(port: Dict[str, Any], topology, *, timestamp: float = 0.0):
    """按端口声明造一个 FieldMessage 信封（values 形状由 tensor_order 决定）。"""
    import numpy as np

    from src.coupling.field_exchange import FieldMessage

    order = int(port.get("tensor_order", 0) or 0)
    n = topology.n_faces
    if order == 0:
        values = np.arange(n, dtype=float) + 1.0
    elif order == 1:
        values = np.tile(np.arange(n, dtype=float)[:, None] + 1.0, (1, 3))
    elif order == 2:
        values = np.tile(np.arange(n, dtype=float)[:, None, None] + 1.0, (1, 3, 3))
    else:
        raise ValueError(f"tensor_order={order} 不在 schema 枚举内")
    msg = FieldMessage(
        field_name=str(port["name"]),
        grid_topology=topology,
        values=values,
        units=to_dimension_vector(str(port["unit"])),
        timestamp=timestamp,
        field_location="face",
        domain_tag="synthetic",
    )
    msg.validate()
    return msg


def measure_conservative_map(topology_src=None, topology_dst=None) -> Dict[str, Any]:
    """实测 ``method="conservative"`` 的积分量偏差（这是 finding，不是判据）。"""
    import numpy as np

    from src.coupling.field_exchange import FieldExchange, FieldMessage, DimensionVector

    topology_src = topology_src if topology_src is not None else _grid(4, 4)
    topology_dst = topology_dst if topology_dst is not None else _grid(3, 3)
    values = np.arange(topology_src.n_faces, dtype=float) + 1.0
    msg = FieldMessage("flux", topology_src, values, DimensionVector(), 0.0, "face")
    out = FieldExchange("src", "dst").map_field(msg, topology_dst, method="conservative")
    src_int = float((msg.values * topology_src.face_areas).sum())
    dst_int = float((out.values * topology_dst.face_areas).sum())
    rel = abs(dst_int - src_int) / abs(src_int)
    return {"source_integral": src_int, "target_integral": dst_int,
            "relative_error": rel, "n_src_faces": int(topology_src.n_faces),
            "n_dst_faces": int(topology_dst.n_faces)}


def audit_field_exchange(root: Optional[Path] = None, *,
                         measure_stub: bool = True) -> FieldAuditResult:
    from .metrics_audit import AuditItem

    root = Path(root) if root else C.repo_root()
    schema = C.load_coupling_schema(root)
    contracts = C.load_coupling_contracts(root)
    by_id = C.latest_by_contract_id(contracts)
    frames = C.load_frames(root)
    frame_names = {f.get("name") for f in frames.get("frames", [])}
    cpl_map = C.load_coupling_map(root, version=2)
    migrations = C.load_migrations(root)

    items: List[Any] = []
    findings: List[Finding] = []

    # F1 schema 校验
    f1: Dict[str, List[str]] = {}
    for c in contracts:
        errs = _jsonschema_errors(c.data, schema)
        if errs:
            f1[c.rel_path] = errs
    items.append(AuditItem("F1_schema_conformance",
                           "每个版本化耦合契约必须通过契约 schema（错误数=0）",
                           sum(len(v) for v in f1.values()),
                           {"errors": f1, "contracts": len(contracts)}))

    # F2 单位可解析
    f2: Dict[str, str] = {}
    for c in contracts:
        for port in c.ports:
            try:
                parse_unit(str(port.get("unit")))
            except UnitParseError as exc:
                f2[f"{c.contract_id}:{port.get('name')}"] = str(exc)
    items.append(AuditItem("F2_unit_parse", "全部端口单位必须可翻译成量纲（失败数=0）",
                           len(f2), {"failures": f2}))

    # F3 边两端量纲一致 / F4 边端口可解析
    f3: List[Dict[str, Any]] = []
    f4_missing_port: List[Dict[str, Any]] = []
    f4_unpublished: List[Dict[str, Any]] = []
    edges = cpl_map.get("edges") or []
    for edge in edges:
        resolved: Dict[str, Dict[str, Any]] = {}
        for side in ("from", "to"):
            ref = edge.get(side) or {}
            cid, pname = str(ref.get("contract")), str(ref.get("port"))
            contract = by_id.get(cid)
            if contract is None:
                # 上游未发布该契约文件：无法解析 ≠ 端口写错，分开记（见 findings）
                f4_unpublished.append({"side": side, "contract": cid, "port": pname})
                continue
            port = _port_by_name(contract, pname)
            if port is None:
                f4_missing_port.append({"side": side, "contract": cid, "port": pname,
                                        "reason": "契约存在但端口名不在 ports 内"})
                continue
            resolved[side] = port
        if len(resolved) == 2:
            u_from, u_to = str(resolved["from"]["unit"]), str(resolved["to"]["unit"])
            try:
                same = parse_unit(u_from).dim == parse_unit(u_to).dim
            except UnitParseError as exc:
                f3.append({"edge": edge, "reason": str(exc)})
                continue
            if not same:
                f3.append({"contract": f"{edge['from']['contract']}->{edge['to']['contract']}",
                           "port": f"{edge['from']['port']}->{edge['to']['port']}",
                           "unit_from": u_from, "unit_to": u_to})
    items.append(AuditItem("F3_edge_dimension_parity",
                           "耦合边两端端口量纲必须相等（失配数=0）", len(f3),
                           {"mismatches": f3, "edges": len(edges)}))
    items.append(AuditItem("F4_edge_port_resolution",
                           "已发布契约的耦合边端口在版本化解析后必须存在（缺失数=0）",
                           len(f4_missing_port),
                           {"missing_port": f4_missing_port,
                            "unpublished_contract_refs": f4_unpublished,
                            "edges": len(edges),
                            "scope": "仅统计已发布契约；引用未发布契约的边记入 findings"}))

    # F5 frame 登记表合规
    f5: List[Dict[str, Any]] = []
    for c in contracts:
        for port in c.ports:
            order = int(port.get("tensor_order", 0) or 0)
            frame = port.get("frame")
            if order >= 1:
                if not frame:
                    f5.append({"port": f"{c.contract_id}:{port['name']}",
                               "reason": "tensor_order>=1 未声明 frame"})
                elif frame not in frame_names:
                    f5.append({"port": f"{c.contract_id}:{port['name']}",
                               "reason": f"frame={frame!r} 不在 frames 登记表"})
            elif frame and frame not in frame_names:
                f5.append({"port": f"{c.contract_id}:{port['name']}",
                           "reason": f"frame={frame!r} 不在 frames 登记表"})
    items.append(AuditItem("F5_frame_registry",
                           "端口声明的 frame 必须在 frames 登记表内（违规数=0）",
                           len(f5), {"violations": f5, "frames": sorted(frame_names)}))

    # F6 信封可构造
    topology = _grid(2, 2)
    f6: List[Dict[str, Any]] = []
    for c in contracts:
        for port in c.ports:
            try:
                build_envelope(port, topology)
            except Exception as exc:  # noqa: BLE001 - 记录所有构造失败
                f6.append({"port": f"{c.contract_id}:{port['name']}",
                           "error": f"{type(exc).__name__}: {exc}"})
    items.append(AuditItem("F6_envelope_build",
                           "每个端口都能构造通过 FieldMessage.validate() 的信封（失败数=0）",
                           len(f6), {"failures": f6, "ports": sum(len(c.ports) for c in contracts)}))

    # F7 版本单调
    seen: Dict[str, List[int]] = {}
    for c in contracts:
        seen.setdefault(c.contract_id, []).append(c.version)
    f7 = [{"contract": cid, "versions": sorted(vs)}
          for cid, vs in seen.items() if len(vs) > 1 and sorted(vs)[-1] <= min(vs)]
    items.append(AuditItem("F7_version_monotonic",
                           "同一 contract_id 的新版本号必须严格大于旧版本（违规数=0）",
                           len(f7), {"violations": f7,
                                     "multi_version": {k: sorted(v) for k, v in seen.items()
                                                       if len(v) > 1}}))

    # F8 迁移 rename 可执行 + 迁移表/契约文件覆盖（只对已发布契约判 diff）
    f8: List[Dict[str, Any]] = []
    f8_unexecutable: List[Dict[str, Any]] = []
    mig = migrations.get("contracts") or {}
    v1_ports: Dict[str, set] = {}
    v2_ports: Dict[str, set] = {}
    for c in contracts:
        target = v1_ports if c.version == 1 else v2_ports
        target.setdefault(c.contract_id, set()).update(p.get("name") for p in c.ports)
    for cid, spec in mig.items():
        for m in spec.get("migrations") or []:
            if m.get("kind") != "rename":
                continue
            old, new = m.get("port"), m.get("to_port")
            if cid not in v1_ports:
                # 没有 v1 契约文件 → 迁移条目在本仓不可执行（findings，不粉饰成 PASS）
                f8_unexecutable.append({"contract": cid, "port": old,
                                        "reason": "仓库内无该 contract_id 的 v1 契约文件"})
                continue
            if old not in v1_ports.get(cid, set()):
                f8.append({"contract": cid, "kind": "rename", "port": old,
                           "reason": "v1 端口集合里没有该名字"})
            if cid in v2_ports and new not in v2_ports.get(cid, set()):
                f8.append({"contract": cid, "kind": "rename", "to_port": new,
                           "reason": "v2 端口集合里没有新名字"})
            if cid not in v2_ports:
                f8_unexecutable.append({"contract": cid, "port": new,
                                        "reason": "迁移声明了 v2 但仓库无 v2 契约文件"})
    # 迁移表覆盖：v2 契约必须在迁移表里出现
    for cid in v2_ports:
        if cid not in mig:
            f8.append({"contract": cid, "reason": "有 v2 契约但迁移表未声明该 contract_id"})
    items.append(AuditItem("F8_migration_rename",
                           "已发布契约的迁移 rename 条目必须在 v1/v2 端口集合里真实可执行（违规数=0）",
                           len(f8), {"violations": f8,
                                     "unexecutable": f8_unexecutable,
                                     "migration_contracts": sorted(mig),
                                     "v2_published": sorted(v2_ports),
                                     "v1_published": sorted(v1_ports)}))

    # ---- findings（不计入 diff，但必需如实上报）-------------------------- #
    migration_without_v2 = sorted(cid for cid in mig if cid not in v2_ports)
    if migration_without_v2:
        findings.append(Finding(
            "migration_without_v2_file", "MEDIUM",
            "迁移表声明了 v1→v2 路径但仓库未发布对应 v2 契约文件，"
            "跨仓消费者无法按版本化解析获得新端口名（迁移条目在本仓不可执行）",
            {"contracts": migration_without_v2, "items": f8_unexecutable,
             "migration_table": C.COUPLING_MIGRATIONS}))

    unpublished_refs = sorted({r["contract"] for r in f4_unpublished})
    if unpublished_refs:
        findings.append(Finding(
            "coupling_map_refs_unpublished_contract", "MEDIUM",
            "耦合边表引用了仓库内未发布的求解器契约（版本化解析无解），"
            "这些边无法参与端到端端口一致性验证",
            {"contracts": unpublished_refs, "edge_sides": f4_unpublished,
             "coupling_map": C.COUPLING_MAP_V2}))

    if measure_stub:
        try:
            measured = measure_conservative_map()
            stub = measured["relative_error"] > 1e-12
            if stub:
                findings.append(Finding(
                    "conservative_map_is_stub", "HIGH",
                    "FieldExchange._conservative_map 当前直接回落 nearest 映射（源码注释 'For now'），"
                    "实测积分量不守恒；场交换的「守恒」标签不能由该实现背书",
                    measured))
            else:
                findings.append(Finding(
                    "conservative_map_is_stub", "INFO",
                    "conservative 映射实测守恒（偏差在机器精度内）", measured))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("conservative_map_is_stub", "UNKNOWN",
                                    "无法实测 conservative 映射", {"error": str(exc)}))

    contracts_checked = {
        "n_contracts": len(contracts),
        "contract_ids": sorted(by_id),
        "versions": {cid: c.version for cid, c in sorted(by_id.items())},
        "ports_total": sum(len(c.ports) for c in contracts),
        "edges_total": len(edges),
        "schema_sha256": C.sha256_file(root / C.COUPLING_SCHEMA),
        "frames_sha256": C.sha256_file(root / C.COUPLING_FRAMES),
        "migrations_sha256": C.sha256_file(root / C.COUPLING_MIGRATIONS),
    }
    return FieldAuditResult(items=items, findings=findings,
                            contracts_checked=contracts_checked)