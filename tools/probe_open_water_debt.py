#!/usr/bin/env python3
"""敞水（B 系列）端点欠账动态复核：J 扫掠探针 + 敞开水面物理不变量裁决。

背景（台账 artifact ``artifacts/validation_debt/ledger.json`` 独立复现的两条欠账）
------------------------------------------------------------------------------
* 报告 1173：``wageningen.py`` 系数表大量条目为 ``±0.00001000`` 占位（39 项 KT 表同一
  数量级重复 13 次）——"引用文献名 ≠ 使用文献系数"。
* 报告 1218：在线 :8001 敞水端点"仍返回占位式数值（J=0.6 时 KT=0.189、KQ=0.0189、
  η0=0.85）"；并给出三条定性反物理证据：KQ 随 J 上升、η 在 0.85 封顶、对
  P/D / Ae/A0 / Z 灵敏度恒为 0。

本探针把这三条**从"由来已久的断言"升级为"可重跑的机器判决"**：对在线端点做
J 扫掠 + 参数灵敏度扫掠，用敞开水面（open water）的五条不变量逐项裁决：

I1 **定义同一性**  η0 = J·KT/(2π·KQ)        —— 恒等式，相对误差应 ~0（舍入级）
I2 **KQ 单调性**  固定几何时 dKQ/dJ < 0     —— 扭矩随进速比下降是定义性质
I3 **KT 单调性**  固定几何时 dKT/dJ < 0     —— 推力随进速比下降
I4 **无量级封顶**  η0 不得出现常数平台（"卡在 0.85"这类钳位）且 η0 < 1
I5 **参数灵敏度** KT / η0 对 P/D、A_E/A_O、Z 必须非零响应（恒为 0 = 未使用系数）

判据
----
每条不变量给出 PASS / FAIL / WITHHELD（探针未取到可比数据）。整端点判级取最差项，
FAIL 即"欠账仍未清偿"。**不引入任何文献点值**：全部判据是定义性质或定性物理，
因此不受"能否联网取 Wageningen 表"的限制——这也正是本复核能在离线环境成立的原因。

用法
----
    python3 tools/probe_open_water_debt.py --out artifacts/validation_debt/open_water_probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8001/api/v1")
PATH = "/propeller-design/wageningen-b"

J_SWEEP: Tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
REF_GEOM = {"P_D": 1.0, "Z": 4, "Ae_Ao": 0.6}
PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"
RANK = {PASS: 0, WITHHELD: 1, FAIL: 2}
PLATEAU_TOL = 1e-9
IDENTITY_TOL_PCT = 0.5


def post(base: str, path: str, body: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(base.rstrip("/") + path, method="POST",
                                 data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _pick(d: Dict[str, Any], *names: str) -> Optional[float]:
    """在响应（可能多层嵌套）里找出第一个命中的数值字段。"""
    stack = [d]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for n in names:
                v = cur.get(n)
                if isinstance(v, (int, float)):
                    return float(v)
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def parse(resp: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    kt = _pick(resp, "KT", "kt", "K_T", "thrust_coefficient")
    kq = _pick(resp, "KQ", "kq", "K_Q", "torque_coefficient")
    eta = _pick(resp, "eta", "eta0", "η", "efficiency", "open_water_efficiency")
    return kt, kq, eta


def sweep(base: str, geom: Dict[str, Any], js: Sequence[float] = J_SWEEP) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for j in js:
        body = {"J": j, **geom}
        try:
            kt, kq, eta = parse(post(base, PATH, body))
            rows.append({"J": j, "KT": kt, "KQ": kq, "eta0": eta, "ok": True, "error": None})
        except urllib.error.HTTPError as e:
            rows.append({"J": j, "KT": None, "KQ": None, "eta0": None, "ok": False,
                         "error": f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"})
        except Exception as e:  # 网络/解析异常一律留痕，不当成通过
            rows.append({"J": j, "KT": None, "KQ": None, "eta0": None, "ok": False,
                         "error": f"{type(e).__name__}: {e}"})
    return rows


def _rel_pct(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-12)
    return 100.0 * abs(a - b) / scale


def judge_identity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """I1: η0 = J·KT/(2π·KQ)。"""
    checks, worst = [], 0.0
    for r in rows:
        if not (r["ok"] and r["KQ"] and r["KT"] is not None and r["eta0"] is not None):
            continue
        if abs(r["KQ"]) < 1e-12:
            continue
        expect = r["J"] * r["KT"] / (2.0 * 3.141592653589793 * r["KQ"])
        err = _rel_pct(expect, r["eta0"])
        worst = max(worst, err)
        checks.append({"J": r["J"], "eta_expected": expect, "eta_reported": r["eta0"],
                       "rel_err_pct": round(err, 4)})
    if not checks:
        return {"id": "I1", "name": "eta0 = J*KT/(2*pi*KQ)", "verdict": WITHHELD,
                "worst_rel_err_pct": None, "n": 0}
    return {"id": "I1", "name": "eta0 = J*KT/(2*pi*KQ)",
            "verdict": PASS if worst <= IDENTITY_TOL_PCT else FAIL,
            "worst_rel_err_pct": round(worst, 4), "n": len(checks), "checks": checks}


def _monotonic(rows: List[Dict[str, Any]], key: str, expect_decreasing: bool = True) -> Dict[str, Any]:
    seq = [(r["J"], r[key]) for r in rows if r["ok"] and r[key] is not None]
    if len(seq) < 3:
        return {"verdict": WITHHELD, "n": len(seq), "violations": []}
    viol = []
    for (j0, v0), (j1, v1) in zip(seq, seq[1:]):
        bad = (v1 > v0 + 1e-12) if expect_decreasing else (v1 < v0 - 1e-12)
        if bad:
            viol.append({"from_J": j0, "to_J": j1, "from": v0, "to": v1,
                         "delta": round(v1 - v0, 10)})
    return {"verdict": PASS if not viol else FAIL, "n": len(seq), "violations": viol,
            "series": [{"J": j, "value": v} for j, v in seq]}


def judge_monotonic(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"id": "I2", "name": "KQ strictly decreasing in J", **_monotonic(rows, "KQ")},
        {"id": "I3", "name": "KT strictly decreasing in J", **_monotonic(rows, "KT")},
    ]


def judge_no_clamp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """I4: η0 不得出现常数平台，且必须 < 1。"""
    seq = [(r["J"], r["eta0"]) for r in rows if r["ok"] and r["eta0"] is not None]
    if len(seq) < 3:
        return {"id": "I4", "name": "no hard-clamped eta0 plateau", "verdict": WITHHELD, "n": len(seq)}
    vals = [v for _, v in seq]
    plateau_hits = []
    for (j0, v0), (j1, v1) in zip(seq, seq[1:]):
        if abs(v1 - v0) <= PLATEAU_TOL:
            plateau_hits.append({"from_J": j0, "to_J": j1, "value": v0})
    over = [v for v in vals if v >= 1.0]
    # "卡在常数上" 若该常数在多个 J 上重复出现（≥2 段相邻相等即为钳位嫌疑）
    clamped = bool(plateau_hits) and (len(set(round(v, 10) for v in vals)) < len(vals))
    return {"id": "I4", "name": "no hard-clamped eta0 plateau",
            "verdict": FAIL if (clamped or over) else PASS,
            "plateau_segments": plateau_hits, "eta_ge_1": over,
            "distinct_values": len(set(round(v, 10) for v in vals)), "n_points": len(vals)}


def judge_sensitivity(base: str) -> List[Dict[str, Any]]:
    """I5: KT/η0 对 P/D、A_E/A_O、Z 的响应必须非零。"""
    out: List[Dict[str, Any]] = []
    probes = [
        ("P_D", [{"P_D": 0.8, "Z": 4, "Ae_Ao": 0.6}, {"P_D": 1.2, "Z": 4, "Ae_Ao": 0.6}]),
        ("Ae_Ao", [{"P_D": 1.0, "Z": 4, "Ae_Ao": 0.4}, {"P_D": 1.0, "Z": 4, "Ae_Ao": 0.85}]),
        ("Z", [{"P_D": 1.0, "Z": 3, "Ae_Ao": 0.6}, {"P_D": 1.0, "Z": 5, "Ae_Ao": 0.6}]),
    ]
    for param, geoms in probes:
        got = []
        for g in geoms:
            try:
                kt, kq, eta = parse(post(base, PATH, {"J": 0.6, **g}))
                got.append({"geom": g, "KT": kt, "KQ": kq, "eta0": eta, "ok": True})
            except Exception as e:
                got.append({"geom": g, "ok": False, "error": f"{type(e).__name__}: {e}"})
        okrows = [g for g in got if g["ok"]]
        if len(okrows) < 2 or any(g["KT"] is None for g in okrows):
            out.append({"id": f"I5-{param}", "param": param, "verdict": WITHHELD, "probes": got})
            continue
        dkt = abs(okrows[1]["KT"] - okrows[0]["KT"])
        deta = (abs((okrows[1]["eta0"] or 0.0) - (okrows[0]["eta0"] or 0.0))
                if all(g["eta0"] is not None for g in okrows) else 0.0)
        alive = (dkt > 1e-9) or (deta > 1e-9)
        out.append({"id": f"I5-{param}", "param": param,
                    "verdict": PASS if alive else FAIL,
                    "delta_KT": round(dkt, 10), "delta_eta0": round(deta, 10), "probes": got})
    return out


def run(base: str) -> Dict[str, Any]:
    rows = sweep(base, REF_GEOM)
    checks: List[Dict[str, Any]] = [judge_identity(rows)]
    checks += judge_monotonic(rows)
    checks.append(judge_no_clamp(rows))
    checks += judge_sensitivity(base)
    worst = max(checks, key=lambda c: RANK[c["verdict"]])
    reachable = any(r["ok"] for r in rows)
    verdict = WITHHELD if not reachable else worst["verdict"]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"base": base, "path": PATH, "ref_geometry": REF_GEOM, "J_sweep": list(J_SWEEP)},
        "endpoint_reachable": reachable,
        "verdict": verdict,
        "failed_invariants": [c["id"] for c in checks if c["verdict"] == FAIL],
        "checks": checks,
        "sweep": rows,
        "note": ("判据为定义性质与定性物理，不含文献点值：因此本复核只裁"
                 "『是否仍是占位式/反物理』，不裁『绝对精度是否达标』；"
                 "后者（对 Wageningen 图谱的点值比对）仍需联网取表，本轮标 WITHHELD。"),
    }


def render_markdown(res: Dict[str, Any]) -> str:
    lines = ["# 敞水端点欠账动态复核（自动生成，勿手改）", "",
             f"- 目标 `{res['target']['base']}{res['target']['path']}`",
             f"- 可达性 **{res['endpoint_reachable']}** · 判决 **{res['verdict']}**",
             f"- 失败不变量 {res['failed_invariants'] or '无'}", "",
             "| 判据 | 名称 | 判决 | 关键量 |", "|---|---|---|---|"]
    for c in res["checks"]:
        if c["verdict"] == FAIL and "violations" in c:
            key = f"违反 {len(c['violations'])}/{max(c['n']-1,1)} 段"
        elif c["id"] == "I1":
            key = f"最差相对误差 {c['worst_rel_err_pct']}%"
        elif c["id"] == "I4":
            key = f"平台段 {len(c.get('plateau_segments', []))}，η≥1 {len(c.get('eta_ge_1', []))}"
        elif c["id"].startswith("I5"):
            key = f"ΔKT {c.get('delta_KT')}, Δη0 {c.get('delta_eta0')}"
        else:
            key = ""
        lines.append(f"| {c['id']} | {c.get('name') or c.get('param','')} | {c['verdict']} | {key} |")
    lines += ["", "## J 扫掠原始点", "", "| J | KT | KQ | η0 |", "|---|---|---|---|"]
    for r in res["sweep"]:
        lines.append(f"| {r['J']} | {r['KT']} | {r['KQ']} | {r['eta0']} |")
    lines.append("")
    lines.append(f"> {res['note']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="敞水端点欠账动态复核")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default="artifacts/validation_debt/open_water_probe.json")
    args = ap.parse_args(argv)
    res = run(args.base)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    md = os.path.join(os.path.dirname(args.out) or ".", "open_water_probe.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(res))
    print(json.dumps({"verdict": res["verdict"], "reachable": res["endpoint_reachable"],
                      "failed": res["failed_invariants"]}, ensure_ascii=False))
    print(f"artifact: {args.out}\nartifact: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())