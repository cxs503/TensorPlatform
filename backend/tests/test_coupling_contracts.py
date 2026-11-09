"""跨求解器多物理场耦合契约标准化的验收测试 (IN63-N08 / m7212).

运行::

    cd backend && python -m pytest tests/test_coupling_contracts.py -q

覆盖五条机器判据
----------------
(a) 单位制转换: 100 组随机物理量 SI<->英制/混合往返, 相对误差 <= 1e-12
(b) 坐标系变换: det(R)=1±1e-12 且 max|R^T R - I| <= 1e-12, 逆变换回原坐标 <= 1e-12
(c) 量纲校验: 注入 10 组量纲不匹配, 全部拦截并报出具体字段
(d) 契约版本: v1->v2 字段不丢失, 破坏性变更必须被显式迁移覆盖且迁移可执行
(e) 负控: 全零物理量不崩溃, 显式声明无物理意义

另含正向对照 (九大学科契约 + 耦合边整包 0 违规) 与 JSON Schema 一致性检查,
避免"只有负控通过"的假绿。
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from services.coupling_contracts import (
    DimensionError,
    FrameError,
    FrameGraph,
    Frame,
    Migration,
    QuantityError,
    RigidTransform,
    UnknownUnitError,
    VersionError,
    apply_migrations,
    buckingham_pi,
    check_compatibility,
    check_declared_groups,
    check_homogeneous_relation,
    check_registry_consistency,
    contract_from_dict,
    convert,
    convert_traced,
    decode,
    dim,
    dim_of_unit,
    encode,
    euler_zup,
    format_dim,
    from_quaternion,
    load_edges,
    load_frames,
    load_registry,
    load_registry_layers,
    make,
    normalize_batch,
    orthonormality_error,
    det,
    det_error,
    parse_unit,
    preferred_unit,
    si_unit_name,
    to_quaternion,
    validate_contract,
    validate_registry,
)
from services.coupling_contracts.contract import Edge
from services.coupling_contracts.units import UNITS

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DATA = REPO / "contracts" / "coupling"

TOL = 1e-12


# ---------------------------------------------------------------------------
# 夹具: 数据文件 (领域字段由 JSON 注入, 测试代码不含学科词条)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def frames_graph() -> FrameGraph:
    return load_frames(json.loads((DATA / "frames.v1.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def registry_v1():
    contracts, violations = load_registry(DATA / "v1")
    assert not violations, [v.to_dict() for v in violations]
    return {c.contract_id: c for c in contracts}


@pytest.fixture(scope="module")
def registry_v2():
    contracts, violations = load_registry(DATA / "v2")
    assert not violations, [v.to_dict() for v in violations]
    return {c.contract_id: c for c in contracts}


@pytest.fixture(scope="module")
def migrations() -> dict:
    return json.loads((DATA / "migrations.v1_to_v2.json").read_text(encoding="utf-8"))["contracts"]


# ═══════════════════════════════════════════════════════════════════════════
# (a) 单位制转换: 100 组随机量往返误差 <= 1e-12
# ═══════════════════════════════════════════════════════════════════════════
_ROUNDTRIP_UNITS = [
    ("N", "lbf"), ("N", "kip"), ("kN", "lbf"), ("MN", "lbf"),
    ("N*m", "lbf*ft"), ("kN*m", "lbf*ft"),
    ("Pa", "psi"), ("MPa", "ksi"), ("kPa", "psf"),
    ("m", "ft"), ("m", "in"), ("km", "nmi"), ("mm", "in"),
    ("m/s", "kn"), ("m/s", "ft/s"), ("kn", "mph"),
    ("kg", "lb"), ("t", "lb"), ("kg", "slug"),
    ("kg/m^3", "lb/ft^3"), ("t/m^3", "lb/ft^3"),
    ("W", "hp"), ("kW", "hp"), ("J", "BTU"), ("kJ", "cal"),
    ("m^2", "ft^2"), ("m^3", "ft^3"), ("m^2/s", "cSt"),
    ("Pa*s", "cP"), ("Hz", "rpm"), ("m/s^2", "ft/s^2"),
]


def test_roundtrip_100_random_quantities_within_1e12():
    """(a) 100 组随机量: A -> B -> A 相对误差 <= 1e-12, 且量纲守恒."""
    rng = random.Random(20261108)
    worst = 0.0
    cases = 0
    for _ in range(100):
        src, dst = _ROUNDTRIP_UNITS[rng.randrange(len(_ROUNDTRIP_UNITS))]
        exponent = rng.uniform(-6.0, 6.0)
        value = rng.choice((-1.0, 1.0)) * (10.0 ** exponent)
        if rng.random() < 0.15:  # 偶发整数/零附近值
            value = rng.uniform(-10.0, 10.0)
        back = convert(convert(value, src, dst), dst, src)
        assert dim_of_unit(src) == dim_of_unit(dst), (src, dst)
        if value == 0.0:
            continue
        rel = abs(back - value) / abs(value)
        worst = max(worst, rel)
        cases += 1
        assert rel <= TOL, f"{value!r} {src}->{dst}->{src}: rel err {rel:.3e}"
    assert cases == 100
    # 留痕: 最坏相对误差 (验收判据数字)
    assert worst <= TOL, worst


def test_conversion_trace_has_si_pivot():
    """换算链可追溯: 源 -> SI 枢轴 -> 目标, 三步行全 + 系数可复算."""
    tr = convert_traced(1.0, "kN*m", "lbf*ft")
    assert len(tr.steps) == 3
    assert tr.steps[1].label == "pivot:SI"
    assert math.isclose(tr.si_value, 1000.0, rel_tol=1e-15)
    assert math.isclose(tr.target_value / tr.si_value, tr.steps[2].factor, rel_tol=1e-15)
    payload = tr.to_dict()
    assert payload["dim"] == "M·L^2·T^-2"
    assert payload["steps"][2]["label"] == "target:lbf*ft"


def test_affine_temperature_units():
    """温标是仿射单位: degC/degF/K 往返 <=1e-12, 且不可参与复合表达式."""
    for value in (-40.0, 0.0, 20.0, 100.0, 1234.5):
        assert abs(convert(convert(value, "degC", "degF"), "degF", "degC") - value) <= 1e-12 * max(
            1.0, abs(value))
    assert abs(convert(convert(300.0, "K", "degF"), "degF", "K") - 300.0) <= 1e-9
    with pytest.raises(DimensionError):
        parse_unit("degC/s")  # 仿射单位不能复合


def test_unit_expression_parser_and_whitelist():
    """单位表达式解析 + 显式白名单(未登记单位必须报错, 拒绝前缀猜测)."""
    e = parse_unit("kg/(m*s^2)")
    assert format_dim(e.dim) == "M·L^-1·T^-2"
    assert math.isclose(e.scale, 1.0)
    assert parse_unit("9.80665*m/s^2").dim == dim(L=1, T=-2)
    with pytest.raises(UnknownUnitError):
        parse_unit("knot_typo")
    with pytest.raises(DimensionError):
        convert(1.0, "kN", "s")


def test_registry_preferred_units_are_consistent():
    """登记表内部自检: 每个制式首选单位的量纲必须与索引一致."""
    assert check_registry_consistency() == []
    for system in ("SI", "IMPERIAL", "MIXED", "NAUTICAL"):
        sym = preferred_unit(dim(M=1, L=1, T=-2), system)
        assert dim_of_unit(sym) == dim(M=1, L=1, T=-2)
    assert si_unit_name(dim(M=1, L=2, T=-2)) == "kg*m^2/s^2"
    assert len(UNITS) >= 100


# ═══════════════════════════════════════════════════════════════════════════
# (b) 坐标系变换: 正交性 + det=1 + 逆变换往返
# ═══════════════════════════════════════════════════════════════════════════
def test_rotation_matrices_orthonormal_det_one():
    """(b) 24 组欧拉角: det(R)=1±1e-12, max|R^T R - I| <= 1e-12, 四元数往返."""
    rng = random.Random(4242)
    worst_ortho = worst_det = worst_quat = 0.0
    for _ in range(24):
        roll, pitch, yaw = (rng.uniform(-180.0, 180.0) for _ in range(3))
        r = euler_zup(roll, pitch, yaw, degrees=True)
        e_o = orthonormality_error(r)
        e_d = det_error(r)
        worst_ortho, worst_det = max(worst_ortho, e_o), max(worst_det, e_d)
        assert e_o <= TOL and e_d <= TOL
        q = to_quaternion(r)
        worst_quat = max(worst_quat, orthonormality_error(from_quaternion(*q)))
        assert worst_quat <= 1e-12
    assert worst_ortho <= TOL and worst_det <= TOL


def test_mirror_matrix_is_rejected():
    """左手/镜像矩阵 (det=-1) 必须被判负, 不能被当成旋转."""
    mirror = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))
    assert det(mirror) == -1.0
    with pytest.raises(FrameError):
        RigidTransform(mirror)
    skew = ((1.0, 0.1, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    with pytest.raises(FrameError):
        RigidTransform(skew)


def test_point_and_vector_roundtrip_through_frame_graph(frames_graph):
    """(b) 点经 3 帧最短路往返误差 <= 1e-12; 矢量变换不含平移."""
    rng = random.Random(77)
    worst = 0.0
    names = sorted(frames_graph.frames)
    for _ in range(40):
        src = names[rng.randrange(len(names))]
        dst = names[rng.randrange(len(names))]
        p = tuple(rng.uniform(-100.0, 100.0) for _ in range(3))
        back = frames_graph.transform_point(frames_graph.transform_point(p, src, dst), dst, src)
        worst = max(worst, max(abs(a - b) for a, b in zip(back, p)))
        assert worst <= TOL, (src, dst, p, back)
    assert worst <= TOL
    # 非平凡位姿: 随机欧拉角 + 平移的探针帧, 往返误差必须仍 <= 1e-12
    probe = FrameGraph()
    for name, frame in frames_graph.frames.items():
        probe.add_frame(frame)
    for (child, parent), tf in frames_graph._edges.items():
        probe.add_relative(child, parent, tf)
    probe.add_frame(Frame("probe"))
    probe.add_relative("probe", "hull_fixed",
                       RigidTransform(euler_zup(37.0, -12.0, 143.0, degrees=True),
                                      (11.5, -4.25, 2.0)))
    wp = 0.0
    for _ in range(20):
        p = tuple(rng.uniform(-200.0, 200.0) for _ in range(3))
        back = probe.transform_point(probe.transform_point(p, "hull_fixed", "probe"), "probe",
                                     "hull_fixed")
        wp = max(wp, max(abs(a - b) for a, b in zip(back, p)))
    assert wp <= TOL, wp
    tr = frames_graph.trace("hull_fixed", "blade_local")
    assert len(tr) == 1 and tr[0]["from"] == "hull_fixed"
    # 矢量不受平移影响, 且纯旋转保模长
    v = (1.0, 2.0, 3.0)
    for dst in names:
        rv = frames_graph.transform_vector(v, "hull_fixed", dst)
        assert abs(math.sqrt(sum(x * x for x in rv)) - math.sqrt(14.0)) <= 1e-12


def test_unreachable_frame_raises(frames_graph):
    solo = FrameGraph()
    solo.add_frame(Frame("island"))
    with pytest.raises(FrameError):
        solo.path("island", "hull_fixed")
    with pytest.raises(FrameError):
        frames_graph.transform_point((1.0, 0.0, 0.0), "hull_fixed", "no_such_frame")


# ═══════════════════════════════════════════════════════════════════════════
# Buckingham π: 无量纲群基与声明校验
# ═══════════════════════════════════════════════════════════════════════════
def test_pi_basis_recovers_reynolds_group():
    variables = [
        ("rho", dim(M=1, L=-3)),
        ("v", dim(L=1, T=-1)),
        ("L", dim(L=1)),
        ("mu", dim(M=1, L=-1, T=-1)),
    ]
    res = buckingham_pi(variables)
    assert res.rank == 3
    assert res.n_groups == 1
    group = res.groups[0]
    exps = dict(group.exponents)
    # 解必须与 Re = rho*v*L/mu 成比例 (整体符号可任意)
    ref = {"rho": 1, "v": 1, "L": 1, "mu": -1}
    scale = None
    for name, e in exps.items():
        if ref[name] == 0:
            assert e == 0
            continue
        ratio = e / ref[name]
        if scale is None:
            scale = ratio
        assert ratio == scale
    assert scale is not None and scale != 0
    # 组合后必须严格无量纲
    assert group_dim_is_zero(variables, group.exponent_map)


def group_dim_is_zero(variables, exponents) -> bool:
    from services.coupling_contracts.buckingham import group_dim

    return group_dim(variables, exponents) == dim()


def test_pi_group_count_equals_n_minus_rank():
    """群数 k = n - rank, 且声明完整群基时通过、缺一条时失败."""
    variables = [
        ("rho", dim(M=1, L=-3)),
        ("v", dim(L=1, T=-1)),
        ("L", dim(L=1)),
        ("g", dim(L=1, T=-2)),
        ("F", dim(M=1, L=1, T=-2)),
    ]
    res = buckingham_pi(variables)
    assert res.n == 5 and res.rank == 3 and res.n_groups == 2
    complete = check_declared_groups(
        variables,
        {"inertia": {"v": 2, "L": -1, "g": -1}, "force": {"F": 1, "rho": -1, "v": -2, "L": -2}},
        require_complete=True,
    )
    assert complete.ok, complete.to_dict()
    incomplete = check_declared_groups(
        variables, {"inertia": {"v": 2, "L": -1, "g": -1}}, require_complete=True)
    assert not incomplete.ok and not incomplete.complete


def test_pi_non_dimensionless_and_redundant_declarations_rejected():
    variables = [
        ("rho", dim(M=1, L=-3)),
        ("v", dim(L=1, T=-1)),
        ("L", dim(L=1)),
        ("g", dim(L=1, T=-2)),
    ]
    bad = check_declared_groups(variables, {"wrong": {"v": 1, "L": -1, "g": -1}})
    assert not bad.ok
    assert "L·T^-1" in bad.declared[0].reason or bad.declared[0].dim != dim()
    dup = check_declared_groups(
        variables,
        {"a": {"v": 2, "L": -1, "g": -1}, "b": {"v": 2, "L": -1, "g": -1}},
    )
    assert not dup.ok and dup.redundant


def test_homogeneous_relation_check():
    variables = [("F", dim(M=1, L=1, T=-2)), ("rho", dim(M=1, L=-3)),
                 ("v", dim(L=1, T=-1)), ("A", dim(L=2))]
    check_homogeneous_relation(
        variables, {"F": 1}, {"rho": 1, "v": 2, "A": 1}, label="F = 0.5 rho v^2 A")
    with pytest.raises(DimensionError):
        check_homogeneous_relation(variables, {"F": 1}, {"rho": 1, "v": 1, "A": 1})


# ═══════════════════════════════════════════════════════════════════════════
# 正向对照: 九大学科契约 + 耦合边整包 0 违规
# ═══════════════════════════════════════════════════════════════════════════
def test_registry_v1_is_clean(registry_v1, frames_graph):
    contracts, violations = load_registry(DATA / "v1")
    assert len(contracts) == 9, "九大学科契约都应被加载"
    assert not violations
    edges = load_edges(DATA / "coupling-map.v1.json")
    assert len(edges) == 10
    rep = validate_registry(contracts, edges, frames_graph)
    assert rep.ok, [v.to_dict() for v in rep.violations]
    assert rep.contracts_checked == 9 and rep.edges_checked == 10
    # π 群: 声明完整群基的契约必须真的完整
    complete = [cid for cid, r in rep.pi_reports.items() if r.require_complete]
    assert complete and all(rep.pi_reports[cid].complete for cid in complete)


def test_contract_data_files_conform_to_json_schema():
    """数据文件必须符合 JSON Schema (jsonschema 缺失时跳过, 不伪装通过)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((DATA / "schema" / "coupling-contract.v1.schema.json").read_text(encoding="utf-8"))
    for path in sorted((DATA / "v1").glob("*.json")) + sorted((DATA / "v2").glob("*.json")):
        jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)
    qschema = json.loads((DATA / "schema" / "physical-quantity.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(encode(make("force", [1.0, 2.0, 3.0], "kN", frame="hull_fixed")), qschema)
    fschema = json.loads((DATA / "schema" / "coupling-frames.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((DATA / "frames.v1.json").read_text(encoding="utf-8")), fschema)


# ═══════════════════════════════════════════════════════════════════════════
# (c) 量纲校验: 注入 10 组量纲不匹配, 全部拦截并报出具体字段
# ═══════════════════════════════════════════════════════════════════════════
_DIM_MISMATCH_CASES = [
    # (端口名, 单位, 错声明的量纲, 期望出现的字段名)
    ("force_x", "kN", "M·L^2·T^-2", "force_x"),
    ("time_step", "s", "L^-1", "time_step"),
    ("pressure", "kPa", "M·L^-3", "pressure"),
    ("velocity", "m/s", "L", "velocity"),
    ("density", "kg/m^3", "L^-3", "density"),
    ("moment", "kN*m", "M·L·T^-2", "moment"),
    ("power", "kW", "M·L^2·T^-2", "power"),
    ("viscosity", "Pa*s", "M·L^-1", "viscosity"),
    ("area", "m^2", "L^3", "area"),
    ("mass", "kg", "kg", "mass"),  # 非法符号 (量纲串里写单位名)
]


@pytest.mark.parametrize("name,unit,declared,field", _DIM_MISMATCH_CASES)
def test_declared_dim_mismatch_is_intercepted(name, unit, declared, field):
    """(c) 10 组注入: 声明量纲与单位推导不一致 -> DIM_DECLARED_MISMATCH + 字段名."""
    c = contract_from_dict({
        "contract_id": "injected.mismatch",
        "version": 1,
        "solver": "injected",
        "ports": [{"name": name, "role": "output", "unit": unit, "declared_dim": declared}],
    })
    violations = validate_contract(c)
    codes = {v.code for v in violations}
    assert codes & {"DIM_DECLARED_MISMATCH", "BAD_DECLARED_DIM"}, violations
    assert any(v.port == field for v in violations), [v.to_dict() for v in violations]


def test_edge_dimension_mismatch_is_intercepted():
    """(c) 耦合边两端量纲不一致 -> EDGE_DIM_MISMATCH, 定位到具体边与输出端口."""
    a = contract_from_dict({
        "contract_id": "k.a", "version": 1, "solver": "a",
        "ports": [{"name": "out_force", "role": "output", "unit": "kN",
                   "tensor_order": 1, "frame": "hull_fixed"}],
    })
    b = contract_from_dict({
        "contract_id": "k.b", "version": 1, "solver": "b",
        "ports": [{"name": "in_pressure", "role": "input", "unit": "kPa"}],
    })
    g = FrameGraph()
    g.add_frame(Frame("hull_fixed"))
    rep = validate_registry([a, b], [Edge("k.a", "out_force", "k.b", "in_pressure")], g)
    assert not rep.ok
    assert "EDGE_DIM_MISMATCH" in rep.codes
    v = next(x for x in rep.violations if x.code == "EDGE_DIM_MISMATCH")
    assert v.port == "out_force" and v.edge == "k.a.out_force->k.b.in_pressure"
    assert "M·L·T^-2" in v.message and "M·L^-1·T^-2" in v.message


def test_missing_and_unknown_frame_are_intercepted():
    g = FrameGraph()
    g.add_frame(Frame("hull_fixed"))
    no_frame = contract_from_dict({
        "contract_id": "k.nf", "version": 1, "solver": "x",
        "ports": [{"name": "v", "role": "input", "unit": "m/s", "tensor_order": 1}],
    })
    assert "MISSING_FRAME" in {v.code for v in validate_contract(no_frame, g)}
    bad_frame = contract_from_dict({
        "contract_id": "k.bf", "version": 1, "solver": "x",
        "ports": [{"name": "v", "role": "input", "unit": "m/s", "tensor_order": 1,
                   "frame": "ghost"}],
    })
    v = validate_contract(bad_frame, g)
    assert {x.code for x in v} == {"UNKNOWN_FRAME"} and v[0].port == "v"


def test_unreachable_frame_on_edge_is_intercepted():
    a = contract_from_dict({
        "contract_id": "k.aa", "version": 1, "solver": "a",
        "ports": [{"name": "f", "role": "output", "unit": "kN", "tensor_order": 1,
                   "frame": "hull_fixed"}],
    })
    b = contract_from_dict({
        "contract_id": "k.bb", "version": 1, "solver": "b",
        "ports": [{"name": "f", "role": "input", "unit": "kN", "tensor_order": 1,
                   "frame": "orbit"}],
    })
    g = FrameGraph()
    g.add_frame(Frame("hull_fixed"))
    g.add_frame(Frame("orbit"))
    rep = validate_registry([a, b], [Edge("k.aa", "f", "k.bb", "f")], g)
    assert rep.codes == ("EDGE_FRAME_UNREACHABLE",)
    # 补上一条可达变换后应当通过
    g.add_relative("orbit", "hull_fixed", RigidTransform(euler_zup(0.3, 0.0, 0.0)))
    rep2 = validate_registry([a, b], [Edge("k.aa", "f", "k.bb", "f")], g)
    assert rep2.ok, [v.to_dict() for v in rep2.violations]


def test_tensor_order_mismatch_and_duplicate_ports():
    dup = contract_from_dict({
        "contract_id": "k.dup", "version": 1, "solver": "x",
        "ports": [{"name": "p", "role": "input", "unit": "kPa"},
                  {"name": "p", "role": "output", "unit": "kPa"}],
    })
    assert "DUPLICATE_PORT" in {v.code for v in validate_contract(dup)}
    a = contract_from_dict({
        "contract_id": "k.t1", "version": 1, "solver": "a",
        "ports": [{"name": "f", "role": "output", "unit": "kN", "tensor_order": 1,
                   "frame": "hull_fixed"}],
    })
    b = contract_from_dict({
        "contract_id": "k.t2", "version": 1, "solver": "b",
        "ports": [{"name": "f", "role": "input", "unit": "kN"}],
    })
    rep = validate_registry([a, b], [Edge("k.t1", "f", "k.t2", "f")])
    assert "EDGE_TENSOR_ORDER_MISMATCH" in rep.codes


# ═══════════════════════════════════════════════════════════════════════════
# (d) 契约版本: 向后兼容 + 破坏性变更迁移
# ═══════════════════════════════════════════════════════════════════════════
def test_v2_merged_registry_is_clean(frames_graph):
    contracts, violations = load_registry_layers(DATA / "v1", DATA / "v2")
    assert not violations
    assert len(contracts) == 9
    versions = {c.contract_id: c.version for c in contracts}
    assert versions["solver.resistance"] == 2 and versions["solver.propulsion"] == 2
    rep = validate_registry(contracts, load_edges(DATA / "coupling-map.v2.json"), frames_graph)
    assert rep.ok, [v.to_dict() for v in rep.violations]


def test_additive_version_is_backward_compatible(registry_v1, registry_v2, migrations):
    cid = "solver.propulsion"
    rep = check_compatibility(
        registry_v1[cid], registry_v2[cid], migrations[cid]["migrations"])
    assert rep.ok and not rep.breaking
    kinds = {c.kind for c in rep.changes}
    assert kinds == {"ADDED_OPTIONAL"}
    assert not rep.uncovered


def test_breaking_changes_without_migration_fail(registry_v1, registry_v2):
    """破坏性变更未声明迁移 -> FAIL, 且逐条报出未覆盖字段."""
    cid = "solver.resistance"
    rep = check_compatibility(registry_v1[cid], registry_v2[cid], [])
    assert not rep.ok
    uncovered = {(c.kind, c.port) for c in rep.uncovered}
    assert ("UNIT_CHANGED", "water_density") in uncovered
    assert ("REMOVED_PORT", "wake_speed") in uncovered
    assert ("ADDED_REQUIRED", "gravity") in uncovered


def test_breaking_changes_with_migration_pass(registry_v1, registry_v2, migrations):
    for cid, entry in migrations.items():
        rep = check_compatibility(registry_v1[cid], registry_v2[cid], entry["migrations"])
        assert rep.ok, (cid, [u.to_dict() for u in rep.uncovered])
        for ch in rep.breaking:
            assert any(m.port in (ch.port, ch.new_value) or m.to_port == ch.port
                       for m in rep.migrations), (cid, ch.to_dict())


def test_version_number_must_increase(registry_v1, registry_v2):
    with pytest.raises(VersionError):
        check_compatibility(registry_v2["solver.resistance"], registry_v1["solver.resistance"])
    with pytest.raises(VersionError):
        check_compatibility(registry_v1["solver.resistance"],
                            registry_v1["solver.resistance"])


def test_migration_must_be_executable_and_preserve_physics(registry_v1, registry_v2, migrations):
    """迁移真实执行: v1 载荷 -> v2 载荷, 数值按新单位语义换算, 必填端口齐备."""
    cid = "solver.resistance"
    old, new = registry_v1[cid], registry_v2[cid]
    payload_v1 = {
        "advance_speed_in": 5.14, "water_density": 1025.0,
        "kinematic_viscosity": 1.19e-6, "waterline_length": 120.0,
        "resistance_force": [250.0, 0.0, 0.0], "friction_coefficient": 0.0015,
        "wetted_area": 2100.0, "wake_speed": 4.2,
    }
    res = apply_migrations(payload_v1, migrations[cid]["migrations"], old, new)
    assert res.ok, [v.to_dict() for v in res.violations]
    data = res.payload
    assert "wake_speed" not in data and data["advance_speed_out"] == 4.2
    assert math.isclose(data["water_density"], 1.025, rel_tol=1e-15)  # kg/m^3 -> t/m^3
    assert math.isclose(data["gravity"], 9.80665, rel_tol=1e-15)      # 默认值补齐
    assert set(data) <= {p.name for p in new.ports}, "迁移不得产生 v2 未声明的端口"
    assert "air_temperature" not in data, "可选端口不得被无依据地补值"
    # 新契约必填端口全部落地
    for p in new.ports:
        if p.required:
            assert p.name in data


def test_seakeeping_migration_converts_angle_units(registry_v1, registry_v2, migrations):
    cid = "solver.seakeeping"
    payload_v1 = {
        "wave_amplitude": 1.5, "wave_period": 8.0, "wave_length": 99.0,
        "heading_angle": 45.0, "roll_restoring_moment": 1200.0,
        "heave_rao": 0.9, "pitch_angle": 3.2, "added_resistance": 12.0,
    }
    res = apply_migrations(payload_v1, migrations[cid]["migrations"],
                           registry_v1[cid], registry_v2[cid])
    assert res.ok, [v.to_dict() for v in res.violations]
    assert math.isclose(res.payload["heading_angle"], math.pi / 4, rel_tol=1e-15)
    assert res.payload["added_resistance_force"] == 12.0


def test_migration_missing_required_port_is_caught(registry_v1, registry_v2, migrations):
    """迁移后仍缺必填端口 -> MIGRATION_MISSING_REQUIRED (迁移不等于免责)."""
    cid = "solver.resistance"
    res = apply_migrations({"advance_speed_in": 1.0}, migrations[cid]["migrations"],
                           registry_v1[cid], registry_v2[cid])
    assert not res.ok
    assert "MIGRATION_MISSING_REQUIRED" in {v.code for v in res.violations}


def test_undeclared_migration_kind_or_note_is_rejected():
    with pytest.raises(VersionError):
        Migration(kind="magic", port="p", note="xxx")
    with pytest.raises(VersionError):
        Migration(kind="reinterpret_dim", port="p", note="")
    with pytest.raises(VersionError):
        Migration(kind="rename", port="p", note="改名")  # 缺 to_port


# ═══════════════════════════════════════════════════════════════════════════
# (e) 负控: 全零物理量不崩溃, 声明无物理意义
# ═══════════════════════════════════════════════════════════════════════════
def test_zero_quantities_do_not_crash(frames_graph):
    scalar = decode({"name": "thrust", "value": 0.0, "unit": "kN"})
    assert scalar.is_zero and scalar.degenerate
    assert "undefined" in scalar.physical_meaning
    assert scalar.magnitude() == 0.0 and scalar.unit_vector() is None
    assert scalar.in_system("IMPERIAL").value == 0.0
    assert scalar.to_si().value == 0.0

    vector = decode({"name": "force", "value": [0.0, 0.0, 0.0], "unit": "kN",
                     "frame": "hull_fixed", "tensor_order": 1}, frames=frames_graph)
    assert vector.degenerate and vector.unit_vector() is None
    rotated = vector.with_frame("earth", frames_graph)
    assert rotated.components == (0.0, 0.0, 0.0)

    dimensionless = decode({"name": "ratio", "value": 0.0, "unit": "1"})
    assert dimensionless.is_zero and not dimensionless.degenerate  # 0 的比值合法


def test_zero_payload_batch_keeps_items_and_flags_them(frames_graph):
    payloads = [
        {"name": "a", "value": 0.0, "unit": "kN"},
        {"name": "b", "value": [0.0, 0.0, 0.0], "unit": "m/s", "frame": "hull_fixed",
         "tensor_order": 1},
        {"name": "c", "value": 3.0, "unit": "rad"},
    ]
    ok, bad = normalize_batch(payloads, system="SI", frames=frames_graph)
    assert not bad and len(ok) == 3
    assert all(item["is_zero"] for item in ok[:2])
    assert "undefined" in ok[0]["physical_meaning"]
    assert ok[2]["value"] == 3.0


def test_invalid_payloads_are_reported_with_field_names():
    good, bad = normalize_batch([
        {"name": "x", "value": 1.0, "unit": "not_a_unit"},
        {"name": "y", "value": [1.0, 2.0], "unit": "m"},
        {"name": "z", "value": 1.0, "unit": "m", "declared_dim": "M"},
        {"name": "w", "value": 1.0, "unit": "kN", "declared_dim": "M·L·T^-2"},
    ])
    assert [g["name"] for g in good] == ["w"] and len(bad) == 3
    names = {b["name"] for b in bad}
    assert names == {"x", "y", "z"}
    assert any("坐标系" in b["reason"] or "分量" in b["reason"] for b in bad if b["name"] == "y")
    good2, bad2 = normalize_batch([{"name": "w", "value": 1.0, "unit": "kN",
                                    "declared_dim": "M·L·T^-2"}])
    assert not bad2 and good2[0]["value"] == 1000.0  # kN -> N (SI)


def test_quantity_requires_frame_for_vectors():
    with pytest.raises(QuantityError):
        make("v", [1.0, 2.0, 3.0], "m/s", tensor_order=1)
    with pytest.raises(QuantityError):
        make("v", [1.0, 2.0], "m/s", tensor_order=1)
    q = make("v", [3.0, 4.0, 0.0], "m/s", frame="hull_fixed", tensor_order=1)
    assert q.magnitude() == 5.0
    assert q.unit_vector() == (0.6, 0.8, 0.0)