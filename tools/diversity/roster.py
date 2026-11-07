"""多样性引擎·氏族清单与席位矩阵（战役节点 IN25-T2）。

**问题（为什么需要这一层）**：平台把同一个目标派给 N 个 agent 时，若 N 个席位共用
同一模型、同一表述、同一先验、同一角色，则 N 次尝试在统计上近似 1 次尝试的重复
抽样（"一百颗同骰子"）——预算被消费而失败空间没有被展开。多样性引擎把「席位」定义
成四轴乘积上的点，并**机器可裁地**保证四条轴同时展开：

  1. 模型族（clade）：deepseek / qwen / minimax / glm，**至少三族**参与；
  2. 问题框架变体（framing）：同一目标的 ≥4 种不同表述/攻击角；
  3. 先验种子（seed）：每席一条由 (族, 框架, 角色) 派生的确定性种子流，两两不同；
  4. 对抗角色（role）：攻坚者 attacker + 破坏者 breaker，**每族两角色都要有席位**。

**零平台域语义**：本模块不认识任何具体学科（不含船舶/物理量/单位），只处理
"目标文本 + 框架模板 + 模型族" 三元组。目标是任意字符串，框架是域无关的攻击角
（正向构造 / 反演 / 极限与界 / 失效面 / 换基），因此可被任何战役节点复用。

**判据的实现位置**（数值由 `tools/diversity/run_node.py` 写进 artifacts）：
  - 氏族覆盖矩阵全格有席位 → `build_seats()` 末尾 `assert` + `coverage_report()`；
  - 先验种子差异 → `seed_divergence()`（最小汉明距离 + 碰撞数）；
  - 角色齐备 → `coverage_report()["role_balance"]`。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

NODE_ID = "IN25-T2"

# 现场中继（/v1/models, 2026-11-07 实测 9 个 id）归并出的模型族。族→该族可用模型 id；
# 首个元素是默认模型。**不在代码里假装存在**：不可达的族由 verify_roster() 判掉。
FAMILY_ROSTER: Dict[str, Tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-flash", "DeepSeek-V4-Flash-0731"),
    "qwen": ("qwen3.7-plus", "qwen3.6-plus", "Qwen3.6-35B-A3B", "Qwen3.8-27B"),
    "minimax": ("MiniMax-M2.5-AWQ",),
    "glm": ("glm-5.2", "glm-5.1"),
}
MIN_FAMILIES = 3
MIN_FRAMINGS = 4
ROLES = ("attacker", "breaker")
ROLE_CN = {"attacker": "攻坚者", "breaker": "破坏者"}


@dataclass(frozen=True)
class Framing:
    """一种"攻击角"：同一目标的不同表述 + 该表述要求的交付物。

    statement 里 `{goal}` 被目标文本替换；deliverable / stress_axis 让下游席位知道
    "这一格要产什么"，因此框架变体不是换个说法，而是**换证据面**。
    """

    key: str
    cn: str
    statement: str
    deliverable: str
    stress_axis: str


# 域无关的五种攻击角。刻意包含"正向"与"反向"（构造 vs 反演）、"定量边界"与
# "定性失效面"、以及"换表示"——五条路径的最优解通常不在同一处，这是多样性的来源。
FRAMINGS: Tuple[Framing, ...] = (
    Framing(
        key="direct",
        cn="正向构造",
        statement=(
            "把目标 {goal} 当作构造任务：给出从头到尾可执行的一条流程，"
            "明确每一步的输入输出、参数取值与验收方式。"
        ),
        deliverable="端到端流程 + 每步参数取值 + 单步可验证的中间量",
        stress_axis="流程完整性",
    ),
    Framing(
        key="inverse",
        cn="反演",
        statement=(
            "把目标 {goal} 当作反问题：从可观测的靶值出发，反推哪些内部参数/初值/"
            "边界条件能同时满足全部约束，并说明解的唯一性范围。"
        ),
        deliverable="反演映射 + 可辨识性分析（哪些参数不可辨识）+ 病态性说明",
        stress_axis="可辨识性",
    ),
    Framing(
        key="limits",
        cn="极限与界",
        statement=(
            "把目标 {goal} 当作极限问题：先找参数趋于极端时解析可解的情形，"
            "用它给出上下界，再用夹逼判断中段行为，明确界在何处失效。"
        ),
        deliverable="解析极限解 + 上下界表 + 界失效的判据",
        stress_axis="界与渐近",
    ),
    Framing(
        key="failure",
        cn="失效面",
        statement=(
            "把目标 {goal} 当作失效面搜索：先构造最坏情形让方案必然失败，"
            "在失败边界上二分，用被排除的区域反推成功域的形状。"
        ),
        deliverable="反例族 + 失败边界 + 成功域的充分条件",
        stress_axis="最坏情形",
    ),
    Framing(
        key="orthogonal",
        cn="换基",
        statement=(
            "把目标 {goal} 当作表示选择问题：更换变量/坐标系/离散化方式，"
            "使原表述中的难点在新表示下消失或变成良态问题，并给出换算关系。"
        ),
        deliverable="新表示 + 与原表示的换算 + 良态性证明或数值证据",
        stress_axis="表示与良态性",
    ),
)


@dataclass(frozen=True)
class Seat:
    """一个席位 = 氏族矩阵上的一个格子。"""

    seat_id: str
    family: str
    model_id: str
    framing: str
    role: str
    seed: int
    hypothesis: str

    def as_dict(self) -> dict:
        return asdict(self)


class RosterError(ValueError):
    """氏族清单/席位矩阵不满足多样性约束。"""


def _canon(obj) -> str:
    """稳定序列化：用于跨进程一致的哈希（禁 dict 顺序/浮点 repr 影响摘要）。"""
    import json

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def derive_seed(family: str, framing: str, role: str) -> int:
    """确定性先验种子：同一 (族, 框架, 角色) 永远同一种子，不同格子两两不同（见测试）。"""
    material = f"{NODE_ID}|{family}|{framing}|{role}|prior-seed-v1".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _pick_model(family: str, framing: str, role: str) -> str:
    """族内选模型：用种子低位取模，保证同族不同格的模型也可能不同（族内也算多样性）。"""
    models = FAMILY_ROSTER[family]
    return models[derive_seed(family, framing, role) % len(models)]


def seat_id(family: str, framing: str, role: str) -> str:
    return f"{NODE_ID}/{family}/{framing}/{role}"


def _hypothesis(family: str, framing: str, role: str) -> str:
    """席位假设（kuà 族对照用）：角色 × 框架给出可证伪的一句话预期。"""
    f = next(fr for fr in FRAMINGS if fr.key == framing)
    if role == "attacker":
        return (
            f"[{family}/{f.cn}] 该族在 {f.stress_axis} 轴上能用「{f.cn}」路径推进目标，"
            f"预期产出 {f.deliverable}，且优于同格其它族的单点突破。"
        )
    return (
        f"[{family}/{f.cn}] 破坏者假设：{f.cn} 路径在 {f.stress_axis} 轴上的关键假设为假，"
        f"可构造反例；若无法构造反例则须给出该路径的充分条件。"
    )


def build_seats(
    goal: str,
    families: Sequence[str] = tuple(FAMILY_ROSTER),
    framings: Sequence[str] = tuple(f.key for f in FRAMINGS),
    roles: Sequence[str] = ROLES,
) -> List[Seat]:
    """构造全格席位矩阵（族 × 框架 × 角色 的完整乘积）。

    硬约束（违反即抛 RosterError，不静默降级）：
      - 目标非空；
      - 至少 MIN_FAMILIES 族、至少 MIN_FRAMINGS 种框架；
      - 角色必须同时含 attacker 与 breaker（缺一即退化为一言堂）；
      - 未知族/框架/角色一律拒绝（拼写漂移会让矩阵悄悄缩小）。
    """
    if not goal or not goal.strip():
        raise RosterError("goal 为空：多样性引擎不接受无目标席位")
    fams = list(dict.fromkeys(families))
    frs = list(dict.fromkeys(framings))
    rls = list(dict.fromkeys(roles))
    unknown_f = [f for f in fams if f not in FAMILY_ROSTER]
    unknown_g = [f for f in frs if f not in {x.key for x in FRAMINGS}]
    unknown_r = [r for r in rls if r not in ROLES]
    if unknown_f or unknown_g or unknown_r:
        raise RosterError(f"未知轴取值: families={unknown_f} framings={unknown_g} roles={unknown_r}")
    if len(fams) < MIN_FAMILIES:
        raise RosterError(f"模型族不足: {len(fams)} < {MIN_FAMILIES}")
    if len(frs) < MIN_FRAMINGS:
        raise RosterError(f"框架变体不足: {len(frs)} < {MIN_FRAMINGS}")
    if set(rls) != set(ROLES):
        raise RosterError(f"对抗角色必须同时含 {ROLES}，实得 {rls}")

    seats: List[Seat] = []
    for family in fams:
        for framing in frs:
            for role in rls:
                seats.append(
                    Seat(
                        seat_id=seat_id(family, framing, role),
                        family=family,
                        model_id=_pick_model(family, framing, role),
                        framing=framing,
                        role=role,
                        seed=derive_seed(family, framing, role),
                        hypothesis=_hypothesis(family, framing, role),
                    )
                )
    # 全格判据在这里就硬判一次：下游（run_node）不必"记得"检查。
    rep = coverage_report(seats, fams, frs)
    if not rep["full_grid"]:
        raise RosterError(f"矩阵未满格: {rep['missing_cells']}")
    return seats


def coverage_report(
    seats: Iterable[Seat], families: Sequence[str] = tuple(FAMILY_ROSTER),
    framings: Sequence[str] = tuple(f.key for f in FRAMINGS),
) -> dict:
    """氏族覆盖矩阵：每格席位数、缺格、角色平衡、族/框架边际覆盖。"""
    fams, frs = list(families), list(framings)
    grid: Dict[Tuple[str, str], Dict[str, int]] = {
        (f, g): {r: 0 for r in ROLES} for f in fams for g in frs
    }
    seats = list(seats)
    for s in seats:
        grid.setdefault((s.family, s.framing), {r: 0 for r in ROLES})[s.role] += 1
    cells = [grid[(f, g)] for f in fams for g in frs]
    per_cell = [sum(c.values()) for c in cells]
    missing = [[f, g] for f in fams for g in frs if sum(grid[(f, g)].values()) == 0]
    missing_role = [[f, g, r] for f in fams for g in frs for r in ROLES if grid[(f, g)][r] == 0]
    fam_counts = {f: sum(1 for s in seats if s.family == f) for f in fams}
    fr_counts = {g: sum(1 for s in seats if s.framing == g) for g in frs}
    return {
        "families": fams,
        "framings": frs,
        "roles": list(ROLES),
        "n_seats": len(seats),
        "n_cells": len(cells),
        "cells_covered": len(cells) - len(missing),
        "coverage_ratio": round((len(cells) - len(missing)) / len(cells), 6) if cells else 0.0,
        "full_grid": not missing and not missing_role,
        "missing_cells": missing,
        "missing_role_cells": missing_role,
        "min_seats_per_cell": min(per_cell) if per_cell else 0,
        "max_seats_per_cell": max(per_cell) if per_cell else 0,
        "role_balance": {
            r: sum(1 for s in seats if s.role == r) for r in ROLES
        },
        "family_marginal": fam_counts,
        "framing_marginal": fr_counts,
        "grid": {f"{f}|{g}": grid[(f, g)] for f in fams for g in frs},
    }


def seed_divergence(seats: Iterable[Seat]) -> dict:
    """先验种子差异：碰撞数（必须 0）+ 两两汉明距离的最小值/中位数。

    "先验不同"若只表现为数值不同但低位同构，仍会退化成同一骰子；因此报告的是
    **比特级**距离，而不是"种子不相等"这种弱判据。
    """
    seeds = [s.seed for s in seats]
    n = len(seeds)
    uniq = len(set(seeds))
    dists: List[int] = []
    for i in range(n):
        for j in range(i + 1, n):
            dists.append(bin(seeds[i] ^ seeds[j]).count("1"))
    dists.sort()
    if not dists:
        return {"n": n, "distinct": uniq, "collisions": n - uniq,
                "min_hamming": None, "median_hamming": None, "max_hamming": None}
    return {
        "n": n,
        "distinct": uniq,
        "collisions": n - uniq,
        "min_hamming": dists[0],
        "p05_hamming": dists[max(0, int(0.05 * (len(dists) - 1)))],
        "median_hamming": dists[len(dists) // 2],
        "max_hamming": dists[-1],
    }


def seat_prompt(seat: Seat, goal: str) -> str:
    """把席位展开成可直接派发的提示词（下游 T3/T4 的接口面）。

    三段式：角色约束（谁在说话）→ 框架表述（同一目标的第 k 种说法）→ 交付与证伪要求。
    破坏者额外被要求给出"杀死该方案的判据"，这是它区别于攻坚者的证据面。
    """
    f = next(fr for fr in FRAMINGS if fr.key == seat.framing)
    head = (
        f"你是{ROLE_CN[seat.role]}席位 {seat.seat_id}（族 {seat.family} / 模型 {seat.model_id}）。\n"
        f"框架：{f.cn}（{f.key}）。先验种子：{seat.seed}（决定你的初始假设与随机流，"
        f"不得沿用他席结论）。\n"
    )
    body = f.statement.format(goal=goal) + "\n"
    if seat.role == "attacker":
        tail = (
            f"交付：{f.deliverable}。必须给出可复算的数字/命令，以及一句可被下一代检验的预期收益。\n"
            f"你的假设（先写在盲预测里，再动手）：{seat.hypothesis}\n"
        )
    else:
        tail = (
            f"交付：针对「{f.cn}」路径的反例族或充分条件，以及杀死该路径的判据（尽量做成一行断言）。\n"
            f"你的假设（先写在盲预测里，再动手）：{seat.hypothesis}\n"
        )
    return head + body + tail


def verify_roster(live_models: Sequence[str]) -> dict:
    """现场对账：只有中继真的提供了该族模型，该族才计入氏族清单。

    防的是"配置文本会撒谎"（历史判例：配置里写过从未存在的模型名）——族的存在性
    由 `/v1/models` 实测决定；族内某个 id 缺失只降级模型选择，不整族作废。
    """
    live = set(live_models)
    families: Dict[str, dict] = {}
    for fam, ids in FAMILY_ROSTER.items():
        present = [m for m in ids if m in live]
        families[fam] = {
            "models_expected": list(ids),
            "models_live": present,
            "live": bool(present),
        }
    n_live = sum(1 for v in families.values() if v["live"])
    return {
        "families": families,
        "n_families_live": n_live,
        "n_families_required": MIN_FAMILIES,
        "ok": n_live >= MIN_FAMILIES,
        "unmatched_live_models": sorted(m for m in live
                                        if not any(m in ids for ids in FAMILY_ROSTER.values())),
    }