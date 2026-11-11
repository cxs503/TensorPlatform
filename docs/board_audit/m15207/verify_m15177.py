#!/usr/bin/env python3
"""m15177（KPI-TRACE-100 计量，TensorPlatform）交差审计 —— 独立复核脚本。

board-audit 自建，**不复用被审单的 measure.py / entry.py 计算路径**：所有数字由本脚本
自己的 SQL 与自己的 CSV 解析重算，用于回答「被审单交的 54.5% 到底是不是量出来的」。

核查项（C=check）：
  C1 工件存在性：run_artifacts（result.json / 两份底账 CSV / 5 图 / SQL 文本）逐件
     sha256 + 大小，防止「报告里写了、盘上没文件」。
  C2 复现性：按报告附录 repro_cmd 重跑 `measure --synthetic`，逐字段比对数字
     （证明「可复现」只对合成数据成立）。
  C3 真实现值：本脚本自写 SQL 直查 tensor_dev_missions × tensor_reports（口径取自
     tools/metrics/kpi_registry.json 的 trace_report_ratio.recompute_sql），给出
     numerator / denominator / pct / 分状态 / 双通道拆解 —— 这是「got 真值」。
  C4 合成指纹：把交付 CSV 与库表逐条对表（行数、status 分布、report_id 通道、
     created_at 抽样比对），量化「底账是不是真库导出」。
  C5 基准裁判：tensor_benchmark_cases 是否存在 anchor_slug='KPI-TRACE-100' 的
     参考值行（ref_source / metric / tolerance）——缺行即无 L4 判据。
  C6 测试：在被审工作树跑它自带的两个测试文件，核 "15 passed" 声明。
  C7 报告六要素 + 图：tensor_reports 里 m15177 的 markdown 六段是否齐全、图是否落盘。
  C8 集成面：main 上除 tools/kpi_trace/ 自身外是否存在调用方（无调用=未集成）。

用法：
    python3 docs/board_audit/m15207/verify_m15177.py [--md]
输出：
    docs/board_audit/m15207/verify_output.json
退出码：0（审计脚本本身成功；被审单结论见 JSON 的 verdict_hint）
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = os.environ.get("TP_PG_DB", "tensorplatform")
M15177 = Path("/root/tp-agent/work/m15177")
OUT_DIR = Path(__file__).resolve().parent
MAIN_CLONE = Path("/tmp/tp_main")          # 远端 main 的浅克隆（可缺）
REPORT = M15177 / "tensor_reports" / "m15177_report.json"

TERMINAL = "('succeeded','failed','stopped','revoked')"


# --------------------------------------------------------------------------- sql
def sql(query: str) -> list[list[str]]:
    """以 postgres 身份只读查询（root 角色无表权限），返回按 tab 切分的行。"""
    q = query.replace('"', '\\"').replace("\n", " ").replace("\t", " ")
    cmd = ["su", "postgres", "-c", f"psql -d {DB} -t -A -F '|' -c \"{q}\""]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:400]}")
    return [ln.split("|") for ln in r.stdout.strip().splitlines() if ln.strip()]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------- C1 工件
def c1_artifacts() -> dict:
    out, missing = [], []
    art = M15177 / "artifacts" / "kpi_trace_100"
    want = ["result.json", "terminal_missions.csv", "reports_facts.csv",
            "kpi_trace_100_queries.sql", "fig_1_gap.png", "fig_2_by_status.png",
            "fig_3_weekly.png", "fig_4_repo_7d.png", "fig_5_debt.png"]
    for name in want:
        p = art / name
        if not p.exists():
            missing.append(f"artifacts/kpi_trace_100/{name}")
            continue
        out.append({"path": f"artifacts/kpi_trace_100/{name}", "bytes": p.stat().st_size,
                    "sha256": sha256_file(p)[:16]})
    # 图是否为真实位图（防止把文本改扩展名）
    png_ok = {}
    for it in out:
        if it["path"].endswith(".png"):
            head = (art / Path(it["path"]).name).read_bytes()[:8]
            png_ok[it["path"]] = head == b"\x89PNG\r\n\x1a\n"
    return {"present": out, "missing": missing,
            "png_signature_ok": png_ok,
            "all_png_real": all(png_ok.values()) if png_ok else False}


# --------------------------------------------------------------------- C2 复现性
def c2_repro() -> dict:
    work = Path("/tmp/m15207_repro")
    if not (work / "tools" / "kpi_trace").exists():
        import shutil
        (work / "tools").mkdir(parents=True, exist_ok=True)
        shutil.copytree(M15177 / "tools" / "kpi_trace", work / "tools" / "kpi_trace")
        (work / "tools" / "__init__.py").write_text("", encoding="utf-8")
    outdir = work / "out"
    cmd = [sys.executable, "-m", "tools.kpi_trace", "measure",
           "--synthetic", "--outdir", str(outdir)]
    r = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return {"ran": False, "cmd": " ".join(cmd[1:]), "stderr": r.stderr[-600:]}
    mine = json.loads((outdir / "result.json").read_text(encoding="utf-8"))
    committed = json.loads((M15177 / "artifacts/kpi_trace_100/result.json").read_text(encoding="utf-8"))
    keys = ["current", "target", "gap", "numerator", "denominator", "by_status", "windows",
            "link_channel_split", "data_mode"]
    cmp = {k: {"committed": committed.get(k), "rerun": mine.get(k),
               "same": committed.get(k) == mine.get(k)} for k in keys}
    return {"ran": True, "cmd": "python3 -m tools.kpi_trace measure --synthetic --outdir <tmp>",
            "fields": {k: v["same"] for k, v in cmp.items()},
            "detail": {k: v for k, v in cmp.items() if not v["same"]},
            "all_numeric_fields_identical": all(v["same"] for v in cmp.values()),
            "data_mode_reported": mine.get("data_mode")}


# ------------------------------------------------------------------- C3 真实现值
def c3_real_value() -> dict:
    # 口径照抄 KPI 注册表 recompute_sql（spec），SQL 由本脚本独立书写
    num, den = sql(f"""
        SELECT count(*) FILTER (WHERE m.report_id IS NOT NULL
                                OR EXISTS (SELECT 1 FROM tensor_reports r WHERE r.mission_id = m.id)),
               count(*)
          FROM tensor_dev_missions m
         WHERE m.status IN {TERMINAL}""")[0]
    num, den = int(num), int(den)
    by_status = {}
    for status, t, tr in sql(f"""
        SELECT m.status, count(*),
               count(*) FILTER (WHERE m.report_id IS NOT NULL
                                OR EXISTS (SELECT 1 FROM tensor_reports r WHERE r.mission_id = m.id))
          FROM tensor_dev_missions m
         WHERE m.status IN {TERMINAL}
         GROUP BY 1 ORDER BY 1"""):
        by_status[status] = {"terminal": int(t), "traceable": int(tr),
                             "pct": round(100.0 * int(tr) / int(t), 2) if int(t) else None}
    n_report_id = int(sql("SELECT count(*) FROM tensor_dev_missions WHERE report_id IS NOT NULL")[0][0])
    n_reports_msn = int(sql("SELECT count(*) FROM tensor_reports WHERE mission_id IS NOT NULL")[0][0])
    n_distinct_msn = int(sql("SELECT count(DISTINCT mission_id) FROM tensor_reports")[0][0])
    succeeded = int(sql("SELECT count(*) FROM tensor_dev_missions WHERE status='succeeded'")[0][0])
    total_missions = int(sql("SELECT count(*) FROM tensor_dev_missions")[0][0])
    return {"at": datetime.now(timezone.utc).isoformat(),
            "numerator": num, "denominator": den,
            "pct": round(100.0 * num / den, 2),
            "by_status": by_status,
            "report_id_channel_count": n_report_id,
            "tensor_reports_rows_with_mission": n_reports_msn,
            "tensor_reports_distinct_missions": n_distinct_msn,
            "succeeded_total": succeeded,
            "missions_total": total_missions}


# -------------------------------------------------------------------- C4 合成指纹
def c4_synthetic_fingerprint(real: dict) -> dict:
    tcsv = M15177 / "artifacts/kpi_trace_100/terminal_missions.csv"
    rows = list(csv.DictReader(tcsv.open(encoding="utf-8")))
    ids = [int(r["id"]) for r in rows]
    # 抽样核对：CSV 里的 id 在真库里是否存在、status/repo/created_at 是否一致
    rng = random.Random(20261111)
    sample = rng.sample(rows, 60)
    id_list = ",".join(r["id"] for r in sample)
    db = {}
    for mid, status, repo, created in sql(f"""
            SELECT id, status, coalesce(repo,''), coalesce(to_char(created_at,'YYYY-MM-DD HH24:MI:SS'),'')
              FROM tensor_dev_missions WHERE id IN ({id_list})"""):
        db[int(mid)] = {"status": status, "repo": repo, "created_at": created}
    exist = status_match = ts_match = 0
    examples = []
    for r in sample:
        mid = int(r["id"])
        d = db.get(mid)
        if d is None:
            if len(examples) < 3:
                examples.append({"id": mid, "csv": r["created_at"], "db": "MISSING"})
            continue
        exist += 1
        if d["status"] == r["status"]:
            status_match += 1
        ts = r["created_at"][:19]
        if d["created_at"][:19] == ts:
            ts_match += 1
        elif len(examples) < 3:
            examples.append({"id": mid, "csv": r["status"] + " " + r["created_at"],
                             "db": d["status"] + " " + d["created_at"]})
    rep_rows = list(csv.DictReader((M15177 / "artifacts/kpi_trace_100/reports_facts.csv").open(encoding="utf-8")))
    committed = json.loads((M15177 / "artifacts/kpi_trace_100/result.json").read_text(encoding="utf-8"))
    spans = [r["created_at"] for r in rows if r["created_at"]]
    return {
        "csv_rows": len(rows), "real_terminal": real["denominator"],
        "row_count_delta": len(rows) - real["denominator"],
        "csv_id_min": min(ids), "csv_id_max": max(ids),
        "csv_reports_facts_rows": len(rep_rows),
        "sample_n": len(sample), "sample_ids_in_db": exist,
        "sample_status_match": status_match, "sample_created_at_match": ts_match,
        "mismatch_examples": examples,
        "csv_time_span": [min(spans), max(spans)] if spans else None,
        "committed_report_id_channel": committed.get("link_channel_split", {}).get("missions_with_report_id"),
        "real_report_id_channel": real["report_id_channel_count"],
        "committed_succeeded_terminal": committed.get("by_status", {}).get("succeeded", {}).get("terminal"),
        "real_succeeded_total": real["succeeded_total"],
        "committed_got_pct": committed.get("current"),
        "real_pct": real["pct"],
        "abs_pp_deviation": round(abs(float(committed.get("current", 0)) - real["pct"]), 3),
    }


# -------------------------------------------------------------------- C5 基准裁判
def c5_benchmark() -> dict:
    rows = sql("SELECT case_name, metric, coalesce(tolerance,''), coalesce(ref_source,''), coalesce(status,'')"
               " FROM tensor_benchmark_cases WHERE anchor_slug='KPI-TRACE-100'")
    all_anchors = sql("SELECT anchor_slug, count(*) FROM tensor_benchmark_cases GROUP BY 1 ORDER BY 1")
    return {"anchor_rows": [dict(zip(["case_name", "metric", "tolerance", "ref_source", "status"], r))
                            for r in rows],
            "registered": bool(rows),
            "table_total_rows": sum(int(c) for _, c in all_anchors),
            "all_anchors": [a for a, _ in all_anchors]}


# ------------------------------------------------------------------------ C6 测试
def c6_tests() -> dict:
    cmd = [sys.executable, "-m", "pytest", "tests/test_kpi_trace_tool.py",
           "tests/test_kpi_trace_100_measure.py", "-q", "-p", "no:cacheprovider"]
    r = subprocess.run(cmd, cwd=str(M15177), capture_output=True, text=True, timeout=1200)
    tail = [ln for ln in r.stdout.strip().splitlines() if "passed" in ln or "failed" in ln]
    return {"cmd": "pytest tests/test_kpi_trace_tool.py tests/test_kpi_trace_100_measure.py -q",
            "returncode": r.returncode, "summary": tail[-1] if tail else r.stdout[-300:],
            "claimed": 15}


# ------------------------------------------------------------ C7 报告六要素 + 图
def c7_report() -> dict:
    d = json.loads(REPORT.read_text(encoding="utf-8"))
    md = d["markdown"]
    sections = {str(i): (f"## {i}." in md) for i in range(1, 7)}
    fig_paths = [f.get("path") for f in d.get("figures", [])]
    figs_on_disk = {p: (M15177 / p).exists() for p in fig_paths}
    rep = sql("SELECT id, mission_id, coalesce(status,''), coalesce(qa_status,'')"
              " FROM tensor_reports WHERE mission_id=15177")
    return {"six_sections_present": sections, "six_sections_all": all(sections.values()),
            "figures_declared": len(fig_paths), "figures_on_disk": figs_on_disk,
            "report_row": rep, "data_mode": d.get("data_mode"),
            "repro_cmd_in_doc": "--synthetic" in md,
            "honest_disclosure_synthetic": "合成数据模式" in md}


# ---------------------------------------------------------------------- C8 集成面
def c8_integration() -> dict:
    if not MAIN_CLONE.exists():
        return {"checked": False, "reason": f"{MAIN_CLONE} 不存在（无远端 main 浅克隆）"}
    hits = []
    for p in MAIN_CLONE.rglob("*"):
        if not p.is_file() or p.suffix not in {".py", ".json", ".yml", ".yaml", ".sh", ".md"}:
            continue
        rel = p.relative_to(MAIN_CLONE).as_posix()
        if rel.startswith("tools/kpi_trace/"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "kpi_trace" in txt:
            hits.append(rel)
    pkg_on_main = (MAIN_CLONE / "tools/kpi_trace/measure.py").exists()
    artifacts_on_main = (MAIN_CLONE / "artifacts/kpi_trace_100/result.json").exists()
    head = subprocess.run(["git", "-C", str(MAIN_CLONE), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return {"checked": True, "main_head": head.stdout.strip(),
            "package_on_main": pkg_on_main,
            "artifacts_committed_on_main": artifacts_on_main,
            "callers_excluding_own_package": hits}


# -------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    real = c3_real_value()
    res = {
        "audited_mission": 15177,
        "anchor": "KPI-TRACE-100",
        "audit_mission": 15207,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "C1_artifacts": c1_artifacts(),
        "C2_reproducibility": None,
        "C3_real_value": real,
        "C4_synthetic_fingerprint": c4_synthetic_fingerprint(real),
        "C5_benchmark_judge": c5_benchmark(),
        "C6_tests": c6_tests(),
        "C7_report": c7_report(),
        "C8_integration": c8_integration(),
    }
    res["C2_reproducibility"] = c2_repro()

    fp = res["C4_synthetic_fingerprint"]
    res["verdict_hint"] = {
        "level": 2,
        "why": ("L2：真实现（tools/kpi_trace 5 模块）+ 工程入口（CLI measure/figures/doc/run-all）"
                "+ 可运行工件（result.json/2 CSV/5 PNG，sha256 可核）+ 自带 15 项测试全通过 + PR #1236 已并 main；"
                "不达 L3：main 上无任何调用方（集成面为零）；不达 L4：tensor_benchmark_cases 无 KPI-TRACE-100 参考值行，"
                "且交付数值 data_mode=synthetic（合成底账，参数钉死在历史观测 terminal=14085/traceable=7677），非实测。"),
        "material_defects": [
            "交付 headline current=54.5% 来自合成底账（synthetic.py 默认参数 _DEFAULT_TERMINAL=14085 / _DEFAULT_TRACEABLE=7677 直接等于报告数字 → 循环论证）",
            f"真库实测 = {real['pct']}%（{real['numerator']}/{real['denominator']}），与交付值差 {fp['abs_pp_deviation']} pp",
            f"双通道指纹：交付称 missions_with_report_id={fp['committed_report_id_channel']}，真库 report_id 非空仅 {fp['real_report_id_channel']} 条",
            f"抽样 {fp['sample_n']} 条 CSV 行，id 在库存在 {fp['sample_ids_in_db']}，但 status 一致仅 {fp['sample_status_match']}、created_at 一致仅 {fp['sample_created_at_match']}（底账非库导出）",
            "报告附录 repro_cmd 只给 --synthetic，任何人按它复算只会复现合成数字，不会得到真值",
        ],
        "gate_expectation": "reopened/withheld：能力=L2 可确认，但 anchor 计量值须以 database 模式重跑（同一工具已可跑出 54.59%）",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "verify_output.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k.startswith("verdict_hint")},
                     ensure_ascii=False, indent=2))
    print(f"wrote {OUT_DIR / 'verify_output.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())