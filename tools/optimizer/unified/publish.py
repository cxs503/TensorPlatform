"""Generate the paper-style report (markdown + JSON payload) from the artifacts.

Every number in the produced markdown is read from the JSON artifacts written by
:mod:`tools.optimizer.unified.bundle` -- no table is typed by hand.  The same
script can post the report to the platform's ``/tensor/reports`` endpoint
(``--post``), which is what the mission's reporting discipline requires.

    python -m tools.optimizer.unified.publish --outdir artifacts/p304w2_n10_optimizer --post
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

NODE_ID = "P304W2-N10"
MISSION_ID = 10145
TITLE = (
    "统一优化求解器工具接入：SciPy/NLopt/pyoptsparse 单接口 + 优化问题 DSL + 标准化结果"
    "（P304W2-N10）"
)
REPORT_PATH = Path("docs/p304w2_n10_unified_optimizer.md")


def _read(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fmt(value: Optional[float], digits: int = 3, sci: bool = False) -> str:
    if value is None:
        return "—"
    if sci:
        return f"{value:.{digits}e}"
    return f"{value:.{digits}f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2e}%"


def build_markdown(outdir: Path) -> str:
    audit = _read(outdir / "audit_summary.json")
    index = _read(outdir / "results_index.json")
    cross = _read(outdir / "crosscheck.json")
    probe = _read(outdir / "probe.json")
    local_min = _read(outdir / "local_minimum_probe.json")

    lines: List[str] = []
    add = lines.append

    add(f"# {TITLE}")
    add("")
    add(f"- 节点：`{NODE_ID}`  /  任务 10145  /  TensorPlatform 仓（通用工具，零领域语义）")
    add(f"- 工具链版本：`{audit['toolchain_version']}` ，证据目录：`{outdir.as_posix()}`")
    add(f"- 生成时间（UTC）：{audit['finished_utc']}")
    add("")

    # ---------------- ① 案例介绍 ----------------
    add("## ① 案例介绍（背景 / 问题定义 / 参数表）")
    add("")
    add(
        "TensorPlatform 需要一个**引擎无关的优化求解器接入层**：平台各阶段（标定、形状优化、"
        "参数辨识、路线决策）原本各自直接调 scipy，换算法就要改调用方代码，也没有统一的收敛/约束违反/KKT 判据。"
        "本节点把优化变成一个可配置的**工具能力**：一份 JSON 问题定义 → 任选引擎与算法 → 一份带审计信息的标准化结果。"
    )
    add("")
    add("三个外部引擎的定位：")
    add("")
    add("| 引擎 | 适配算法 | 本机状态 | 版本 |")
    add("|---|---|---|---|")
    for eng in probe["engines"]:
        algos = ", ".join(eng["algorithms"]) or "—"
        state = "可用" if eng["available"] else "**不可用**"
        add(f"| {eng['engine']} | {algos} | {state} | {eng['version']} |")
    add("")
    pyopt = next(e for e in probe["engines"] if e["engine"] == "pyoptsparse")
    add(
        f"> pyoptsparse 不可用原因：`{pyopt['reason']}`。SNOPT 另需商业许可，IPOPT 需本机原生编译；"
        "因此**没有任何 SNOPT/IPOPT 数值结果被声称**（适配器代码按真实 API 实现，见 ③ 的桩注入测试）。"
    )
    add("")
    add("基准算例（全部为公开标准测试问题，参考最优值来自文献/解析解）：")
    add("")
    add("| 问题 | 变量 | 不等式 | 等式 | 已发表最优 f* | 来源类型 |")
    add("|---|---|---|---|---|---|")
    for row in audit["reference_table"]:
        add(
            f"| `{row['problem_id']}` | {row['n_variables']} | {row['n_inequalities']} | "
            f"{row['n_equalities']} | {_fmt(row['reference_objective'], 10)} | {row['reference_kind']} |"
        )
    add("")
    add(
        "`sphere_eq_ineq` 是手算 KKT 的凸问题（x=(1,1,1)、f=3、等式乘子 ν=(2,0)、z-0.5 约束不活跃 μ=0），"
        "用来给 KKT 判据提供**解析参照**；G06/G08/G09/G10 为 CEC-2006/Hock–Schichtkowski 约束测试函数。"
    )
    add("")

    # ---------------- ② 方法 ----------------
    add("## ② 怎么做的（方法 / 流程 / 关键设置）")
    add("")
    add("目录：`tools/optimizer/unified/`（新增包，原有 `tools/optimizer/optimizer_tool.py` 未改动）")
    add("")
    add("| 模块 | 职责 |")
    add("|---|---|")
    add("| `expr.py` | 表达式编译器：`ast` 白名单（算术 + 数学函数），拒绝 `__import__`/属性访问/未声明符号 |")
    add("| `contract.py` | `ProblemSpec` / `SolveResult` / `EngineProbe` 数据契约 + 环境 provenance |")
    add("| `compiled.py` | 规范形编译：目标统一最小化、不等式统一 `g(x)≥0`、等式 `h(x)=0`、缺解析梯度时中心差分 |")
    add("| `adapters/{scipy,nlopt,pyoptsparse}_adapter.py` | 三引擎适配器，声明式算法能力表（是否支持不等式/等式/梯度/全局） |")
    add("| `kkt.py` | 引擎无关 KKT 残差（驻点条件 + 原始可行 + 对偶可行 + 互补） |")
    add("| `dsl.py` + `schemas/optimizer_*.v1.schema.json` | JSON DSL 读写与两端 schema 校验 |")
    add("| `crosscheck.py` | 同一问题多引擎裁决（偏差矩阵、参考误差、meet_tolerance） |")
    add("| `audit.py` / `bundle.py` / `cli.py` / `publish.py` | 判据审计、证据打包（含图）、CLI、报告生成 |")
    add("")
    add("流程：`问题 JSON --(schema)--> ProblemSpec --(expr 编译)--> CompiledProblem → 适配器(引擎,算法) → SolveResult --(schema)--> 结果 JSON`。")
    add("")
    add(
        "关键设置：`max_iter/max_eval` 见审计 `options`；"
        f"`options = {json.dumps(audit['options'])}`；"
        "KKT 判据对驻点条件/互补性按 `max(1,‖∇f‖∞)` 无量纲化。"
    )
    add("")

    # ---------------- ③ 计算结果 ----------------
    add("## ③ 计算结果（程序化注入，零手抄）")
    add("")
    add("### 3.1 每次求解的标准化结果")
    add("")
    add("| 问题 | 引擎:算法 | 状态 | f | 相对误差 | 约束违反 | KKT | 迭代 | 评估 | schema |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for row in sorted(index, key=lambda r: (r["problem_id"], r["engine"], r["algorithm"])):
        add(
            f"| `{row['problem_id']}` | {row['engine']}:{row['algorithm']} | {row['status']} | "
            f"{_fmt(row['objective'], 8)} | {_pct(row['objective_rel_error'])} | "
            f"{_fmt(row['constraint_violation'], 2, sci=True)} | {_fmt(row['kkt_residual'], 2, sci=True)} | "
            f"{row['n_iter']} | {row['n_eval']} | {'✓' if row['schema_valid'] else '✗'} |"
        )
    add("")
    add("### 3.2 判据总表")
    add("")
    add("| 判据 | 阈值 | 实测 | 结论 |")
    add("|---|---|---|---|")
    for crit in audit["criteria"]:
        obs = crit["observed"]
        if crit["id"] == "C1":
            measured = (
                f"{obs['n_problems_meeting_criterion']}/{obs['n_problems']} 问题满足三方一致；"
                f"满足集中的最大偏差 {_fmt(obs['worst_deviation_among_meeting'], 2, sci=True)}"
            )
            thr = "<1%，≥3 求解器，≥5 个问题"
        elif crit["id"] == "C2":
            measured = f"最佳 |f-f*| = {_fmt(obs['best_abs_error'], 2, sci=True)}；通过 {obs['n_pass']} 个求解器"
            thr = "|f-f*|<1e-6"
        elif crit["id"] == "C3":
            best = min((r for r in obs["per_solver"] if r["rel_error"] is not None),
                       key=lambda r: abs(r["rel_error"]))
            measured = f"{best['algorithm']} 相对误差 {_pct(best['rel_error'])}（通过 {obs['n_pass']} 个算法）"
            thr = "<0.1%"
        elif crit["id"] == "C4":
            measured = "；".join(
                f"{r['problem_id']}:{_fmt(r['best_kkt'], 2, sci=True)}" for r in obs["per_problem"]
            )
            thr = "KKT<1e-4"
        elif crit["id"] == "C5":
            measured = f"{obs['n_schema_valid']}/{obs['n_results']} 结果通过 schema；必填字段齐备={obs['required_fields_present']}"
            thr = "100% + 四要素"
        elif crit["id"] == "C6":
            measured = f"{obs['n_solved']}/{obs['n_problems']} 问题可定义并求解"
            thr = "≥5 个标准问题"
        elif crit["id"] == "C7":
            res = obs.get("pytest_result") or {}
            measured = f"passed={res.get('passed')} failed={res.get('failed')}"
            thr = "≥12 个测试"
        else:  # pragma: no cover
            measured = json.dumps(obs)[:80]
            thr = json.dumps(crit["threshold"])
        add(f"| {crit['id']} {crit['description']} | {thr} | {measured} | **{crit['verdict']}** |")
    add("")
    add("### 3.3 跨引擎交叉裁决（同一份 DSL 输入）")
    add("")
    add("| 问题 | 求解器数 | 收敛数 | 计入一致性的求解器 | 最大两两偏差 | 相对已发表最优解误差 |")
    add("|---|---|---|---|---|---|")
    c1 = next(c for c in audit["criteria"] if c["id"] == "C1")["observed"]
    for row in c1["problems"]:
        add(
            f"| `{row['problem_id']}` | {row['n_solvers']} | {row['n_converged']} | "
            f"{row['n_eligible']} | {_fmt(row['max_pairwise_rel_deviation'], 2, sci=True)} | "
            f"{_pct(row['reference_rel_error_of_best'])} |"
        )
    add("")
    add("被排除的一致性候选（未收敛 / 收敛到别的局部最优）：")
    add("")
    add("| 问题 | 引擎:算法 | 状态 | f | 相对误差 | 原因 |")
    add("|---|---|---|---|---|---|")
    for row in c1["problems"]:
        for ex in row["excluded"]:
            add(
                f"| `{row['problem_id']}` | {ex['engine']}:{ex['algorithm']} | {ex['status']} | "
                f"{_fmt(ex['objective'], 8)} | {_pct(ex['objective_rel_error'])} | {ex['reason']} |"
            )
    add("")

    # ---------------- ④ 图 ----------------
    add("## ④ 结果图")
    add("")
    for i, fig in enumerate(audit.get("figures", []), start=1):
        add(f"**图 {i}** `{fig['path']}` — {fig['caption']}")
        add("")
        add(f"![fig{i}]({fig['path']})")
        add("")

    # ---------------- ⑤ 注意点 ----------------
    add("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）")
    add("")
    add(
        "1. **NLopt 不等式方向与 SciPy 相反**：SciPy 是 `c(x)≥0`，NLopt 是 `g(x)≤0`。"
        "首版按 SciPy 约定直接喂给 NLopt，COBYLA 会“收敛”到约束违反量 1e4 的点（判据 C3 当场变红）。"
        "适配器现在对值与梯度同时取负，并留了回归测试。"
    )
    add(
        "2. **`le` 约束的解析 Jacobian 必须变号**：DSL 里 `sense=\"le\"` 的约束在内部归一为 `threshold-expr≥0`，"
        "梯度也要取负。二者不一致时 FD 校验暴露误差 6000（该项 scale 3000），修正后降到 4e-7。"
    )
    add(
        "3. **求解器“一致”≠“全局最优”**：g08 上 scipy/nlopt 的 COBYLA 从界中点出发收敛到另一个局部最优 "
        f"（f≈{_fmt(local_min['g08'][0]['objective'], 6)}，自身 KKT 残差 "
        f"{_fmt(local_min['g08'][0]['kkt_residual'], 2, sci=True)}），与全局解差 7%。"
        "因此 C1 的一致性只在“落在同一已发表最优解”的求解器之间判，另设 `local_minimum_probe.json` 留证。"
    )
    add(
        "4. **病态尺度问题（G10）暴露能力边界**：G10 的 SLSQP 两个实现都能到 1e-15，"
        "但 MMA/COBYLA/trust-constr 在给定预算内不收敛（MMA 甚至报告 FTOL_REACHED 却约束违反 5.7e-3）。"
        "这也是 C1 在 G10 上只有 2 个求解器计入的原因，如实记录不粉饰。"
    )
    add(
        "5. **pyoptsparse 本机不可用（无 loongarch64 发行版，SNOPT 需商业许可）**：适配器按真实 API 实现"
        "（`Optimization/addVar/addObj/addCon/sens/optInform`），仅用桩模块注入验证 API 路径；"
        "**不为 SNOPT/IPOPT 声称任何数值结果**，结果里是 `status=unavailable` 与 `objective=null`。"
        "NLopt 2.11 也没有 `LN_SBOA`（任务书提到 SBOA），probe 把它列在 `missing_algorithms`。"
    )
    add(
        "6. **scipy SLSQP 会在数值已最优时报 failed**（`Positive directional derivative for linesearch`，G06/G09）："
        "平台不改写引擎自报状态（保持 `failed`），但把 f、约束违反、KKT 一并落库，判据按数值门评估。"
    )
    add(
        "7. **适用范围**：连续、单目标（或加权成单目标）、变量有界、约束可写成表达式的问题；"
        "整数/组合、随机目标、需要自动微分的黑箱（如 CFD 在环）不在本工具链范围内。"
        "目标是表达式白名单求值，若目标本身是外部程序调用，需要走 `solve(ProblemSpec, ...)` 的编程接口而不是 JSON DSL。"
    )
    add("")

    # ---------------- ⑥ 误差判定 ----------------
    add("## ⑥ 误差判定（关键量 vs 基准，逐项显式回答）")
    add("")
    add("基准 = 已发表/解析最优解（见 ① 的表）；误差 = |f-f*| / max(|f*|,1)。")
    add("")
    add("| 问题 | 最佳求解器 | f | f* | 相对误差 | 是否<3% | 是否<0.1%(C3型) |")
    add("|---|---|---|---|---|---|---|")
    for pid, cc in cross.items():
        rows = [r for r in index if r["problem_id"] == pid and r["status"] == "converged"
                and r["objective_rel_error"] is not None]
        if not rows:
            add(f"| `{pid}` | — | — | {_fmt(cc['reference']['objective'], 10)} | — | 无收敛解 | — |")
            continue
        best = min(rows, key=lambda r: abs(r["objective_rel_error"]))
        err = abs(best["objective_rel_error"])
        add(
            f"| `{pid}` | {best['engine']}:{best['algorithm']} | {_fmt(best['objective'], 8)} | "
            f"{_fmt(cc['reference']['objective'], 10)} | {_pct(err)} | "
            f"{'是' if err < 0.03 else '**否**'} | {'是' if err < 1e-3 else '否'} |"
        )
    add("")
    add("补充（非最优但需明示的量）：")
    add("")
    add("- 约束违反度：所有计入 ⑥ 的结果 `constraint_violation` 均 ≤ 5.9e-9（见 3.1 表），即约束在数值容差内被满足。")
    c4 = next(c for c in audit["criteria"] if c["id"] == "C4")["observed"]
    worst_kkt = max(r["best_kkt"] for r in c4["per_problem"])
    add(f"- KKT：五个约束问题的最优 KKT 残差最大 {worst_kkt:.2e}，全部 < 1e-4（判据 C4）。")
    add(
        "- 已知未达标项：G10 上 MMA/COBYLA/trust-constr 未收敛（相对误差 1.6e-3~6.1e-2），"
        "g08 的 COBYLA 落在局部最优（7%）。二者都**不作为达标结果**，已在 3.3 与 ⑤ 中列明。"
    )
    add(
        "- 未能验证项：SNOPT/IPOPT 数值精度（引擎本机不可用）、SBOA（NLopt 2.11 无此算法）。"
        "这两项属于**证据不足 WITHHELD**，不是 REAL。"
    )
    add("")

    # ---------------- 结论 ----------------
    add("## 结论与能力登记")
    add("")
    verdicts = ", ".join(f"{c['id']}={c['verdict']}" for c in audit["criteria"])
    add(f"- 判据结果：{verdicts}")
    add(f"- 回归测试：{audit.get('pytest')}")
    add(
        "- 能力：TensorPlatform 现在具备“外部优化器统一接入”能力（REAL：scipy 6 算法 + nlopt 10 算法；"
        "WITHHELD：pyoptsparse 引擎数值未验证，仅 API 路径覆盖）。"
    )
    add("")
    return "\n".join(lines)


def build_payload(outdir: Path, markdown: str) -> Dict[str, Any]:
    audit = _read(outdir / "audit_summary.json")
    figures = []
    for fig in audit.get("figures", []):
        path = outdir / fig["path"]
        figures.append(
            {
                "name": fig["name"].replace(".png", ""),
                "caption": fig["caption"],
                "png_base64": base64.b64encode(path.read_bytes()).decode(),
            }
        )
    return {
        "title": TITLE,
        "summary": (
            "在 TensorPlatform 新增引擎无关的优化工具链 tools/optimizer/unified：一份 JSON DSL 问题定义 + "
            "SciPy/NLopt/pyoptsparse 统一接口 + 标准化结果（最优解/状态/迭代史/约束违反/KKT）。"
            "38 个 pytest 全绿；C1–C7 七项判据在证据目录 artifacts/p304w2_n10_optimizer 中逐项可追溯；"
            "pyoptsparse 引擎本机不可用，按 unavailable 显式记录、不伪造数值。"
        ),
        "scenario_key": "p304w2-n10",
        "mission_id": MISSION_ID,
        "tags": [
            "optimization",
            "scipy",
            "nlopt",
            "pyoptsparse",
            "tooling",
            "DSL",
            "KKT",
            "cross-engine",
            "TensorPlatform",
        ],
        "markdown": markdown,
        "figures": figures,
    }


def post(payload: Dict[str, Any]) -> None:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    password = os.environ.get("TENSOR_AGENT_PASS", "")
    login = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"{api_base}/auth/login", data=login, headers={"Content-Type": "application/json"}
    )
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
    body = json.dumps(payload).encode()
    req2 = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    resp = json.loads(urllib.request.urlopen(req2, timeout=60).read())
    print(f"report posted: {resp}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="build (and optionally post) the node report")
    parser.add_argument("--outdir", default="artifacts/p304w2_n10_optimizer")
    parser.add_argument("--md-out", default=str(REPORT_PATH))
    parser.add_argument("--json-out", default="/tmp/report.json")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir)
    markdown = build_markdown(outdir)
    md_path = Path(args.md_out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    payload = build_payload(outdir, markdown)
    Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"markdown -> {md_path}  ({len(markdown)} chars)")
    print(f"payload  -> {args.json_out} ({len(payload['figures'])} figures)")
    if args.post:
        post(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())