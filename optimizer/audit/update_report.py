"""把 m1610 的重跑结果程序化注入 ``optimizer/REPORT.md``（零手抄）。

回答两件事：
1. m1601 判 MOCK 的旧结论（旧 REPORT.md 的 §3/§9）**作废**，指向新审计文档；
2. 新的 CMA-ES 重跑数字（objective 已委托正典 Holtrop-1984）全部从
   ``optimizer/runs/opt_run_holtrop1984/optimization_result.json`` 与
   ``artifacts/optimizer_objective_audit/optimizer_objective_audit.json`` 读出。

用法： python3 -m optimizer.audit.update_report
幂等：banner 块用 HTML 注释标记包夹，重复运行只替换块内内容。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "optimizer" / "REPORT.md"
RUN_JSON = ROOT / "optimizer" / "runs" / "opt_run_holtrop1984" / "optimization_result.json"
AUDIT_JSON = ROOT / "artifacts" / "optimizer_objective_audit" / "optimizer_objective_audit.json"
OLD_RUNS = ("opt_run", "repro_test")

START = "<!-- M1610-BANNER-START -->"
END = "<!-- M1610-BANNER-END -->"
SUPERSEDE_MARK = "> ⚠ **旧结论作废（m1610）**"


def _f(v, nd=6):
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e5):
        return f"{v:.{nd}e}"
    return f"{v:.{nd}g}" if isinstance(v, (int, float)) else str(v)


def banner() -> str:
    run = json.loads(RUN_JSON.read_text())
    aud = json.loads(AUDIT_JSON.read_text())
    meta = run["metadata"]
    by = {c["id"]: c for c in aud["checks"]}
    lby = {c["id"]: c for c in aud["legacy_track"]["checks"]}
    live_c5 = by["C5"]["values"]
    leg_c5 = lby["C5"]["values"]
    best = run["top5_candidates"][0]
    comp = best["components"]

    lines = [
        START,
        "",
        "## 0. m1610 修订：目标函数物理内核改为委托正典 Holtrop-1984（重跑）",
        "",
        "**m1601 复核结论（本文件 §3/§9 的旧数字）作废**：彼时目标函数 `evaluate_resistance`",
        "的 Ct 恒等于 ITTC-57 Cf（Cr/Cf≈3e-5 vs 正典 0.335），相关补贴 Ca 以 [m²] 量加到 [N]，",
        "Ct 相对正典 Holtrop-1984 误差 %s%%，证据等级 MOCK。m1610 起物理内核委托" % _f(leg_c5["err_pct_Ct"], 4),
        "`backend/api/holtrop1984.py`（agent/m1553 落地），旧手拟链条仅作 fallback 且继续标注 MOCK。",
        "复核见 `docs/verification/optimizer-objective-physics-audit.md`。",
        "",
        "### 重跑设置（与 §2 相同，仅物理内核更换）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 目标函数证据等级 | **{run['objective_evidence_level']}** |",
        f"| 物理来源 | `{run['objective_physics_source']}` |",
        f"| seed / 预算 / patience | {meta['seed']} / {meta['max_evals']} / {meta['patience']} |",
        f"| 设计 Fn | {_f(meta['design_Fn'], 3)} |",
        f"| 实际评估次数 / 代数 | {meta['total_evaluations']} / {meta['generations']} |",
        f"| 收敛 | {meta['converged']}（{meta['convergence_reason']}） |",
        f"| 结果文件 | `optimizer/runs/opt_run_holtrop1984/optimization_result.json` |",
        "",
        "### 重跑结果",
        "",
        "| 量 | 旧（MOCK 手拟链） | 新（正典 Holtrop-1984） |",
        "|---|---|---|",
        f"| Baseline Ct | 0.00285303（= 裸 ITTC-57 Cf） | {_f(run['baseline']['Ct'])} |",
        f"| Best Ct | 0.00285299 | {_f(meta['best_Ct'])} |",
        f"| 改善 | 1.4e-05（0.0014%，人工乘子读数） | {_f(meta['improvement_pct'], 4)}% |",
        f"| Ct 相对误差 vs 正典 | {_f(leg_c5['err_pct_Ct'], 4)}% | {_f(live_c5['err_pct_Ct'], 4)}% |",
        "",
        "最优设计点（归一化参数）与阻力分解：",
        "",
        "| 参数 | 值 |",
        "|---|---|",
    ]
    for k, v in best["params_named"].items():
        lines.append(f"| {k} | {_f(v, 4)} |")
    lines += [
        "",
        "| 分量（设计点） | 值 |",
        "|---|---|",
        f"| Cf（ITTC-57） | {_f(comp['Cf'])} |",
        f"| (1+k)Rf / Rf | {_f(comp['Rv_N'], 5)} / {_f(comp['Rf_N'], 5)} N |",
        f"| Rw / Rb / Rtr | {_f(comp['Rw_N'], 5)} / {_f(comp['Rb_N'], 5)} / {_f(comp['Rtr_N'], 5)} N |",
        f"| Ra（相关补贴） | {_f(comp['Ra_N'], 5)} N |",
        f"| Rt | {_f(comp['Rt_N'], 5)} N |",
        f"| Ct = Rt/(½ρV²S) | {_f(comp['Ct'])} |",
        f"| 湿面积 S / (1+k) | {_f(comp['S_wet_m2'], 5)} m² / {_f(comp['form_factor_1pk'], 5)} |",
        "",
        "**解读**：改善幅度从 0.0014% 变为 " + _f(meta["improvement_pct"], 3) + "% 不是回归——"
        "旧值来自人工乘子（`bow_factor` /"
        "`bulb_factor` / `stem_factor` 的线性折扣），新值来自 Cwp / Abt / hb / LCB 四个真实",
        "水动力通路；deck camber 与肩部曲率在 Holtrop-1984 中没有静水阻力通路，故被最优解自由",
        "取值。该改善应读作\"型线微调量级\"，不得当作可获利的节能百分比。",
        "",
        END,
    ]
    return "\n".join(lines)


def main() -> int:
    if not RUN_JSON.exists():
        print(f"缺少重跑结果：{RUN_JSON}")
        return 2
    text = REPORT.read_text()
    block = banner()
    if START in text and END in text:
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        text = head + block + tail
    else:
        marker = "\n---\n"
        idx = text.find(marker)
        text = (text[:idx + len(marker)] + "\n" + block + "\n" + text[idx + len(marker):]
                if idx != -1 else text + "\n" + block + "\n")

    if SUPERSEDE_MARK not in text:
        text = text.replace(
            "## 3. Computational Results",
            SUPERSEDE_MARK + "：下表的 Ct 来自旧手拟链条（MOCK），见 §0 的重跑结果。\n\n"
            "## 3. Computational Results", 1)
    REPORT.write_text(text)

    note = ("> ⚠ **作废（m1610）**：本目录的优化结果由旧手拟阻力链（证据等级 MOCK，Ct≡ITTC-57 Cf）"
            "产生，不得作为能力台账证据引用。重跑结果见 `../opt_run_holtrop1984/`"
            "（目标函数已委托正典 Holtrop-1984，证据等级 REAL）。\n")
    for name in OLD_RUNS:
        d = ROOT / "optimizer" / "runs" / name
        if d.is_dir():
            (d / "SUPERSEDED.md").write_text(note)
    print(f"wrote {REPORT.relative_to(ROOT)}（banner 注入）+ 旧 run 目录作废标记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())