#!/usr/bin/env python3
"""耦合守恒契约框架的机器验收运行器 (p307w1-n04).

跑齐战役判据 (a)~(g), 程序化落盘 ``artifacts/coupling_conservation/acceptance.json``
+ 图 ``conservation_acceptance.png``。所有数字从被测 API 直接取, 零手抄。

用法::

    python3 tools/coupling_conservation/run_acceptance.py            # 全量
    python3 tools/coupling_conservation/run_acceptance.py --no-figure # 只出 JSON

合成数据全部是通用量纲示例 (kg/s, W, N*m) 与抽象节点名 (A/B/C/D), 不指向任何
具体学科; 域词表从 ``config/domain_word_denylist.json`` 以数据形式注入。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.coupling_conservation import (  # noqa: E402
    EXPLICIT,
    IMPLICIT,
    SUBSTEP,
    AcceptanceReport,
    ClockEvent,
    CouplingChain,
    Criterion,
    ExchangePort,
    FluxLedger,
    FluxSample,
    INWARD,
    OUTWARD,
    TransferLaw,
    adjudicate,
    check_balance,
    check_exchange,
    check_transitivity,
    dim,
    substep_schedule,
)
from services.coupling_conservation import rel_residual  # noqa: E402

MASS_FLOW = dim(M=1, T=-1)
ENERGY_FLOW = dim(M=1, L=2, T=-3)
TORQUE = dim(M=1, L=2, T=-2)
ENERGY = dim(M=1, L=2, T=-2)
ART = ROOT / "artifacts" / "coupling_conservation"
TOL = Fraction(1, 10 ** 10)
TRANS_TOL = Fraction(1, 10 ** 12)


# ---------------------------------------------------------------------------
# (a) 量纲检查检出率
# ---------------------------------------------------------------------------
def criterion_a() -> Criterion:
    rng = random.Random(30704)
    injected = detected = correct_clean = false_alarm = 0
    for _ in range(300):
        base = [rng.randint(-2, 2) for _ in range(7)]
        p_dim = tuple(base)
        if rng.random() < 0.5:
            idx = rng.randrange(7)
            base[idx] += rng.choice([-1, 1, 2])
            c_dim = tuple(base)
        else:
            c_dim = p_dim
        p = ExchangePort("p", "producer", "q", p_dim, "", 0, None, OUTWARD)
        c = ExchangePort("c", "consumer", "q", c_dim, "", 0, None, INWARD)
        codes = check_exchange(p, c).codes
        hit = "DIM_MISMATCH" in codes
        if p_dim != c_dim:
            injected += 1
            detected += int(hit)
        else:
            correct_clean += int(not hit)
            false_alarm += int(hit)

    # 量纲盲区: 同量纲不同守恒量类别 (力矩 vs 能量)
    p = ExchangePort("p", "producer", "torque", TORQUE, "N*m", 0, None, OUTWARD)
    c = ExchangePort("c", "consumer", "energy", ENERGY, "J", 0, None, INWARD)
    kind_check = check_exchange(p, c)

    rate = detected / injected if injected else 0.0
    return Criterion(
        criterion_id="a",
        name="量纲检查: 注入不匹配 100% 检出 (含同量纲类别盲区)",
        passed=(rate == 1.0 and false_alarm == 0 and "QUANTITY_KIND" in kind_check.codes),
        numbers={
            "n_injected_mismatch": injected,
            "n_detected": detected,
            "detection_rate": rate,
            "n_clean_pairs": correct_clean,
            "false_alarms": false_alarm,
            "same_dim_kind_violation_codes": list(kind_check.codes),
        },
        notes="正向对照 (量纲相同不报 DIM_MISMATCH) 与同量纲类别检查同时成立才判 PASS",
    )


# ---------------------------------------------------------------------------
# (b) 解析守恒界面的残差
# ---------------------------------------------------------------------------
def _face_samples(quantity, d, total, n_faces, sign, side):
    """把总通量精确分到 n 个面 (有理数, 严格守恒)。"""
    w = Fraction(1, n_faces)
    return [
        FluxSample(quantity, d, sign * total * w, side, time=Fraction(k, n_faces))
        for k in range(n_faces)
    ]


def criterion_b() -> Criterion:
    total = Fraction(7, 3)                      # 解析可精确表示的总通量
    samples = _face_samples("mass_flow", MASS_FLOW, total, 4, +1, "producer")
    samples += _face_samples("mass_flow", MASS_FLOW, total, 7, -1, "consumer")
    bal = check_balance(samples, tolerance=TOL)

    # 多步累积: 40 步, 每步两侧通量不同但两侧严格相等 -> 累积残差必须为 0
    ledger = FluxLedger("mass_flow", MASS_FLOW)
    for k in range(1, 41):
        v = Fraction(k, 7)
        ledger.record([
            FluxSample("mass_flow", MASS_FLOW, v, "producer"),
            FluxSample("mass_flow", MASS_FLOW, -v, "consumer"),
        ])

    # 能量通量的同一性质 (换量纲, 不改代码)
    e_total = Fraction(11, 5)
    e_samples = _face_samples("energy_flow", ENERGY_FLOW, e_total, 3, +1, "producer")
    e_samples += _face_samples("energy_flow", ENERGY_FLOW, e_total, 5, -1, "consumer")
    e_bal = check_balance(e_samples, tolerance=TOL)

    passed = (
        bal.rel_residual <= TOL
        and ledger.cumulative_rel_residual <= TOL
        and e_bal.rel_residual <= TOL
    )
    return Criterion(
        criterion_id="b",
        name="守恒验证: 解析守恒界面残差 < 1e-10",
        passed=passed,
        numbers={
            "interface_rel_residual": float(bal.rel_residual),
            "interface_rel_residual_exact": str(bal.rel_residual),
            "cumulative_rel_residual_40_steps": float(ledger.cumulative_rel_residual),
            "energy_interface_rel_residual": float(e_bal.rel_residual),
            "tolerance": float(TOL),
            "n_faces_producer": 4,
            "n_faces_consumer": 7,
        },
        notes="精确有理数记账下残差恒为 0 (优于判据 1e-10 整整 10 个量级)",
    )


# ---------------------------------------------------------------------------
# (c) 守恒违规检出与量化
# ---------------------------------------------------------------------------
def criterion_c() -> Criterion:
    base = 1
    rows = []
    for exp in (3, 6, 9, 12):
        leak = Fraction(1, 10 ** exp)
        samples = [
            FluxSample("mass_flow", MASS_FLOW, Fraction(base), "producer"),
            FluxSample("mass_flow", MASS_FLOW, -(Fraction(base) - leak), "consumer"),
        ]
        bal = check_balance(samples, tolerance=TOL)
        expected = leak / (Fraction(2) - leak)
        rows.append({
            "injected_leak": float(leak),
            "measured_rel_residual": float(bal.rel_residual),
            "expected_rel_residual": float(expected),
            "quantification_error": float(abs(bal.rel_residual - expected)),
            "detected": bool(not bal.passed),
            "excess_over_tolerance": float(bal.excess),
        })
    detectable = [r for r in rows if r["injected_leak"] >= 1e-9]
    detected_rate = sum(1 for r in detectable if r["detected"]) / len(detectable)
    max_quant_err = max(r["quantification_error"] for r in rows)
    # 1e-12 的注入低于 1e-10 容差, 框架不应误报为"守恒破坏" (灵敏度下限如实登记)
    sub_tol_row = rows[-1]
    return Criterion(
        criterion_id="c",
        name="守恒违规检出: 注入不守恒 100% 检出并量化残差",
        passed=(detected_rate == 1.0 and max_quant_err == 0.0),
        numbers={
            "injections": rows,
            "detection_rate_above_tolerance": detected_rate,
            "max_quantification_error": max_quant_err,
            "sensitivity_floor": float(TOL),
            "sub_tolerance_injection_detected": sub_tol_row["detected"],
        },
        notes=(
            "残差与注入泄漏精确相等 (量化误差 0); 1e-12 注入低于 1e-10 容差, "
            "如实登记为灵敏度下限, 不声称检出"
        ),
    )


# ---------------------------------------------------------------------------
# (d) 时钟语义
# ---------------------------------------------------------------------------
def criterion_d() -> Criterion:
    # 显式协议: 注入 3 种时间偏移, 全部必须被检出且 gap 精确等于偏移
    rows = []
    for off in (Fraction(1, 10 ** 3), Fraction(1, 10 ** 6), Fraction(0)):
        ev = ClockEvent(
            producer="A", consumer="B", field="f",
            t_write=off, window=(Fraction(0), Fraction(1)),
            t_read=Fraction(0),
        )
        res = adjudicate([ev], EXPLICIT)
        rows.append({
            "injected_offset": float(off),
            "reported_max_gap": float(res.max_time_gap),
            "gap_error": float(abs(res.max_time_gap - off)),
            "detected": bool(not res.passed),
            "codes": list(res.codes),
        })
    nonzero = [r for r in rows if r["injected_offset"] > 0]
    det_rate = sum(1 for r in nonzero if r["detected"]) / len(nonzero)
    clean_ok = rows[-1]["detected"] is False
    max_gap_err = max(r["gap_error"] for r in rows)

    # 子循环: 对齐通过, 半个细步的错位必须报出精确时间差
    dt_fine = Fraction(1, 100)
    sched = substep_schedule((Fraction(0), Fraction(1, 10)), dt_fine)
    aligned = adjudicate(
        [ClockEvent("A", "B", "f", t, (Fraction(0), Fraction(1, 10)),
                    t_read=Fraction(1, 10), dt_fine=dt_fine) for t in sched[:-1]],
        SUBSTEP,
    )
    unaligned_ev = ClockEvent("A", "B", "f", Fraction(25, 1000),
                              (Fraction(0), Fraction(1, 10)),
                              t_read=Fraction(1, 10), dt_fine=dt_fine)
    unaligned = adjudicate([unaligned_ev], SUBSTEP)

    # 数据竞争: 两个生产者在重叠窗口写同一字段
    race = adjudicate([
        ClockEvent("A", "B", "f", Fraction(0), (Fraction(0), Fraction(1, 2))),
        ClockEvent("C", "B", "f", Fraction(1, 4), (Fraction(1, 4), Fraction(3, 4))),
    ], IMPLICIT)

    passed = (
        det_rate == 1.0 and clean_ok and aligned.passed
        and "UNALIGNED" in unaligned.codes
        and abs(unaligned.max_time_gap - Fraction(5, 1000)) == 0
        and "DATA_RACE" in race.codes
        and max_gap_err == 0.0
    )
    return Criterion(
        criterion_id="d",
        name="时钟语义: 注入时间不同步 100% 检出并报出精确时间差",
        passed=passed,
        numbers={
            "explicit_offset_trials": rows,
            "detection_rate_nonzero": det_rate,
            "max_gap_report_error": max_gap_err,
            "aligned_substep_passed": aligned.passed,
            "unaligned_codes": list(unaligned.codes),
            "unaligned_reported_gap": float(unaligned.max_time_gap),
            "unaligned_injected_gap": 0.005,
            "race_codes": list(race.codes),
            "race_overlap": float(race.max_time_gap),
        },
        notes="gap 与注入偏移精确相等 (Fraction 记账, 无浮点尾数)",
    )


# ---------------------------------------------------------------------------
# (e) 组合验证: 链式 == 直达
# ---------------------------------------------------------------------------
def _three_node_chain(leak=Fraction(0), n_mid_faces=3):
    ch = CouplingChain(nodes=("A", "B", "C"))
    ch.add_law(TransferLaw("A", "B", "mass_flow", MASS_FLOW, MASS_FLOW,
                           unit_scale=Fraction(1, 1000),
                           weights=(Fraction(1, n_mid_faces),) * n_mid_faces,
                           label="kg/g + 面重分布"))
    ch.add_law(TransferLaw("B", "C", "mass_flow", MASS_FLOW, MASS_FLOW,
                           weights=(Fraction(1, 2), Fraction(1, 2)), loss=leak))
    return ch


def criterion_e() -> Criterion:
    direct = TransferLaw("A", "C", "mass_flow", MASS_FLOW, MASS_FLOW,
                         unit_scale=Fraction(1, 1000))
    chain = _three_node_chain()
    ok = check_transitivity(chain, upstream="A", downstream="C",
                            conserved_value=1000, measured_downstream=1,
                            direct_law=direct)
    # 4 节点链 (多一对耦合) 也必须精确传递
    ch4 = CouplingChain(nodes=("A", "B", "C", "D"))
    ch4.add_law(TransferLaw("A", "B", "mass_flow", MASS_FLOW, MASS_FLOW,
                            unit_scale=Fraction(1, 1000)))
    ch4.add_law(TransferLaw("B", "C", "mass_flow", MASS_FLOW, MASS_FLOW,
                            weights=(Fraction(1, 4),) * 4))
    ch4.add_law(TransferLaw("C", "D", "mass_flow", MASS_FLOW, MASS_FLOW,
                            weights=(Fraction(1, 2), Fraction(1, 2))))
    ok4 = check_transitivity(ch4, upstream="A", downstream="D",
                             conserved_value=1000, measured_downstream=1,
                             direct_law=direct)

    leak = Fraction(1, 10 ** 6)
    leaky = check_transitivity(_three_node_chain(leak=leak), upstream="A",
                               downstream="C", conserved_value=1000,
                               measured_downstream=1, direct_law=direct)
    passed = (
        ok.mismatch <= TRANS_TOL and ok.passed
        and ok4.mismatch <= TRANS_TOL and ok4.passed
        and not leaky.passed
        and leaky.mismatch == leak
    )
    return Criterion(
        criterion_id="e",
        name="组合验证: A→B→C 链式残差 == A→C 直达残差 (误差 < 1e-12)",
        passed=passed,
        numbers={
            "chain_3node": ok.to_dict(),
            "chain_4node": ok4.to_dict(),
            "leaky_intermediate": leaky.to_dict(),
            "injected_intermediate_loss": float(leak),
            "transitivity_tol": float(TRANS_TOL),
        },
        notes="守恒转接 (单位换算+面重分布) 下链式与直达残差精确相等; 非守恒中间界面被量化检出",
    )


# ---------------------------------------------------------------------------
# (f) 零领域语义
# ---------------------------------------------------------------------------
def _load_scanner():
    spec = importlib.util.spec_from_file_location(
        "domain_leak_scan", ROOT / "tools" / "audit" / "domain_leak_scan.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def criterion_f() -> Criterion:
    mod = _load_scanner()
    targets = [
        "backend/services/coupling_conservation",
        "tools/coupling_conservation",
        "backend/tests/test_coupling_conservation.py",
    ]
    result = mod.scan(targets, root=ROOT)
    return Criterion(
        criterion_id="f",
        name="零领域语义: 框架源码/测试对平台域词表零命中",
        passed=result["hit_count"] == 0,
        numbers={
            "files_scanned": len(result["files_scanned"]),
            "word_count": result["word_count"],
            "hit_count": result["hit_count"],
            "hits": result["hits"][:10],
            "targets": targets,
        },
        notes="域词表以数据形式来自 config/domain_word_denylist.json, 代码内无领域词条",
        artifacts=["config/domain_word_denylist.json"],
    )


# ---------------------------------------------------------------------------
# (g) pytest 用例数
# ---------------------------------------------------------------------------
def criterion_g() -> Criterion:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_coupling_conservation.py",
         "-q", "--collect-only"],
        cwd=ROOT / "backend", capture_output=True, text=True,
    )
    import re
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    n = 0
    for ln in tail:
        # pytest -q --collect-only 汇总行形如 "tests/xxx.py: 36"
        m = re.search(r"\.py:\s*(\d+)\s*$", ln)
        if m:
            n += int(m.group(1))
    if n == 0:  # 其他 pytest 版本的汇总行
        for ln in reversed(tail):
            m = re.search(r"collected (\d+) item", ln) or re.search(r"(\d+) tests? collected", ln)
            if m:
                n = int(m.group(1))
                break
    return Criterion(
        criterion_id="g",
        name="pytest 用例数 ≥ 20",
        passed=n >= 20,
        numbers={"collected_tests": n, "required_min": 20,
                 "exit_code": proc.returncode},
        artifacts=["backend/tests/test_coupling_conservation.py"],
    )


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def make_figure(report: AcceptanceReport, out: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[warn] matplotlib 不可用, 跳过图: {exc}")
        return None

    by_id = {c.criterion_id: c for c in report.criteria}
    c_rows = by_id["c"].numbers["injections"]
    d_rows = by_id["d"].numbers["explicit_offset_trials"]
    e = by_id["e"].numbers

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))

    ax = axes[0]
    xs = [r["injected_leak"] for r in c_rows]
    ys = [r["measured_rel_residual"] for r in c_rows]
    ax.loglog(xs, ys, "o-", label="measured residual")
    ax.loglog(xs, xs, "k--", lw=1, label="injected leak (identity)")
    ax.axhline(float(TOL), color="r", ls=":", label="tolerance 1e-10")
    ax.set_xlabel("injected relative leak")
    ax.set_ylabel("reported relative residual")
    ax.set_title("(c) violation detection & quantification")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[1]
    xs = [r["injected_offset"] for r in d_rows if r["injected_offset"] > 0]
    ys = [r["reported_max_gap"] for r in d_rows if r["injected_offset"] > 0]
    ax.loglog(xs, ys, "s-", label="reported clock gap")
    ax.loglog(xs, xs, "k--", lw=1, label="injected offset (identity)")
    ax.set_xlabel("injected time offset [s]")
    ax.set_ylabel("reported max time gap [s]")
    ax.set_title("(d) clock semantics adjudication")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[2]
    labels = ["A→B→C\n(conservative)", "A→B→C→D\n(conservative)", "A→B→C\n(leaky mid)"]
    vals = [
        max(float(e["chain_3node"]["mismatch"]), 1e-18),
        max(float(e["chain_4node"]["mismatch"]), 1e-18),
        max(float(e["leaky_intermediate"]["mismatch"]), 1e-18),
    ]
    colors = ["#2ca02c", "#2ca02c", "#d62728"]
    ax.bar(labels, vals, color=colors)
    ax.axhline(float(TRANS_TOL), color="r", ls=":", label="transitivity tol 1e-12")
    ax.set_yscale("log")
    ax.set_ylabel("|chain residual - direct residual|")
    ax.set_title("(e) chain vs direct transitivity")
    ax.legend(fontsize=8)
    for rect, v in zip(ax.patches, vals):
        ax.annotate(f"{v:.1e}", (rect.get_x() + rect.get_width() / 2, v),
                    ha="center", va="bottom", fontsize=8)

    fig.suptitle("Multi-physics coupling conservation contract framework — acceptance (m9997)",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="耦合守恒契约框架机器验收")
    ap.add_argument("--out", default=str(ART / "acceptance.json"))
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args(argv)

    report = AcceptanceReport(
        mission_id="9997",
        meta={
            "node": "p307w1-n04-multi-physics-conservation",
            "module": "backend/services/coupling_conservation",
            "note": "zero domain semantics; domain vocabulary injected as data",
        },
    )
    for fn in (criterion_a, criterion_b, criterion_c, criterion_d,
               criterion_e, criterion_f, criterion_g):
        report.add(fn())

    fig = None
    if not args.no_figure:
        fig = make_figure(report, ART / "conservation_acceptance.png")
        if fig is not None:
            report.criteria[4].artifacts.append(str(fig.relative_to(ROOT)))

    path = report.write(args.out)
    print(report.summary_table())
    print(f"\n报告: {path}  passed={report.passed}  failed={report.failed_ids}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())