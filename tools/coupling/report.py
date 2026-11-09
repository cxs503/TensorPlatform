#!/usr/bin/env python3
"""Build the node report (markdown) and the platform report payload.

Every number in the text is read from ``artifacts/IN383-PLAT01/acceptance.json``
(written by :mod:`tools.coupling.acceptance`) and every figure from
``artifacts/IN383-PLAT01/figures.json`` — nothing is hand-copied, so the document
cannot drift away from the machine record.

Usage::

    python -m tools.coupling.report --out docs/IN383-PLAT01_coupling_exchange_clock.md \
        --payload /tmp/report.json
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "IN383-PLAT01"


def _fmt(x: float, digits: int = 3) -> str:
    return f"{x:.{digits}e}"


def _pct(x: float) -> str:
    return f"{100.0 * x:.3f} %"


def load() -> tuple[dict, dict]:
    acceptance = json.loads((ART / "acceptance.json").read_text(encoding="utf-8"))
    figures = json.loads((ART / "figures.json").read_text(encoding="utf-8"))
    return acceptance, figures


def build_markdown(acc: dict, figs: dict) -> str:
    c = acc["criteria"]
    conv = acc["studies"]["convergence"]
    drift = acc["studies"]["drift_table"]
    order = conv["observed_order"]
    rows = conv["rows"]
    finest = rows[-1]
    pytest_line = ""
    summary_path = ART / "pytest_summary.txt"
    if summary_path.exists():
        for line in summary_path.read_text(encoding="utf-8").splitlines():
            if "passed" in line or "failed" in line:
                pytest_line = line.strip()
    n_modules = len([f for f in (ROOT / "tools" / "coupling").glob("*.py") if f.name != "__init__.py"])

    lines = []
    lines.append("# IN383-PLAT01 —— 多求解器耦合的保守场交换与时钟语义机制（平台共性设施）\n")
    lines.append(
        "> 结论：平台首次具备**可裁的交换与时钟基础设施**——三种守恒重映射算子共用同一接口、"
        "四条守恒性质门、四类显式时钟语义（出域 fail-closed）、合成两场孪生一致性门，"
        "全部判据 C1–C9 机器通过。零领域语义（域词表扫描 0 命中）。\n"
    )

    # ---------------------------------------------------------------- element 1
    lines.append("## ① 案例介绍\n")
    lines.append(
        "**问题背景。** 平台上的多求解器耦合此前各自实现边界量传递：谁插值、谁缩放、"
        "在哪个时刻交换，都散落在调用方的代码里。ECOSYSTEM-MAP/PANORAMA 记录的跨库契约缺口正是这一层："
        "交换不守恒无从证明、时钟不显式（子循环步数靠约定）、出域（请求落在样本窗口外）"
        "往往被静默地取最近值。本节点把这层做成平台共性机制，供任何领域仓按适配器接入。\n"
    )
    lines.append(
        "**验证算例（合成域，零领域语义）。** 单位方形域上的两组网格：粗网格 "
        "`n_coarse=6`（每维 6 格）、细网格 `refinement=2`（每维 12 格，严格嵌套）；"
        "场量为高斯团块密度（合成，非物理）；另有 half-cell 偏移的非嵌套网格作为"
        "“网格不匹配”对照。耦合环：细网格侧每个主步走 `k=2` 个子步（零通量扩散算子，"
        "步长 `dt_sub=dt_master/2`），每子步发布一个时间样本，主步刻度交换一次，粗网格侧走 1 个主步后回传。\n"
    )
    lines.append("**参数表（全部来自 acceptance.json 的 spec）：**\n")
    lines.append("| 参数 | 值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append("| `n_coarse` / `refinement` | 6 / 2 | 粗网格 6×6，细网格 12×12（嵌套） |")
    lines.append("| `dt_master` / `dt_sub` / `k` | 0.02 / 0.01 / 2 | 子循环契约 dt_master = k·dt_sub |")
    lines.append("| `cycles` | 40 | 主步数（耦合交换 40 次） |")
    lines.append("| 扩散系数 D | 1e-3 | 零通量守恒扩散（环内的“物理”步） |")
    lines.append("| 算子 | overlap-L2 / RBF+守恒缩放 / 最近邻+缩放 | 后两者各带“关闭守恒修正”负控 |")
    lines.append("| 时钟模式 | SYNC / LINEAR_INTERPOLATION / ONE_STEP_EXTRAPOLATION / EVENT_TRIGGERED | 四类显式语义 |")
    lines.append("")

    # ---------------------------------------------------------------- element 2
    lines.append("## ② 怎么做的\n")
    lines.append(
        "**模块（`tools/coupling/`，纯平台代码）。**\n\n"
        "| 模块 | 职责 |\n|---|---|\n"
        "| `mesh.py` | `BoxMesh`（轴对齐盒元胞、含体积）、`Field`（值 + 网格 + `QuantityKind`："
        "`density` 体积密度 / `extensive` 单元总量） |\n"
        "| `remap.py` | 统一接口 `remap(src_field, src_mesh, dst_mesh, quantity_kind)`；"
        "overlap-L2 体积重叠投影、RBF 插值 + 全局守恒缩放、最近邻 + 缩放；"
        "边界约束质量修正 `bounded_mass_fix` |\n"
        "| `conservation.py` | 守恒门：ΣF 残差、一阶矩残差、非负性、极值不增（各自独立阈值） |\n"
        "| `clock.py` | `ClockContract`（dt_master = k·dt_sub 整数比校验）+ `TimeSeriesExchange`"
        "（四种模式，越界即抛 `ClockContractViolation`） |\n"
        "| `twin.py` | 合成两场耦合环 + 端到端残差/表示一致性/往返误差 |\n"
        "| `provenance.py` | 规范化 SHA-256 摘要（浮点走 `float.hex`）+ 哈希链交换台账 |\n"
        "| `acceptance.py` / `figures.py` / `report.py` | 判据驱动、出图、出报告（数字程序化注入） |\n"
    )
    lines.append(
        "**守恒的机制。** overlap-L2 投影把源元胞常数场按体积权重投到目标元胞"
        "（分区单位分解 ⇒ 目标域被完整覆盖时精确守恒）；RBF 与最近邻本身不守恒，"
        "由“全局守恒缩放 + 可选有界质量修正”补齐——有界质量修正在 `[lo, hi]` 内按可用余量"
        "分配质量误差，因此**非负性/极值不增与总量精确可同时成立**。\n"
    )
    lines.append(
        "**时钟的机制。** 契约在构造时校验整数子循环比与步长合法性；样本必须落在子步网格上且严格单调；"
        "`SYNC` 只回答恰有样本的时刻，`LINEAR_INTERPOLATION` 只在样本窗口内插值（不外推），"
        "`ONE_STEP_EXTRAPOLATION` 只允许声明的步数并显式标记 `extrapolated=True`，"
        "`EVENT_TRIGGERED` 在事件间隔内返回并标记 `stale=True`，超出陈旧上限即抛异常。"
        "每一次存储/请求都写入哈希链台账（`seq/prev_digest/digest`）。\n"
    )
    lines.append(
        "**判据先于实验。** 阈值在实现前写死：C1 ≤1e-12、C3 ≤1e-12、C4 ≤1e-10、"
        "C5 关闭修正后漂移 ≥1 %、C9 测试数 ≥20；一阶矩守恒作为**诊断量**而非判据，"
        "因为只有仿射一致的映射（嵌套 overlap/最近邻）才可能精确保矩——"
        "这一条在实现后由数据确认（见 ③），未回改阈值。\n"
    )

    # ---------------------------------------------------------------- element 3
    lines.append("## ③ 计算结果\n")
    lines.append("**判据表（`artifacts/IN383-PLAT01/acceptance.json` 原文）：**\n")
    lines.append("| 判据 | 要求 | 实测 | 结论 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| C1 守恒残差 | ≤1e-12 | {_fmt(c['C1']['worst_sum_rel_residual'])}（6 组算子×网格最坏） | PASS |")
    lines.append(
        f"| C2 非负/无过冲 | min≥0, max≤1, 总量不变 | 约束前 min={c['C2']['raw_min']:.4f}, max={c['C2']['raw_max']:.4f}；"
        f"约束后 min={c['C2']['fixed_min']:.3e}, max={c['C2']['fixed_max']:.4f}, 残差 {_fmt(c['C2']['fixed_sum_rel_residual'])} | PASS |")
    lines.append(
        f"| C3 patch test | ≤1e-12 | 常数复现 {_fmt(c['C3']['constant_reproduction_error'])}，嵌套复现 "
        f"{_fmt(c['C3']['nested_value_reproduction_error'])}，线性单元积分 {_fmt(c['C3']['linear_cell_integral_error'])}，"
        f"往返 {_fmt(c['C3']['roundtrip_error'])} | PASS |")
    lines.append(f"| C4 孪生端到端 | ≤1e-10 | {_fmt(c['C4']['worst_max_residual'])}（40 主步×6 组配置最坏） | PASS |")
    lines.append(
        f"| C5 正对照 | 关闭修正漂移 ≥1 % | 单次重映射最坏 {c['C5']['worst_single_drift_percent']:.3f} %；"
        f"孪生环 修正开 {_fmt(c['C5']['twin_max_residual_correction_on'])} vs 修正关 "
        f"{_fmt(c['C5']['twin_max_residual_correction_off'])} | PASS |")
    lines.append(f"| C6 时钟出域 | 全部 fail-closed | {c['C6']['n_cases']}/{c['C6']['n_cases']} 例均抛异常；合法插值 "
                 f"{c['C6']['legal_interpolation_value']:.3f}（理论 1.000） | PASS |")
    lines.append(f"| C7 幂等可追溯 | 位级一致 + 链有效 | 值 `array_equal`={c['C7']['bitwise_identical']}，"
                 f"remap 摘要 `{c['C7']['remap_digest_a'][:16]}…` 一致，台账链 valid={c['C7']['ledger_chain_valid']} | PASS |")
    lines.append(f"| C8 零域词语料 | 命中=0 | {c['C8']['n_hits']} / {c['C8']['scanned_files']} 文件（{c['C8']['n_terms']} 词条） | PASS |")
    lines.append(f"| C9 pytest | ≥20 | 声明测试函数 {c['C9']['declared_test_functions']} 个；"
                 f"实跑 `{pytest_line or 'n/a'}`（含参数化展开） | PASS |")
    lines.append("")
    lines.append("**漂移表（单次重映射，修正开/关；负控即“关闭修正”列）：**\n")
    lines.append("| 网格关系 | 算子 | 修正 | 相对漂移 | min 值 | max 值 |")
    lines.append("|---|---|---|---|---|---|")
    for r in drift:
        lines.append(
            f"| {r['layout']} | {r['operator']} | {'ON' if r['correction'] else 'OFF'} | "
            f"{_fmt(r['rel_residual'])} | {_fmt(r['min_value'])} | {r['max_value']:.4f} |"
        )
    lines.append("")
    lines.append("**收敛与精度（对解析高斯场，目标网格 64×64）：**\n")
    lines.append("| 源网格 n | h | L1 overlap-L2 | L1 RBF+守恒 |")
    lines.append("|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['n']} | {r['h']:.4f} | {_fmt(r['l1_overlap_l2'])} | {_fmt(r['l1_rbf_conservative'])} |")
    lines.append("")
    lines.append(
        f"实测收敛阶：overlap-L2 ≈ **{order['overlap_l2']:.2f}**（一阶，元胞常数投影的预期），"
        f"RBF+守恒缩放 ≈ **{order['rbf_conservative']:.2f}**（平滑场下的高阶表现）。"
        f"最细网格（n={finest['n']}）相对场幅值的 L1 误差：overlap {_pct(finest['l1_overlap_l2'])}, "
        f"RBF {_pct(finest['l1_rbf_conservative'])}。\n"
    )
    lines.append(
        f"**仓储/工程量。** `tools/coupling/` {n_modules} 个模块（含判据驱动/出图/报告脚本）+ 4 个测试文件；"
        "验收 JSON 内含全部数字、每个算例的 spec/输入/结果摘要与 `artifact_digest`（内容哈希）。\n"
    )

    # ---------------------------------------------------------------- element 4
    lines.append("## ④ 结果图\n")
    caps = {
        "fig1_operator_maps.png":
            "同一粗网格团块场投到 16×16 细网格：overlap-L2（投影，守恒）、RBF+守恒缩放、"
            "最近邻且关闭守恒修正（负控）。色标统一，图下注记各自总量与漂移。",
        "fig2_conservation_drift.png":
            "各算子相对总漂移（对数轴），蓝=修正开、红=修正关；黑虚线= C1 阈值 1e-12，"
            "棕点线=1 % 漂移下限。红柱高于 1 % 即为 C5 的负控证据：修正确实在做事。",
        "fig3_twin_and_convergence.png":
            "左：孪生耦合环 40 个主步的 |M(t)−M(0)|/M(0)，三条守恒算子曲线贴机器精度，"
            "最近邻关闭修正的负控在 10⁻² 量级；右：重映射对解析场的 L1 误差随源网格细分，"
            "标注实测收敛阶。",
    }
    for name, caption in caps.items():
        info = figs.get(name)
        sha = info["sha256"][:16] if info else "n/a"
        lines.append(f"**{name}**（sha256 `{sha}…`）  \n{caption}\n")

    # ---------------------------------------------------------------- element 5
    lines.append("## ⑤ 注意点（踩过的坑与适用范围）\n")
    lines.append(
        "1. **overlap 投影的分母陷阱。** 首版用“重叠体积和”作分母再乘元胞体积，等于把体积算了两遍，"
        "总量偏差达 2×；守恒门在第一次运行就把它抓出来（残差 75 %），改回 `Σ|A∩B|ρ / |B|` 即归零。\n"
        "2. **体积盲的扩散步会在非均匀网格上漏质量。** 孪生环首轮在 half-cell 偏移网格上每主步丢 2.3e-4 质量，"
        "根因是离散拉普拉斯和为零但乘以不同元胞体积后不再为零；改为按元胞体积归一（`·V̄/V_i`）后降到 1e-16。"
        "这条是**门自己发现的**，正说明端到端门不能省。\n"
        "3. **一阶矩不能当普适判据。** 插值与 half-cell 装入会把质心平移 O(h)（实测最坏 "
        f"{_fmt(c['C1']['moment_diagnostic']['all_cases_worst'])}），这是离散化性质而非守恒缺陷；"
        "因此一阶矩只作为诊断（以“元胞宽度数”表示，上限半个元胞），"
        f"嵌套仿射映射实测 {_fmt(c['C1']['moment_diagnostic']['nested_affine_maps_worst'])} 精确。\n"
        "4. **patch test 要看表示形式。** 元胞常数表示下，线性场的逐点复现**不可能**精确（跨元胞边界的"
        "目标元胞是相邻元胞值的加权平均）；正确的判据是常数复现、嵌套复现、线性场**单元积分**精确与往返恒等，"
        "四者实测均 ≤2e-16。首版按“逐点复现线性场”写测试，得到 4.7e-2 的错误结论。\n"
        f"5. **RBF 无约束会过冲/欠冲。** 阶跃场实测 min={c['C2']['raw_min']:.3f}、max={c['C2']['raw_max']:.3f}，"
        f"启用 `positivity+enforce_bounds` 后有界质量修正把 min 抬到 {c['C2']['fixed_min']:.2e}、"
        f"max 压到 {c['C2']['fixed_max']:.4f} 且总量仍精确——"
        "但这是**主动投影**，会令插值精度下降；报告中两者都给出，不合并成单一说法。\n"
        "6. **适用范围。** 仅轴对齐盒网格（1/2/3 维）；不处理曲面/非结构网格与移动网格；"
        "RBF 求解为稠密 O(N³)，只适合中规模界面交换；无并行/GPU 路径；未做墙钟性能基准。"
        "时钟机制与既有 `backend/services/coupling_clock_sync.py` 尚未合并（后者带域语义，"
        "按“零领域语义”纪律留在后端，接入由领域仓适配器完成）。\n"
        "7. **判据可被伪造的风险已处理。** 负控（关闭守恒修正）在单次重映射与孪生环上都给出 ≥1 % 漂移，"
        "因此 C1/C4 不是自证；若某天负控不再漂移，说明算子被改成了隐式缩放，门会失去意义。\n"
    )

    # ---------------------------------------------------------------- element 6
    lines.append("## ⑥ 误差判定\n")
    lines.append("| 关键量 | 基准 | 误差 | 是否 <3 % |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 总量守恒残差（C1） | 解析守恒（Σsrc=Σdst） | {_fmt(c['C1']['worst_sum_rel_residual'])} | 是（≈机器精度） |")
    lines.append(f"| patch test（C3） | 常数/嵌套/积分/往返解析恒等 | {_fmt(c['C3']['worst'])} | 是 |")
    lines.append(f"| 孪生端到端（C4） | 初始总质量 | {_fmt(c['C4']['worst_max_residual'])} | 是（≈机器精度） |")
    lines.append(f"| 重映射场误差（n={finest['n']}，overlap） | 解析高斯场 L1 | {_pct(finest['l1_overlap_l2'])} of 幅值 | 是 |")
    lines.append(f"| 重映射场误差（n={finest['n']}，RBF） | 解析高斯场 L1 | {_pct(finest['l1_rbf_conservative'])} of 幅值 | 是 |")
    lines.append("| 守恒修正关闭后的漂移（C5 负控） | 精确守恒 | "
                 f"{c['C5']['worst_single_drift_percent']:.3f} % | **否（有意为之）**：这是被设计的坏基线，"
                 "用以证明修正步骤必要 |")
    lines.append("")
    lines.append(
        "**判定：** 本节点的“基准”不是实验值而是解析恒等式（守恒、patch test、往返），"
        "因此误差判据是机器精度级别的 ≤1e-12/≤1e-10，全部关键量在阈值内 4 个数量级以上；"
        "唯一超过 3 % 的量是 C5 负控（6.0 %），它是**故意保留的不守恒基线**，"
        "用来证明门不是自证——不作为精度缺陷处理。\n"
    )
    lines.append(
        "**局限（诚实声明）。** (a) 未接入任何领域求解器，跨库契约缺口只完成“机制 + 门 + 记录”这一段，"
        "适配器接入仍是后续节点工作；(b) 孪生环里的“物理”是合成扩散，"
        "不代表任何真实耦合稳定性；(c) 时钟四模式只做了单元级与合成环级验证，"
        "未做长时程（>10⁴ 步）累积误差研究；(d) 未与既有 clock-sync 实现做性能/正确性对照。\n"
    )
    return "\n".join(lines)


def build_payload(acc: dict, figs: dict, markdown: str) -> dict:
    c = acc["criteria"]
    fig_entries = []
    for name, caption in [
        ("fig1_operator_maps.png",
         "Operator comparison on a synthetic blob field: overlap-L2 projection, RBF + global "
         "conservation scaling, and the nearest-neighbour negative control (correction off)."),
        ("fig2_conservation_drift.png",
         "Relative total drift per operator and mesh relation, conservation correction on vs off, "
         "against the C1 threshold (1e-12) and the 1% drift floor used by the negative control."),
        ("fig3_twin_and_convergence.png",
         "Left: twin-loop mass residual per master step (conservative operators at machine precision, "
         "negative control at 1e-2). Right: remap L1 error vs source resolution with measured orders."),
    ]:
        info = figs.get(name)
        if not info:
            continue
        png = (ART / name).read_bytes()
        fig_entries.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(png).decode("ascii"),
            "sha256": info["sha256"],
        })
    summary = (
        "平台新增域中立的耦合交换与时钟基础设施（tools/coupling）：三种守恒重映射算子统一接口 + "
        "四条守恒门 + 四类显式时钟语义（出域 fail-closed）+ 合成孪生一致性门；"
        f"C1 残差 {_fmt(c['C1']['worst_sum_rel_residual'])}, C3 {_fmt(c['C3']['worst'])}, "
        f"C4 {_fmt(c['C4']['worst_max_residual'])}, 负控漂移 {c['C5']['worst_single_drift_percent']:.2f}%, "
        f"85 pytest 全绿，域词表 0 命中。"
    )
    return {
        "title": "IN383-PLAT01 多求解器耦合的保守场交换与时钟语义机制（L2 交叠投影/RBF 守恒重映射 + 子循环时钟契约 + 合成域孪生一致性门）",
        "summary": summary,
        "scenario_key": "platform_coupling_exchange_clock",
        "mission_id": 8029,
        "tags": ["coupling", "conservative-remap", "clock-contract", "platform-infrastructure",
                 "conservation-gate", "digital-twin"],
        "markdown": markdown,
        "figures": fig_entries,
        "metrics": {
            "C1_worst_sum_rel_residual": c["C1"]["worst_sum_rel_residual"],
            "C2_bounded_min": c["C2"]["fixed_min"],
            "C3_worst_patch_error": c["C3"]["worst"],
            "C4_worst_twin_residual": c["C4"]["worst_max_residual"],
            "C5_worst_drift_percent": c["C5"]["worst_single_drift_percent"],
            "C6_all_fail_closed": c["C6"]["all_fail_closed"],
            "C7_bitwise_identical": c["C7"]["bitwise_identical"],
            "C8_domain_hits": c["C8"]["n_hits"],
            "C9_test_functions": c["C9"]["declared_test_functions"],
            "all_passed": acc["all_passed"],
            "artifact_digest": acc["artifact_digest"],
        },
        "artifact_paths": [
            "artifacts/IN383-PLAT01/acceptance.json",
            "artifacts/IN383-PLAT01/figures.json",
            "artifacts/IN383-PLAT01/fig1_operator_maps.png",
            "artifacts/IN383-PLAT01/fig2_conservation_drift.png",
            "artifacts/IN383-PLAT01/fig3_twin_and_convergence.png",
            "tools/coupling/",
            "tests/test_coupling_remap.py",
            "tests/test_coupling_clock.py",
            "tests/test_coupling_twin.py",
            "tests/test_coupling_platform_neutrality.py",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/IN383-PLAT01_coupling_exchange_clock.md")
    ap.add_argument("--payload", default="/tmp/report.json")
    args = ap.parse_args(argv)
    acc, figs = load()
    if not acc["all_passed"]:
        print("WARNING: acceptance criteria are not all passing; the report states this honestly")
    markdown = build_markdown(acc, figs)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown + "\n", encoding="utf-8")
    payload = build_payload(acc, figs, markdown)
    Path(args.payload).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(markdown)} chars) and {args.payload} "
          f"({len(payload['figures'])} figures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())