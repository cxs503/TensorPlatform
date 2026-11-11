"""在线运行期证据采集：OpenAPI 面清单 + 声明式探针 + 判据求值。

为什么需要这一层
================
静态审计（``tools.capability_audit_gate``）只看源码与工件，**不执行**任何东西；
台账里的人审结论则来自文档引用。两者都回答不了"此刻这套服务真的这么算吗"。
本模块把"在线探针"变成可复算的证据：每个探针的请求、响应、判据测量值、
响应摘要全部落到工件里，重跑即可复现。

零领域语义
==========
端点、请求体、判据、阈值全部来自 spec（配置）。本模块只有 HTTP 与算术。

降级纪律
========
* 服务不可达 / 探针异常 → 判据 ``NOT_EXECUTED``，**不算通过**，等级回落 WITHHELD；
* HTTP 4xx/5xx 依 spec 判据裁定（spec 可以显式期望 422，如"几何不自洽必须被拒"）。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any

from .expr import ExprError, evaluate, rel_error

PASS = "PASS"
FAIL = "FAIL"
NOT_EXECUTED = "NOT_EXECUTED"


def _sha(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def fetch_openapi(base_url: str, openapi_path: str = "/openapi.json", timeout_s: float = 20.0) -> dict:
    """取在线 OpenAPI（能力"面"的唯一机器可读真值）。"""
    url = base_url.rstrip("/") + openapi_path
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 - 内网固定候选地址
        raw = resp.read()
    doc = json.loads(raw)
    return {
        "url": url,
        "status": 200,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "paths": sorted((doc.get("paths") or {}).keys()),
        "title": (doc.get("info") or {}).get("title"),
        "version": (doc.get("info") or {}).get("version"),
    }


def surface_check(patterns: list[str], online_paths: list[str]) -> dict:
    """能力面清单核验：声明的端点模式在在线 OpenAPI 里是否存在。"""
    present, absent = [], []
    for pat in patterns:
        hit = [p for p in online_paths if p == pat or p.startswith(pat)]
        (present if hit else absent).append({"pattern": pat, "matches": hit[:4]})
    return {
        "declared": len(patterns),
        "present": present,
        "absent": absent,
        "status": PASS if patterns and not absent else (NOT_EXECUTED if not patterns else FAIL),
    }


def _request(base_url: str, method: str, path: str, body: Any, timeout_s: float) -> dict:
    url = base_url.rstrip("/") + path
    data = None
    if method != "GET" and body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 内网固定候选地址
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except Exception as exc:  # noqa: BLE001 - 采集层必须把任何失败变成"未执行"证据
        return {
            "url": url, "method": method, "path": path, "body": body,
            "status": None, "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "response": None, "response_sha256": None,
        }
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001 - 非 JSON 响应照样留证
        payload = {"_raw": raw[:2000].decode("utf-8", "replace")}
    return {
        "url": url, "method": method, "path": path, "body": body,
        "status": status, "error": None,
        "elapsed_ms": round((time.time() - started) * 1000, 1),
        "response": payload, "response_sha256": _sha(payload),
    }


def _dotted(payload: Any, dotted: str) -> Any:
    if not dotted:
        return payload
    cur = payload
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            return None
    return cur


def _numeric_series(samples: list[dict], path: str) -> list[float]:
    out = []
    for sample in samples:
        val = _dotted(sample.get("response"), path)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out.append(float(val))
    return out


def evaluate_criteria(criteria: list[dict], samples: list[dict], sweep_values: list[Any] | None) -> list[dict]:
    """对一次探针的全部判据求值，返回可入判据表的行。"""
    rows: list[dict] = []
    for crit in criteria:
        kind = crit["kind"]
        row: dict[str, Any] = {
            "id": crit["id"],
            "kind": kind,
            "class": crit.get("class", "contract"),
            "path": crit.get("path"),
            "verdict": NOT_EXECUTED,
            "measured": None,
            "threshold": None,
            "detail": "",
        }
        executed = [s for s in samples if s.get("status") is not None]
        if not executed:
            row["detail"] = "probe not executed (service unreachable)"
            rows.append(row)
            continue

        if kind == "status":
            codes = [s["status"] for s in samples]
            row["measured"] = codes
            row["threshold"] = crit["expect"]
            ok = all(c in crit["expect"] for c in codes)
            row["verdict"] = PASS if ok else FAIL
            row["detail"] = f"status codes {codes} vs expected {crit['expect']}"

        elif kind == "field_present":
            sample = executed[0]["response"]
            val = _dotted(sample, crit["path"])
            row["measured"] = "present" if val is not None else "missing"
            row["threshold"] = "present"
            row["verdict"] = PASS if val is not None else FAIL

        elif kind == "field_range":
            series = _numeric_series(executed, crit["path"])
            lo, hi = crit.get("min"), crit.get("max")
            if not series:
                row["detail"] = "field not numeric / missing"
                row["verdict"] = FAIL
            else:
                worst = None
                for val in series:
                    bad = (lo is not None and val < lo) or (hi is not None and val > hi)
                    if bad:
                        worst = val
                        break
                row["measured"] = series[0] if len(series) == 1 else series
                row["threshold"] = {"min": lo, "max": hi}
                row["verdict"] = FAIL if worst is not None else PASS

        elif kind == "list_min_len":
            sample = executed[0]["response"]
            val = _dotted(sample, crit["path"])
            n = len(val) if isinstance(val, list) else 0
            row["measured"] = n
            row["threshold"] = crit["min_len"]
            row["verdict"] = PASS if n >= int(crit["min_len"]) else FAIL

        elif kind == "identity":
            ok_all, worst_rel, worst_detail = True, 0.0, ""
            for sample in executed:
                try:
                    lhs, sym_l = evaluate(crit["lhs"], sample["response"])
                    rhs, sym_r = evaluate(crit["rhs"], sample["response"])
                except ExprError as exc:
                    row["verdict"] = NOT_EXECUTED
                    row["detail"] = f"expression error: {exc}"
                    ok_all = None
                    break
                mode = crit.get("mode", "abs_rel")
                tol = float(crit.get("tolerance", 0.0))
                if mode == "lte":
                    rel = rel_error(max(lhs, rhs), rhs) if lhs > rhs else 0.0
                    ok = lhs <= rhs * (1.0 + tol) + abs(tol)
                elif mode == "gte":
                    rel = rel_error(min(lhs, rhs), rhs) if lhs < rhs else 0.0
                    ok = lhs >= rhs * (1.0 - tol) - abs(tol)
                else:
                    rel = rel_error(lhs, rhs)
                    ok = (rel <= tol) if mode == "abs_rel" else (abs(lhs - rhs) <= tol)
                if not ok:
                    ok_all = False
                if rel >= worst_rel:
                    worst_rel = rel
                    worst_detail = (f"lhs={lhs:.6g} rhs={rhs:.6g} mode={mode} "
                                    f"dev={rel:.3e} symbols={sorted(set(sym_l) | set(sym_r))}")
            if ok_all is not None:
                row["measured"] = worst_rel
                row["threshold"] = {"mode": crit.get("mode", "abs_rel"), "tolerance": crit.get("tolerance", 0.0)}
                row["verdict"] = PASS if ok_all else FAIL
                row["detail"] = worst_detail

        elif kind == "monotonic":
            series = _numeric_series(executed, crit["path"])
            direction = crit.get("direction", "increasing")
            row["measured"] = series
            row["threshold"] = direction
            if len(series) < 2:
                row["verdict"] = FAIL
                row["detail"] = "fewer than 2 samples"
            else:
                pairs = list(zip(series, series[1:]))
                ok = all((b > a) if direction == "increasing" else (b < a) for a, b in pairs)
                row["verdict"] = PASS if ok else FAIL
                row["detail"] = f"series={series} sweep={sweep_values}"

        elif kind == "non_constant":
            series = _numeric_series(executed, crit["path"])
            row["measured"] = series
            min_rel = float(crit.get("min_rel_spread", 1e-3))
            row["threshold"] = {"min_rel_spread": min_rel}
            if len(series) < 2:
                row["verdict"] = FAIL
                row["detail"] = "fewer than 2 samples"
            else:
                spread = (max(series) - min(series)) / max(abs(min(series)), 1e-12)
                row["verdict"] = PASS if spread >= min_rel else FAIL
                row["detail"] = f"relative spread={spread:.3e} over {len(series)} samples"

        rows.append(row)
    return rows


def run_probe(base_url: str, probe: dict, timeout_s: float = 20.0) -> dict:
    """执行一条探针（含可选参数扫描），返回样本序列 + 判据行。"""
    sweep = probe.get("sweep")
    body = probe.get("body")
    sweep_values: list[Any] | None = None
    samples: list[dict] = []
    if sweep:
        sweep_values = list(sweep.get("values") or [])
        for value in sweep_values:
            resolved = json.loads(json.dumps(body) if body is not None else "{}")
            resolved[sweep["field"]] = value
            samples.append(_request(base_url, probe.get("method", "GET"), probe["path"], resolved, timeout_s))
    else:
        samples.append(_request(base_url, probe.get("method", "GET"), probe["path"], body, timeout_s))
    criteria_rows = evaluate_criteria(probe.get("criteria") or [], samples, sweep_values)
    return {
        "id": probe["id"],
        "method": probe.get("method", "GET"),
        "path": probe["path"],
        "sweep": {"field": sweep["field"], "values": sweep_values} if sweep else None,
        "samples": samples,
        "criteria": criteria_rows,
        "executed": any(s.get("status") is not None for s in samples),
        "digest": _sha({"path": probe["path"], "samples": [s.get("response_sha256") for s in samples],
                        "criteria": [(r["id"], r["verdict"], r["measured"]) for r in criteria_rows]}),
    }