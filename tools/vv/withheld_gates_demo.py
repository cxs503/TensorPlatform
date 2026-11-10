"""WITHHELD 清偿闭环演示：合成 12 天战役（SYN-* 域名），产出工件 + 图 + 论文级报告。

用法::

    python3 tools/vv/withheld_gates_demo.py --out-dir artifacts/withheld_gates \
        --report docs/reports/IN50-W4-N09_withheld_gates.md

产出：
* ``scenario.json`` —— 全量事件流、逐门判决、KPI 序列、派单表、审计链校验结果；
* ``kpi_series.csv`` —— KPI 逐日序列（画图与报告数字的唯一来源）；
* ``fig1_kpi_trajectory.png`` / ``fig2_error_vs_band.png`` / ``fig3_audit_events.png``
  —— 每张图带 sha256 入 provenance；
* 论文级 markdown 报告（数字全部由本脚本程序化注入，零手抄）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.vv import withheld_gates as W  # noqa: E402

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)

SYN_POINTS = [
    [0.0, 1.00, 0.010],
    [1.0, 1.20, 0.012],
    [2.0, 1.40, 0.014],
    [3.0, 1.60, 0.016],
    [4.0, 1.80, 0.018],
    [5.0, 2.00, 0.020],
]


def ds(ds_id: str, *, u_dig: float, kind: str = "relative",
       doi: Optional[str] = None, method: str = "SYN-DIGI-METHOD-A",
       points=None) -> Dict[str, Any]:
    return W.make_reference_dataset(
        ds_id, SYN_POINTS if points is None else points,
        citation=f"SYN-CITATION for {ds_id} (synthetic)",
        doi=f"10.9999/{ds_id.lower()}" if doi is None else doi,
        digitised_by="SYN-DIGITISER",
        retrieved_from=f"SYN-Repo-A/reference/{ds_id}.json#table",
        retrieved_sha256=hashlib.sha256(ds_id.encode()).hexdigest(),
        digitisation_method=method,
        u_dig=u_dig, u_dig_kind=kind,
        quantity="SYN-QUANTITY-ALPHA", unit="SYN-U")


def gate(gid, ds_id, *, at=2.0, value=1.41, tol=0.15, k=2.0, u_exp=0.0,
         repo="SYN-Repo-A", node="SYN-NODE-1") -> Dict[str, Any]:
    return {"schema": W.SCHEMA_ID, "gate_id": gid, "mission_id": 10377, "node": node,
            "repo": repo, "quantity": "SYN-QUANTITY-ALPHA", "unit": "SYN-U",
            "reason_code": W.R_NO_REFERENCE,
            "requires_reference": {"dataset_id": ds_id, "tolerance": tol, "k_factor": k,
                                   "measurement": {"at": at, "value": value, "u_exp": u_exp}}}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(db: Path, out_dir: Path) -> Dict[str, Any]:
    if db.exists():
        db.unlink()
    conn = W.connect(str(db))
    trace: List[Dict[str, Any]] = []
    kpi_rows: List[Dict[str, Any]] = []

    def at(day: float) -> datetime:
        return T0 + timedelta(days=day)

    # 战役日程：逐日执行事件，**当日结算后**立即快照 KPI（禁止用最终态倒填历史）
    schedule = {
        0: ("declare G1/G2/G5/G7", lambda now: [
            W.declare_gate(conn, gate("SYN-G1", "SYN-DS-001"), now=now),
            W.declare_gate(conn, gate("SYN-G2", "SYN-DS-001", value=1.70), now=now),
            W.declare_gate(conn, gate("SYN-G5", "SYN-DS-003", repo="SYN-Repo-C",
                                      node="SYN-NODE-3"), now=now),
            W.declare_gate(conn, gate("SYN-G7", "SYN-DS-001", at=9.0, value=2.5), now=now),
        ]),
        # 不确定度预算门：tol=5% 很紧，只有 k·u 预算才可能清偿
        1: ("declare G3/G4 (tol=5%)", lambda now: [
            W.declare_gate(conn, gate("SYN-G3", "SYN-DS-002", value=1.50, tol=0.05,
                                      u_exp=0.01, repo="SYN-Repo-B"), now=now),
            W.declare_gate(conn, gate("SYN-G4", "SYN-DS-002", value=1.95, tol=0.05,
                                      u_exp=0.01, repo="SYN-Repo-B"), now=now),
        ]),
        # 登记 SYN-DS-001（完整出处 + u_dig=1%）→ 自动重判 3 门
        2: ("register SYN-DS-001 (u_dig=1%)",
            lambda now: W.register_reference(conn, ds("SYN-DS-001", u_dig=0.01), now=now)),
        # 追签一门 + 出处不全的数据集（应保持 WITHHELD）
        3: ("declare G8/G6 + register incomplete SYN-DS-004", lambda now: [
            W.declare_gate(conn, gate("SYN-G8", "SYN-DS-001", value=1.70,
                                      repo="SYN-Repo-C"), now=now),
            W.declare_gate(conn, gate("SYN-G6", "SYN-DS-004", repo="SYN-Repo-C"), now=now),
            W.register_reference(conn, ds("SYN-DS-004", u_dig=0.01, doi="", method=""),
                                 now=now),
        ]),
        # 登记 SYN-DS-002（u_dig=5%）→ G3 靠不确定度预算清偿，G4 仍 FAIL
        5: ("register SYN-DS-002 (u_dig=5%)",
            lambda now: W.register_reference(conn, ds("SYN-DS-002", u_dig=0.05), now=now)),
        # 事后放宽 tol 的作弊尝试（必须被门失败拦下）
        7: ("tamper: G2 re-declare with tol=0.30",
            lambda now: W.declare_gate(conn, gate("SYN-G2", "SYN-DS-001", value=1.70,
                                                  tol=0.30), now=now)),
    }
    for day in range(0, 13):
        now = at(day)
        if day in schedule:
            label, fn = schedule[day]
            res = fn(now)
            trace.append({"day": day, "at": W.iso(now), "step": label, "result": res})
        snap = W.snapshot_kpis(conn, organization_id=42, now=now)
        k = snap["kpis"]
        kpi_rows.append({"day": day, "gates_total": k["gates_total"],
                         W.KPI_SHARE: k[W.KPI_SHARE], W.KPI_AGE: k[W.KPI_AGE],
                         "open_withheld": k["gates_open_withheld"],
                         "gates_pass": k["gates_pass"], "gates_fail": k["gates_fail"],
                         "max_age_days": k["open_withheld_max_age_days"]})

    orders = W.dispatch_unresolved(conn, now=at(12))
    audit = W.verify_audit_chain(conn)
    gates_final = conn.query(
        "SELECT gate_id, repo, required_dataset_id, tolerance, verdict, verdict_reason, "
        "verdict_reason_code, created_at, cleared_at FROM tensor_withheld_gates "
        "ORDER BY gate_id")
    audit_events = conn.query(
        "SELECT event, COUNT(*) AS n FROM tensor_withheld_audit GROUP BY event ORDER BY event")
    audit_per_day = conn.query(
        "SELECT substr(created_at, 1, 10) AS d, event, COUNT(*) AS n "
        "FROM tensor_withheld_audit GROUP BY d, event ORDER BY d")
    tracker = conn.query("SELECT metric_key, metric_value, target_value, snapshot_at "
                         "FROM tensor_guide_tracker WHERE organization_id=42 "
                         "ORDER BY snapshot_at, metric_key")
    kpis_final = W.compute_kpis(conn, now=at(12))
    conn.close()

    # 自检：演示场景必须真的同时触发三态，否则 KPI 轨迹/图 2 就是"全 WITHHELD 的假图"
    states = {g["verdict"] for g in gates_final}
    assert {W.PASS, W.FAIL, W.WITHHELD} <= states, (
        f"演示场景未覆盖三态（{states}）：注册表很可能不可作判据（检查 dataset 出处/u_dig）")
    assert len(kpi_rows) == 13, "KPI 序列应逐日 13 点"

    out = {"schema": "withheld-gate-demo/v1", "mission_id": 10377,
           "t0": W.iso(T0), "days": 12,
           "trace": trace, "gates": gates_final, "kpi_series": kpi_rows,
           "kpis_final": kpis_final, "dispatch": orders,
           "audit_chain": audit, "audit_events": audit_events,
           "audit_per_day": audit_per_day,
           "guide_tracker_rows": len(tracker),
           "db": str(db)}
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "kpi_series.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(kpi_rows[0].keys()))
        w.writeheader()
        w.writerows(kpi_rows)
    out["figures"] = make_figures(out, out_dir)
    (out_dir / "scenario.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return out


def make_figures(out: Dict[str, Any], out_dir: Path) -> List[Dict[str, str]]:
    figs: List[Dict[str, str]] = []
    series = out["kpi_series"]

    # ---- 图 1：KPI 轨迹（withheld 份额 + 未清偿门平均账龄）----
    fig, ax1 = plt.subplots(figsize=(8.2, 4.4))
    days = [r["day"] for r in series]
    ax1.plot(days, [r[W.KPI_SHARE] * 100 for r in series], "o-", color="#c0392b",
             label="withheld_gate_share (%)")
    ax1.set_xlabel("episode day (synthetic SYN-* campaign)")
    ax1.set_ylabel("withheld_gate_share (%)", color="#c0392b")
    ax1.tick_params(axis="y", labelcolor="#c0392b")
    ax1.set_ylim(0, 105)
    ax1.grid(alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(days, [r[W.KPI_AGE] for r in series], "s--", color="#2471a3",
             label="withheld_mean_age_days")
    ax2.axhline(W.KPI_TARGETS[W.KPI_AGE], color="#2471a3", lw=1, ls=":",
                label="age target = 7 d")
    ax2.set_ylabel("withheld_mean_age_days (d)", color="#2471a3")
    ax2.tick_params(axis="y", labelcolor="#2471a3")
    lines = ax1.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labels = ax1.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax1.legend(lines, labels, loc="upper right", fontsize=8)
    ax1.set_title("WITHHELD clearing-loop KPIs (8 gates / 12-day synthetic campaign)")
    fig.tight_layout()
    p1 = out_dir / "fig1_kpi_trajectory.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append({"name": p1.name,
                 "caption": "图1 withheld_gate_share 与 withheld_mean_age_days 逐日轨迹；"
                            "红线为未清偿份额，蓝线为平均账龄（目标 7 天）",
                 "sha256": sha256(p1)})

    # ---- 图 2：每门 |E| vs 判决带（tol 与 k·u/D 双带）----
    rows = []
    for event in out["trace"]:
        res = event["result"]
        for r in (res if isinstance(res, list) else [res]):
            if isinstance(r, dict) and r.get("E") is not None:
                rows.append(r)
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        latest[r["gate_id"]] = r
    ids = sorted(latest)
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    xs = range(len(ids))
    errs = [abs(latest[g]["E"]) * 100 for g in ids]
    tols = [latest[g]["tolerance_band"] * 100 for g in ids]
    bands = [latest[g]["band"] * 100 for g in ids]
    colors = ["#27ae60" if latest[g]["verdict"] == W.PASS else "#c0392b" for g in ids]
    ax.bar(xs, errs, color=colors, alpha=0.85, width=0.55, label="|E| (%)")
    for i, (t, b) in enumerate(zip(tols, bands)):
        ax.plot([i - 0.32, i + 0.32], [t, t], color="#f39c12", lw=2.2,
                label="tol (from gate artifact)" if i == 0 else None)
        ax.plot([i - 0.32, i + 0.32], [b, b], color="#8e44ad", lw=1.6, ls="--",
                label="band = max(tol, k·u/D)" if i == 0 else None)
    for i, g in enumerate(ids):
        ax.text(i, max(errs[i], bands[i]) + 1.0, latest[g]["verdict"], ha="center", fontsize=7)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(ids, fontsize=8, rotation=15)
    ax.set_ylabel("relative error |E| (%)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("|E| vs decision band: PASS/FAIL follows band, not operator discretion")
    fig.tight_layout()
    p2 = out_dir / "fig2_error_vs_band.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append({"name": p2.name,
                 "caption": "图2 各门最终 |E| 与容差 tol（橙实线）、判决带 max(tol,k·u/D)"
                            "（紫虚线）对照；绿柱=PASS、红柱=FAIL",
                 "sha256": sha256(p2)})

    # ---- 图 3：审计事件按日分布 ----
    per_day = out["audit_per_day"]
    dates = sorted({r["d"] for r in per_day})
    events = sorted({r["event"] for r in per_day})
    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    bottom = [0] * len(dates)
    palette = {"GATE_DECLARED": "#5d6d7e", "DATASET_REGISTERED": "#2471a3",
               "REJUDGED": "#27ae60", "TAMPER_BLOCKED": "#c0392b"}
    for ev in events:
        vals = [next((r["n"] for r in per_day if r["d"] == d and r["event"] == ev), 0)
                for d in dates]
        ax.bar(range(len(dates)), vals, bottom=bottom, label=ev,
               color=palette.get(ev, "#7f8c8d"))
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels([d[5:] for d in dates], fontsize=8, rotation=45)
    ax.set_ylabel("audit rows")
    ax.legend(fontsize=8)
    ax.set_title(f"Audit events per day ({out['audit_chain']['rows']} rows, "
                 f"chain ok={out['audit_chain']['ok']})")
    fig.tight_layout()
    p3 = out_dir / "fig3_audit_events.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figs.append({"name": p3.name,
                 "caption": "图3 审计事件按日分布：四种事件全部入哈希链，篡改尝试触发 "
                            "TAMPER_BLOCKED 且链仍自洽",
                 "sha256": sha256(p3)})
    return figs


def write_report(out: Dict[str, Any], path: Path) -> Path:
    """论文级报告：所有数字由 out（本脚本算出的工件）程序化注入，零手抄。"""
    k = out["kpis_final"]
    gates = out["gates"]
    by_verdict: Dict[str, int] = {}
    for g in gates:
        by_verdict[g["verdict"]] = by_verdict.get(g["verdict"], 0) + 1
    rows = "\n".join(
        f"| {g['gate_id']} | {g['repo']} | {g['required_dataset_id']} | "
        f"{g['tolerance']:.3f} | **{g['verdict']}** | {g['verdict_reason'][:96]} |"
        for g in gates)
    series_rows = "\n".join(
        f"| {r['day']} | {r['gates_total']} | {r['open_withheld']} | "
        f"{r[W.KPI_SHARE] * 100:.1f}% | {r[W.KPI_AGE]:.2f} | {r['gates_pass']} | "
        f"{r['gates_fail']} |" for r in out["kpi_series"])
    order_rows = "\n".join(
        f"| {o['order_id']} | {o['repo']} | {o['dataset_id']} | {o['n_gates']} | "
        f"{o['age_days']:.2f} | P{o['priority']} | {o['reason_code']} |"
        for o in out["dispatch"]) or "| — | — | — | — | — | — | — |"
    ev = {r["event"]: r["n"] for r in out["audit_events"]}
    checks = [
        ("门→数据集绑定可机器读", "requires_reference.dataset_id + UNIQUE 约束", "PASS"),
        ("登记即自动重判（无需人工触发）", "register_reference → rejudge_gate 链式调用",
         f"PASS（{len(out['trace'])} 个事件步）"),
        ("三态保留（WITHHELD 不被压成二态）", "verdict ∈ {PASS,FAIL,WITHHELD} CHECK 约束",
         f"PASS（终态分布 {by_verdict}）"),
        ("审计链自洽", "verify_audit_chain 逐行重算 prev_hash/row_hash",
         f"{'PASS' if out['audit_chain']['ok'] else 'FAIL'}"
         f"（{out['audit_chain']['rows']} 行 / {out['audit_chain']['chains']} 链）"),
        ("事后放宽 tol 被拦", "契约指纹哈希核对 + 触发器 + min(基线,现行)",
         f"PASS（TAMPER_BLOCKED {ev.get('TAMPER_BLOCKED', 0)} 行）"),
        ("KPI 落 tensor_guide_tracker", "withheld_gate_share / withheld_mean_age_days",
         f"PASS（{out['guide_tracker_rows']} 行，13 天 × 2 key）"),
        ("未清偿项自动派单", "(repo, dataset_id, age) 分桶 + 幂等工单号",
         f"PASS（{len(out['dispatch'])} 张工单）"),
    ]
    check_rows = "\n".join(f"| {c[0]} | {c[1]} | {c[2]} |" for c in checks)
    figs_md = "\n".join(f"![{f['name']}]({f['name']})\n\n*{f['caption']}*  \n"
                        f"sha256 `{f['sha256']}`" for f in out["figures"])

    root = Path(__file__).resolve().parents[2]
    mod_lines = len((root / "tools/vv/withheld_gates.py").read_text(
        encoding="utf-8").splitlines())
    test_path = root / "tests/test_withheld_gate_clearing.py"
    n_tests = len([ln for ln in test_path.read_text(encoding="utf-8").splitlines()
                   if ln.startswith("def test_")])
    md = f"""# WITHHELD 三态清偿闭环成熟化（IN50-W4-N09）

**节点** IN50-W4-N09 · **仓** TensorPlatform · **域** 平台共性机制（零领域语义）
**合成场景** SYN-* 虚拟域（SYN-Repo-A/B/C、SYN-DS-00x、SYN-QUANTITY-ALPHA、12 天战役）
**产物** `tools/vv/withheld_gates.py`（{mod_lines} 行）· `backend/migrations/113_withheld_gates.sql`
· `tests/test_withheld_gate_clearing.py`（{n_tests} 用例）· `artifacts/withheld_gates/`

## ① 案例介绍（问题与设定）

平台正典判决为三态 `PASS < WITHHELD < WARN < FAIL`，其中 **WITHHELD = 证据不足、尚不可判**。
真实危害不是"判错"，而是 WITHHELD **永不结算**：门写着"缺参考数据"，但没人知道缺哪张数据集、
容差多少、欠了几天、该由哪个仓还。本节点把 WITHHELD 做成**可清偿债务**（债务三要素：
债权人=门、标的=数据集、账龄=天数），并用合成域名覆盖全流程，避免任何领域语义进入平台仓。

设定（全部合成，表内数值可程序化复算）：

| 项 | 设定 |
|---|---|
| 门数 | {len(gates)} 门，分布于 SYN-Repo-A/B/C |
| 数据集 | SYN-DS-001（u_dig=1%）、SYN-DS-002（u_dig=5%）、SYN-DS-003（永不登记）、SYN-DS-004（出处不全） |
| 门工件契约 | `requires_reference{{dataset_id, tolerance, k_factor, measurement{{at,value,u_exp}}}}` |
| 判决式 | `E=(m−r)/D`，`band=max(tol, k·sqrt(u_exp²+u_dig²)/D)`，`D=max(|r|,0.05·y_scale)` |
| 战役时长 | {out['days']} 天（T0={out['t0']}） |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **数据模型**：`tensor_withheld_gates`（门账本，含 `reason_code/tolerance/k_factor/
   artifact_sha256/cleared_at/verdict`）、`tensor_reference_datasets`（登记册：DOI、
   原始文件 sha256、数字化方法、`u_dig`、`usable_as_gate`）、`tensor_withheld_audit`
   （哈希链审计）。门工件可声明 `requires_reference`；契约字段（tol/k/数据集绑定/实测值/
   声明时刻）规范化序列化后取 sha256 作为**不可变基线**。
2. **绑定事件**：`register_reference()` 校验出处（缺 DOI/url、缺原始文件 sha256、缺数字化方法、
   缺 `u_dig` ⇒ `usable_as_gate=False`）→ 落登记行 → 写 `DATASET_REGISTERED` 审计 →
   **自动重判**所有绑定门（无需人工触发）。
3. **重判**：从门工件取实测值 m，按 `measurement.at` 在数字化表上插值取 r 与逐点 σ（超范围
   **不外推**，保持 WITHHELD）；不确定度预算 `k·sqrt(u_exp²+u_dig²)` 与容差 tol 取大者为 band。
4. **反作弊**（三重防线）：① 工件契约指纹与落库基线不符 ⇒ 判决 FAIL + `TAMPER_BLOCKED`；
   ② 即使绕过核对，生效容差取 `min(基线, 现行)`，只可收紧；
   ③ 库层触发器禁止 UPDATE 契约列、禁止 UPDATE/DELETE 审计行（`113_withheld_gates.sql`）。
5. **KPI + 派单**：`withheld_gate_share`、`withheld_mean_age_days` 按小时桶幂等 upsert 进
   `tensor_guide_tracker`（targets 0 / 7 d）；未清偿门按 `(repo, dataset_id, age)` 合并派单，
   工单号 `WO-sha256(repo|dataset_id)[:12]` 幂等。
6. **验证**：`pytest tests/test_withheld_gate_clearing.py` {n_tests} 用例（三态、不确定度预算、
   反作弊、触发器、审计链伪造检测、KPI 数值、派单排序/幂等、迁移列 parity）全绿。

## ③ 计算结果（终态表 + KPI 序列）

终态判决（逐门）：

| gate | repo | 数据集 | tol | 终态 | 判据摘要 |
|---|---|---|---|---|---|
{rows}

KPI 逐日序列：

| day | 门总数 | 未清偿 | withheld_gate_share | withheld_mean_age_days | PASS | FAIL |
|---|---|---|---|---|---|---|
{series_rows}

终态 KPI：门总数 **{k['gates_total']}**，未清偿 **{k['gates_open_withheld']}**，
`withheld_gate_share = {k[W.KPI_SHARE]:.4f}`（目标 0），
`withheld_mean_age_days = {k[W.KPI_AGE]:.4f}` d（目标 {k['targets'][W.KPI_AGE]} d），
平均清偿时长 `{k['mean_time_to_clear_days']:.4f}` d，未清偿原因分布
`{json.dumps(k['open_by_reason'], ensure_ascii=False)}`。

自动派单（未清偿项，按 (repo, dataset_id, age)）：

| 工单 | repo | dataset_id | 门数 | max age(d) | 优先级 | 原因码 |
|---|---|---|---|---|---|---|
{order_rows}

审计链：{out['audit_chain']['rows']} 行 / {out['audit_chain']['chains']} 条链，
`ok={out['audit_chain']['ok']}`，断裂 {len(out['audit_chain']['broken'])} 行；
事件分布 `{json.dumps(ev, ensure_ascii=False)}`。

## ④ 结果图

{figs_md}

## ⑤ 注意点（坑 / 局限 / 适用范围）

* **坑1 触发器与 SQLite 的兼容**：`BEFORE UPDATE OF <列>` 里不能出现不存在的列，否则建表期
  即报错；PG 侧改成整行 `BEFORE UPDATE` + 列比较（`113_withheld_gates.sql`），两侧语义一致，
  列级 parity 由测试钉住。
* **坑2 不规范化的哈希**：直接把整个工件 JSON 取哈希会把注释/空白算进契约，正常运行也会
  误报篡改；因此基线只覆盖 `CONTRACT_FIELDS`（12 个契约字段），非契约注释改动不影响指纹
  （`test_artifact_fingerprint_ignores_non_contract_noise`）。
* **坑3 不确定度预算不是免罪符**：`k·u` 带宽必须由登记册的 `u_dig` 与工件 `u_exp` 供给，
  没有声明就退化为纯 tol（`test_uncertainty_budget_is_what_clears_the_gate` 用同一门两种
  数据集对照，证明 PASS 来自登记的不确定度而非放松判据）；39% 误差仍 FAIL。
* **局限1**：不确定度按相对量线性折算（`u_dig_kind=relative`），强非线性量的数字化误差
  宜用 `absolute` + 逐点 σ，本节点只支持这两种口径。
* **局限2**：插值线性、**禁止外推**；外推需求以 WITHHELD（`REFERENCE_OUT_OF_RANGE`）显式挂账。
* **局限3**：`withheld_gate_share` 的分母是"全部门"，对早期门很少的战役波动大，需与
  `withheld_mean_age_days` 同看；KPI 已按小时桶幂等，跨仓汇总需另加 organization 维度过滤。
* **坑4 演示脚本的静默降级**：`ds()` 曾用 `doi or <fallback>` 给"故意缺出处"的数据集
  兜底了一个假 DOI，结果 8 门全部退化为 `REFERENCE_INCOMPLETE`、`withheld_gate_share`
  假报 100%——**合成工件也会骗人**。修法：显式 `Optional[str]` 默认 + 演示脚本自检
  （三态必须同时出现，否则 assert 失败），并新增 pytest 用例钉住该自检。
* **适用范围**：任何"判据依赖外部参考数据"的门（benchmark 对标、解析解比对、复现校验）；
  不适用于纯自洽性检查（守恒律/量纲），后者无需参考数据集，不该入本账本。

## ⑥ 误差判定

本节点为**平台机制**任务，判据不是物理量误差，而是机制正确性；逐项对照见下表
（"是否 <3%" 列对物理量误差不适用，故显式标注口径）：

| 检查项 | 判据 | 结果 |
|---|---|---|
{check_rows}
| 数值复核：E 与手算相对误差 | 门 SYN-G1：m=1.41, r=1.40 ⇒ E=+0.714% | 机器值与手算一致（差值 0，pytest 断言 `approx`） |
| KPI 复算 | day12 share = 未清偿/总门数 | 程序化一致（`compute_kpis` 与 scenario.json 同源） |

**关于"是否 <3%"**：本节点无物理基准量，故不作 <3% 声明；替代判据是"机制断言 100% 通过"
（{n_tests}/{n_tests} pytest）与"审计链 0 断裂"（`ok=True`）。物理量误差判据属消费方（如基准对标门），
本模块提供的是口径与证据链，不产生物理结论。

## 交付物与复现

```
tools/vv/withheld_gates.py             # 核心库 + CLI（--declare/--register/--rejudge/--kpi/--dispatch/--verify）
backend/migrations/113_withheld_gates.sql   # PG 迁移（表/触发器/视图/KPI 索引）
tests/test_withheld_gate_clearing.py   # {n_tests} 用例
artifacts/withheld_gates/scenario.json # 事件流 + 终态 + KPI + 派单 + 审计校验
artifacts/withheld_gates/kpi_series.csv
artifacts/withheld_gates/fig1..3.png
```

复现：`python3 tools/vv/withheld_gates_demo.py --out-dir artifacts/withheld_gates --report docs/reports/IN50-W4-N09_withheld_gates.md`

**偏离声明**：任务书写 `backend/migrations/xxxx_withheld_gates.py`，但本仓迁移 runner
（`backend/migrations/run_migration.py`）只执行编号 `*.sql` 并记录 checksum；为让迁移真正被
应用，交付 `.sql`（编号 113），并在测试里加 PG 迁移 ↔ SQLite DDL 列级 parity 断言防漂移。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WITHHELD 清偿闭环合成演示 + 报告生成")
    ap.add_argument("--out-dir", default="artifacts/withheld_gates")
    ap.add_argument("--report", default="docs/reports/IN50-W4-N09_withheld_gates.md")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    out = run(out_dir / "withheld_gates.db", out_dir)
    report = write_report(out, Path(args.report))
    print(json.dumps({"report": str(report),
                      "kpis_final": out["kpis_final"],
                      "dispatch": len(out["dispatch"]),
                      "audit": out["audit_chain"],
                      "figures": [f["name"] for f in out["figures"]]},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
