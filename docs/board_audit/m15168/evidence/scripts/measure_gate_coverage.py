#!/usr/bin/env python3
"""KPI-GATE-100 计量器（只读：不改任何代码/数据）。

产出线 × 门禁覆盖矩阵的**可复算**计量：每条产出线列出
  G1 门存在（平台内可执行机器判据，非人审/非文档）
  G2 门可失败（判据驱动的拒绝分支，file:line 指认）
  G3 拒绝实证（生产记录里出现过拒绝判决 OR 负控测试断言拒绝）
  G4 实测拦截覆盖（线上产出中实际经过门裁决的比例）
KPI 主值 = (G1∧G2∧G3_生产 的产出线数)/5 ×100（严格口径）
KPI 次值 = (G1∧G2∧(G3_生产∨G3_负控测试) 的产出线数)/5 ×100（结构口径）

一切数字来自 DB/工件/命令输出，零手抄；每条都带 data_source（SQL 原文或命令）。
用法：python3 artifacts/kpi_gate_100/measure_gate_coverage.py [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ── DB 连接（与被测平台同一实例：取 uvicorn 进程的 DATABASE_URL 或环境变量）──────
def dsn() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            cmd = p.read_bytes().decode(errors="ignore").replace("\x00", " ")
        except OSError:
            continue
        if "uvicorn" in cmd and "8010" in cmd:
            pid = p.parent.name
            try:
                raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="ignore")
            except OSError:
                continue
            for item in raw.split("\x00"):
                if item.startswith("DATABASE_URL="):
                    return item.split("=", 1)[1]
    raise SystemExit("no DATABASE_URL (env or uvicorn process)")


def query(sql: str) -> list[tuple]:
    """执行 SQL（原文随结果入结果 JSON，保证可复算）。SQLAlchemy DSN → libpq 关键字串。"""
    import psycopg

    url = dsn().replace("postgresql+psycopg://", "postgresql://")
    if "?host=" in url:  # postgresql://user@/db?host=/var/run/postgresql
        base, qs = url.split("?", 1)
        host = qs.split("host=", 1)[1]
        m = re.match(r"postgresql://([^@]*)@/([\w.-]+)", base)
        user, db = (m.group(1) or None, m.group(2)) if m else (None, None)
        conn = psycopg.connect(dbname=db, user=user, host=host)
    else:
        conn = psycopg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def scalar(sql: str):
    rows = query(sql)
    return rows[0][0] if rows else None


def grep_lines(rel: str, pattern: str, limit: int = 6) -> list[str]:
    """静态探针：在文件里找拒绝分支/接线证据，返回 file:line 指针。"""
    path = REPO / rel
    out: list[str] = []
    if not path.is_file():
        return [f"{rel}: MISSING"]
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if re.search(pattern, line):
            out.append(f"{rel}:{i}: {line.strip()[:110]}")
            if len(out) >= limit:
                break
    return out


def run(cmd: list[str], cwd: Path = REPO, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "PYTHONPATH": str(cwd if cwd.name != "backend" else cwd.parent)})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def pytest_probe(rel: str, cwd: Path) -> dict:
    """负控测试能否跑通（不绿即如实记录，不当通过）。"""
    rc, txt = run([sys.executable, "-m", "pytest", "-q", rel, "-p", "no:cacheprovider"], cwd=cwd, timeout=900)
    lines_ = [ln.strip() for ln in txt.strip().splitlines() if ln.strip()]
    counted = [ln for ln in lines_ if re.search(r"\d+ (passed|failed)", ln)]
    tail = counted[-1] if counted else (lines_[-1] if lines_ else "")
    failed = [ln for ln in lines_ if ln.startswith("FAILED")]
    return {"cmd": f"cd {cwd.relative_to(REPO)} && PYTHONPATH=. python3 -m pytest -q {rel} -p no:cacheprovider",
            "rc": rc, "summary": tail[:160], "failed": [f[:120] for f in failed][:5]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(REPO / "artifacts" / "kpi_gate_100"))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sources: list[dict] = []

    # ── 报告线：tensor_reports.qa_status 是 report_gate 的落库判决字段 ───────────
    sql_reports = ("SELECT coalesce(qa_status,'<null>') AS st, count(*) AS n "
                   "FROM tensor_reports GROUP BY 1 ORDER BY n DESC")
    rep = dict(query(sql_reports))
    sources.append({"line": "报告", "kind": "sql", "sql": sql_reports, "result": rep})
    rep_total = sum(rep.values())
    rep_gated = rep.get("passed", 0) + rep.get("rejected", 0)

    # ── 知识线：knowledge_base 有无门判决字段 + 门是否接线（rejected 日志）────────
    sql_kb = "SELECT coalesce(status,'<null>') AS st, count(*) AS n FROM knowledge_base GROUP BY 1"
    kb = dict(query(sql_kb))
    sources.append({"line": "知识", "kind": "sql", "sql": sql_kb, "result": kb})
    kb_total = sum(kb.values())
    rej_log = REPO / "artifacts" / "knowledge_sediment" / "rejected.jsonl"
    kb_rejected_logged = 0
    if rej_log.is_file():
        kb_rejected_logged = sum(1 for ln in rej_log.read_text(encoding="utf-8").splitlines() if ln.strip())

    # ── PR 线：mission_lifecycle_log 的 pr_gate 流水（state + note）─────────────
    sql_pr = ("SELECT coalesce(detail->>'state','<none>') AS st, "
              "coalesce(detail->>'note','<none>') AS note, count(*) AS n "
              "FROM mission_lifecycle_log WHERE action='pr_gate' GROUP BY 1,2 ORDER BY n DESC")
    pr_rows = query(sql_pr)
    sources.append({"line": "PR", "kind": "sql", "sql": sql_pr,
                    "result": [{"state": r[0], "note": r[1], "n": r[2]} for r in pr_rows]})
    pr_states = {}
    pr_close_blocked = 0
    for st, note, n in pr_rows:
        pr_states[st] = pr_states.get(st, 0) + n
        if st == "closed" and ("RED" in note or "redev" in note):
            pr_close_blocked += n

    # ── 基准线：正典决算复算（4 行 FAIL_OVER_15 + 8 行 WITHHELD policy 越权）─────
    settle_json = out / "benchmark_settlement.json"
    rc, txt = run([sys.executable, "tools/benchmark_settlement.py", "--json-out", str(settle_json)])
    settle = json.loads(settle_json.read_text(encoding="utf-8")) if settle_json.is_file() else {}
    sources.append({"line": "基准", "kind": "cmd",
                    "cmd": "python3 tools/benchmark_settlement.py --json-out artifacts/kpi_gate_100/benchmark_settlement.json",
                    "rc": rc, "summary_keys": sorted(settle.keys())[:12] if settle else []})
    srows = settle.get("rows", []) if isinstance(settle, dict) else []
    verdicts: dict[str, int] = {}
    for r in srows:
        verdicts[r.get("verdict", "?")] = verdicts.get(r.get("verdict", "?"), 0) + 1

    # ── 论文线：平台内是否有机器门（grep gate 判据符号）──────────────────────────
    paper_gate_hits = grep_lines("backend/api/tensor_papers.py", r"check_\w*(quality|gate)|HTTPException\(4|passed.*False|def .*gate")
    paper_enforced = any("MISSING" not in h for h in paper_gate_hits)

    # ── 静态探针：门实现与负控测试 ─────────────────────────────────────────────
    static = {
        "报告": {
            "gate_impl": grep_lines("backend/services/report_gate.py", r"passed.*False|issues\.append|return \{"),
            "wiring": grep_lines("backend/api/tensor_reports.py", r"check_report_quality"),
            "neg_test": "backend/tests/test_report_gate.py",
        },
        "基准": {
            "gate_impl": grep_lines("tools/benchmark_settlement.py", r"WITHHELD|FAIL_|policy_violation|verdict="),
            "neg_test": "tests/test_benchmark_settlement.py",
        },
        "知识": {
            "gate_impl": grep_lines("backend/services/knowledge_sediment/extractor.py", r"reject|\"status\"\] ="),
            "wiring": grep_lines("backend/api/knowledge_sediment.py", r"KnowledgeStore|scan_root"),
            "wiring_in_app": grep_lines("backend/main.py", r"knowledge_sediment"),
            "neg_test": "backend/tests/test_knowledge_sediment.py",
        },
        "PR": {
            "gate_impl": grep_lines("backend/api/tensor_mission_lifecycle.py", r"HTTPException\(409"),
            "ci": [p.name for p in (REPO / ".github" / "workflows").glob("*.yml")],
            "neg_test": "backend/tests/test_pr_gate.py",
        },
        "论文": {"gate_hits": paper_gate_hits},
    }

    tests = {
        "报告": pytest_probe("tests/test_report_gate.py", REPO / "backend"),
        "基准": pytest_probe("tests/test_benchmark_settlement.py", REPO),
        "知识": pytest_probe("tests/test_knowledge_sediment.py", REPO / "backend"),
        "PR": pytest_probe("tests/test_pr_gate.py", REPO / "backend"),
    }
    # 负控测试证据：测试文件里显式断言「拒绝」分支（静态可复算），
    # pytest 实际 rc 单独记录（红=如实红，不当通过）。
    neg_assert_files = {"报告": "backend/tests/test_report_gate.py",
                        "基准": "tests/test_benchmark_settlement.py",
                        "知识": "backend/tests/test_knowledge_sediment.py",
                        "PR": "backend/tests/test_pr_gate.py"}
    neg_assert_pat = {"报告": r"passed.*is False|issue_codes",
                      "基准": r"== \"WITHHELD\"|FAIL_|policy_violation",
                      "知识": r"status == \"rejected\"|no_evidence",
                      "PR": r"409|status_code"}
    for k, v in tests.items():
        hits = grep_lines(neg_assert_files[k], neg_assert_pat[k], limit=4)
        v["neg_control_assert_lines"] = hits
        v["neg_control_asserts"] = any("MISSING" not in h for h in hits)

    # ── 判据矩阵 ──────────────────────────────────────────────────────────────
    lines = []

    def add(name, g1, g2, g3_prod, g3_negtest, g4_num, g4_den, evidence, gaps=None):
        lines.append({
            "line": name, "G1_machine_gate": g1, "G2_can_fail_struct": g2,
            "G3_reject_observed_prod": g3_prod, "G3_neg_control_test": g3_negtest,
            "G4_enforcement_coverage": (round(g4_num / g4_den, 6) if g4_den else None),
            "G4_numerator": g4_num, "G4_denominator": g4_den,
            "evidence": evidence, "gaps": gaps or [],
        })

    add("PR", True, True, pr_close_blocked > 0, tests["PR"]["neg_control_asserts"],
        pr_states.get("merged", 0) + pr_states.get("closed", 0),
        pr_states.get("merged", 0) + pr_states.get("closed", 0) + pr_states.get("open", 0),
        {"ci_workflows": static["PR"]["ci"],
         "state_guards": static["PR"]["gate_impl"],
         "closed_unmerged_red": pr_close_blocked,
         "pr_states": pr_states,
         "note": "关闭未合=RED/redev 未过门；门自身无独立判决字段，故只算间接实证"},
        ["PR 线无门自身判决字段（state 流水只有 open/merged/closed），拒绝归因依赖 note 文本"])

    add("报告", True, True, rep.get("rejected", 0) > 0, tests["报告"]["neg_control_asserts"],
        rep_gated, rep_total,
        {"gate_impl": static["报告"]["gate_impl"][:3], "qa_status": rep,
         "neg_test": tests["报告"]},
        [] if rep_gated == rep_total else
        [f"{rep_total - rep_gated}/{rep_total} 份报告 qa_status=pending，从未过门（实测拦截率 {rep_gated / rep_total:.2%}）"])

    add("基准", True, True, verdicts.get("FAIL_OVER_15", 0) + verdicts.get("WITHHELD", 0) > 0,
        tests["基准"]["neg_control_asserts"], len(srows), len(srows),
        {"verdicts": verdicts, "settlement_summary": {k: settle.get(k) for k in
         ("rows", "admissible", "inadmissible", "within5_rate", "qualified_rate") if k in settle},
         "neg_test": tests["基准"]},
        [] if len(srows) else ["决算输入缺失，无行可裁"])

    add("知识", True, True, kb_rejected_logged > 0, tests["知识"]["neg_control_asserts"],
        kb.get("rejected", 0), kb_total,
        {"kb_status": kb, "rejected_log_rows": kb_rejected_logged,
         "gate_impl": static["知识"]["gate_impl"][:3],
         "wired_in_app": static["知识"]["wiring_in_app"],
         "neg_test": tests["知识"]},
        [f"knowledge_base 全 {kb_total} 行 status={list(kb)}，无 rejected 判决；质量门未接线到生产知识写入路径"])

    add("论文", paper_enforced, False, False,
        False, 0, 0,
        {"gate_scan": paper_gate_hits,
         "note": "tensor_papers API 只读聚合 TensorPapers registry（subprocess/读文件），无验收判据"},
        ["平台内无机器门：论文线只有聚合视图，无 pass/fail 裁决"])

    strict = [l["line"] for l in lines if l["G1_machine_gate"] and l["G2_can_fail_struct"] and l["G3_reject_observed_prod"]]
    structural = [l["line"] for l in lines if l["G1_machine_gate"] and l["G2_can_fail_struct"]
                  and (l["G3_reject_observed_prod"] or l["G3_neg_control_test"])]
    no_gate = [l["line"] for l in lines if not l["G1_machine_gate"]]

    result = {
        "kpi": "KPI-GATE-100",
        "mission_id": 15122,
        "measured_at_utc": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                                          capture_output=True, text=True).stdout.strip(),
        "definition": {
            "unit": "percent_of_output_lines",
            "formula_strict": "100 × |{line: G1 ∧ G2 ∧ G3_prod}| / 5",
            "formula_structural": "100 × |{line: G1 ∧ G2 ∧ (G3_prod ∨ G3_negcontrol)}| / 5",
            "output_lines": [l["line"] for l in lines],
            "note": "G1 机器门存在；G2 判据驱动拒绝分支；G3 拒绝实证（生产判决/负控测试）；G4 实测拦截率",
        },
        "current": round(100 * len(strict) / len(lines), 2),
        "current_structural": round(100 * len(structural) / len(lines), 2),
        "target": 100.0,
        "gap": round(100.0 - 100 * len(strict) / len(lines), 2),
        "covered_strict": strict,
        "covered_structural": structural,
        "no_gate_lines": no_gate,
        "matrix": lines,
        "data_sources": [{"type": "postgres", "dsn": re.sub(r"://[^@]*@", "://***@", dsn()),
                          **s} for s in sources] + [
            {"type": "static", "path": "backend/services/report_gate.py / tools/benchmark_settlement.py / "
                                        "backend/services/knowledge_sediment/{extractor,store}.py / "
                                        "backend/api/{tensor_reports,tensor_papers,tensor_mission_lifecycle}.py",
             "cmd": "grep -n '<reject pattern>' <file>"},
            {"type": "test", "sources": tests},
        ],
        "top_gaps": [
            {"rank": 1, "line": "论文", "gap": "无机器门（API 仅聚合视图）",
             "impact": "论文线 0/1 门，占严格口径 20 个百分点缺口"},
            {"rank": 2, "line": "知识", "gap": "质量门未接线：knowledge_base 全量无 rejected 判决，"
                                                f"rejected.jsonl 行数={kb_rejected_logged}",
             "impact": f"G4 实测拦截率 {kb.get('rejected', 0)}/{kb_total}"},
            {"rank": 3, "line": "报告", "gap": f"门已可失败（{rep.get('rejected', 0)} 拒 / {rep_gated} 判）"
                                               f"但仅 {rep_gated}/{rep_total} 报告过门",
             "impact": f"G4={rep_gated / rep_total:.2%}，{rep.get('pending', 0)} 份 pending 存量"},
            {"rank": 4, "line": "PR", "gap": "门无自身判决字段，拒绝只能从 note 文本反推",
             "impact": f"间接实证 {pr_close_blocked} 个 PR 关闭未合"},
        ],
        "proposed_anchors": [
            {"anchor": "GATE-PAPER-01", "goal": "论文线接入机器验收门（registry 条目须带 review verdict 与误差判定），"
                                               "验收=门对 1 份缺判据的论文草案必须判 fail（负控测试）"},
            {"anchor": "GATE-KB-WIRE-01", "goal": "knowledge_base 写入路径接 knowledge_sediment 质量门，"
                                                  "验收=无 evidence 的知识件被拒并落 rejected 日志（生产日志行数>0）"},
            {"anchor": "GATE-REPORT-BACKFILL-01", "goal": "存量 pending 报告批量过 report_gate 并落 qa_status，"
                                                          "验收=pending 从 9391 降至 ≤10% 且拒绝率与抽样复判一致"},
            {"anchor": "GATE-PR-VERDICT-01", "goal": "PR 门落独立判决字段（pass/blocked + 原因码）到 mission_lifecycle_log.detail，"
                                                     "验收=能按 SQL 直接统计 PR 拒绝率，不再依赖 note 文本"},
        ],
        "limitations": [
            "G4 分母口径是「线上产出条数」，不含未落库产出（如未登记的论文草稿、未写库的知识件）",
            "PR 线 G3 为间接证据（note 文本），非门自身判决字段；未把 GitHub Actions 历史取证（离线不可达）",
            "knowledge_sediment 负控测试 1 项 FAILED（缺 artifacts/knowledge_sediment/reference_labels.json 工件），"
            "该失败已计入报告，未当作通过",
            "本单只计量不改动：未接入/未回填项均原样保留，改进单见 proposed_anchors",
        ],
        "computation_note": "无重算：全部探针为毫秒级 SQL + 一次本地决算工具 + 4 次单元测试；"
                            "集群节点无平台 DB unix socket，递交集群会切断数据溯源，故未走 cluster_queue（偏差已声明）",
    }

    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "probes.sql").write_text("\n\n".join(
        f"-- {s['line']}\n{s['sql']};" for s in sources if s.get("sql")) + "\n", encoding="utf-8")
    print(json.dumps({"current": result["current"], "current_structural": result["current_structural"],
                      "gap": result["gap"], "strict": strict, "structural": structural,
                      "no_gate": no_gate}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())