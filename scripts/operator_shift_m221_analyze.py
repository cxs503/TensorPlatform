#!/usr/bin/env python3
"""值班长 m221 班次：SDAA 验证案例扫荡线完工审计（纯分析，零手抄）。

数据源：平台 API /api/v1/tensor/missions（列表 + 逐单详情），全部数字程序化取自 API。
本脚本只读 API，不写业务代码、不改判据、不改任何阈值。

口径（显式声明）：
  L1 mission 结局  = API status
  L1 流程断点       = status==failed 且 result gate/reason 命中「未调用 finish」/「工作树无任何改动」
  L2 mission 结论   = result 正文中的**严格判据标记**：
                      PASS: 判定：PASS / verdict=PASS / overall PASS / all_pass true / 全部 PASS / 全绿 / n/m PASS
                      FAIL: honest-FAIL / 判定：FAIL / verdict=FAIL / overall FAIL / 0/n PASS
                      同一正文同时命中两类 → MIXED（按未定对待，不替案例下结论）
  L3 案例终判       = 案例内**最新一条带严格标记**的 mission 的 L2 结论（MIXED/WITHHELD 单不下结论）
  失败归因          = 只对「案例终判 = honest-FAIL」的案例，用其最新标记单正文关键词归类：
                      triton 绕行 / 环境(内存·OOM·设备) / 判据真红(误差超阈) / 其他
"""
import json
import os
import re
import subprocess
import sys

BASE = os.environ["TENSOR_API_BASE"]
TOKEN = open("/tmp/token.txt").read().strip()
SWEEP_MARK = "SDAA 验证案例扫荡"

PASS_PAT = re.compile(
    r"判定[：:]\s*\**\s*PASS|verdict\s*=\s*PASS|overall\s+PASS|all[ _]?pass['\"]?\s*[:=]\s*true"
    r"|全部\s*PASS|全\s*PASS|全绿|ALL PASS|n_pass\s*=\s*\d+/\d+|\d+/\d+\s*(?:项)?(?:判据|门)?\s*全?\s*PASS"
    r"|(?:两|三|四|五|六|七|八|九|十|\d+)\s*门\s*(?:全|均)?\s*PASS|(?-i:\bVERIFIED\b)|<\s*3\s*%\s*达标"
    r"|[Gg]ates?\s+(?:all\s+)?PASS|PASS\s*（",
    re.I,
)
FAIL_PAT = re.compile(
    r"honest[-_ ]?FAIL|判定[：:]\s*\**\s*(?:HONEST[-_ ]?)?FAIL|verdict\s*=\s*FAIL|overall\s+FAIL"
    r"|0/\d+\s*PASS|如实(?:保留|记录)\s*FAIL",
    re.I,
)
PROC_PAT = re.compile(r"未调用 finish|工作树无任何改动")
DEV_PAT = re.compile(r"/dev/tcaicard")
TRI_PAT = re.compile(r"[Tt]riton")
ENV_PAT = re.compile(r"OOM|[内存]分配|allocat|设备错误|驱动|显存|内存不足|墙钟预算|预算限制")
RED_PAT = re.compile(r"超[过]?\s*3\s*%|>\s*3\s*%|误差\s*[+\-−]?\d+(?:\.\d+)?\s*%")


def api(path):
    r = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {TOKEN}", f"{BASE}{path}"],
        capture_output=True,
        text=True,
    )
    return json.loads(r.stdout)


def mission_text(m):
    r = m.get("result") or {}
    ev = r.get("evidence") or {}
    sm = ev.get("summary") or {}
    gate = ev.get("checks")
    ps = [
        r.get("headline"),
        r.get("checks"),
        r.get("summary"),
        gate if isinstance(gate, str) else (json.dumps(gate, ensure_ascii=False) if gate else None),
        sm.get("headline"),
        sm.get("checks"),
        sm.get("declared_summary"),
    ]
    return "\n".join(p for p in ps if isinstance(p, str) and p)


def case_of(goal):
    cands = [c for c in re.findall(r"案例\s*\**\s*`?([A-Za-z][A-Za-z0-9_]*)", goal) if len(c) >= 3]
    if cands:
        # 取最长候选，避免正文中的单字母误捡
        return max(cands, key=len)
    return "UNKNOWN"


def proc_fail_of(m):
    """流程断点判定：只看平台落盘的 gate/reason 与 headline，不扫正文（避免正文诊断他人失败造成假阳）。"""
    r = m.get("result") or {}
    ev = r.get("evidence") or {}
    gate = ev.get("checks")
    reason = ""
    if isinstance(gate, dict):
        reason = str(gate.get("reason") or "")
    elif isinstance(gate, str):
        reason = gate
    head = str(r.get("headline") or "")
    return bool(PROC_PAT.search(reason) or head.startswith("未完成"))


def declared(text):
    """FAIL 级标记优先（绝不粉饰 honest-FAIL），其次 PASS 级标记，否则 WITHHELD。"""
    if FAIL_PAT.search(text):
        return "HONEST_FAIL"
    if PASS_PAT.search(text):
        return "PASS"
    return "WITHHELD"


def main():
    listing = api("/tensor/missions?limit=500")
    items = listing["items"]
    sweep = [m for m in items if SWEEP_MARK in (m.get("goal") or "")]
    details = {m["id"]: api(f"/tensor/missions/{m['id']}") for m in sweep}

    rows = []
    for mid, m in sorted(details.items()):
        goal, text, st = m.get("goal") or "", mission_text(m), m.get("status")
        is_lbm = m.get("repo") == "TensorLBM"
        rows.append(
            dict(
                id=mid,
                repo=m.get("repo"),
                status=st,
                case=case_of(goal) if is_lbm else "SHIFT",
                is_shift=not is_lbm,
                round_tag=(re.search(r"【([^】]*)】", goal).group(1) if re.search(r"【([^】]*)】", goal) else ""),
                declared=declared(text) if is_lbm else "",
                proc_fail=(st == "failed") and proc_fail_of(m),
                device=bool(DEV_PAT.search(text)),
                triton=bool(TRI_PAT.search(text)),
                env=bool(ENV_PAT.search(text)),
                text_len=len(text),
                title=((m.get("result") or {}).get("headline") or "")[:120],
            )
        )
        rows[-1]["text"] = text

    l1 = [r for r in rows if not r["is_shift"] and r["status"] in ("succeeded", "failed")]

    cases = {}
    for r in l1:
        cases.setdefault(r["case"], []).append(r)
    case_tbl = {}
    for c, ms in cases.items():
        ms.sort(key=lambda r: r["id"])
        latest = None
        for r in ms:
            if r["declared"] in ("PASS", "HONEST_FAIL"):
                latest = r
        verdict = latest["declared"] if latest else ("MIXED" if any(m["declared"] == "MIXED" for m in ms) else "WITHHELD")
        reason = ""
        if verdict == "HONEST_FAIL":
            t = latest["text"]
            if TRI_PAT.search(t):
                reason = "triton 绕行"
            elif ENV_PAT.search(t):
                reason = "环境(内存/OOM/设备)"
            elif RED_PAT.search(t):
                reason = "判据真红(误差超阈)"
            else:
                reason = "其他/未分类"
        case_tbl[c] = dict(
            verdict=verdict,
            verdict_mission=latest["id"] if latest else None,
            verdict_mission_status=latest["status"] if latest else None,
            verdict_from_proc_fail_mission=bool(latest and latest["proc_fail"]),
            n_missions=len(ms),
            n_succeeded=sum(1 for m in ms if m["status"] == "succeeded"),
            n_failed=sum(1 for m in ms if m["status"] == "failed"),
            n_proc_fail=sum(1 for m in ms if m["proc_fail"]),
            device=any(m["device"] for m in ms),
            fail_reason=reason,
            title=latest["title"] if latest else "",
        )

    vcount = {}
    for c, d in case_tbl.items():
        vcount[d["verdict"]] = vcount.get(d["verdict"], 0) + 1

    rcount = {}
    for c, d in case_tbl.items():
        if d["verdict"] == "HONEST_FAIL":
            rcount[d["fail_reason"]] = rcount.get(d["fail_reason"], 0) + 1

    out = dict(
        mission_total=listing["total"],
        listing_returned=len(items),
        sweep_missions=len(sweep),
        tensorlbm_missions=len(l1),
        shift_missions=sum(1 for r in rows if r["is_shift"]),
        status={s: sum(1 for r in l1 if r["status"] == s) for s in ("succeeded", "failed")},
        proc_fail=sum(1 for r in l1 if r["proc_fail"]),
        proc_fail_pct=round(100.0 * sum(1 for r in l1 if r["proc_fail"]) / max(1, len(l1)), 1),
        proc_fail_among_failed=sum(1 for r in l1 if r["proc_fail"] and r["status"] == "failed"),
        device_missions=sum(1 for r in l1 if r["device"]),
        n_cases=len(case_tbl),
        case_verdict_counts=vcount,
        honest_fail_reasons=rcount,
        cases=case_tbl,
        inflight=[dict(id=m["id"], status=m["status"], repo=m.get("repo"), goal=(m.get("goal") or "")[:70].replace("\n", " ")) for m in items if m["status"] in ("queued", "running")],
        rows=[{k: v for k, v in r.items() if k != "text"} for r in sorted(l1, key=lambda r: r["id"])],
    )
    json.dump(out, open("/tmp/m221_analysis.json", "w"), ensure_ascii=False, indent=1)

    print("missions total", out["mission_total"], "listed", out["listing_returned"], "sweep", out["sweep_missions"])
    print("TensorLBM sweep missions", out["tensorlbm_missions"], out["status"], "proc_fail", out["proc_fail"], f"({out['proc_fail_pct']}%)")
    print("cases", out["n_cases"], "verdicts", vcount, "honest reasons", rcount)
    for c in sorted(case_tbl):
        d = case_tbl[c]
        print(f"  {c:32s} {d['verdict']:12s} m={d['verdict_mission']} {str(d['verdict_mission_status']):9s} pf={d['verdict_from_proc_fail_mission']!s:5s} n={d['n_missions']:2d} succ={d['n_succeeded']:2d} fail={d['n_failed']:2d} dev={d['device']!s:5s} {d['fail_reason']}")


if __name__ == "__main__":
    sys.exit(main())