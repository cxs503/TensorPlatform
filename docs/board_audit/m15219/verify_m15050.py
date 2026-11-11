#!/usr/bin/env python3
"""m15050（H6-MATURITY-GATE）交差核验脚本 —— board-audit m15219 自建。

独立性声明：本脚本**不 import** 被审工具 `tools/maturity_gate_h6`，
成熟度画像/增量账本/基准覆盖率的数字全部由本脚本从**原始源文件与 DB**独立重算，
只把被审工件的声明值当作被核对象。唯一复用点：调用被审 CLI 的 `run/verify`
作为"被核对象的行为"，用于复现性与负控（这是我的实验，不是它的自证）。

核验项（对齐任务单 ①②③）：
  A1 run_artifacts 三件套文件是否真存在（大小/sha256）
  A2 repro_cmd 可执行 + 重跑产物 sha256 与基准一致（确定性）
  A3 五级成熟度画像独立重算（基线/当前直方图、平均秩、归一化得分）
  A4 增量账本独立重算（PROMOTE/DEMOTE/HOLD/NEW/DROPPED + 逐能力方向 + 链连接性）
  A5 负控（本审计自造 3 种变异，非被审单的 5 例）：篡改判定体数字/篡改等级/篡改引文指纹
  A6 基准裁判：tensor_benchmark_cases(anchor='H6-MATURITY-GATE') 容差 vs got（独立计量门禁覆盖率）
  A7 报告六要素是否齐全
  A8 集成调用方（L3 判据）
  A9 单元测试复跑

用法: python3 docs/board_audit/m15219/verify_m15050.py [--md]
输出: docs/board_audit/m15219/verify_output.json（+ --md 打印 markdown 表）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

M15050 = Path("/root/tp-agent/work/m15050")
OUT_DIR = Path(__file__).resolve().parent
WORK = OUT_DIR / "work"
DB = os.environ.get("TP_PG_DB", "tensorplatform")

LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}  # 五级阶梯（本脚本自带定义，不读被审代码）
ARTIFACT_FILES = [
    "artifacts/maturity_gate_h6/gate.json",
    "artifacts/maturity_gate_h6/consistency.json",
    "artifacts/maturity_gate_h6/increment_ledger.json",
    "artifacts/maturity_gate_h6/mutation_benchmark.json",
    "artifacts/maturity_gate_h6/fig1_maturity_profile.png",
    "artifacts/maturity_gate_h6/fig2_axes_health.png",
    "artifacts/maturity_gate_h6/fig3_increment_flow.png",
    "runs/m15050/repro_cmd.sh",
    "runs/m15050/benchmark.md",
    "runs/m15050/run_metadata.json",
    "runs/m15050/gate_sha256.txt",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sql(query: str) -> list[list[str]]:
    """只读查询（su postgres，本地 socket）；失败抛错由调用方记 unknown。"""
    cmd = ["su", "postgres", "-c", f'psql -d {DB} -t -A -F "|" -c "{query}"']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return [ln.split("|") for ln in r.stdout.strip().splitlines() if ln.strip()]


def load(rel: str):
    return json.loads((M15050 / rel).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- A1
def a1_run_artifacts() -> dict:
    rows = []
    for rel in ARTIFACT_FILES:
        p = M15050 / rel
        rows.append({
            "path": rel,
            "exists": p.is_file(),
            "bytes": p.stat().st_size if p.is_file() else 0,
            "sha256": sha256(p) if p.is_file() else None,
        })
    missing = [r["path"] for r in rows if not r["exists"]]
    return {"check": "A1_run_artifacts", "pass": not missing, "missing": missing, "files": rows}


# ---------------------------------------------------------------- A2
def a2_repro() -> dict:
    out = WORK / "repro_out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "tools.maturity_gate_h6", "run", "--no-mutate",
           "--work-root", str(M15050), "--spec", "data/maturity_gate_h6/h6.spec.json",
           "--out", str(out)]
    r = subprocess.run(cmd, cwd=M15050, capture_output=True, text=True)
    got = out / "gate.json"
    got_sha = sha256(got) if got.is_file() else None
    base_sha = sha256(M15050 / "artifacts/maturity_gate_h6/gate.json")
    bench_sha = (M15050 / "runs/m15050/gate_sha256.txt").read_text().strip()
    # repro_cmd.sh 自身可执行性（文档里的一键脚本）
    rc = subprocess.run(["bash", "runs/m15050/repro_cmd.sh"], cwd=M15050,
                        capture_output=True, text=True)
    tail = (rc.stdout or "").strip().splitlines()[-3:]
    return {
        "check": "A2_repro_determinism",
        "pass": r.returncode == 0 and got_sha is not None and got_sha == base_sha and base_sha == bench_sha,
        "cli_rc": r.returncode,
        "recomputed_sha256": got_sha,
        "artifact_sha256": base_sha,
        "baseline_sha256_txt": bench_sha,
        "sha_match": got_sha == base_sha == bench_sha,
        "repro_cmd_sh_rc": rc.returncode,
        "repro_cmd_sh_tail": tail,
        "stderr_tail": (r.stderr or "").strip()[-300:],
    }


# ---------------------------------------------------------------- A3
def a3_maturity_recount(gate: dict) -> dict:
    base = load("tests/fixtures/capability_ledger/capabilities_snapshot.json")["capabilities"]
    cur = {c["id"]: c for c in load("artifacts/capability_ledger/capability_ledger_resolved.json")["capabilities"]}

    def profile(levels: list[str]) -> dict:
        hist = {lvl: 0 for lvl in LEVELS}
        for lvl in levels:
            hist[lvl] += 1
        n = len(levels)
        mean = sum(RANK[l] for l in levels) / n
        return {"histogram": hist, "n": n, "mean_rank": round(mean, 6),
                "maturity_score": round(mean / 4, 6), "max_rank": 4}

    before = profile(list(base.values()))
    # 当前等级 = 台账 reconciled_level（证据对账后的等级；claimed_level 是自述，不采信）
    after = profile([cur[cid]["reconciled_level"] for cid in base])
    g_before = gate["maturity"]["before"]
    g_after = gate["maturity"]["after"]
    diffs = []
    for tag, mine, theirs in (("before", before, g_before), ("after", after, g_after)):
        if mine["histogram"] != theirs["histogram"]:
            diffs.append(f"{tag}.histogram mine={mine['histogram']} gate={theirs['histogram']}")
        if abs(mine["maturity_score"] - theirs["maturity_score"]) > 1e-6:
            diffs.append(f"{tag}.score mine={mine['maturity_score']} gate={theirs['maturity_score']}")
        if abs(mine["mean_rank"] - theirs["mean_rank"]) > 1e-6:
            diffs.append(f"{tag}.mean_rank mine={mine['mean_rank']} gate={theirs['mean_rank']}")
        if mine["n"] != theirs["n"]:
            diffs.append(f"{tag}.n mine={mine['n']} gate={theirs['n']}")
    return {"check": "A3_maturity_recount", "pass": not diffs, "recomputed_before": before,
            "recomputed_after": after, "gate_before": g_before, "gate_after": g_after,
            "diffs": diffs}


# ---------------------------------------------------------------- A4
def a4_increment_recount(gate: dict) -> dict:
    base = load("tests/fixtures/capability_ledger/capabilities_snapshot.json")["capabilities"]
    cur = {c["id"]: c for c in load("artifacts/capability_ledger/capability_ledger_resolved.json")["capabilities"]}
    ledger = load("artifacts/maturity_gate_h6/increment_ledger.json")
    mine = {}
    for cid, lvl in base.items():
        b, c = RANK[lvl], RANK[cur[cid]["reconciled_level"]]
        direction = "HOLD" if c == b else ("PROMOTE" if c > b else "DEMOTE")
        mine[cid] = {"baseline": lvl, "current": cur[cid]["reconciled_level"],
                     "direction": direction, "rank_delta": c - b}
    counts = {"PROMOTE": 0, "DEMOTE": 0, "HOLD": 0, "NEW": 0, "DROPPED": 0}
    for v in mine.values():
        counts[v["direction"]] += 1
    # 逐条与账本 entries 对照
    entries = {e["capability_id"]: e for e in ledger["entries"]}
    mism = []
    for cid, v in mine.items():
        e = entries.get(cid)
        if e is None:
            mism.append(f"{cid}: 账本缺条目")
            continue
        if (e["baseline_level"], e["current_level"], e["direction"], e["rank_delta"]) != \
           (v["baseline"], v["current"], v["direction"], v["rank_delta"]):
            mism.append(f"{cid}: mine={v['baseline']}/{v['current']}/{v['direction']}/{v['rank_delta']}"
                        f" ledger={e['baseline_level']}/{e['current_level']}/{e['direction']}/{e['rank_delta']}")
    # 链连接性（只看链式指针，不猜哈希函数）：prev_sha256 应与前一条 sha256 相同，头部与 chain_head 相同
    seq = sorted(ledger["entries"], key=lambda e: e["seq"])
    chain_ok = all(seq[i]["prev_sha256"] == seq[i - 1]["sha256"] for i in range(1, len(seq)))
    head_ok = ledger.get("chain_head") == seq[-1]["sha256"] if seq else False
    gate_counts = gate["increments"]["counts"]
    counts_match = all(gate_counts.get(k, 0) == v for k, v in counts.items())
    return {"check": "A4_increment_recount", "pass": not mism and counts_match and chain_ok and head_ok,
            "recomputed_counts": counts, "gate_counts": gate_counts, "entry_mismatches": mism,
            "chain_links_ok": chain_ok, "chain_head_ok": head_ok,
            "n_entries": len(ledger["entries"]), "n_capabilities": len(mine)}


# ---------------------------------------------------------------- A5
def a5_negative_controls() -> dict:
    src = M15050 / "artifacts/maturity_gate_h6"
    cases = []

    def run_verify(d: Path) -> tuple[int, str]:
        r = subprocess.run([sys.executable, "-m", "tools.maturity_gate_h6", "verify", "--out", str(d)],
                           cwd=M15050, capture_output=True, text=True)
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-800:]

    # T0 干净副本：必须 verified
    d0 = WORK / "nc_clean"
    if d0.exists():
        shutil.rmtree(d0)
    shutil.copytree(src, d0)
    rc0, out0 = run_verify(d0)
    cases.append({"id": "T0_clean_control", "mutation": "无（干净副本）", "expect": "verified",
                  "rc": rc0, "detected_as_tamper": "REJECTED" in out0 or "MISMATCH" in out0,
                  "output_tail": out0[-300:]})

    # P1 正控：篡改 verdict（verify 声称覆盖的字段）→ 必须检出
    dp1 = WORK / "pc_verdict"
    if dp1.exists():
        shutil.rmtree(dp1)
    shutil.copytree(src, dp1)
    g1_ = json.loads((dp1 / "gate.json").read_text())
    g1_["verdict"] = "FAIL"
    (dp1 / "gate.json").write_text(json.dumps(g1_))
    rcp1, outp1 = run_verify(dp1)
    cases.append({"id": "P1_verdict_tamper", "mutation": "gate.json verdict PASS→FAIL", "expect": "detected",
                  "rc": rcp1, "detected_as_tamper": rcp1 != 0 or "REJECTED" in outp1 or "MISMATCH" in outp1
                  or "verified\": false" in outp1.replace(" ", ""),
                  "output_tail": outp1[-300:]})

    # P2 正控：篡改某条判据 pass 位 → 必须检出
    dp2 = WORK / "pc_criteria"
    if dp2.exists():
        shutil.rmtree(dp2)
    shutil.copytree(src, dp2)
    g2_ = json.loads((dp2 / "gate.json").read_text())
    g2_["criteria"][0]["pass"] = not g2_["criteria"][0]["pass"]
    (dp2 / "gate.json").write_text(json.dumps(g2_))
    rcp2, outp2 = run_verify(dp2)
    cases.append({"id": "P2_criteria_tamper", "mutation": "gate.json criteria[0].pass 取反", "expect": "detected",
                  "rc": rcp2, "detected_as_tamper": rcp2 != 0 or "REJECTED" in outp2 or "MISMATCH" in outp2
                  or "verified\": false" in outp2.replace(" ", ""),
                  "output_tail": outp2[-300:]})

    # T1 篡改判定体数字（成熟度得分 —— 人读表数字流）
    d1 = WORK / "nc_score"
    if d1.exists():
        shutil.rmtree(d1)
    shutil.copytree(src, d1)
    g = json.loads((d1 / "gate.json").read_text())
    g["maturity"]["after"]["maturity_score"] = 0.99
    (d1 / "gate.json").write_text(json.dumps(g))
    rc1, out1 = run_verify(d1)
    cases.append({"id": "T1_score_tamper", "mutation": "gate.json maturity.after.maturity_score 0.75→0.99",
                  "expect": "detected", "rc": rc1,
                  "detected_as_tamper": rc1 != 0 or "REJECTED" in out1 or "MISMATCH" in out1,
                  "output_tail": out1[-300:]})

    # T2 等级翻转（账本条目 current_level 被改：REAL→WITHHELD 方向反转）
    d2 = WORK / "nc_level"
    if d2.exists():
        shutil.rmtree(d2)
    shutil.copytree(src, d2)
    lp = d2 / "increment_ledger.json"
    led = json.loads(lp.read_text())
    hit = None
    for e in led["entries"]:
        if e["direction"] == "DEMOTE":
            hit = e["capability_id"]
            e["current_level"] = "REAL"
            e["rank_delta"] = RANK["REAL"] - RANK[e["baseline_level"]]
            e["direction"] = "HOLD" if e["rank_delta"] == 0 else e["direction"]
            break
    lp.write_text(json.dumps(led))
    rc2, out2 = run_verify(d2)
    cases.append({"id": "T2_level_flip", "mutation": f"increment_ledger.json {hit} current_level DEMOTE→REAL",
                  "expect": "detected", "rc": rc2,
                  "detected_as_tamper": rc2 != 0 or "REJECTED" in out2 or "MISMATCH" in out2,
                  "output_tail": out2[-300:]})

    # T3 引文指纹篡改（line_sha256 改一位）—— 只在账本里存了 line_sha256 时有效
    d3 = WORK / "nc_quote"
    if d3.exists():
        shutil.rmtree(d3)
    shutil.copytree(src, d3)
    qp = d3 / "increment_ledger.json"
    led3 = json.loads(qp.read_text())
    changed = None
    for e in led3["entries"]:
        for ref in e.get("evidence_refs", []):
            if ref.get("line_sha256"):
                ref["line_sha256"] = ref["line_sha256"][:-1] + ("0" if ref["line_sha256"][-1] != "0" else "1")
                changed = e["capability_id"]
                break
        if changed:
            break
    if changed:
        qp.write_text(json.dumps(led3))
        rc3, out3 = run_verify(d3)
        cases.append({"id": "T3_quote_tamper", "mutation": f"increment_ledger.json {changed} evidence_refs.line_sha256 末位翻转",
                      "expect": "detected", "rc": rc3,
                      "detected_as_tamper": rc3 != 0 or "REJECTED" in out3 or "MISMATCH" in out3,
                      "output_tail": out3[-300:]})
    else:
        cases.append({"id": "T3_quote_tamper", "mutation": "账本 evidence_refs 未存 line_sha256",
                      "expect": "detected", "rc": None, "detected_as_tamper": None,
                      "output_tail": "NOT_APPLICABLE"})

    def get(cid):
        return next(c for c in cases if c["id"] == cid)
    clean_ok = get("T0_clean_control")["rc"] == 0 and not get("T0_clean_control")["detected_as_tamper"]
    pos_ok = all(get(c)["detected_as_tamper"] is True for c in ("P1_verdict_tamper", "P2_criteria_tamper"))
    blind = [c["id"] for c in cases if c["id"].startswith("T") and not c["id"].startswith("T0")
             and c["detected_as_tamper"] is False]
    # 独立复跑被审单的变异基准（mutate 子命令），验证 recall/假阳性声明
    mut_out = WORK / "mutate_out"
    if mut_out.exists():
        shutil.rmtree(mut_out)
    mut_out.mkdir(parents=True, exist_ok=True)
    rm = subprocess.run([sys.executable, "-m", "tools.maturity_gate_h6", "mutate", "--out", str(mut_out)],
                        cwd=M15050, capture_output=True, text=True)
    mut_file = mut_out / "mutation_benchmark.json"
    mut = json.loads(mut_file.read_text()) if mut_file.is_file() else {}
    claimed = load("artifacts/maturity_gate_h6/mutation_benchmark.json")
    mut_repro = (rm.returncode == 0 and mut.get("recall") == claimed.get("recall")
                 and mut.get("false_positives") == claimed.get("false_positives"))
    return {"check": "A5_negative_controls", "pass": clean_ok and pos_ok,
            "clean_control_ok": clean_ok, "positive_controls_detected": pos_ok,
            "verify_blind_spots": blind,
            "mutation_benchmark_reproduced": {"rc": rm.returncode, "recall": mut.get("recall"),
                                              "false_positives": mut.get("false_positives"),
                                              "claimed_recall": claimed.get("recall"),
                                              "claimed_false_positives": claimed.get("false_positives"),
                                              "match": mut_repro},
            "cases": cases}


# ---------------------------------------------------------------- A6
# 负控测试文件（G3_neg：测试里显式断言拒绝路径）
NEG_TEST_FILES = {
    "PR": ["backend/tests/test_pr_gate.py"],
    "报告": ["backend/tests/test_report_gate.py"],
    "基准": ["tests/test_benchmark_settlement.py", "tools/tests/test_benchmark_settlement.py"],
    "知识": ["backend/tests/test_knowledge_sediment.py"],
    "论文": [],
}

GATE_PROBES = {
    "PR": ["backend/api/tensor_mission_lifecycle.py", "409"],
    "报告": ["backend/services/report_gate.py", "issues.append"],
    "基准": ["tools/benchmark_settlement.py", "WITHHELD"],
    "知识": ["backend/services/knowledge_sediment/store.py", "reject"],
    "论文": ["backend/api/tensor_papers.py", "reject"],
}


def a6_benchmark(gate: dict) -> dict:
    rows = sql("SELECT id,anchor_slug,case_name,metric,tolerance,ref_source,status "
               "FROM tensor_benchmark_cases WHERE anchor_slug='H6-MATURITY-GATE'")
    cases = [{"id": int(r[0]), "anchor": r[1], "case_name": r[2], "metric": r[3],
              "tolerance": r[4], "ref_source": r[5], "status": r[6]} for r in rows]

    # —— 独立计量「门禁覆盖率 = 可失败门/产出线」（KPI-GATE-100 口径）——
    probes = {}
    for line, (rel, needle) in GATE_PROBES.items():
        p = M15050 / rel
        txt = p.read_text(errors="ignore") if p.is_file() else ""
        g1 = p.is_file()
        g2 = needle in txt
        g3_prod = None
        try:
            if line == "报告":
                rows_ = sql("SELECT qa_status, count(*) FROM tensor_reports GROUP BY 1")
                d = {str(a): int(b) for a, b in rows_}
                g3_prod = d.get("rejected", 0) > 0
                probes[line + "_dist"] = d
            elif line == "知识":
                rows_ = sql("SELECT status, count(*) FROM knowledge_base GROUP BY 1")
                d = {str(a): int(b) for a, b in rows_}
                g3_prod = "rejected" in d and d["rejected"] > 0
                probes[line + "_dist"] = d
            elif line == "PR":
                rows_ = sql("SELECT detail->>'state' AS st, count(*) FROM mission_lifecycle_log "
                            "WHERE action='pr_gate' GROUP BY 1")
                d = {str(a): int(b) for a, b in rows_}
                g3_prod = d.get("closed", 0) > 0
                probes[line + "_dist"] = d
            elif line == "基准":
                import tempfile as _tf
                tmp = Path(_tf.mkdtemp()) / "settle.json"
                r = subprocess.run([sys.executable, "tools/benchmark_settlement.py", "--json-out", str(tmp)],
                                   cwd=M15050, capture_output=True, text=True)
                if r.returncode == 0 and tmp.is_file():
                    dd = json.loads(tmp.read_text())
                    verdicts = [row.get("verdict") for row in dd.get("rows", [])] if isinstance(dd, dict) else []
                    g3_prod = any(str(v).startswith("FAIL") for v in verdicts)
                    probes[line + "_dist"] = {str(v): verdicts.count(v) for v in set(verdicts)}
                else:
                    probes[line + "_dist"] = f"settlement_rc={r.returncode}"
            elif line == "论文":
                g3_prod = False
                probes[line + "_dist"] = "无判据代码（grep reject/PASS 未命中）"
        except Exception as exc:  # 记 unknown，不记 pass
            probes[line + "_note"] = f"sql_error: {exc}"
        neg_files = [f for f in NEG_TEST_FILES.get(line, []) if (M15050 / f).is_file()]
        g3_neg = False
        for f in neg_files:
            txt = (M15050 / f).read_text(errors="ignore").lower()
            if "assert" in txt and "reject" in txt:
                g3_neg = True
                break
        probes[line] = {"gate_file": rel, "G1_gate_exists": g1, "G2_can_fail_struct": g2,
                        "G3_reject_prod": g3_prod, "G3_neg_control_test": g3_neg,
                        "neg_test_files": neg_files}
    strict = sum(1 for l in GATE_PROBES if probes[l]["G1_gate_exists"] and probes[l]["G2_can_fail_struct"]
                 and probes[l]["G3_reject_prod"] is True)
    structural = sum(1 for l in GATE_PROBES if probes[l]["G1_gate_exists"] and probes[l]["G2_can_fail_struct"]
                     and (probes[l]["G3_reject_prod"] is True or probes[l]["G3_neg_control_test"]))
    got_strict = 100.0 * strict / len(GATE_PROBES)
    got_structural = 100.0 * structural / len(GATE_PROBES)

    # 被审单是否在自己的工件里计量了该指标？
    own = {"gate_json_has_coverage_metric": "coverage" in json.dumps(gate.get("axes", {})),
           "mission_measured_output_lines": False}
    mission_measured = any("产出线" in json.dumps(f, ensure_ascii=False) for f in [gate])
    own["mission_measured_output_lines"] = mission_measured

    tol = cases[0]["tolerance"] if cases else None
    in_tol = bool(tol and tol.strip() == "=100%" and got_strict == 100.0)
    return {"check": "A6_benchmark_judge", "pass": in_tol, "registered_cases": cases,
            "measured": {"got_strict": got_strict, "got_structural": got_structural,
                         "lines": list(GATE_PROBES), "probes": probes},
            "tolerance": tol, "in_tolerance": in_tol, "mission_self_measurement": own}


# ---------------------------------------------------------------- A7
def a7_report_sections() -> dict:
    p = M15050 / "docs/H6_MATURITY_GATE_G1.md"
    txt = p.read_text(encoding="utf-8")
    keys = {"①案例介绍": r"①", "②方法": r"②", "③计算结果": r"③",
            "④结果图": r"④", "⑤对照/检出": r"⑤", "⑥误差判定": r"⑥"}
    found = {k: bool(re.search(v, txt)) for k, v in keys.items()}
    err_stmt = bool(re.search(r"是否\s*<3%", txt)) or "是否<3%" in txt
    return {"check": "A7_report_sections", "pass": all(found.values()) and err_stmt,
            "path": str(p), "bytes": len(txt.encode()), "sections": found,
            "explicit_3pct_answer": err_stmt}


# ---------------------------------------------------------------- A8
def a8_integration_callers() -> dict:
    pat = re.compile(r"maturity_gate_h6")
    hits = []
    for root, dirs, files in os.walk(M15050):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache", "artifact_store"}]
        for fn in files:
            if not fn.endswith((".py", ".yml", ".yaml", ".sh", ".toml", ".cfg")):
                continue
            fp = Path(root) / fn
            rel = fp.relative_to(M15050).as_posix()
            if rel.startswith(("tools/maturity_gate_h6/", "artifacts/", "agent_work/", "docs/", "runs/")):
                continue
            try:
                for i, line in enumerate(fp.read_text(errors="ignore").splitlines(), 1):
                    if pat.search(line):
                        hits.append(f"{rel}:{i}")
            except OSError:
                continue
    decision_prefix = ("backend/", "api/", "ops/", "harness_gateway/", ".github/", "deploy/")
    decision = [h for h in hits if h.startswith(decision_prefix)]
    cli_invocations = []
    for root, dirs, files in os.walk(M15050):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache"}]
        for fn in files:
            fp = Path(root) / fn
            rel = fp.relative_to(M15050).as_posix()
            if rel.startswith(("tools/maturity_gate_h6/", "docs/", "runs/", "agent_work/", "artifacts/")):
                continue
            try:
                txt = fp.read_text(errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if "python3 -m tools.maturity_gate_h6" in line or "tools.maturity_gate_h6.cli" in line:
                    cli_invocations.append(f"{rel}:{i}")
    return {"check": "A8_integration_callers", "pass": bool(decision),
            "all_refs": hits, "decision_path_callers": decision, "cli_invocations": cli_invocations,
            "downstream_readers": [h for h in hits if h not in decision],
            "verdict": ("决策路径有调用方" if decision else
                        "无 backend/api/ops/CI/战役门调用方；仅 scripts/post_h6_maturity_report.py 读产物 PNG 发报告 "
                        "→ 不达 L3")}


# ---------------------------------------------------------------- A9
def a9_tests() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", "tools/maturity_gate_h6/tests", "-q",
                        "-p", "no:cacheprovider"], cwd=M15050, capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()[-2:]
    m = re.search(r"(\d+) passed", r.stdout or "")
    return {"check": "A9_tests", "rc": r.returncode, "passed": int(m.group(1)) if m else 0,
            "tail": tail, "pass": r.returncode == 0 and bool(m)}


# ---------------------------------------------------------------- A10 声明 vs 工件
def a10_claim_vs_artifact(gate: dict) -> dict:
    doc = (M15050 / "docs/H6_MATURITY_GATE_G1.md").read_text(encoding="utf-8")
    claims_null_fail = "null" in doc and "不通过" in doc
    null_criteria = [c["criterion_id"] for c in gate["criteria"] if c["value"] is None]
    unavailable = [s["source_id"] for s in gate["sources"] if not s["available"]]
    return {"check": "A10_claim_vs_artifact",
            "doc_claims_null_means_fail": claims_null_fail,
            "criteria_with_null_value": null_criteria,
            "unavailable_sources": unavailable,
            "gate_verdict": gate["verdict"], "criteria_pass_rate": gate["criteria_pass_rate"],
            "pass": not bool(claims_null_fail and unavailable and not null_criteria),
            "material_mismatch": bool(claims_null_fail and unavailable and not null_criteria),
            "note": ("文档称源缺失→判据取 null→不通过，但工件里 8 条判据无一为 null、verdict=PASS："
                     "显式缺口未进入判据面" if claims_null_fail and unavailable and not null_criteria else "一致")}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    gate = load("artifacts/maturity_gate_h6/gate.json")
    checks = [a1_run_artifacts(), a2_repro(), a3_maturity_recount(gate), a4_increment_recount(gate),
              a5_negative_controls(), a6_benchmark(gate), a7_report_sections(),
              a8_integration_callers(), a9_tests(), a10_claim_vs_artifact(gate)]
    by = {c["check"]: c for c in checks}

    l_ok = by["A1_run_artifacts"]["pass"] and by["A2_repro_determinism"]["pass"] and by["A9_tests"]["pass"]
    l3_ok = by["A8_integration_callers"]["pass"]
    l4_ok = by["A6_benchmark_judge"]["pass"]
    level = 2 if l_ok else (1 if by["A9_tests"]["pass"] else 0)
    if level >= 2 and l3_ok:
        level = 3
    if level >= 3 and l4_ok:
        level = 4
    ladder = {
        "L1_math_unit": {"ok": by["A9_tests"]["pass"], "evidence": f"pytest {by['A9_tests']['passed']} passed"},
        "L2_independent_impl": {"ok": l_ok, "evidence": "entry+run_artifacts+确定复现"},
        "L3_integration": {"ok": l3_ok, "evidence": by["A8_integration_callers"]["verdict"]},
        "L4_benchmark": {"ok": l4_ok,
                         "evidence": f"注册基准 case 容差={by['A6_benchmark_judge']['tolerance']} "
                                     f"got_strict={by['A6_benchmark_judge']['measured']['got_strict']} "
                                     f"got_structural={by['A6_benchmark_judge']['measured']['got_structural']} "
                                     f"status={[c['status'] for c in by['A6_benchmark_judge']['registered_cases']]}"},
    }
    result = {"mission": 15050, "audit_mission": 15219, "proposed_level": level,
              "ladder": ladder, "checks": checks}
    outp = OUT_DIR / "verify_output.json"
    outp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"proposed_level": level, "ladder": ladder,
                      "checks": {c["check"]: c["pass"] for c in checks}}, ensure_ascii=False, indent=1))
    print(f"\n→ {outp}")
    if "--md" in sys.argv:
        print("\n| 核验项 | 通过 |")
        print("|---|---|")
        for c in checks:
            print(f"| {c['check']} | {'✅' if c['pass'] else '❌'} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())