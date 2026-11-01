"""capgate —— 船舶总体性能平台「能力证据门」(capability evidence gate)

背景
----
`platform_harness/capabilities.json` 是平台 21 项能力证据等级（REAL/MOCK/STUB/
WITHHELD/NOT_SUPPORTED）的唯一声明源，agent 侧 `audit_capabilities` 工具直接读它。
该文件是**手工维护的声明**：没有任何 `verified_by` 指针，也没有任何探针把
"REAL" 这个断言与可机检的物理/解析基准绑起来。历史上已多次出现「声明 REAL、
实测伪造」（如 m1032：敞水系数 KQ≡KT/10 伪造、ITTC-78 总阻力 +41%）。

本模块把这层欠账变成机器判据：
  1) 注册表 schema 门：REAL/WITHHELD 等级必须声明 `verified_by`（探针 id 列表）；
  2) 学科域覆盖矩阵：9 个船舶总体性能学科域 × 注册能力，区分
     SERVABLE_REAL / REGISTERED_ONLY / UNREGISTERED；
  3) 端点可达性探针（在线，只读 GET/POST）：声明端点 404/405 → NOT_MOUNTED，
     与 `reachable_direct: true` 冲突即 REACH_DRIFT；
  4) 物理/解析探针（离线纯函数）：敞水系退化、ITTC-1957 摩擦线、静水力积分恒等式、
     GM 恒等式、硬编码回声指纹。每一项都带基准出处（basis）。
  5) 对账：声明等级 vs 探针判据 → DRIFT / UNVERIFIED / CONSISTENT。

用法
----
    python3 tools/capgate/capgate.py --registry <capabilities.json> [--live] \
        --out-dir artifacts/capgate --doc docs/verification/<name>.md \
        --report-json /tmp/report.json --mission-id 1476

纯标准库（matplotlib 仅用于出图，缺失时跳过）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import math
import os
import pathlib
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# 常量 / 学科域映射
# --------------------------------------------------------------------------- #
EVIDENCE_LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
EVIDENCE_RANK = {"REAL": 3, "WITHHELD": 2, "MOCK": 1, "STUB": 1, "NOT_SUPPORTED": 0}

#: 船舶总体性能 9 学科域 → 注册表中可承载它的能力 id。
#: 空列表 = 该学科在 21 项能力注册表中**完全没有能力位**（UNREGISTERED）。
DOMAIN_MAP = {
    "阻力": ["calc.resistance", "shiphull.generate", "geometry.generate"],
    "推进": ["propeller.open_water"],
    "耐波": [],
    "操纵": [],
    "稳性": ["calc.stability", "geometry.hydro_curves"],
    "结构": [],
    "振动噪声": ["misc.stubs"],      # 仅经 misc.stubs 的 /vibration/predict 承载
    "绿色航行": ["misc.stubs"],      # 仅经 misc.stubs 的 /green-shipping/efficiency
    "冰区航行": ["misc.stubs"],      # 仅经 misc.stubs 的 /ice-navigation/route
}

#: 声明等级为这些值时，必须给出 verified_by 机检指针（schema 门）
NEEDS_PROBE = ("REAL", "WITHHELD")

RHO_SW = 1.025  # 海水密度 t/m³（平台 hydrostatics.py 同源常数）


# --------------------------------------------------------------------------- #
# 1) 注册表 schema 门
# --------------------------------------------------------------------------- #
def load_registry(path: str) -> dict:
    """读 capabilities.json；缺文件/坏 JSON 抛错（不静默降级）。"""
    with open(path, "r", encoding="utf-8") as fh:
        reg = json.load(fh)
    if "capabilities" not in reg or not isinstance(reg["capabilities"], dict):
        raise ValueError(f"{path}: 缺 capabilities 字典")
    return reg


def schema_findings(reg: dict) -> list[dict]:
    """schema 门：返回所有「声明了证据等级但没有机检指针」的条目。"""
    out = []
    for cap_id, cap in sorted(reg["capabilities"].items()):
        ev = str(cap.get("evidence", "?")).upper()
        if ev not in EVIDENCE_LEVELS:
            out.append({"capability": cap_id, "kind": "UNKNOWN_LEVEL",
                        "detail": f"非法证据等级 {ev!r}"})
            continue
        probes = cap.get("verified_by") or []
        if ev in NEEDS_PROBE and not probes:
            out.append({"capability": cap_id, "kind": "UNVERIFIED_EVIDENCE",
                        "detail": f"声明 {ev} 但未声明 verified_by 探针"})
        elif ev in NEEDS_PROBE and not _probe_registered(probes):
            out.append({"capability": cap_id, "kind": "DANGLING_PROBE",
                        "detail": f"verified_by {probes} 在探针表中不存在"})
    return out


def _probe_registered(probe_ids) -> bool:
    return all(pid in PROBES for pid in probe_ids)


# --------------------------------------------------------------------------- #
# 2) 学科域覆盖矩阵
# --------------------------------------------------------------------------- #
def domain_coverage(reg: dict, reach: dict | None = None) -> dict:
    """9 学科域 × 注册能力 → 覆盖判定。

    reach: {capability_id: "MOUNTED_OK"|"MOUNTED_ERROR"|"NOT_MOUNTED"|...}（在线探针结果，
    可缺；缺则不参与可达性判定）。
    """
    reach = reach or {}
    caps = reg["capabilities"]
    rows, summary = [], {"SERVABLE_REAL": 0, "REGISTERED_ONLY": 0, "UNREGISTERED": 0}
    for domain, cap_ids in DOMAIN_MAP.items():
        present = [c for c in cap_ids if c in caps]
        ev = {c: str(caps[c].get("evidence", "?")).upper() for c in present}
        best = max((EVIDENCE_RANK.get(e, 0) for e in ev.values()), default=-1)
        servable = [
            c for c in present
            if ev[c] == "REAL"
            and caps[c].get("reachable_direct") is True
            and not any(k.startswith(c + ":") and v == "NOT_MOUNTED"
                        for k, v in reach.items())
        ]
        if servable:
            verdict = "SERVABLE_REAL"
        elif present:
            verdict = "REGISTERED_ONLY"
        else:
            verdict = "UNREGISTERED"
        summary[verdict] += 1
        rows.append({"domain": domain, "capabilities": present, "evidence": ev,
                     "best_rank": best, "servable": servable, "verdict": verdict})
    return {"rows": rows, "summary": summary, "n_domains": len(DOMAIN_MAP)}


def reach_conflicts(reg: dict, reach: dict) -> list[dict]:
    """声明可达 vs 在线探针不一致。"""
    out = []
    for cap_id, cap in sorted(reg["capabilities"].items()):
        for ep in cap.get("endpoints") or []:
            st = reach.get(f"{cap_id}:{ep['path']}")
            if st == "NOT_MOUNTED":
                out.append({"capability": cap_id, "path": ep["path"],
                            "declared_reachable": bool(cap.get("reachable_direct")),
                            "probe": st})
    return out


# --------------------------------------------------------------------------- #
# 3) 物理 / 解析探针（纯函数，离线可测）
# --------------------------------------------------------------------------- #
def _res(probe, capability, verdict, criterion, metrics, basis, note=""):
    return {"probe": probe, "capability": capability, "verdict": verdict,
            "criterion": criterion, "metrics": metrics, "basis": basis, "note": note}


def probe_propeller_series(points: list[dict], rel_tol: float = 1e-6) -> dict:
    """敞水特性退化探针：KQ≡KT/k 这类硬编码伪造的指纹是 KT/KQ 与 J 无关。

    basis: 敞水试验的物理约束 —— KT(J)、KQ(J) 均随进速比单调降，且
    η_o=J·KT/(2π·KQ) ∈ (0,1]，KT/KQ 不可能对 J 保持不变（ITTC 敞水试验规程 7.5-02-03-01）。
    """
    cap = "propeller.open_water"
    pts = [p for p in points if float(p.get("J", 0.0)) > 0.0
           and float(p.get("KT", 0.0)) > 0.0 and float(p.get("KQ", 0.0)) > 0.0]
    if len(pts) < 3:
        return _res("propeller_series", cap, "INCONCLUSIVE", "≥3 个 J>0 点",
                    {"n_points": len(pts)}, "ITTC 7.5-02-03-01")
    ratios = [float(p["KT"]) / float(p["KQ"]) for p in pts]
    spread = (max(ratios) - min(ratios)) / (sum(ratios) / len(ratios))
    kt = [float(p["KT"]) for p in sorted(pts, key=lambda p: float(p["J"]))]
    mono = all(kt[i] > kt[i + 1] for i in range(len(kt) - 1))
    eta = [float(p["J"]) * float(p["KT"]) / (2 * math.pi * float(p["KQ"])) for p in pts]
    eta_ok = all(0.0 < e <= 1.0 for e in eta)
    metrics = {"ratio_spread": round(spread, 12), "KT_monotone_decreasing": mono,
               "eta_o_range": [round(min(eta), 6), round(max(eta), 6)]}
    if spread < rel_tol:
        return _res("propeller_series", cap, "FAIL",
                    "KT/KQ 相对离散 > 1e-6（否则判硬编码伪造）", metrics,
                    "ITTC 7.5-02-03-01", note="KT/KQ 对 J 恒定 → 疑似 KQ≡KT/k 伪造")
    if not mono or not eta_ok:
        return _res("propeller_series", cap, "FAIL",
                    "KT 单调降且 0<η_o≤1", metrics, "ITTC 7.5-02-03-01",
                    note="敞水曲线违反单调性/效率物理界")
    return _res("propeller_series", cap, "PASS", "KT/KQ 有离散 + 单调降 + 0<η_o≤1",
                metrics, "ITTC 7.5-02-03-01")


def ittc57_cf(Re: float) -> float:
    """ITTC-1957 平板摩擦系数 cf = 0.075/(log10 Re - 2)²。"""
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def probe_friction_line(points: list[dict], tol_pass: float = 0.01,
                        tol_fail: float = 0.05) -> dict:
    """阻力链路摩擦分量探针：返回的 cf 必须落在 ITTC-1957 线上（±1%）。"""
    cap = "calc.resistance"
    errs = []
    for p in points:
        Re, cf = float(p["Re"]), float(p["cf"])
        ref = ittc57_cf(Re)
        errs.append(abs(cf - ref) / ref)
    if not errs:
        return _res("ittc57_friction", cap, "INCONCLUSIVE", "≥1 个 (Re, cf) 样本",
                    {}, "ITTC-1957")
    worst = max(errs)
    metrics = {"n": len(errs), "max_rel_err": round(worst, 6),
               "mean_rel_err": round(sum(errs) / len(errs), 6)}
    verdict = "PASS" if worst <= tol_pass else ("FAIL" if worst > tol_fail else "INCONCLUSIVE")
    return _res("ittc57_friction", cap, verdict, f"max rel err ≤ {tol_pass:.0%}",
                metrics, "ITTC-1957 平板摩擦线")


def probe_hydro_identities(payload: dict, tol: float = 1e-3, tol_fd: float = 0.03) -> dict:
    """静水力积分恒等式探针（独立交叉校验：积分量 vs 微分量）。

    基准（解析恒等）：
      TPC  ≡ ρ·Awp/100                    （每厘米吃水吨数定义）
      Δ    ≡ ρ·∇                          （阿基米德）
      Cb(d)≡ ∇/(L·B·d)                    （方形系数定义）
      dΔ/dd ≈ ρ·Awp                       （水线面积是一阶导 —— 与积分路径独立的交叉检验）
    """
    cap = "geometry.hydro_curves"
    L, B = float(payload["L"]), float(payload["B"])
    rho = float(payload.get("rho", RHO_SW))
    cur = payload["curves"]
    drafts = [float(x) for x in cur["drafts"]]
    vol = [float(x) for x in cur["displacement_volume"]]
    disp = [float(x) for x in cur["displacement"]]
    awp = [float(x) for x in cur["Awp"]]
    cb = [float(x) for x in cur["Cb"]]
    tpc = [float(x) for x in cur["TPC"]]
    m = {}
    m["tpc_max_rel_err"] = round(max(abs(t - rho * a / 100.0) / (rho * a / 100.0)
                                     for t, a in zip(tpc, awp)), 9)
    m["disp_max_rel_err"] = round(max(abs(d - rho * v) / (rho * v)
                                      for d, v in zip(disp, vol)), 9)
    m["cb_max_rel_err"] = round(max(abs(c - v / (L * B * dd)) / (v / (L * B * dd))
                                    for c, v, dd in zip(cb, vol, drafts)), 6)
    fd = []
    for i in range(1, len(drafts) - 1):
        d_disp = (disp[i + 1] - disp[i - 1]) / (drafts[i + 1] - drafts[i - 1])
        ref = rho * awp[i]
        fd.append(abs(d_disp - ref) / ref)
    m["ddisp_ddraft_rel_err"] = round(max(fd), 6) if fd else None
    worst = max(m["tpc_max_rel_err"], m["disp_max_rel_err"], m["cb_max_rel_err"])
    ok = worst <= tol and (m["ddisp_ddraft_rel_err"] or 0.0) <= tol_fd
    verdict = "PASS" if ok else "FAIL"
    return _res("hydro_identities", cap, verdict,
                f"恒等式 rel err ≤ {tol:g} 且 dΔ/dd ≤ {tol_fd:.0%}",
                m, "TPC/Δ/Cb 定义 + 阿基米德原理（微-积分交叉检验）")


def probe_gm_identity(payload: dict, tol: float = 0.01) -> dict:
    """稳性初稳心恒等式：GM = KB + BMt − KG。"""
    cap = "calc.stability"
    kb, bm, kg = float(payload["KB"]), float(payload["BMt"]), float(payload["KG"])
    gm = float(payload["GM"])
    ref = kb + bm - kg
    err = abs(gm - ref) / max(abs(ref), 1e-9)
    return _res("gm_identity", cap, "PASS" if err <= tol else "FAIL",
                f"|GM-(KB+BMt-KG)|/|GM| ≤ {tol:.0%}",
                {"GM_reported": gm, "GM_from_identity": round(ref, 6),
                 "rel_err": round(err, 6)}, "初稳性 GM=KB+BMt−KG（静水力解析恒等）")


def probe_hardcode_echo(runs: list[dict], rel_tol: float = 1e-12) -> dict:
    """硬编码/回声指纹探针：不同输入产出逐位相同的数值指纹 → MOCK。

    只比较数值叶子（labels/scenario 字符串除外），避免把合法的同源常数（ρ 等）误判。
    """
    cap = "misc.stubs"
    if len(runs) < 2:
        return _res("hardcode_echo", cap, "INCONCLUSIVE", "≥2 组不同输入的输出",
                    {"n_runs": len(runs)}, "MOCK 指纹法（misc.stubs 桶）")
    fps = [_numeric_fingerprint(r) for r in runs]
    same = all(f == fps[0] for f in fps)
    metrics = {"n_runs": len(runs), "distinct_fingerprints": len({tuple(f) for f in fps}),
               "n_numeric_leaves": len(fps[0])}
    return _res("hardcode_echo", cap, "FAIL" if same else "PASS",
                "不同输入 → 不同数值指纹",
                metrics, "MOCK 指纹法（misc.stubs 桶）",
                note="输出与输入无关 → 硬编码" if same else "")


def _numeric_fingerprint(obj, out=None):
    if out is None:
        out = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(round(float(obj), 9))
    elif isinstance(obj, dict):
        for k in sorted(obj):
            _numeric_fingerprint(obj[k], out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numeric_fingerprint(v, out)
    return out


PROBES = {
    "propeller_series": probe_propeller_series,
    "ittc57_friction": probe_friction_line,
    "hydro_identities": probe_hydro_identities,
    "gm_identity": probe_gm_identity,
    "hardcode_echo": probe_hardcode_echo,
}


# --------------------------------------------------------------------------- #
# 4) 在线端点可达性探针（只读；GET 直连、POST 空体触发校验错误即视为已挂载）
# --------------------------------------------------------------------------- #
REACH_MOUNTED = ("MOUNTED_OK", "MOUNTED_VALIDATION", "MOUNTED_AUTH", "MOUNTED_ERROR")


def classify_status(code: int) -> str:
    if code == 200:
        return "MOUNTED_OK"
    if code in (404, 405):
        return "NOT_MOUNTED"
    if code == 422 or code == 400:
        return "MOUNTED_VALIDATION"
    if code in (401, 403):
        return "MOUNTED_AUTH"
    if 500 <= code < 600:
        return "MOUNTED_ERROR"
    return f"HTTP_{code}"


def probe_reachability(reg: dict, base: str = "http://127.0.0.1:8001",
                       timeout: float = 8.0) -> dict:
    """对每个声明端点做一次只读探测，返回 {cap:path -> 分类}。"""
    reach = {}
    for cap_id, cap in sorted(reg["capabilities"].items()):
        for ep in cap.get("endpoints") or []:
            url = base.rstrip("/") + ep["path"]
            method = ep.get("method", "GET").upper()
            data = None
            if method == "POST":
                data = b"{}"
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    reach[f"{cap_id}:{ep['path']}"] = classify_status(resp.status)
            except urllib.error.HTTPError as exc:
                reach[f"{cap_id}:{ep['path']}"] = classify_status(exc.code)
            except Exception as exc:            # 连接失败
                reach[f"{cap_id}:{ep['path']}"] = f"UNREACHABLE:{type(exc).__name__}"
    return reach


# --------------------------------------------------------------------------- #
# 5) 对账
# --------------------------------------------------------------------------- #
def reconcile(reg: dict, probe_results: list[dict], reach: dict | None = None) -> list[dict]:
    """声明等级 vs 探针判据。"""
    reach = reach or {}
    by_cap: dict[str, list[dict]] = {}
    for r in probe_results:
        by_cap.setdefault(r["capability"], []).append(r)
    out = []
    for cap_id, cap in sorted(reg["capabilities"].items()):
        ev = str(cap.get("evidence", "?")).upper()
        res = by_cap.get(cap_id, [])
        fails = [r for r in res if r["verdict"] == "FAIL"]
        unmoored = [k for k, v in reach.items()
                    if k.startswith(cap_id + ":") and v == "NOT_MOUNTED"]
        if fails and ev == "REAL":
            status = "DRIFT_DECLARED_REAL_PROBE_FAIL"
        elif fails and ev in ("STUB", "MOCK"):
            status = "CONSISTENT"          # 声明本来就是假，探针也判假 → 一致
        elif unmoored and ev == "REAL":
            status = "DRIFT_UNREACHABLE"
        elif res and not fails:
            status = "PROBE_PASS"
        elif ev in NEEDS_PROBE:
            status = "UNVERIFIED"
        else:
            status = "DECLARED_ONLY"
        out.append({"capability": cap_id, "declared": ev, "n_probes": len(res),
                    "probe_verdicts": [r["verdict"] for r in res],
                    "unmounted_endpoints": unmoored, "status": status})
    return out


def gate_summary(reg: dict, findings, coverage, recon) -> dict:
    caps = reg["capabilities"]
    n = len(caps)
    n_real = sum(1 for c in caps.values() if str(c.get("evidence")).upper() == "REAL")
    n_real_with_probe = sum(1 for k, c in caps.items()
                            if str(c.get("evidence")).upper() == "REAL"
                            and (c.get("verified_by") or []))
    drift = [r for r in recon if r["status"].startswith("DRIFT")]
    return {
        "n_capabilities": n,
        "n_declared_REAL": n_real,
        "n_declared_REAL_with_verified_by": n_real_with_probe,
        "verified_by_coverage": round(n_real_with_probe / n_real, 4) if n_real else None,
        "n_schema_findings": len(findings),
        "n_domains": coverage["n_domains"],
        "domain_summary": coverage["summary"],
        "domain_real_rate": round(coverage["summary"]["SERVABLE_REAL"] / coverage["n_domains"], 4),
        "n_drift": len(drift),
        "drift_capabilities": [r["capability"] for r in drift],
        "n_unverified": sum(1 for r in recon if r["status"] == "UNVERIFIED"),
        "n_probe_pass": sum(1 for r in recon if r["status"] == "PROBE_PASS"),
    }


# --------------------------------------------------------------------------- #
# 6) 夹具（合成 + 回归）；供测试与离线运行
# --------------------------------------------------------------------------- #
def fixture_payloads() -> dict:
    """离线探针夹具。

    - synthetic_*: 由解析关系正向生成的「物理自洽」样本，用于证明探针不误报；
    - regress_*:  历史实测缺陷的回归夹具（出处见 source 字段），用于证明探针能抓假。
    """
    # 静水力自洽样本：矩形等截面船型（解析解可算）
    L, B = 100.0, 20.0
    drafts = [1.0, 2.0, 3.0, 4.0, 5.0]
    awp = [L * B for _ in drafts]
    vol = [L * B * d for d in drafts]
    return {
        "synthetic_propeller_ok": {
            "source": "synthetic: KT=0.35-0.30J, KQ=0.042-0.030J（B-series 量级）",
            "points": [{"J": j, "KT": 0.35 - 0.30 * j, "KQ": 0.042 - 0.030 * j}
                       for j in (0.1, 0.3, 0.5, 0.7)]},
        "regress_propeller_kq_kt_div10": {
            "source": "回归夹具：报告 1032（m1443）实测敞水系数伪造 KQ≡KT/10",
            "points": [{"J": j, "KT": kt, "KQ": kt / 10.0}
                       for j, kt in ((0.1, 0.30), (0.3, 0.26), (0.5, 0.22), (0.7, 0.18))]},
        "synthetic_friction_ok": {
            "source": "synthetic: cf 直接取 ITTC-1957 线",
            "points": [{"Re": Re, "cf": ittc57_cf(Re)}
                       for Re in (1e8, 5e8, 2e9)]},
        "regress_friction_plus41": {
            "source": "回归夹具：报告 1032 实测总阻力 +41%（摩擦分量被放大）",
            "points": [{"Re": Re, "cf": ittc57_cf(Re) * 1.41}
                       for Re in (1e8, 5e8, 2e9)]},
        "synthetic_hydro_ok": {
            "source": "synthetic: 矩形等截面船型解析解（Awp=LB, ∇=LBd, TPC=ρAwp/100）",
            "payload": {"L": L, "B": B, "rho": RHO_SW,
                        "curves": {"drafts": drafts, "displacement_volume": vol,
                                   "displacement": [RHO_SW * v for v in vol],
                                   "Awp": awp, "Cb": [v / (L * B * d) for v, d in zip(vol, drafts)],
                                   "TPC": [RHO_SW * a / 100.0 for a in awp]}}},
        "regress_hydro_mtc_style": {
            "source": "回归夹具：Awp 未乘 ρ 导致 TPC 低 2.44%（常量错置）",
            "payload": {"L": L, "B": B, "rho": RHO_SW,
                        "curves": {"drafts": drafts, "displacement_volume": vol,
                                   "displacement": [v for v in vol],  # 漏了 ρ
                                   "Awp": awp, "Cb": [v / (L * B * d) for v, d in zip(vol, drafts)],
                                   "TPC": [a / 100.0 for a in awp]}}},
        "synthetic_gm_ok": {"source": "synthetic",
                            "payload": {"KB": 4.0, "BMt": 6.5, "KG": 7.0, "GM": 3.5}},
        "regress_gm_mismatch": {"source": "回归夹具：GM 未含 BMt 项",
                                "payload": {"KB": 4.0, "BMt": 6.5, "KG": 7.0, "GM": -3.0}},
        "regress_stub_echo": {"source": "回归夹具：不同输入逐位同输出（硬编码）",
                              "runs": [{"eta": 0.85, "power_kw": 1234.5, "fuel_tpd": 42.0},
                                       {"eta": 0.85, "power_kw": 1234.5, "fuel_tpd": 42.0}]},
        "synthetic_vary_ok": {"source": "synthetic: 输出随输入变化",
                              "runs": [{"eta": 0.85}, {"eta": 0.72}]},
    }


def run_fixture_probes(fx: dict | None = None) -> list[dict]:
    fx = fx or fixture_payloads()
    return [
        probe_propeller_series(fx["synthetic_propeller_ok"]["points"]),
        probe_propeller_series(fx["regress_propeller_kq_kt_div10"]["points"]),
        probe_friction_line(fx["synthetic_friction_ok"]["points"]),
        probe_friction_line(fx["regress_friction_plus41"]["points"]),
        probe_hydro_identities(fx["synthetic_hydro_ok"]["payload"]),
        probe_hydro_identities(fx["regress_hydro_mtc_style"]["payload"]),
        probe_gm_identity(fx["synthetic_gm_ok"]["payload"]),
        probe_gm_identity(fx["regress_gm_mismatch"]["payload"]),
        probe_hardcode_echo(fx["regress_stub_echo"]["runs"]),
        probe_hardcode_echo(fx["synthetic_vary_ok"]["runs"]),
    ]


# --------------------------------------------------------------------------- #
# 7) 产物：JSON / Markdown / 图 / 平台报告载荷
# --------------------------------------------------------------------------- #
DOMAIN_EN = {"阻力": "Resistance", "推进": "Propulsion", "耐波": "Seakeeping",
             "操纵": "Manoeuvring", "稳性": "Stability", "结构": "Structure",
             "振动噪声": "Vib./Noise", "绿色航行": "Green", "冰区航行": "Ice"}


def _cjk_font():
    """找一个可用 CJK 字体；没有就退回英文标签（避免豆腐块图）。"""
    try:
        from matplotlib import font_manager as fm
    except Exception:
        return None
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei",
                 "WenQuanYi Micro Hei", "Source Han Sans CN", "SimHei"):
        try:
            if any(name == f.name for f in fm.fontManager.ttflist):
                return name
        except Exception:
            continue
    return None


def _figure(summary: dict, coverage: dict, path: pathlib.Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    cjk = _cjk_font()
    if cjk:
        matplotlib.rcParams["font.sans-serif"] = [cjk]
        matplotlib.rcParams["axes.unicode_minus"] = False
    dn = (lambda d: d) if cjk else (lambda d: DOMAIN_EN.get(d, d))
    L = (lambda zh, en: zh) if cjk else (lambda zh, en: en)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    rows = coverage["rows"]
    colors = {"SERVABLE_REAL": "#2a9d8f", "REGISTERED_ONLY": "#e9c46a",
              "UNREGISTERED": "#e76f51"}
    ax = axes[0]
    xs = range(len(rows))
    ax.bar(xs, [1] * len(rows), color=[colors[r["verdict"]] for r in rows], width=0.7)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([dn(r["domain"]) for r in rows], rotation=45, ha="right", fontsize=9)
    ax.set_yticks([])
    for i, r in enumerate(rows):
        ax.text(i, 0.5, r["verdict"].replace("_", "\n"), ha="center", va="center", fontsize=7)
    ax.set_title(L("9 学科域覆盖判定（capability registry）",
                       "Domain coverage verdict (capability registry)"), fontsize=10)

    ax = axes[1]
    labels = ([L("REAL 声明", "declared REAL"), L("…带 verified_by", "…with verified_by"),
               L("域可服务 REAL", "domains REAL-servable"), L("漂移项", "drift"),
               L("未验证项", "unverified")])
    vals = [summary["n_declared_REAL"], summary["n_declared_REAL_with_verified_by"],
            summary["domain_summary"]["SERVABLE_REAL"], summary["n_drift"],
            summary["n_unverified"]]
    ax.bar(labels, vals, color=["#457b9d", "#a8dadc", "#2a9d8f", "#e63946", "#f4a261"])
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_title(L("证据链机检覆盖", "Machine-checked evidence coverage"), fontsize=10)
    ax.tick_params(axis="x", labelsize=8, rotation=20)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def _md_report(summary, findings, coverage, recon, probes, reach, moniker) -> str:
    lines = [f"# 能力证据门 capgate 报告 · {moniker}", "",
             "## 1 摘要（数字全部由 capgate.py 程序化注入）", "",
             "| 指标 | 值 |", "| --- | --- |"]
    for k in ("n_capabilities", "n_declared_REAL", "n_declared_REAL_with_verified_by",
              "verified_by_coverage", "n_schema_findings", "n_domains",
              "domain_real_rate", "n_drift", "n_unverified", "n_probe_pass"):
        lines.append(f"| {k} | {summary[k]} |")
    lines += ["", "## 2 9 学科域覆盖矩阵", "",
              "| 学科域 | 注册能力 | 证据等级 | 可服务(REAL+可达) | 判定 |",
              "| --- | --- | --- | --- | --- |"]
    for r in coverage["rows"]:
        lines.append("| {} | {} | {} | {} | {} |".format(
            r["domain"], ", ".join(r["capabilities"]) or "—",
            ", ".join(f"{k}={v}" for k, v in r["evidence"].items()) or "—",
            ", ".join(r["servable"]) or "—", r["verdict"]))
    lines += ["", "## 3 探针结果（离线夹具：合成自洽 + 历史缺陷回归）", "",
              "| 探针 | 载体 | 判定 | 基准 | 关键量 |", "| --- | --- | --- | --- | --- |"]
    for p in probes:
        key = ", ".join(f"{k}={v}" for k, v in list(p["metrics"].items())[:3])
        lines.append(f"| {p['probe']} | {p['capability']} | {p['verdict']} | "
                     f"{p['basis']} | {key} |")
    if reach:
        lines += ["", "## 4 在线端点可达性（只读探测）", "", "| 端点 | 分类 |", "| --- | --- |"]
        for k, v in sorted(reach.items()):
            lines.append(f"| {k} | {v} |")
    lines += ["", "## 5 声明 vs 探针 对账", "", "| 能力 | 声明 | 探针 | 状态 |", "| --- | --- | --- | --- |"]
    for r in recon:
        lines.append(f"| {r['capability']} | {r['declared']} | "
                     f"{','.join(r['probe_verdicts']) or '—'} | {r['status']} |")
    if findings:
        lines += ["", "## 6 schema 门发现", ""]
        for f in findings:
            lines.append(f"- `{f['capability']}` {f['kind']}: {f['detail']}")
    return "\n".join(lines) + "\n"


def _report_payload(summary, coverage, moniker, mission_id, md, fig_path):
    fig_b64, cap = None, ""
    if fig_path and pathlib.Path(fig_path).exists():
        fig_b64 = base64.b64encode(pathlib.Path(fig_path).read_bytes()).decode()
        cap = "左：9 学科域覆盖判定；右：证据链机检覆盖（REAL 声明 vs 带探针 vs 可服务域 vs 漂移/未验证）。"
    return {
        "title": f"平台能力证据门 capgate：21 项能力声明 vs 机检（9 域仅 {summary['domain_summary']['SERVABLE_REAL']} 域可服务 REAL，"
                 f"verified_by 覆盖 {summary['verified_by_coverage']:.0%}）· {moniker}",
        "summary": (f"把 capabilities.json 的手工证据等级变成机器判据：schema 门（REAL/WITHHELD 必须带 verified_by）"
                    f"+ 9 学科域覆盖矩阵 + 端点可达性探针 + 5 类物理/解析探针 + 对账。"
                    f"结果：{summary['n_capabilities']} 项能力中声明 REAL {summary['n_declared_REAL']} 项，"
                    f"其中带机检指针 {summary['n_declared_REAL_with_verified_by']} 项"
                    f"（覆盖 {summary['verified_by_coverage']:.1%}）；9 学科域里可服务 REAL {summary['domain_summary']['SERVABLE_REAL']} 个、"
                    f"仅注册不可用 {summary['domain_summary']['REGISTERED_ONLY']} 个、完全无能力位 {summary['domain_summary']['UNREGISTERED']} 个。"),
        "scenario_key": "capability-evidence-gate",
        "mission_id": mission_id,
        "tags": ["infra", "evidence-gate", "audit", "capabilities", "verification-debt"],
        "markdown": md,
        "figures": ([{"name": "capgate.png", "caption": cap, "png_base64": fig_b64}]
                    if fig_b64 else []),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--live", action="store_true", help="对声明端点做只读可达性探测")
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--out-dir", default="artifacts/capgate")
    ap.add_argument("--doc", default=None)
    ap.add_argument("--report-json", default=None)
    ap.add_argument("--mission-id", default=0, type=int)
    ap.add_argument("--moniker", default=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    args = ap.parse_args(argv)

    reg = load_registry(args.registry)
    findings = schema_findings(reg)
    reach = probe_reachability(reg, args.base) if args.live else {}
    coverage = domain_coverage(reg, reach)
    probes = run_fixture_probes()
    recon = reconcile(reg, probes, reach)
    summary = gate_summary(reg, findings, coverage, recon)
    summary["reach_probe_count"] = len(reach)
    summary["reach_mounted"] = sum(1 for v in reach.values() if v in REACH_MOUNTED)
    drift_eps = reach_conflicts(reg, reach)
    summary["n_reach_conflicts"] = len(drift_eps)

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fig = out / "capgate.png"
    has_fig = _figure(summary, coverage, fig)
    md = _md_report(summary, findings, coverage, recon, probes, reach, args.moniker)
    artifact = {"generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "registry": os.path.abspath(args.registry), "summary": summary,
                "schema_findings": findings, "domain_coverage": coverage,
                "probes": probes, "reconcile": recon, "reach": reach,
                "reach_conflicts": drift_eps, "figure": str(fig) if has_fig else None}
    (out / "capgate.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    if args.doc:
        doc = pathlib.Path(args.doc)
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(md, encoding="utf-8")
    if args.report_json:
        payload = _report_payload(summary, coverage, args.moniker, args.mission_id, md,
                                  fig if has_fig else None)
        pathlib.Path(args.report_json).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return artifact


if __name__ == "__main__":
    main()