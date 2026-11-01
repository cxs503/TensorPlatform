"""W8-N01~N07 跨模块接口契约登记表与校验器 (interface contract registry).

设计约束
--------
1. 契约是**声明式**的: 端口 (Port) 带单位、坐标系、语义说明; 校验器只读声明,
   不依赖任何模块内部实现 —— 因此可以在上游分支未合并时先行验收契约。
2. 校验必须**能判负**: 故意注入单位错配 / 坐标系错配 / 未声明终端输出 /
   反馈环未延时, 必须稳定报出对应 code (见 tests/test_ice_integration_gate.py)。
3. 反馈环 (冰载荷 <-> 液晃) 合法, 但必须至少有一条边声明 delayed=True
   (显式一步滞后), 否则视为代数环 -> 违约。这是数据流贯通测试的前提。
4. 每条输出端口必须"有去向": 被下游边消费, 或显式声明 terminal=True 并
   给出 consumer_hint (报告/API/图). 未声明的悬空输出 -> 违约。

坐标系约定 (ship 总体性能惯例, z-up 右手系)
-------------------------------------------
ship_fixed_zup : x 艏, y 右舷(starboard), z 向上; 横摇绕 +x 右手正向.
                 本门统一取"正值 = 右舷下沉" (starboard-down positive),
                 符号一致性由 tests 的 sign_check 机器验证。
earth_zup      : 大地系, x 正北, y 正东, z 向上.
tank_local_zup : 液舱局部系, 原点在舱底几何中心, x 沿舱长, z 向上.
n_a            : 不适用 (标量量: 温度/质量/比值).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .dimensions import DimensionError, Dim, dim_of, format_dim

FRAMES: Dict[str, dict] = {
    "ship_fixed_zup": {"handed": "right", "z": "up", "note": "x 艏, y 右舷, roll 绕 +x, 正值右舷下沉"},
    "earth_zup": {"handed": "right", "z": "up", "note": "大地系"},
    "tank_local_zup": {"handed": "right", "z": "up", "note": "液舱局部系"},
    "n_a": {"handed": "n_a", "z": "n_a", "note": "标量量, 无坐标系"},
}

#: 跨坐标系边必须登记的变换 (3x3 旋转, 必须正交且 det=+1)
FRAME_TRANSFORMS: Dict[str, Tuple[Tuple[float, ...], ...]] = {
    "identity": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "earth_to_ship_roll": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "tank_to_ship_translation": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
}


@dataclass(frozen=True)
class Port:
    name: str
    unit: str
    frame: str = "n_a"
    desc: str = ""
    external: bool = False   # 外部输入 (环境/工况/传感器), 不需上游边
    terminal: bool = False   # 终端输出 (报告/API/图), 无需下游边
    consumer_hint: str = ""

    @property
    def dim(self) -> Dim:
        return dim_of(self.unit)


@dataclass(frozen=True)
class NodeContract:
    node: str
    title: str
    version: str
    inputs: Tuple[Port, ...]
    outputs: Tuple[Port, ...]
    source_hint: str = ""  # 上游仓库/模块提示 (用于兼容性探测)

    def port(self, name: str, kind: str) -> Port:
        pool = self.inputs if kind == "in" else self.outputs
        for p in pool:
            if p.name == name:
                return p
        raise KeyError(f"{self.node} 无 {kind} 端口 {name}")


@dataclass(frozen=True)
class Edge:
    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
    transform: str = "identity"
    delayed: bool = False
    desc: str = ""


# ---------------------------------------------------------------------------
# 七个节点的契约 (与 W8-N01..N07 任务书物理量一一对应)
# ---------------------------------------------------------------------------
CONTRACTS: Dict[str, NodeContract] = {
    "N01": NodeContract(
        node="N01",
        title="冰-结构接触相变与融膜演化 (Clausius-Clapeyron / Stefan)",
        version="1.0.0",
        source_hint="tensorphysics:src/tensorphysics/phase (worktree m1212)",
        inputs=(
            Port("ice_thickness", "m", desc="冰厚", external=True),
            Port("ice_compressive_strength", "Pa", desc="冰抗压强度", external=True),
            Port("ship_speed", "m/s", desc="冲撞速度", external=True),
            Port("contact_area", "m^2", desc="名义接触面积", external=True),
            Port("ambient_temperature", "K", desc="环境温度", external=True),
        ),
        outputs=(
            Port("contact_force", "N", "ship_fixed_zup", "接触力 (峰值/时均)"),
            Port("contact_moment_x", "N*m", "ship_fixed_zup", "绕 x 的接触力矩"),
            Port("contact_force_time_series", "N", "ship_fixed_zup", "接触力时历 (10 ms, 力学用)",
                 terminal=True, consumer_hint="时历工件 -> artifacts/"),
            Port("acoustic_force_series", "N", "ship_fixed_zup", "冰挤压高频脉动时历 (1 ms, SEA 用)"),
            Port("melt_film_thickness", "m", desc="融膜厚度", terminal=True,
                 consumer_hint="诊断量 -> 报告表 3 / artifacts"),
            Port("melt_mass_rate", "kg/s", desc="融化质量流率"),
            Port("interface_temperature", "K", desc="压力融化界面温度", terminal=True,
                 consumer_hint="诊断量 -> 报告表 3"),
        ),
    ),
    "N02": NodeContract(
        node="N02",
        title="液舱晃荡 (势流自由面 + 壁面冲击压力)",
        version="1.0.0",
        source_hint="TensorLBM sloshing (worktree m1216)",
        inputs=(
            Port("roll_angle", "rad", "ship_fixed_zup", "横摇角"),
            Port("roll_acceleration", "rad/s^2", "ship_fixed_zup", "横摇角加速度 (晃荡激励)"),
            Port("fill_ratio", "1", desc="充液率", external=True),
            Port("tank_length", "m", desc="舱长", external=True),
            Port("tank_width", "m", desc="舱宽", external=True),
            Port("liquid_depth", "m", desc="液深", external=True),
            Port("liquid_density", "kg/m^3", desc="液货密度", external=True),
        ),
        outputs=(
            Port("slosh_moment_x", "N*m", "ship_fixed_zup", "液晃反作用力矩"),
            Port("free_surface_elevation", "m", "tank_local_zup", "波面 elevation", terminal=True,
                 consumer_hint="场量 -> 报告图 4"),
            Port("wall_impact_pressure", "Pa", "tank_local_zup", "壁板冲击压力峰值", terminal=True,
                 consumer_hint="诊断量 -> 报告表 3"),
            Port("natural_frequency", "rad/s", desc="一阶固有频率", terminal=True,
                 consumer_hint="解析对标 -> 报告表 2"),
            Port("liquid_mass", "kg", desc="液货质量", terminal=True,
                 consumer_hint="质量守恒审计"),
        ),
    ),
    "N03": NodeContract(
        node="N03",
        title="滚-液晃 2-DOF 耦合与稳性裕度 (IMO IS 2008)",
        version="1.0.0",
        source_hint="TensorPlatform backend/services/ice_integration (本门参考实现)",
        inputs=(
            Port("excitation_moment_x", "N*m", "ship_fixed_zup", "冰激励力矩"),
            Port("excitation_force", "N", "ship_fixed_zup", "冰激励力 (纵荡)"),
            Port("slosh_moment_x", "N*m", "ship_fixed_zup", "液晃反馈力矩"),
            Port("ship_mass", "kg", desc="排水量", external=True),
            Port("roll_inertia", "kg*m^2", desc="含附加惯量的横摇惯量", external=True),
            Port("metacentric_height", "m", desc="GM0", external=True),
            Port("kg_height", "m", desc="KG", external=True),
        ),
        outputs=(
            Port("roll_angle", "rad", "ship_fixed_zup", "横摇响应时历"),
            Port("roll_rate", "rad/s", "ship_fixed_zup", "横摇角速度时历"),
            Port("roll_acceleration", "rad/s^2", "ship_fixed_zup", "横摇角加速度时历"),
            Port("forward_speed", "m/s", "earth_zup", "纵荡速度时历"),
            Port("roll_amplification_factor", "1", desc="横摇峰值放大因子", terminal=True,
                 consumer_hint="判据 (c) -> 报告表 2"),
            Port("gm_corrected", "m", desc="自由液面修正后 GM", terminal=True,
                 consumer_hint="IMO 判据 -> 报告表 3"),
            Port("gz_max", "m", desc="GZ 曲线最大复原力臂", terminal=True,
                 consumer_hint="IMO 判据 -> 报告表 3"),
            Port("stability_margin", "m", desc="IMO 稳性裕度", terminal=True,
                 consumer_hint="IMO 判据 -> 报告表 3"),
            Port("imo_pass_flag", "1", desc="1=PASS, 0=FAIL", terminal=True,
                 consumer_hint="验收判据 -> acceptance.json"),
        ),
    ),
    "N04": NodeContract(
        node="N04",
        title="冰激高频振动与噪声 SEA (统计能量分析)",
        version="1.0.0",
        source_hint="tensorphysics:src/tensorphysics/sea (worktree m1213)",
        inputs=(
            Port("contact_force_time_series", "N", "ship_fixed_zup", "冰载荷高频时历 (1 ms 采样)"),
            Port("sample_interval", "s", desc="时历采样间隔", external=True),
            Port("plate_area", "m^2", desc="板格面积", external=True),
            Port("plate_thickness", "m", desc="板厚", external=True),
            Port("structural_damping", "1", desc="内损耗因子", external=True),
        ),
        outputs=(
            Port("modal_density", "s", desc="模态密度 (modes/Hz)", terminal=True,
                 consumer_hint="解析对标 -> 报告表 2"),
            Port("coupling_loss_factor", "1", desc="耦合损耗因子", terminal=True,
                 consumer_hint="互易性校验 -> 报告表 2"),
            Port("subsystem_energy", "J", desc="子系统能量", terminal=True,
                 consumer_hint="功率平衡审计"),
            Port("velocity_level", "1", desc="振动速度级 dB (ref 1e-9 m/s)", terminal=True,
                 consumer_hint="结果表"),
            Port("radiated_sound_power", "W", desc="辐射声功率", terminal=True,
                 consumer_hint="结果表"),
            Port("power_balance_residual", "1", desc="SEA 功率平衡残差", terminal=True,
                 consumer_hint="守恒审计 (d)"),
        ),
    ),
    "N05": NodeContract(
        node="N05",
        title="海水吸入系统冰块堵塞 (Beverloo + Ergun)",
        version="1.0.0",
        source_hint="TensorDEM:src/tensordem (worktree m1214)",
        inputs=(
            Port("ice_concentration", "1", desc="碎冰密集度"),
            Port("intake_flow_demand", "m^3/s", desc="冷却水需求流量", external=True),
            Port("orifice_diameter", "m", desc="格栅通流尺度", external=True),
            Port("particle_size", "m", desc="碎块特征尺寸", external=True),
            Port("grid_slot_width", "m", desc="格栅缝宽", external=True),
        ),
        outputs=(
            Port("discharge_rate", "m^3/s", desc="Beverloo 排料率", terminal=True,
                 consumer_hint="解析对标 -> 报告表 2"),
            Port("blockage_probability", "1", desc="堵塞概率", terminal=True,
                 consumer_hint="结果表"),
            Port("pressure_drop", "Pa", desc="Ergun 压损", terminal=True,
                 consumer_hint="结果表"),
            Port("cooling_availability", "1", desc="冷却可用性 (0-1)", terminal=True,
                 consumer_hint="包络判据"),
        ),
    ),
    "N06": NodeContract(
        node="N06",
        title="细冰晶/油脂冰流变与推进器推力损失",
        version="1.0.0",
        source_hint="TensorPlatform frazil 模块 (worktree m1215)",
        inputs=(
            Port("frazil_concentration", "1", desc="体积分数 phi", external=True),
            Port("melt_mass_rate", "kg/s", desc="相变注入冰晶质量流率"),
            Port("ship_speed", "m/s", "earth_zup", desc="航速"),
            Port("shaft_power", "W", desc="轴功率", external=True),
        ),
        outputs=(
            Port("effective_viscosity", "Pa*s", desc="有效黏度", terminal=True,
                 consumer_hint="流变对标 -> 报告表 2"),
            Port("thrust_loss_fraction", "1", desc="推力损失比例", terminal=True,
                 consumer_hint="包络判据"),
            Port("delivered_thrust", "N", desc="可用推力", terminal=True,
                 consumer_hint="与阻力对比"),
            Port("operational_envelope_status", "1", desc="1=可作业", terminal=True,
                 consumer_hint="包络判据"),
        ),
    ),
    "N07": NodeContract(
        node="N07",
        title="数字孪生: 多源数据 + 物理模型 EKF 状态估计",
        version="1.0.0",
        source_hint="TensorPlatform backend/services/digital_twin (agent/m797) + worktree m1218",
        inputs=(
            Port("measured_roll", "rad", "ship_fixed_zup", "IMU 横摇量测"),
            Port("measured_roll_rate", "rad/s", "ship_fixed_zup", "IMU 角速度量测"),
            Port("measured_moment", "N*m", "ship_fixed_zup", "应变/冰雷达反演力矩"),
            Port("model_roll_prediction", "rad", "ship_fixed_zup", "物理模型一步预测"),
            Port("timestamp", "s", desc="时戳", external=True),
        ),
        outputs=(
            Port("estimated_roll", "rad", "ship_fixed_zup", "融合后横摇", terminal=True,
                 consumer_hint="孪生输出 -> API/报告表 3"),
            Port("estimated_roll_rate", "rad/s", "ship_fixed_zup", "融合后角速度", terminal=True,
                 consumer_hint="结果表"),
            Port("estimated_ice_concentration", "1", desc="反演冰况代理量"),
            Port("estimated_roll_std", "rad", desc="估计标准差", terminal=True,
                 consumer_hint="一致性检验"),
            Port("innovation_norm", "1", desc="归一化新息", terminal=True,
                 consumer_hint="一致性检验"),
            Port("state_health", "1", desc="健康度 0-1", terminal=True,
                 consumer_hint="结果表"),
        ),
    ),
}

#: 数据流边 (上游 -> 下游). 反馈环必须显式 delayed=True.
EDGES: Tuple[Edge, ...] = (
    Edge("N01", "contact_moment_x", "N03", "excitation_moment_x", desc="冲撞力矩驱动横摇"),
    Edge("N01", "contact_force", "N03", "excitation_force", desc="冲撞力驱动纵荡"),
    Edge("N01", "acoustic_force_series", "N04", "contact_force_time_series",
         desc="冰挤压高频脉动 -> SEA 力谱输入"),
    Edge("N01", "melt_mass_rate", "N06", "melt_mass_rate", desc="相变注入冰晶/融水"),
    Edge("N03", "roll_angle", "N02", "roll_angle"),
    Edge("N03", "roll_acceleration", "N02", "roll_acceleration"),
    Edge("N02", "slosh_moment_x", "N03", "slosh_moment_x", delayed=True,
         desc="液晃反作用, 一步滞后避免代数环"),
    Edge("N03", "roll_angle", "N07", "model_roll_prediction", desc="物理模型预测"),
    Edge("N03", "roll_angle", "N07", "measured_roll", desc="IMU 横摇通道"),
    Edge("N03", "roll_rate", "N07", "measured_roll_rate", desc="IMU 角速度通道"),
    Edge("N01", "contact_moment_x", "N07", "measured_moment", desc="应变反演力矩"),
    Edge("N07", "estimated_ice_concentration", "N05", "ice_concentration", desc="孪生反演冰况"),
    Edge("N03", "forward_speed", "N06", "ship_speed", transform="earth_to_ship_roll",
         desc="船体系速度 -> 大地系航速"),
)


@dataclass
class Violation:
    code: str
    where: str
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "where": self.where, "detail": self.detail}


@dataclass
class ContractReport:
    nodes: List[str] = field(default_factory=list)
    edges_checked: int = 0
    ports_checked: int = 0
    violations: List[Violation] = field(default_factory=list)
    delays: List[str] = field(default_factory=list)
    loops: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges_checked": self.edges_checked,
            "ports_checked": self.ports_checked,
            "violations": [v.as_dict() for v in self.violations],
            "delayed_edges": self.delays,
            "loops": self.loops,
            "error_count": len(self.violations),
            "ok": self.ok,
        }


def is_valid_rotation(m: Sequence[Sequence[float]], tol: float = 1e-9) -> bool:
    a = np.asarray(m, dtype=float)
    if a.shape != (3, 3):
        return False
    if abs(np.linalg.det(a) - 1.0) > tol:
        return False
    return float(np.max(np.abs(a @ a.T - np.eye(3)))) <= tol


def _simple_cycles(nodes: Sequence[str], edges: Sequence[Edge]) -> List[List[str]]:
    """Tarjan-less 简单环枚举 (节点数 = 7, DFS 足够)."""
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for e in edges:
        adj.setdefault(e.src_node, []).append(e.dst_node)
    cycles: List[List[str]] = []

    def dfs(start: str, cur: str, path: List[str], seen: set) -> None:
        for nxt in adj.get(cur, []):
            if nxt == start and len(path) >= 2:
                cycles.append(path + [start])
            elif nxt not in seen and len(path) < len(nodes):
                seen.add(nxt)
                dfs(start, nxt, path + [nxt], seen)
                seen.discard(nxt)

    for n in nodes:
        dfs(n, n, [n], {n})
    return cycles


def validate_contracts(
    contracts: Dict[str, NodeContract] | None = None,
    edges: Sequence[Edge] | None = None,
) -> ContractReport:
    """校验整条链: 端口存在性 / 量纲兼容 / 坐标系变换合法 / 反馈环显式延时 / 悬空端口."""
    contracts = CONTRACTS if contracts is None else contracts
    edges = EDGES if edges is None else edges
    rep = ContractReport(nodes=sorted(contracts))

    # 1) 端口自检: 单位必须登记, 坐标系必须登记
    for node, c in contracts.items():
        for kind, ports in (("in", c.inputs), ("out", c.outputs)):
            for p in ports:
                rep.ports_checked += 1
                try:
                    dim_of(p.unit)
                except KeyError as exc:
                    rep.violations.append(Violation("UNKNOWN_UNIT", f"{node}.{kind}.{p.name}", str(exc)))
                if p.frame not in FRAMES:
                    rep.violations.append(Violation("UNKNOWN_FRAME", f"{node}.{kind}.{p.name}", p.frame))

    # 2) 边校验
    for e in edges:
        rep.edges_checked += 1
        where = f"{e.src_node}.{e.src_port}->{e.dst_node}.{e.dst_port}"
        if e.src_node not in contracts or e.dst_node not in contracts:
            rep.violations.append(Violation("MISSING_NODE", where, f"{e.src_node}|{e.dst_node}"))
            continue
        try:
            sp = contracts[e.src_node].port(e.src_port, "out")
            dp = contracts[e.dst_node].port(e.dst_port, "in")
        except KeyError as exc:
            rep.violations.append(Violation("MISSING_PORT", where, str(exc)))
            continue
        try:
            if sp.dim != dp.dim:
                rep.violations.append(
                    Violation("DIMENSION_MISMATCH", where,
                              f"{sp.unit}{format_dim(sp.dim)} -> {dp.unit}{format_dim(dp.dim)}")
                )
        except DimensionError as exc:  # pragma: no cover
            rep.violations.append(Violation("DIMENSION_ERROR", where, str(exc)))
        if sp.frame != dp.frame:
            if e.transform not in FRAME_TRANSFORMS:
                rep.violations.append(
                    Violation("FRAME_TRANSFORM_UNDECLARED", where, f"{sp.frame}->{dp.frame}")
                )
            elif not is_valid_rotation(FRAME_TRANSFORMS[e.transform]):
                rep.violations.append(Violation("FRAME_TRANSFORM_INVALID", where, e.transform))
            elif sp.frame != "n_a" and dp.frame != "n_a":
                rep.violations.append(
                    Violation("FRAME_MISMATCH", where,
                              f"坐标系不一致且未做显式旋转: {sp.frame}->{dp.frame}")
                )
        if e.delayed:
            rep.delays.append(where)

    # 3) 端口连通性: 非外部输入必须被喂; 输出必须有去向或声明终端
    fed = {(e.dst_node, e.dst_port) for e in edges}
    used = {(e.src_node, e.src_port) for e in edges}
    for node, c in contracts.items():
        for p in c.inputs:
            if not p.external and (node, p.name) not in fed:
                rep.violations.append(Violation("UNFED_INPUT", f"{node}.{p.name}", "无上游边"))
            if p.external and (node, p.name) in fed:
                rep.violations.append(Violation("EXTERNAL_BUT_FED", f"{node}.{p.name}", "外部输入又被上游喂"))
        for p in c.outputs:
            if (node, p.name) not in used and not p.terminal:
                rep.violations.append(Violation("DANGLING_OUTPUT", f"{node}.{p.name}", "无下游消费者且未声明终端"))

    # 4) 反馈环: 有环则至少一条边必须 delayed
    seen_keys = set()
    for cyc in _simple_cycles(list(contracts), edges):
        key = "->".join(sorted(set(cyc)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rep.loops.append("->".join(cyc))
        loop_edges = [e for e in edges if e.src_node in cyc and e.dst_node in cyc]
        if not any(e.delayed for e in loop_edges):
            rep.violations.append(
                Violation("ALGEBRAIC_LOOP", "->".join(cyc), "反馈环无显式延时")
            )
    return rep


def edge_units_table() -> List[dict]:
    """所有边的单位/量纲/换算系数表 (报告与审计链工件)."""
    from .dimensions import conversion_factor

    rows = []
    for e in EDGES:
        sp = CONTRACTS[e.src_node].port(e.src_port, "out")
        dp = CONTRACTS[e.dst_node].port(e.dst_port, "in")
        rows.append(
            {
                "edge": f"{e.src_node}.{e.src_port}->{e.dst_node}.{e.dst_port}",
                "src_unit": sp.unit,
                "dst_unit": dp.unit,
                "src_dim": format_dim(sp.dim),
                "dst_dim": format_dim(dp.dim),
                "factor": conversion_factor(sp.unit, dp.unit),
                "src_frame": sp.frame,
                "dst_frame": dp.frame,
                "transform": e.transform,
                "delayed": e.delayed,
            }
        )
    return rows