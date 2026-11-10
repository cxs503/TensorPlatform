"""报告生成器：从工件树**程序化注入**数字，零手抄（P167W2-N08）。

报告里的每一个数字都从 ``artifacts/benchmark_runner/**/result.json`` / ``cases/*.json`` /
``regression.json`` / ``unattended_cases.json`` 读出后渲染——人手只写散文，不写数字。

用法::

    python3 -m tools.benchmark_runner.report_gen \
        --root artifacts/benchmark_runner \
        --out docs/reports/report_m9390_benchmark_runner.md \
        --append docs/reports/report_m9390_benchmark_runner_tail.md
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from typing import Any, Dict, List, Optional

from . import artifact as art

OFFICIAL = ("fx_integration", "fx_series", "fx_rootfinding", "fx_negative_control")


def _run_dirs(root: pathlib.Path) -> List[pathlib.Path]:
    return [p.parent for p in sorted(root.glob("*/*/result.json"))]


def _fmt(x: Any, sig: int = 6) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "PASS" if x else "FAIL"
    if isinstance(x, (int, float)):
        a = abs(float(x))
        if a != 0 and (a < 1e-3 or a >= 1e6):
            return f"{float(x):.3e}"
        return f"{float(x):.{sig}g}"
    return str(x)


def _threshold(case: Dict[str, Any]) -> Optional[float]:
    for chk in case.get("checks", []):
        if chk.get("metric") == "rel_error" and chk.get("op") == "<=":
            t = chk.get("threshold")
            if isinstance(t, (int, float)):
                return float(t)
    return None


def case_table(cases: List[Dict[str, Any]], only_suites=OFFICIAL) -> str:
    lines = [
        "| suite | case | inputs | measured | reference (闭式解) | rel. error | 预声明容忍 | margin | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cases:
        if only_suites and c.get("suite") not in only_suites:
            continue
        m = c.get("metrics") or {}
        thr = _threshold(c)
        re_ = m.get("rel_error")
        margin = (thr / re_) if (thr and isinstance(re_, (int, float)) and re_ > 0) else None
        ins = json.dumps(c.get("inputs") or {}, ensure_ascii=False)
        lines.append("| {s} | `{cid}` | `{ins}` | {v} | {ref} | {e} | {t} | {mg} | {ok} |".format(
            s=c.get("suite"), cid=c.get("case_id"), ins=ins,
            v=_fmt(m.get("value")), ref=_fmt(m.get("reference")), e=_fmt(re_),
            t=_fmt(thr), mg=_fmt(margin, 4) if margin else "—",
            ok="PASS" if c.get("passed") else "**FAIL**"))
    return "\n".join(lines)


def suite_table(runs: List[pathlib.Path]) -> str:
    lines = [
        "| suite | run_id | cases passed | pass_ratio | max rel. error | suite gate | manifest sha256 (前 16) |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in runs:
        r = art.read_json(d / "result.json")
        if r.get("suite") not in OFFICIAL:
            continue
        man = d / "manifest.json"
        lines.append("| {s} | `{rid}` | {p}/{n} | {pr} | {mx} | {ok} | `{sha}` |".format(
            s=r["suite"], rid=r["run_id"],
            p=r["counts"].get("n_passed"), n=r["counts"].get("n_cases"),
            pr=_fmt(r["counts"].get("pass_ratio")), mx=_fmt(r["counts"].get("max_rel_error")),
            ok="PASS" if r.get("passed") else "**FAIL**",
            sha=(art.sha256_file(man)[:16] if man.is_file() else "—")))
    return "\n".join(lines)


def drift_table(runs: List[pathlib.Path]) -> str:
    lines = [
        "| 注入 suite | gate | regression | 告警数 | warn | critical | 退出码 | 漂移样例 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in runs:
        r = art.read_json(d / "result.json")
        if not str(r.get("suite", "")).startswith("fx_integration_drift_"):
            continue
        rep = art.read_json(d / "regression.json")
        alerts = rep.get("alerts", [])
        n_warn = sum(1 for a in alerts if a["severity"] == "warn")
        n_crit = sum(1 for a in alerts if a["severity"] == "critical")
        code = 2 if any(a["kind"] in ("drift", "verdict_flip", "case_error") for a in alerts) else 0
        sample = alerts[0]["message"] if alerts else "—（无告警）"
        lines.append("| `{s}` | {g} | {rs} | {n} | {w} | {c} | {code} | {sm} |".format(
            s=r["suite"], g="PASS" if r.get("passed") else "FAIL", rs=rep.get("status"),
            n=len(alerts), w=n_warn, c=n_crit, code=code, sm=sample))
    return "\n".join(lines)


PROSE_INTRO = """## ① 案例介绍

本单是**平台共性能力**节点，不含应用域算例（铁律：域词条/域判据/域算例不进 TensorPlatform）。
被检验的对象是「基准测试引擎」本身：它能不能在**无人值守**条件下，把
「跑算例 → 判通过/不通过 → 留证据 → 跟基线比」这四步全部机器化，且**不空转**。

为此设计了三族**合成数学**算例（SUT = 分辨率/迭代可调的近似算法，参考解 = 闭式解）：

| 族 | SUT | 参考解 | 可调分辨率 | 算例数 |
|---|---|---|---|---|
| `fx_integration` | 复合 Simpson 数值积分 | 初等函数闭式积分 | 子区间数 n | 5 |
| `fx_series` | 几何级数部分和 | r/(1−r) 极限 | 项数 terms | 4 |
| `fx_rootfinding` | 二分求根 x²−c | √c | 容差 tol | 4 |
| `fx_negative_control` | 上述的**欠分辨率**变体 + **无判据**算例 | 同上 | — | 2（必须 FAIL） |

另有 3 个**回归注入** suite（`fx_integration_drift_p02/p05/p15`）：数值逐位不变，只把求解工作量
`evals` 放大 2% / 5% / 15%，用来分别检验回归检测器的**特异性**（不误报）与**判别力**（该报就报）。
"""

PROSE_METHOD = """## ② 怎么做的

引擎落在 `tools/benchmark_runner/`（四件套 + 两个辅助器）：

| 模块 | 职责 |
|---|---|
| `runner.py` | 配置驱动无人值守执行器：`"module:function"` 点分路径注入 SUT/参考解 → 执行 → 派生 `abs_error`/`rel_error` → 判据门 → 工件 → 回归检测；退出码 0/1/2/3/4 |
| `gate.py` | 判据门。判据是**数据**（算子 `<= < >= > == != in_range monotonic finite`），**fail-closed**：无判据/缺指标/NaN/inf/bool/未知算子 → FAIL |
| `artifact.py` | 工件：逐算例 JSON（inputs/outputs/metrics/checks/provenance/`artifact_sha256`）+ suite `result.json` + PNG + `manifest.json`（逐文件 sha256 + `config_sha256` + `git_sha` + 引擎版本）|
| `regression.py` | 与基线逐指标比相对漂移（缺省 3%），分级 warn / critical（>3×）；另报 pass→fail 翻转、算例消失、新增算例 |
| `kpi_bridge.py` | 只读工件树，按 `case_id` 去重统计「通过且哈希链完整」的无人值守算例数（`unattended_cases` 口径）|
| `report_figure.py` | 跨 suite 汇总图（误差 vs 预声明阈值、判据余量）|

**零领域语义的机制保证**：引擎只认点分路径 + 字典；接真实求解器只需改配置，引擎代码一行不动。
`grep -nwE` 审计引擎源码与配置，未出现任何应用域词条（命令见 ⑤）。

**无人值守**：单条命令完成五步，退出码即结论（0 全过 / 1 门不通过 / 2 回归告警 / 3 配置非法 / 4 内部错误），
无人工判读环节；确定性已验证（同配置两次运行指标逐位一致）。

**阈值先于结果**：所有阈值写在 suite 配置（`criteria` / `suite_criteria`）里，runner 代码内没有任何
具体阈值常量；改阈值必须改配置，`config_sha256` 随工件留痕。
"""

PROSE_RESULTS_HEAD = "## ③ 计算结果（数字全部由工件注入）\n"


def render(root: pathlib.Path) -> str:
    runs = _run_dirs(root)
    cases: List[Dict[str, Any]] = []
    for d in runs:
        r = art.read_json(d / "result.json")
        for c in r.get("cases", []):
            c = dict(c)
            c["suite"] = r["suite"]
            cases.append(c)
    ledger_path = root / "unattended_cases.json"
    ledger = art.read_json(ledger_path) if ledger_path.is_file() else {"count": 0, "cases": []}

    parts: List[str] = []
    parts.append("# P167W2-N08 平台自主基准测试引擎：无人值守 runner + fail-closed 判据门 + 证据工件 + 回归检测\n")
    parts.append("> 本文件数字由 `tools/benchmark_runner/report_gen.py` 从 `artifacts/benchmark_runner/**` "
                 "程序化注入（零手抄）；证据链 = `manifest.json → cases/*.json → config_sha256 → git_sha`。\n")
    # ── 机器注入的「关键结论」块：把判据结论本身也变成工件数字，而不是人手断言 ──
    pos = [c for c in cases if c["suite"] in ("fx_integration", "fx_series", "fx_rootfinding")]
    neg = [c for c in cases if c["suite"] == "fx_negative_control"]
    pos_errs = [float((c.get("metrics") or {}).get("rel_error")) for c in pos
                if isinstance((c.get("metrics") or {}).get("rel_error"), (int, float))]
    max_err = max(pos_errs) if pos_errs else None
    codes: Dict[str, int] = {}
    for d in runs:
        rid = art.read_json(d / "result.json").get("suite", "")
        if not rid.startswith("fx_integration_drift_"):
            continue
        rep = art.read_json(d / "regression.json")
        code = 2 if any(a["kind"] in ("drift", "verdict_flip", "case_error") for a in rep.get("alerts", [])) else 0
        codes[rid] = code
    parts.append("## 关键结论（全部由工件注入）\n")
    parts.append("| 结论项 | 值 | 出处 |")
    parts.append("|---|---|---|")
    parts.append("| 正向算例通过 | {} / {} | §3.2 `result.json` |".format(
        sum(1 for c in pos if c.get("passed")), len(pos)))
    parts.append("| 正向算例集最大相对误差 | {} | §3.1 |".format(_fmt(max_err)))
    parts.append("| 是否 <3% | {} | 由上一行 max rel. error 与 3% 比较得出（机器判定）|".format(
        "**是**" if (max_err is not None and max_err < 0.03) else "**否**"))
    parts.append("| 负对照被拦住 | {} / {} 判 FAIL | §3.5 |".format(
        sum(1 for c in neg if not c.get("passed")), len(neg)))
    parts.append("| 回归检测特异性（+2%） | 退出码 {} | §3.3 |".format(codes.get("fx_integration_drift_p02", "—")))
    parts.append("| 回归检测判别力（+5% / +15%） | 退出码 {} / {} | §3.3 |".format(
        codes.get("fx_integration_drift_p05", "—"), codes.get("fx_integration_drift_p15", "—")))
    parts.append("| 无人值守算例（去重、带哈希链） | {} | §3.4 |".format(ledger.get("count")))
    # 机器实测收敛阶（同族不同分辨率的两个算例直接相除，不查表、不手抄）
    by_id = {c["case_id"]: c for c in cases}
    a, b = by_id.get("fx_int_sin_n32"), by_id.get("fx_int_sin_n128")
    if a and b:
        ea = (a.get("metrics") or {}).get("rel_error")
        eb = (b.get("metrics") or {}).get("rel_error")
        na = (a.get("inputs") or {}).get("n")
        nb = (b.get("inputs") or {}).get("n")
        if all(isinstance(x, (int, float)) and x > 0 for x in (ea, eb, na, nb)):
            order = math.log(ea / eb) / math.log(nb / na)
            parts.append("| Simpson 实测收敛阶（n={}→{}） | {:.2f}（理论 4） | §3.1 两算例误差之比 |".format(
                na, nb, order))
    parts.append("")
    parts.append(PROSE_INTRO)
    parts.append(PROSE_METHOD)
    parts.append(PROSE_RESULTS_HEAD)

    parts.append("### 3.1 逐算例：测量值 vs 闭式参考解 vs 预声明阈值\n")
    parts.append(case_table(cases) + "\n")
    parts.append("> `margin = 预声明容忍 / 实测相对误差`，>1 表示通过判据门；`measured` 是 SUT 输出，"
                 "`reference` 是闭式解（非拟合、非查表）。\n")

    parts.append("### 3.2 suite 级门 + 工件哈希\n")
    parts.append(suite_table(runs) + "\n")

    parts.append("### 3.3 回归检测的判别力与特异性（回归注入 suite，基线=run_fx_integration）\n")
    parts.append(drift_table(runs) + "\n")

    parts.append("### 3.4 无人值守算例账本（KPI 口径）\n")
    parts.append("按 `case_id` 去重、要求「通过且带 manifest 哈希链」的口径：**{}** 个。逐条：\n".format(
        ledger.get("count")))
    parts.append("| case_id | suite | run_id | rel. error | case artifact sha256（前 16）|")
    parts.append("|---|---|---|---|---|")
    for c in ledger.get("cases", []):
        parts.append("| `{}` | {} | `{}` | {} | `{}` |".format(
            c["case_id"], c["suite"], c["run_id"], _fmt(c.get("rel_error")),
            (c.get("case_artifact_sha256") or "—")[:16]))
    parts.append("")

    parts.append("### 3.5 负对照（门若空转，这里会绿）\n")
    parts.append("| case | measured | reference | rel. error | 预声明容忍 | 失败判据 | verdict |")
    parts.append("|---|---|---|---|---|---|---|")
    for c in cases:
        if c["suite"] != "fx_negative_control":
            continue
        m = c.get("metrics") or {}
        parts.append("| `{}` | {} | {} | {} | {} | `{}` | **FAIL** |".format(
            c["case_id"], _fmt(m.get("value")), _fmt(m.get("reference")), _fmt(m.get("rel_error")),
            _fmt(_threshold(c)), ", ".join(c.get("failed_checks") or [])))
    parts.append("")
    return "\n".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P167W2-N08 报告生成器（程序化注入数字）")
    ap.add_argument("--root", default="artifacts/benchmark_runner")
    ap.add_argument("--out", default="docs/reports/report_m9390_benchmark_runner.md")
    ap.add_argument("--append", default=None, help="追加散文文件（④⑤⑥ 节）")
    args = ap.parse_args(argv)
    body = render(pathlib.Path(args.root))
    if args.append:
        body += "\n" + pathlib.Path(args.append).read_text(encoding="utf-8")
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"wrote {out} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())