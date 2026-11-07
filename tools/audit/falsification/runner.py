"""探针执行器：把 capabilities.json 里的 callable_spec 变成真实调用与刀具证据。

两个传输层：
  - `HttpTransport`   —— 打 :8001 在线船舶平台（审计主战场）；
  - `FunctionTransport` —— 进程内路由表（变异体自检用，证明刀具本身有检出能力）。

所有调用按 (method, path, body) 去重缓存，保证同一请求体只发一次 → 结果可复现、
可离线重放（trace 落盘）。
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from . import knives

DEFAULT_BASE = "http://127.0.0.1:8001"


# ---------------------------------------------------------------- 工具
def dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _v_ms(body: Dict[str, Any]) -> Optional[float]:
    v = knives.safe_float(body.get("speed"))
    if v is None:
        return None
    unit = (body.get("speed_unit") or "kn").lower()
    if unit in ("kn", "knot", "knots"):
        return v * knives.KNOT_TO_MS
    if unit in ("m/s", "ms"):
        return v
    return None


# ---------------------------------------------------------------- 解析参照实现
def ittc57_cf_of_body(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    v = _v_ms(body)
    L = knives.safe_float(body.get("length"))
    if v is None or not L:
        return None
    nu = knives.safe_float(body.get("water_viscosity")) or knives.NU_DEFAULT
    return knives.ittc57_cf(knives.reynolds(v, L, nu))


def rf_of_response(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    """Rf = 0.5*rho*V^2*S*Cf_total / 1000（单位 kN；Cf_total = 光滑 Cf + ITTC-1978 粗糙度补贴 dcf）。

    平台把粗糙度补贴并进摩擦项（响应里 cf_total 字段显式给出），故恒等式用 cf_total；
    若用光滑 cf，则该式与 rf 的差额恰为 dcf 项（本函数保留该口径说明，避免误判 MOCK）。
    """
    v = _v_ms(body)
    S = knives.safe_float(resp.get("wetted_surface"))
    cf = knives.safe_float(resp.get("cf_total")) or knives.safe_float(resp.get("cf"))
    rho = knives.safe_float(body.get("water_density")) or knives.RHO_DEFAULT
    if None in (v, S, cf):
        return None
    return 0.5 * rho * v * v * S * cf / 1000.0   # 平台阻力字段单位为 kN


def gm_of_response(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    kb, bm = knives.safe_float(resp.get("kb")), knives.safe_float(resp.get("bm"))
    kg = knives.safe_float(body.get("kg"))
    if None in (kb, bm, kg):
        return None
    return knives.gm_identity(kb, bm, kg)


def gz_max_of_response(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    """直壁式公式只在「甲板边缘入水」前的倾角域内成立；超出该域 tan^2(phi) 发散
    （数学必然、物理无效）。故解析求极值的域取平台自报的 heel_validity_limit_deg。"""
    gm, bm = knives.safe_float(resp.get("gm")), knives.safe_float(resp.get("bm"))
    if None in (gm, bm):
        return None
    limit = knives.safe_float(resp.get("heel_validity_limit_deg")) or 89.0
    return knives.gz_wallsided_max(gm, bm, phi_max_deg=min(limit, 89.0))["gz_max"]


def eta0_of_response(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    J = knives.safe_float(body.get("J"))
    KT, KQ = knives.safe_float(resp.get("KT")), knives.safe_float(resp.get("KQ"))
    if None in (J, KT, KQ) or KQ == 0:
        return None
    return knives.eta0_identity(J, KT, KQ)


def volume_of_body(resp: Dict[str, Any], body: Dict[str, Any]) -> Optional[float]:
    L = knives.safe_float(body.get("Lpp"))
    B = knives.safe_float(body.get("B"))
    T = knives.safe_float(body.get("T"))
    cb = knives.safe_float(body.get("Cb"))
    if None in (L, B, T, cb):
        return None
    return knives.box_volume(L, B, T, cb)


REF_FNS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Optional[float]]] = {
    "ittc57_cf_of_body": ittc57_cf_of_body,
    "rf_of_response": rf_of_response,
    "gm_of_response": gm_of_response,
    "gz_max_of_response": gz_max_of_response,
    "eta0_of_response": eta0_of_response,
    "volume_of_body": volume_of_body,
}


# ---------------------------------------------------------------- 传输层
class HttpTransport:
    name = "http"

    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.n_http_calls = 0

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
             fresh: bool = False) -> Dict[str, Any]:
        """fresh=True 时绕过缓存（刀 A2 的重复性探针必须真的重发请求）。"""
        key = json.dumps([method.upper(), path, body], sort_keys=True)
        if key in self.cache and not fresh:
            return {**self.cache[key], "cached": True}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method.upper())
        req.add_header("Content-Type", "application/json")
        self.n_http_calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode()
                try:
                    js = json.loads(raw)
                except Exception:
                    js = {"_raw": raw[:2000]}
                out = {"status": r.status, "json": js, "error": None}
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:800]
            out = {"status": e.code, "json": None, "error": detail}
        except Exception as e:  # 连接失败/超时：不得静默当成功
            out = {"status": None, "json": None, "error": f"{type(e).__name__}: {e}"}
        self.cache[key] = out
        return out


class FunctionTransport:
    name = "function"

    def __init__(self, routes: Dict[str, Callable[[Dict[str, Any]], Any]]):
        self.routes = routes
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.n_http_calls = 0

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
             fresh: bool = False) -> Dict[str, Any]:
        key = json.dumps([method.upper(), path, body], sort_keys=True)
        if key in self.cache and not fresh:
            return {**self.cache[key], "cached": True}
        self.n_http_calls += 1
        fn = self.routes.get(path)
        if fn is None:
            out = {"status": 404, "json": None, "error": f"no route {path}"}
        else:
            try:
                js = fn(body or {})
                out = {"status": 200, "json": js, "error": None}
            except Exception as e:
                out = {"status": 500, "json": None, "error": f"{type(e).__name__}: {e}"}
        self.cache[key] = out
        return out


# ---------------------------------------------------------------- 单能力探针
def _record(res: Dict[str, Any], sample: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    js = res.get("json")
    extracted = {k: dig(js, k) for k in keys} if isinstance(js, (dict, list)) else {k: None for k in keys}
    return {
        "label": sample["label"],
        "knobs": sample.get("knobs") or {},
        "body": sample["body"],
        "status": res.get("status"),
        "error": res.get("error"),
        "response_digest": digest(js) if js is not None else None,
        "extracted": extracted,
    }


def probe_numeric(entry: Dict[str, Any], transport) -> Dict[str, Any]:
    """执行一个数值型能力的全部样本 → 三把刀的原始证据。"""
    spec = entry["callable_spec"]
    path, method = spec["path"], spec["method"]
    keys = spec["extract_keys"]
    calls: List[Dict[str, Any]] = []
    by_label: Dict[str, Dict[str, Any]] = {}

    def run(sample: Dict[str, Any]) -> Dict[str, Any]:
        res = transport.call(method, path, sample["body"])
        rec = _record(res, sample, keys)
        calls.append(rec)
        by_label[sample["label"]] = rec
        return rec

    for grp in spec["perturbation_groups"]:
        for s in grp["samples"]:
            run(s)
        for s in grp.get("confirm") or []:
            run(s)
    for s in spec.get("extra_samples") or []:
        run(s)
    for series in spec.get("monotone_series") or []:
        for s in series["points"]:
            run(s)

    def resp_of(label: str) -> Dict[str, Any]:
        rec = by_label.get(label) or {}
        return rec.get("extracted") or {}

    knife_results: List[Dict[str, Any]] = []
    # ---- 刀 A2：同一请求重复 3 次（绕过缓存），输出必须逐位相同
    base_sample = _all_samples(spec)[0] if _all_samples(spec) else None
    if base_sample is not None:
        reps = []
        for i in range(3):
            res = transport.call(method, path, base_sample["body"], fresh=True)
            rec = _record(res, {"label": f"repeat_{i + 1}", "knobs": {},
                                "body": base_sample["body"]}, keys)
            calls.append(rec)
            reps.append({"label": rec["label"], "response": rec["extracted"]})
        kr = knives.input_output_determinism(reps)
        kr["driver"] = "repeatability"
        kr["spec"] = "同一请求重复 3 次（空扰动，检验输出是否含随机数）"
        knife_results.append(kr)
    # ---- 刀 A
    for grp in spec["perturbation_groups"]:
        primary = [{"label": s["label"], "knobs": s.get("knobs"),
                    "response": resp_of(s["label"])} for s in grp["samples"]]
        confirm = [{"label": s["label"], "knobs": s.get("knobs"),
                    "response": resp_of(s["label"])} for s in (grp.get("confirm") or [])]
        kr = knives.perturbation_invariance(primary, confirm or None, grp.get("require"))
        kr["driver"] = grp["driver"]
        kr["spec"] = grp["spec"]
        knife_results.append(kr)
    # ---- 刀 B
    checks = []
    for chk in spec.get("analytic_checks") or []:
        observed = resp_of(_observed_sample_label(spec, chk)).get(chk["observed_key"])
        ref = None
        formula = chk.get("formula")
        mode = chk.get("ref_mode")
        if mode == "fn_body":
            ref = REF_FNS[chk["ref_fn"]]({}, _base_body(spec))
        elif mode == "fn_response":
            ref = REF_FNS[chk["ref_fn"]](resp_of(_observed_sample_label(spec, chk)), _base_body(spec))
        elif mode == "other_sample":
            ref = resp_of(chk["other_sample"]).get(chk["other_key"])
        elif mode == "cross_endpoint":
            s = next((x for x in _all_samples(spec) if x["label"] == chk["sample"]), None)
            if s is not None:
                r2 = transport.call("POST", chk["cross_path"], s["body"])
                rec2 = _record(r2, {"label": f"cross::{chk['cross_path']}",
                                    "knobs": {}, "body": s["body"]},
                               [chk["cross_extract"]])
                calls.append(rec2)
                ref = rec2["extracted"].get(chk["cross_extract"])
        checks.append({**chk, "observed": observed, "reference": ref, "formula": formula})
    if checks:
        knife_results.append(knives.analytic_crosscheck(checks))
    # ---- 刀 C
    mono_results = []
    for series in spec.get("monotone_series") or []:
        pts = []
        for s in series["points"]:
            x = (s.get("knobs") or {}).get(series["x_key"], s["body"].get(series["x_key"]))
            y = resp_of(s["label"]).get(series["y_key"])
            pts.append({"x": x, "y": y})
        mr = knives.monotonicity(pts, series["direction"])
        mr["series"] = series["id"]
        mr["y_key"] = series["y_key"]
        mono_results.append(mr)
        knife_results.append(mr)

    agg = knives.aggregate_verdict(knife_results)
    # ---- STUB 特征：全部响应空/零
    numeric_vals = [knives.safe_float(v) for rec in calls for v in (rec["extracted"] or {}).values()]
    numeric_vals = [v for v in numeric_vals if v is not None]
    stub_like = (not numeric_vals) or all(abs(v) < 1e-300 for v in numeric_vals)
    statuses = [rec["status"] for rec in calls]
    ok = any(s == 200 for s in statuses)
    return {
        "capability": entry["id"],
        "kind": "numeric",
        "path": path,
        "entrypoint_status": "ok" if ok else ("error" if statuses else "not_attempted"),
        "http_statuses": sorted({str(s) for s in statuses}),
        "calls": calls,
        "knife_results": knife_results,
        "aggregate": agg,
        "stub_like": stub_like,
    }


def _all_samples(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for grp in spec.get("perturbation_groups") or []:
        out.extend(grp["samples"])
        out.extend(grp.get("confirm") or [])
    out.extend(spec.get("extra_samples") or [])
    return out


def _base_body(spec: Dict[str, Any]) -> Dict[str, Any]:
    groups = spec.get("perturbation_groups") or []
    if groups and groups[0]["samples"]:
        return groups[0]["samples"][0]["body"]
    return {}


def _observed_sample_label(spec: Dict[str, Any], chk: Dict[str, Any]) -> str:
    return chk.get("sample") or _base_body_label(spec)


def _base_body_label(spec: Dict[str, Any]) -> str:
    groups = spec.get("perturbation_groups") or []
    if groups and groups[0]["samples"]:
        return groups[0]["samples"][0]["label"]
    return "base"


def probe_non_numeric(entry: Dict[str, Any], transport) -> Dict[str, Any]:
    """查询类 / 未定位端点：只做可达性与完整性取证，不宣布证据等级。"""
    ep = entry["entrypoint"] or {}
    path = ep.get("path")
    method = ep.get("method") or "GET"
    cands = list(ep.get("candidates") or [])
    if path:
        cands = [path] + cands
    tries = []
    for cand in cands:
        res = transport.call(method, cand, None)
        js = res.get("json")
        tries.append({
            "path": cand, "method": method, "status": res.get("status"),
            "error": res.get("error"),
            "response_digest": digest(js) if js is not None else None,
            "top_level_keys": sorted(js.keys())[:15] if isinstance(js, dict) else None,
            "n_items": len(js) if isinstance(js, list) else (len(js) if isinstance(js, dict) else None),
        })
    reachable = [t for t in tries if t["status"] == 200]
    if reachable:
        status = "ok"
    elif tries and all(t["status"] == 404 for t in tries):
        status = "absent"
    elif not tries:
        status = "not_attempted"
    else:
        status = "error"
    return {
        "capability": entry["id"],
        "kind": "non_numeric",
        "entrypoint_status": status,
        "tries": tries,
        "knife_results": [],
        "aggregate": knives.aggregate_verdict([]),
        "reason": entry["callable_spec"].get("reason"),
    }


def probe_ledger(ledger: Dict[str, Any], transport, only: Optional[List[str]] = None) -> Dict[str, Any]:
    traces = []
    for entry in ledger["capabilities"]:
        if only and entry["id"] not in only:
            continue
        spec = entry.get("callable_spec") or {}
        if spec.get("kind") == "http_json":
            traces.append(probe_numeric(entry, transport))
        else:
            traces.append(probe_non_numeric(entry, transport))
    return {
        "transport": getattr(transport, "name", "?"),
        "n_http_calls": getattr(transport, "n_http_calls", None),
        "capabilities": traces,
    }