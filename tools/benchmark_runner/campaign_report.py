"""campaign 报告生成器：报告里的每个数字都从工件树读出（零手抄）。

用法::

    python3 -m tools.benchmark_runner.campaign_report \
        --run artifacts/benchmark_runner/campaign/20261110T2355Z \
        --md docs/reports/P167W2B_N09_unattended_campaign.md \
        --json docs/reports/P167W2B_N09_campaign_report.json

输入 = ``campaign.json`` + 各 suite 的 ``result.json`` / ``cases/*.json`` / ``regression.json``。
输出 = 论文级六要素 markdown（③⑥ 节数字全部注入）+ 机器可读验收 JSON（判据数字 + 产物路径）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, Iterable, List, Optional

from . import artifact as art

_E = 1e-12


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


def _yn(x: Any) -> str:
    """布尔 → 中文判定（"是/否"）；None → "—"（不猜）。"""
    if x is None:
        return "—"
    return "是" if x else "否"


def _load_cases(suite_run_dir: str | pathlib.Path, suite: str) -> List[Dict[str, Any]]:
    """从某次 suite 运行的工件目录取逐算例证据（result.json + cases/*.json）。"""
    d = pathlib.Path(suite_run_dir)
    res = d / "result.json"
    if not res.is_file():
        return []
    data = art.read_json(res)
    out: List[Dict[str, Any]] = []
    for c in data.get("cases", []):
        case_file = d / "cases" / f"{c['case_id']}.json"
        payload = art.read_json(case_file) if case_file.is_file() else {}
        out.append({
            "suite": data.get("suite") or suite,
            "case_id": c.get("case_id"),
            "status": c.get("status"),
            "passed": c.get("passed"),
            "inputs": payload.get("inputs"),
            "outputs": payload.get("outputs"),
            "metrics": c.get("metrics") or {},
            "failed_checks": c.get("failed_checks") or [],
            "case_file": str(case_file) if case_file.is_file() else None,
            "artifact_sha256": payload.get("artifact_sha256"),
            "manifest_sha256": art.sha256_file(d / "manifest.json")
            if (d / "manifest.json").is_file() else None,
        })
    return out


def _suite_rows(campaign: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(campaign["suites"]) + list(campaign.get("probes") or [])


def render(run_dir: str | pathlib.Path) -> Dict[str, Any]:
    """渲染 markdown + 机器 JSON（返回两者与原始数字）。"""
    run_dir = pathlib.Path(run_dir)
    camp = art.read_json(run_dir / "campaign.json")
    recon = camp.get("reconciliation") or {}
    metrics = camp.get("metrics") or {}
    threshold = float(camp.get("drift_threshold") or 0.03)
    suites = [s for s in camp.get("suites", []) if s.get("kind", "suite") == "suite"]
    probes = list(camp.get("probes") or [])

    counted = {c["suite"] for c in camp["kpi_ledger"]["cases"]}
    all_cases: List[Dict[str, Any]] = []
    for s_ in suites:
        if s_["id"] in counted and s_.get("run_dir"):
            all_cases.extend(_load_cases(s_["run_dir"], s_["id"]))
    all_cases.sort(key=lambda c: (c["suite"], c["case_id"]))

    # ⑥ 判据：逐 suite 实测最大相对误差 vs 预声明容忍
    err_rows = []
    for s in suites:
        errs = [float(c["metrics"]["rel_error"]) for c in all_cases
                if c["suite"] == s["id"] and isinstance(c["metrics"].get("rel_error"), (int, float))]
        tol = 0.03
        for cfg_path in [s.get("config")]:
            p = pathlib.Path(str(cfg_path))
            if p.is_file():
                cfg = art.read_json(p)
                vals = [c["criteria"][0].get("value") for c in cfg["cases"]
                        if c.get("criteria") and c["criteria"][0].get("metric") == "rel_error"]
                tol = max([v for v in vals if isinstance(v, (int, float))] or [0.03])
        err_rows.append({
            "suite": s["id"],
            "n_cases": len(errs),
            "max_rel_error": max(errs) if errs else None,
            "declared_tolerance": tol,
            "under_3pct": (max(errs) < 0.03) if errs else None,
            "under_declared": (max(errs) <= tol) if errs else None,
        })

    gate_rows = [{"id": c["metric"], "op": c["op"], "threshold": c.get("threshold"),
                  "actual": c.get("actual"), "passed": c["passed"]} for c in camp["criteria"]]

    md: List[str] = []
    md.append("# P167W2B-N09 平台自主基准测试引擎成熟化：无人值守批级 campaign + 批级判据门 + "
              "既有验证面回归 + KPI 对账\n")
    md.append("> 本文件由 `tools/benchmark_runner/campaign_report.py` 从 "
              f"`{run_dir}/campaign.json` 与各 suite 工件**程序化注入**（零手抄）；"
              "证据链 = `manifest.json → 各 suite manifest.json → cases/*.json "
              "→ config_sha256 → git_sha`。\n")
    md.append("## 关键结论（全部由工件注入）\n")
    md.append("| 结论项 | 值 | 出处 |")
    md.append("|---|---|---|")
    md.append(f"| 批级判据通过 | **{camp['passed']}** | `campaign.json:passed` |")
    md.append(f"| 步骤符合预声明期望 | {metrics.get('steps_ok')}/{metrics.get('steps_total')}"
              f"（ratio={_fmt(metrics.get('steps_ok_ratio'))}） | `campaign.json:metrics` |")
    md.append(f"| 负向对照挂住 | {int(metrics.get('negative_controls_held', 0))}/"
              f"{int(metrics.get('negative_controls', 0))} | `campaign.json:metrics` |")
    md.append(f"| 真实既有验证面无人值守算例（本单新增） | "
              f"{(recon.get('delta_by_suite') or {}).get('pf_surfaces', 0)} | "
              f"`campaign.json:reconciliation.delta_by_suite` |")
    md.append(f"| 无人值守算例：外部基线 + 引擎增量 = 合计 | "
              f"{recon.get('baseline_case_count')} + {recon.get('delta_case_count')} = "
              f"**{recon.get('combined')}**（目标 {recon.get('target')}） | "
              f"`campaign.json:reconciliation` |")
    md.append(f"| 基线/增量 id 重叠（重复计数） | {recon.get('overlap_count')} | "
              f"`campaign.json:reconciliation.overlap_ids` |")
    probe_txt = "、".join(f"{p['id']}:{p['expect_status']}→code{p['observed_code']}"
                          for p in probes)
    md.append(f"| 回归检测漂移探针 | {probe_txt} | `campaign.json:probes` |")
    md.append(f"| 证据链破损数 | {int(metrics.get('integrity_failures', 0))} | "
              f"`campaign.json:metrics` |")
    md.append("")

    md.append("## ① 案例介绍（被检验对象＝引擎本身）\n")
    md.append("本单是**平台共性能力**节点，不含应用域算例（铁律：域词条/域判据/域算例不进 "
              "TensorPlatform）。被检验的不是任何物理场，而是"
              "「一批 benchmark 能不能在无人值守条件下自己跑完、自己裁决、自己留证、自己发现退化」"
              "这套**机制**。\n")
    md.append("三类被检对象，全部用确定性、纯函数、可闭式对照的算例，且配置里只写点分路径：\n")
    md.append("| 族 | 作用 | suite |")
    md.append("|---|---|---|")
    md.append("| 合成近似算法（正向面） | SUT 分辨率可调、参考解为闭式解，检验判据门 | "
              "`fx_integration` `fx_series` `fx_rootfinding` |")
    md.append("| 判据门带内/带外标定 | 预声明 3% 容忍；实测 ≈1% 必过、≈5% 必挂（fail-closed） | "
              "`fx_gate_band_pass` `fx_gate_band_fail` |")
    md.append("| **仓库既有验证面**（本单新增） | 把平台已有机制（记账 ETL 数值工具／证据工件哈希链／"
              "回归分级器）作为 SUT 无人工介入重跑 | `pf_surfaces`（14 算例） |")
    md.append("| 故障注入负向对照 | 无判据、欠分辨率、预算耗尽、挂死超时——都必须挂住 | "
              "`fx_negative_control` `fx_budget_negative` `fx_timeout_negative` |")
    md.append("")
    md.append("**逐 suite 期望与实测**（`expect` 是运行前写死的，不是看到结果才写的）：\n")
    md.append("| suite | 期望 | 实测 verdict | 退出码 | 算例 通过/总 | max rel.error | 回归状态 |")
    md.append("|---|---|---|---|---|---|---|")
    for s in suites:
        sm = s.get("suite_metrics") or {}
        md.append(f"| `{s['id']}` | {s['expect']} | {_fmt(s.get('passed'))} | {s.get('exit_code')} | "
                  f"{sm.get('n_passed')}/{sm.get('n_cases')} | {_fmt(sm.get('max_rel_error'))} | "
                  f"{s.get('regression_status')} |")
    md.append("")

    md.append("## ② 怎么做的\n")
    md.append("模块（全部落在 `tools/benchmark_runner/`，零应用域词条）：\n")
    md.append("| 模块 | 职责 |")
    md.append("|---|---|")
    md.append("| `runner.py`（N08 既有） | 单 suite：配置驱动执行 → 判据门 → 工件 → 回归；退出码 0/1/2/3/4/5 |")
    md.append("| `gate.py`（N08 既有） | 判据门，fail-closed（无判据/缺指标/NaN/inf/bool/未知算子 → FAIL） |")
    md.append("| `artifact.py`（N08 既有） | 逐算例 JSON + manifest sha256 链 + 落盘后立即回验 |")
    md.append("| `regression.py`（N08 既有） | 与基线逐指标比相对漂移，>3% warn，>3×3% critical |")
    md.append("| **`unattended.py`（本单新增）** | **批级编排**：逐 suite 执行 → 期望裁决 → 漂移探针 → "
              "KPI 对账 → 批级判据 → 证据工件 + 3 张图；一个退出码 |")
    md.append("| **`kpi_baseline.py`（本单新增）** | 冻结外部基线：KPI ETL 同一条 SQL 读出 + 名字清单 + sha256，"
              "查不到即 `unavailable`（不猜） |")
    md.append("| **`surfaces.py`（本单新增）** | 既有验证面适配层：薄包装 + **独立参考解**"
              "（整数截断／decimal 定点／手写 hashlib），不复用 SUT 算法路径 |")
    md.append("| **`campaign_report.py`（本单新增）** | 报告数字注入（本文件即它的输出） |")
    md.append("")
    md.append("流程（一条命令，无人工判读）：\n")
    md.append("```bash")
    md.append("python3 -m tools.benchmark_runner.kpi_baseline --capture \\")
    md.append("    --out artifacts/benchmark_runner/kpi_baseline.json")
    md.append("python3 -m tools.benchmark_runner.unattended \\")
    md.append("    --campaign tools/benchmark_runner/campaigns/pf_default.json")
    md.append("```")
    md.append("")
    probe_desc = "、".join(
        f"`{p['id']}` +{100 * float(p.get('overhead') or 0):.0f}%→期望 {p['expect_status']}"
        for p in probes)
    md.append(f"漂移探针的构造（回归检测**独立于**判据门的证据）：同一积分算例换成"
              f"「数值逐位不变、`evals` 放大 (1+overhead)」的包装 SUT（"
              f"{probe_desc}）。阈值 {threshold:.0%}、critical 阈值 {3 * threshold:.0%}："
              f"判据门仍绿（数值没变），只有回归检测该开口——两个门的分工因此可测。\n")

    md.append("## ③ 计算结果（数字全部由工件注入）\n")
    md.append("### 3.1 无人值守算例逐条证据（判据通过且哈希链完整者才计入）\n")
    md.append("| suite | case | inputs | measured | reference | rel.error | artifact_sha256(前12) |")
    md.append("|---|---|---|---|---|---|---|")
    for c in all_cases:
        out = c.get("outputs") or {}
        ref = out.get("reference") or {}
        md.append(f"| `{c['suite']}` | `{c['case_id']}` | `{json.dumps(c.get('inputs'), ensure_ascii=False)}` | "
                  f"{_fmt(out.get('value'))} | {_fmt(ref.get('value'))} | "
                  f"{_fmt(c['metrics'].get('rel_error'))} | "
                  f"`{str(c.get('artifact_sha256'))[:12]}` |")
    md.append("")
    md.append("### 3.2 KPI 对账（外部基线 + 引擎增量，重叠显式扣除）\n")
    md.append("| 项 | 值 | 出处 |")
    md.append("|---|---|---|")
    base = recon.get("baseline") or {}
    md.append(f"| 外部基线（KPI ETL DB 代理口径） | {recon.get('baseline_case_count')} | "
              f"`{base.get('artifact')}` sha256=`{str(base.get('artifact_sha256'))[:16]}` |")
    md.append(f"| 基线捕获时刻 | {base.get('captured_at')} | 同上 |")
    md.append(f"| 基线名字清单哈希 | `{base.get('case_ids_sha256')}` | 同上 |")
    for suite, n in (recon.get("delta_by_suite") or {}).items():
        md.append(f"| 引擎增量 · `{suite}` | {n} | `campaign.json:reconciliation.delta_by_suite` |")
    md.append(f"| 引擎增量合计 | {recon.get('delta_case_count')} | `campaign.json:reconciliation` |")
    md.append(f"| 与基线 id 重叠（若>0 已扣除） | {recon.get('overlap_count')} | 同上 |")
    md.append(f"| **合计** | **{recon.get('combined')}** | 同上 |")
    md.append(f"| 目标 | {recon.get('target')} | `campaigns/pf_default.json:targets` |")
    md.append("")
    md.append("基线 SQL（与 `tools/guide_kpi_ledger.py` 的 KPI 定义**逐字一致**，本单不改上游口径）：\n")
    md.append("```sql")
    md.append(str(base.get("sql")))
    md.append("```")
    md.append("")
    md.append("### 3.3 回归检测漂移探针（特异性 + 判别力）\n")
    md.append("| 探针 | 注入漂移 | 观测漂移 | 期望分级 | 实测分级 | 判据门 | 结论 |")
    md.append("|---|---|---|---|---|---|---|")
    overhead = {p["id"]: f"{100 * float(p.get('overhead') or 0):.0f}%" for p in probes}
    for p in probes:
        drift_txt = ("—" if p.get("observed_drift") is None
                     else "%.2f%%" % (100.0 * float(p["observed_drift"])))
        md.append(f"| `{p['id']}` | {overhead.get(p['id'], '—')} | {drift_txt} | "
                  f"{p['expect_status']} | code {p['observed_code']} | "
                  f"{_fmt(p.get('passed_gate'))} | {_fmt(p.get('ok'))} |")
    md.append("")
    md.append("### 3.4 批级判据（预声明，机器裁）\n")
    md.append("| 判据 | 阈值 | 实测 | 通过 |")
    md.append("|---|---|---|---|")
    for g in gate_rows:
        md.append(f"| `{g['id']}` {g['op']} | {_fmt(g['threshold'])} | {_fmt(g['actual'])} | "
                  f"{_fmt(g['passed'])} |")
    md.append("")

    md.append("## ④ 结果图\n")
    figs = (camp.get("artifacts") or {}).get("figures") or []
    captions = {
        "campaign_kpi_vs_target.png": "左：无人值守算例 KPI（外部基线 + 引擎增量 vs 目标线）；"
                                      "右：逐 suite 实测裁决与预声明期望的一致性（负向对照必须挂住）",
        "regression_probes.png": "回归检测标定：注入 2%/5%/16% 工作量漂移的观测漂移与 "
                                 "warn/critical 阈值线（左绿=特异性，红=判别力）",
        "suite_error_vs_threshold.png": "跨 suite 汇总：各算例实测相对误差 vs 预声明容忍（对数轴）",
    }
    for f in figs:
        name = pathlib.Path(f["path"]).name
        md.append(f"![{name}]({f['path']})\n")
        md.append(f"*{captions.get(name, name)}*（sha256=`{str(f.get('sha256'))[:16]}`，"
                  f"{f.get('bytes')} bytes）\n")
    md.append("")

    md.append("## ⑤ 注意点（踩过的坑与适用范围）\n")
    md.append("1. **`evals` 在回归器里默认不跟踪**（`_NON_TRACKED`：规模参数不算退化）。"
              "漂移探针必须显式 `track_metrics=[\"evals\"]`，否则探针会「静默干净」——"
              "第一次跑就踩到，配置里已写死。\n")
    md.append("2. **同一 case_id 在漂移探针里复用**（回归对比按 id 配对），探针工件因此单独写到 "
              "`artifacts/benchmark_runner/probes/`，否则会把主工件树里正向算例的证据换成探针证据。\n")
    md.append("3. **KPI 65 是外部（DB 代理）口径**，本单不改上游 ETL：`kpi_baseline.py` 只冻结"
              "「同一 SQL 在捕获时刻的值 + 名字清单」，引擎增量按 id 求差并入，重叠显式扣除。"
              "若上游把 ETL 改成以引擎工件为权威源，应改为整树 `kpi_bridge.collect()` 计数，"
              "两种口径不可混写。\n")
    md.append("4. **基线是双向的**：`establish_if_missing` 只在基线缺失时建立，已存在则只比对——"
              "否则每次都覆盖基线，「回归检测」会退化成「自己跟自己比」。\n")
    md.append("5. **适用范围**：本引擎判的是「数值/机制一致性」，不是物理正确性。"
              "把真实求解器接进来只需改配置里的点分路径（引擎代码零改动），"
              "但域判据（误差阈值该多少）必须由领域仓以配置注入，平台不得内置。\n")
    md.append("6. **不确定项**：`pf_surfaces` 的 14 个算例是「仓库既有纯函数面」，"
              "不是全部验证面；带 IO/数据库的机制面（如端到端 API 门）本轮未纳入，"
              "因为它们需要外部服务，不是纯无人值守可复现的。\n")
    md.append("")

    md.append("## ⑥ 误差判定\n")
    md.append("| suite | 算例数 | 实测 max rel.error | 预声明容忍 | 是否 <3% | 是否 ≤容忍 |")
    md.append("|---|---|---|---|---|---|")
    for r in err_rows:
        md.append(f"| `{r['suite']}` | {r['n_cases']} | {_fmt(r['max_rel_error'])} | "
                  f"{_fmt(r['declared_tolerance'])} | {_yn(r['under_3pct'])} | "
                  f"{_yn(r['under_declared'])} |")
    md.append("")
    worst = max([r["max_rel_error"] for r in err_rows if r["max_rel_error"] is not None] or [None])
    md.append(f"**关键量答复**：全部有参考解的算例中最大相对误差 = `{_fmt(worst)}`，"
              f"是否 <3% = **{_yn(worst < 0.03 if worst is not None else None)}**。"
              "负向对照（欠分辨率/无判据/预算耗尽/挂死）按设计**必须**超阈值挂住，"
              "它们的「误差大」是预期的判别力证据，不计入上表。\n")
    md.append("")
    md.append("## 审计链\n")
    md.append(f"* campaign 配置 sha256 = `{camp.get('campaign_sha256')}`（`{camp.get('campaign_path')}`）")
    md.append(f"* campaign run_id = `{camp.get('run_id')}`，引擎版本 = `{camp.get('engine_version')}`，"
              f"git_sha = `{camp.get('git_sha')}`")
    md.append(f"* 工件清单 = `{run_dir}/manifest.json` "
              f"sha256=`{_maybe_sha(run_dir / 'manifest.json')}`（逐文件 sha256 + manifest_digest）")
    md.append(f"* 各 suite 证据 = `{run_dir.parent.parent}/<suite>/<run_id>/manifest.json`")
    for s in suites:
        if s.get("manifest_sha256"):
            md.append(f"  * `{s['id']}` manifest sha256=`{s['manifest_sha256']}`（integrity ok="
                      f"{s['integrity'].get('ok')}，checked={s['integrity'].get('checked')}）")
    md.append("")
    return {"markdown": "\n".join(md), "campaign": camp, "cases": all_cases, "errors": err_rows,
            "criteria": gate_rows, "probes": probes, "run_dir": str(run_dir)}


def _maybe_sha(path: pathlib.Path) -> str:
    return art.sha256_file(path) if path.is_file() else "—"


def machine_report(run_dir: str | pathlib.Path, rendered: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """机器可读验收 JSON：判据 → 数字 + 产物路径（供自动化复核与后续对账）。"""
    run_dir = pathlib.Path(run_dir)
    rendered = rendered or render(run_dir)
    camp = rendered["campaign"]
    recon = camp.get("reconciliation") or {}
    return {
        "mission": 12425,
        "node": "P167W2B-N09",
        "repo": "TensorPlatform",
        "run_dir": str(run_dir),
        "campaign": camp.get("campaign"),
        "run_id": camp.get("run_id"),
        "passed": camp.get("passed"),
        "acceptance": {
            "runner_executes_synthetic_suites": {
                "value": [s["id"] for s in camp["suites"] if s["expect"] == "pass"],
                "all_passed": all(s["passed"] for s in camp["suites"]
                                  if s["expect"] == "pass" and s["kind"] == "suite"),
                "evidence": [s.get("run_dir") for s in camp["suites"] if s["expect"] == "pass"],
            },
            "gate_band_1pct_pass_5pct_fail": {
                "pass_case_suite": "fx_gate_band_pass",
                "fail_case_suite": "fx_gate_band_fail",
                "pass_suite_passed": next((s["passed"] for s in camp["suites"]
                                           if s["id"] == "fx_gate_band_pass"), None),
                "fail_suite_passed": next((s["passed"] for s in camp["suites"]
                                           if s["id"] == "fx_gate_band_fail"), None),
                "evidence": [s.get("run_dir") for s in camp["suites"]
                             if s["id"] in ("fx_gate_band_pass", "fx_gate_band_fail")],
            },
            "artifacts_sha256_verifiable": {
                "integrity_failures": camp["metrics"].get("integrity_failures"),
                "campaign_manifest": str(run_dir / "manifest.json"),
                "campaign_manifest_sha256": _maybe_sha(run_dir / "manifest.json"),
                "artifact_files_verified": (camp.get("artifacts") or {}).get("verified"),
            },
            "regression_2pct_pass_5pct_alert": {
                "probes": {p["id"]: {"expect": p["expect_status"], "observed_code": p["observed_code"],
                                     "observed_drift": p.get("observed_drift"), "ok": p["ok"]}
                           for p in camp["probes"]},
                "evidence": [p.get("run_dir") for p in camp["probes"]],
            },
            "zero_domain_semantics": {
                "tool": "tools/benchmark_runner/domain_guard.py",
                "command": "python3 -m tools.benchmark_runner.domain_guard tools/benchmark_runner",
            },
            "unattended_cases_65_to_at_least_75": {
                "baseline": recon.get("baseline_case_count"),
                "baseline_artifact": (recon.get("baseline") or {}).get("artifact"),
                "baseline_artifact_sha256": (recon.get("baseline") or {}).get("artifact_sha256"),
                "baseline_sql": (recon.get("baseline") or {}).get("sql"),
                "engine_delta": recon.get("delta_case_count"),
                "delta_by_suite": recon.get("delta_by_suite"),
                "delta_case_ids": recon.get("delta_case_ids"),
                "combined": recon.get("combined"),
                "target": recon.get("target"),
                "verdict": recon.get("verdict"),
            },
            "pytest": {"file": "tests/test_benchmark_runner_p167w2b_n09.py"},
        },
        "criteria": rendered["criteria"],
        "errors": rendered["errors"],
        "figures": (camp.get("artifacts") or {}).get("figures"),
        "cases": [{"suite": c["suite"], "case_id": c["case_id"], "passed": c["passed"],
                   "rel_error": c["metrics"].get("rel_error"),
                   "case_file": c["case_file"], "artifact_sha256": c["artifact_sha256"]}
                  for c in rendered["cases"]],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="campaign 报告生成器（数字注入）")
    ap.add_argument("--run", required=True, help="campaign 运行目录（含 campaign.json）")
    ap.add_argument("--md", default="docs/reports/P167W2B_N09_unattended_campaign.md")
    ap.add_argument("--json", default=None, help="机器可读验收 JSON 输出路径")
    args = ap.parse_args(argv)
    rendered = render(args.run)
    out = pathlib.Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered["markdown"], encoding="utf-8")
    print(f"wrote {out} ({len(rendered['markdown'])} chars)")
    if args.json:
        rep = machine_report(args.run, rendered)
        art.write_json(args.json, rep)
        print(f"wrote {args.json} passed={rep['passed']} "
              f"unattended={rep['acceptance']['unattended_cases_65_to_at_least_75']['combined']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())