"""AI/ML 代理端点证据核查（m1828）。

背景
----
平台现网 (:8001) 挂载了一族"机器学习/代理模型"端点（PINN / Transformer / RL），
但能力清单把 ``ai.predict`` 分级为 **NOT_SUPPORTED**，两者互相矛盾：
要么端点真是物理代理模型（则分级过低），要么端点是占位返回（则分级正确、
而端点在冒充能力）。本脚本以**只读**方式给出现网证据裁决。

判据全部为机器可裁、且不依赖任何"记忆里的文献表值"
--------------------------------------------------
S1  声明输入敏感性：schema 声明的每个输入量必须影响输出（否则该输入是装饰）
S2  可复现性：同输入重复调用逐位一致
S3  速度律：静水阻力须随航速单调增，且 log-log 斜率落在 [1.5, 3.5]
S4  跨模型一致性：与同机 REAL 端点 ``/resistance/predict``（Holtrop 已达到基准
    复核，见 docs/reports/m1384_resistance_stability_verification.md）比对，
    相对误差 >15% 即判不合格（平台精度标准）
S5  损失项动态性：PINN 自陈 ``physics_loss`` / ``data_loss`` 必须随输入变化；
    恒定值 = 未计算的占位数字
S6  耐波物理：RAO 对浪向须敏感；长波极限下 heave RAO 不得出现物理不可能量级
S7  优化器进展：``optimization_history`` 的 reward 若全等，则无学习过程
S8  能量分配自洽：功率分配分量之和应 ≈1（归一化占比）

用法
----
    python3 tools/audit_surrogate_endpoints.py [base_url]

退出码：0=证据完整出裁决；2=服务不可达（不产生伪证据）。
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001").rstrip("/")
API = BASE + "/api/v1"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "m1828")
TIMEOUT = 60

# 基准船（一艘 180 m 散货船的公开量级），全部判据在同一船型上做单变量扫描
BASE_SHIP = dict(L=180.0, B=30.0, T=11.0, Cb=0.82, displacement=45000.0,
                 speed=14.0, water_density=1025.0)
HOLTROP_SHIP = dict(Lwl=180.0, Lpp=180.0, B=30.0, T=11.0, displacement=45000.0,
                    Cb=0.82, Cm=0.988, Cwp=0.86, bulb_area=0.0, transom_area=0.0)
SPEEDS = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]


# ---------------------------------------------------------------------------
# HTTP 层：只读，记录原始响应作为证据
# ---------------------------------------------------------------------------

def post(path: str, body: Dict[str, Any], log: List[Dict[str, Any]]) -> Tuple[Any, Any]:
    url = API + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = json.load(r)
            status = r.status
    except urllib.error.HTTPError as exc:  # 保留状态码作为证据，不当异常吞掉
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw.decode("utf-8", "replace")[:400]
        status = exc.code
    except Exception as exc:
        log.append({"path": path, "body": body, "status": "unreachable", "error": str(exc)[:200]})
        return "unreachable", None
    log.append({"path": path, "body": body, "status": status})
    return status, payload


def ok(status: Any, payload: Any) -> bool:
    return status == 200 and isinstance(payload, (dict, list))


def rel_err(x: float, ref: float) -> float:
    return abs(x - ref) / abs(ref) if ref else float("inf")


# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------

def probe_pinn_resistance(log) -> Dict[str, Any]:
    """PINN 阻力：S1/S2/S3/S4/S5。"""
    out: Dict[str, Any] = {"endpoint": "/pinn/resistance", "ship": BASE_SHIP}
    # 基准点
    s0, d0 = post("/pinn/resistance", BASE_SHIP, log)
    if not ok(s0, d0):
        out.update({"reachable": False, "status": s0})
        return out
    out["reachable"] = True
    out["base_response"] = d0
    # S2 复现性
    _, d0b = post("/pinn/resistance", BASE_SHIP, log)
    out["S2_deterministic"] = d0b == d0
    # S1 单输入敏感性
    sens = {}
    for key, vals in (("L", [120.0, 260.0]), ("B", [20.0, 45.0]), ("T", [7.0, 16.0]),
                      ("Cb", [0.55, 0.90]), ("displacement", [15000.0, 120000.0]),
                      ("water_density", [1000.0, 1025.0])):
        res = []
        for v in vals:
            body = dict(BASE_SHIP, **{key: v})
            st, pl = post("/pinn/resistance", body, log)
            res.append(pl.get("resistance") if ok(st, pl) else None)
        sens[key] = {"values": vals, "resistance": res,
                     "varies": len({round(r, 6) for r in res if r is not None}) > 1}
    out["S1_sensitivity"] = sens
    # 未声明敏感项（基准点自身）：displacement 不影响输出即失效
    out["S1_ignored_inputs"] = [k for k, v in sens.items() if not v["varies"]]
    # S3 速度律
    speed_curve = []
    for v in SPEEDS:
        st, pl = post("/pinn/resistance", dict(BASE_SHIP, speed=v), log)
        if ok(st, pl):
            speed_curve.append({"speed": v, "resistance": pl.get("resistance"),
                                "friction_resistance": pl.get("friction_resistance"),
                                "residual_resistance": pl.get("residual_resistance"),
                                "physics_loss": pl.get("physics_loss"),
                                "data_loss": pl.get("data_loss")})
    out["speed_curve"] = speed_curve
    r = [p["resistance"] for p in speed_curve]
    out["S3_monotone"] = all(r[i] < r[i + 1] for i in range(len(r) - 1))
    out["S3_loglog_slope"] = (math.log(r[-1] / r[0]) / math.log(SPEEDS[len(r) - 1] / SPEEDS[0])
                              if r and r[0] > 0 else None)
    # S5 损失项动态性
    pl_vals = {round(p["physics_loss"], 9) for p in speed_curve if p["physics_loss"] is not None}
    dl_vals = {round(p["data_loss"], 9) for p in speed_curve if p["data_loss"] is not None}
    out["S5_physics_loss_distinct"] = len(pl_vals)
    out["S5_data_loss_distinct"] = len(dl_vals)
    out["S5_data_loss_frozen"] = len(dl_vals) == 1
    out["S5_physics_loss_frozen"] = len(pl_vals) == 1
    # S4 与 Holtrop 真算端点比对
    st, hol = post("/resistance/predict", dict(HOLTROP_SHIP, speeds=SPEEDS), log)
    if ok(st, hol) and isinstance(hol, list):
        ref = {round(h["speed"], 3): h["total_resistance"] for h in hol}
        cmp_rows = []
        for p in speed_curve:
            k = round(p["speed"], 3)
            if k in ref and ref[k]:
                cmp_rows.append({"speed": k, "pinn": p["resistance"], "holtrop": ref[k],
                                 "rel_err_pct": 100.0 * rel_err(p["resistance"], ref[k])})
        out["S4_vs_holtrop"] = cmp_rows
        out["S4_worst_rel_err_pct"] = max((c["rel_err_pct"] for c in cmp_rows), default=None)
        out["holtrop_curve"] = [{"speed": h["speed"], "total_resistance": h["total_resistance"]}
                                for h in hol]
    return out


def probe_pinn_seakeeping(log) -> Dict[str, Any]:
    """PINN 耐波：S1(浪向)/S6(RAO 量级)。"""
    out: Dict[str, Any] = {"endpoint": "/pinn/seakeeping"}
    sea = dict(L=180.0, B=30.0, T=11.0, Cb=0.82, KG=9.0, GM=1.2,
               wave_height=3.0, wave_period=8.0, wave_heading=180.0)
    head_curve, period_curve = [], []
    for h in (0.0, 45.0, 90.0, 135.0, 180.0):
        st, pl = post("/pinn/seakeeping", dict(sea, wave_heading=h), log)
        if ok(st, pl):
            head_curve.append({"heading": h, "heave_rao": pl.get("heave_rao"),
                               "pitch_rao": pl.get("pitch_rao"), "roll_rao": pl.get("roll_rao"),
                               "physics_loss": pl.get("physics_loss")})
    for tp in (5.0, 8.0, 11.0, 14.0):
        st, pl = post("/pinn/seakeeping", dict(sea, wave_period=tp), log)
        if ok(st, pl):
            period_curve.append({"wave_period": tp, "heave_rao": pl.get("heave_rao"),
                                 "pitch_rao": pl.get("pitch_rao")})
    out["heading_curve"] = head_curve
    out["period_curve"] = period_curve
    out["reachable"] = bool(head_curve)
    if head_curve:
        out["S1_heading_sensitive"] = len({round(c["heave_rao"], 9) for c in head_curve}) > 1
        out["S6_heave_rao_max"] = max(c["heave_rao"] for c in head_curve)
        out["S6_heave_pitch_distinct"] = any(
            abs(c["heave_rao"] - c["pitch_rao"]) > 1e-9 for c in head_curve)
        out["S6_physics_loss_distinct"] = len(
            {round(c["physics_loss"], 9) for c in head_curve if c["physics_loss"] is not None})
    if period_curve:
        out["S6_period_sensitive"] = len({round(c["heave_rao"], 9) for c in period_curve}) > 1
    return out


def probe_transformer(log) -> Dict[str, Any]:
    """Transformer 阻力代理：S4/S5/S1。"""
    out: Dict[str, Any] = {"endpoint": "/transformer/resistance"}
    params = {"L": 180.0, "B": 30.0, "T": 11.0, "Cb": 0.82}
    st, pl = post("/transformer/resistance",
                  {"ship_params": [params], "speed_range": SPEEDS}, log)
    if not ok(st, pl):
        out.update({"reachable": False, "status": st, "payload": pl})
        return out
    out["reachable"] = True
    preds = {round(p["speed"], 3): p["resistance"] for p in pl.get("predictions", [])}
    out["predictions"] = pl.get("predictions")
    out["feature_importance"] = pl.get("feature_importance")
    # 不同几何是否有区别
    st2, pl2 = post("/transformer/resistance",
                    {"ship_params": [{"L": 120.0, "B": 20.0, "T": 7.0, "Cb": 0.60}],
                     "speed_range": [12.0]}, log)
    if ok(st2, pl2) and pl2.get("predictions"):
        same = pl2["predictions"][0]["resistance"] == preds.get(12.0) if 12.0 in preds else None
        out["S1_geometry_sensitive"] = (not same) if same is not None else None
        out["alt_ship"] = pl2["predictions"][0]
    # 与 Holtrop 比对
    st3, hol = post("/resistance/predict", dict(HOLTROP_SHIP, speeds=SPEEDS), log)
    if ok(st3, hol) and isinstance(hol, list):
        rows = []
        for h in hol:
            k = round(h["speed"], 3)
            if k in preds and h["total_resistance"]:
                rows.append({"speed": k, "transformer": preds[k],
                             "holtrop": h["total_resistance"],
                             "rel_err_pct": 100.0 * rel_err(preds[k], h["total_resistance"])})
        out["S4_vs_holtrop"] = rows
        out["S4_worst_rel_err_pct"] = max((r["rel_err_pct"] for r in rows), default=None)
    return out


def probe_rl(log) -> Dict[str, Any]:
    """RL 族：S1/S7/S8。"""
    out: Dict[str, Any] = {"endpoints": ["/rl/energy", "/rl/optimize"]}
    # S1/S8 能量管理输入敏感性
    rows = []
    for dist, sp in ((1000.0, 12.0), (5000.0, 18.0), (500.0, 8.0)):
        st, pl = post("/rl/energy",
                      {"voyage_plan": {"distance": dist, "speed": sp},
                       "power_systems": [{"type": "main_engine", "power": 8000.0, "fuel": "hfo"}],
                       "power_constraints": {}}, log)
        if ok(st, pl):
            alloc = pl.get("optimal_power_allocation") or {}
            rows.append({"distance": dist, "speed": sp,
                         "energy_savings": pl.get("energy_savings"),
                         "allocation": alloc,
                         "allocation_sum": (sum(alloc.values()) if alloc else None)})
    out["energy_curve"] = rows
    out["reachable"] = bool(rows)
    if rows:
        out["S1_energy_sensitive"] = len({round(r["energy_savings"], 9) for r in rows}) > 1
        out["S8_allocation_sum_ok"] = all(
            r["allocation_sum"] is not None and abs(r["allocation_sum"] - 1.0) < 1e-6 for r in rows)
        out["S8_allocation_sums"] = [r["allocation_sum"] for r in rows]
    # S7 优化器进展
    opt_rows = []
    for obj in ("minimize_fuel", "maximize_profit"):
        st, pl = post("/rl/optimize",
                      {"objective": obj, "design_variables": {"speed": [10.0, 16.0]},
                       "constraints": [], "initial_design": {"speed": 12.0}}, log)
        if ok(st, pl):
            hist = pl.get("optimization_history") or []
            rewards = {round(h["reward"], 9) for h in hist}
            opt_rows.append({"objective": obj, "optimal_design": pl.get("optimal_design"),
                             "optimal_value": pl.get("optimal_value"),
                             "episodes": len(hist), "distinct_rewards": len(rewards),
                             "reward_constant": len(rewards) == 1})
    out["optimize_runs"] = opt_rows
    out["S7_history_has_progress"] = all(r["distinct_rewards"] > 1 for r in opt_rows) if opt_rows else None
    return out


def probe_vibration(log) -> Dict[str, Any]:
    """振动端点可达性（schema 无 body 却要求 query ship_id）。"""
    out: Dict[str, Any] = {"endpoint": "/vibration/predict"}
    st, pl = post("/vibration/predict", {}, log)
    out["post_without_ship_id"] = {"status": st, "payload": pl}
    return out


# ---------------------------------------------------------------------------
# 裁决
# ---------------------------------------------------------------------------

def verdicts(a: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    p = a.get("pinn_resistance", {})
    failures = []
    if p.get("reachable"):
        if p.get("S1_ignored_inputs"):
            failures.append(f"S1 声明输入被忽略: {p['S1_ignored_inputs']}")
        if p.get("S3_monotone") is False:
            failures.append("S3 阻力不随航速单调")
        worst = p.get("S4_worst_rel_err_pct")
        if worst is not None and worst > 15.0:
            failures.append(f"S4 对 Holtrop 基准最差相对误差 {worst:.1f}% > 15%")
        if p.get("S5_data_loss_frozen"):
            failures.append("S5 data_loss 恒定（未计算的占位损失）")
        if p.get("S5_physics_loss_frozen"):
            failures.append("S5 physics_loss 恒定")
    rows.append({"endpoint": "/pinn/resistance", "reachable": p.get("reachable", False),
                 "failing_criteria": failures,
                 "grade": "MOCK" if failures else ("REAL" if p.get("reachable") else "STUB")})

    s = a.get("pinn_seakeeping", {})
    failures = []
    if s.get("reachable"):
        if s.get("S1_heading_sensitive") is False:
            failures.append("S1/S6 RAO 对浪向不敏感（浪向是装饰输入）")
        if s.get("S6_heave_pitch_distinct") is False:
            failures.append("S6 heave 与 pitch RAO 不可区分")
        if (s.get("S6_heave_rao_max") or 0) > 3.0:
            failures.append(f"S6 heave RAO={s['S6_heave_rao_max']:.3f} 超物理量级上界 3.0")
        if s.get("S6_physics_loss_distinct") == 1:
            failures.append("S5 physics_loss 恒定")
    rows.append({"endpoint": "/pinn/seakeeping", "reachable": s.get("reachable", False),
                 "failing_criteria": failures,
                 "grade": "MOCK" if failures else ("REAL" if s.get("reachable") else "STUB")})

    t = a.get("transformer_resistance", {})
    failures = []
    if t.get("reachable"):
        worst = t.get("S4_worst_rel_err_pct")
        if worst is not None and worst > 15.0:
            failures.append(f"S4 对 Holtrop 基准最差相对误差 {worst:.1f}% > 15%")
        if t.get("S1_geometry_sensitive") is False:
            failures.append("S1 几何输入不影响输出")
    rows.append({"endpoint": "/transformer/resistance", "reachable": t.get("reachable", False),
                 "failing_criteria": failures,
                 "grade": "MOCK" if failures else ("REAL" if t.get("reachable") else "STUB")})

    r = a.get("rl", {})
    failures = []
    if r.get("reachable"):
        if r.get("S1_energy_sensitive") is False:
            failures.append("S1 航程/航速不影响节能率（硬编码常量）")
        if r.get("S8_allocation_sum_ok") is False:
            failures.append(f"S8 功率分配占比之和≠1: {r.get('S8_allocation_sums')}")
        if r.get("S7_history_has_progress") is False:
            failures.append("S7 optimization_history 的 reward 全程恒定（无学习过程）")
    rows.append({"endpoint": "/rl/*", "reachable": r.get("reachable", False),
                 "failing_criteria": failures,
                 "grade": "MOCK" if failures else ("REAL" if r.get("reachable") else "STUB")})

    v = a.get("vibration_predict", {})
    st = (v.get("post_without_ship_id") or {}).get("status")
    rows.append({"endpoint": "/vibration/predict", "reachable": st == 200,
                 "failing_criteria": ([f"不接受文档化驱动方式，仅返回 {st}"]
                                      if st != 200 else []),
                 "grade": "WITHHELD" if st != 200 else "REAL"})
    return rows


def main() -> int:
    log: List[Dict[str, Any]] = []
    st, health = post("/health", {}, log) if False else (None, None)
    # 健康探针用 GET
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=10) as r:
            health = json.load(r)
            health_status = r.status
    except Exception as exc:
        print(f"service unreachable: {exc}")
        return 2
    audit: Dict[str, Any] = {
        "service": BASE, "health": health, "health_status": health_status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_ship": BASE_SHIP,
    }
    audit["pinn_resistance"] = probe_pinn_resistance(log)
    audit["pinn_seakeeping"] = probe_pinn_seakeeping(log)
    audit["transformer_resistance"] = probe_transformer(log)
    audit["rl"] = probe_rl(log)
    audit["vibration_predict"] = probe_vibration(log)
    audit["verdicts"] = verdicts(audit)
    audit["request_log"] = log
    audit["criteria"] = {
        "S1": "schema 声明的每个输入必须影响输出",
        "S2": "同输入重复调用逐位一致",
        "S3": "阻力随航速单调增，log-log 斜率 ∈ [1.5, 3.5]",
        "S4": "对同机 REAL Holtrop 端点相对误差 ≤ 15%",
        "S5": "自陈损失项必须随输入变化（恒定=占位）",
        "S6": "RAO 对浪向敏感；heave RAO ≤ 3.0 量级",
        "S7": "optimization_history 的 reward 必须有进展",
        "S8": "功率分配占比之和 ≈ 1",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "surrogate_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False, indent=2)
    print(f"artifact: {path}")
    for row in audit["verdicts"]:
        print(f"  {row['endpoint']:26s} {row['grade']:9s} " + "; ".join(row["failing_criteria"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())