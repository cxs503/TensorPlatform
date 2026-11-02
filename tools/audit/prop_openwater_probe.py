#!/usr/bin/env python3
"""推进域敞水性能探针（只读）：对 :8001 的 Wageningen B 系列端点做参数网格采样。

用途
----
把「敞水性能端点是否物理可信」变成可机器重跑的证据。探针只做 HTTP GET/POST
读取，不写远端状态。

用法
----
    python3 tools/audit/prop_openwater_probe.py \
        --base http://127.0.0.1:8001/api/v1 \
        --out artifacts/audit/prop_openwater_probe.json

输出 JSON 结构
--------------
{
  "base": ..., "endpoint": "propeller-design/wageningen-b", "captured_at": ...,
  "grid": {"Z": [...], "P_D": [...], "Ae_Ao": [...], "J": [...]},
  "samples": [{"Z":4,"P_D":1.0,"Ae_Ao":0.7,"J":0.6,"KT":...,"KQ":...,"eta0":...,
               "method":...,"http_status":200}]
}
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "propeller-design/wageningen-b"


def call(base: str, path: str, body: dict, timeout: float = 20.0) -> tuple:
    """POST 一次；返回 (http_status, payload)。异常不抛出，转成 (status, {"error":...})。"""
    req = urllib.request.Request(
        f"{base.rstrip('/')}/{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要留证
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # pragma: no cover - 非 JSON 错误体
            payload = {"error": str(exc)}
        return exc.code, payload
    except Exception as exc:  # pragma: no cover - 连接失败
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def frange(start: float, stop: float, step: float) -> list:
    out, k = [], 0
    while True:
        v = start + k * step
        if v > stop + 1e-9:
            break
        out.append(round(v, 6))
        k += 1
    return out


def build_grid(args) -> dict:
    return {
        "Z": list(args.blades),
        "P_D": list(args.pitch),
        "Ae_Ao": list(args.area),
        "J": frange(args.j_min, args.j_max, args.j_step),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("TENSOR_SHIP_API", "http://127.0.0.1:8001/api/v1"))
    ap.add_argument("--out", default="artifacts/audit/prop_openwater_probe.json")
    ap.add_argument("--blades", type=int, nargs="+", default=[4, 5])
    ap.add_argument("--pitch", type=float, nargs="+", default=[0.6, 0.8, 1.0, 1.2, 1.4])
    ap.add_argument("--area", type=float, nargs="+", default=[0.40, 0.70, 1.00])
    ap.add_argument("--j-min", type=float, default=0.0)
    ap.add_argument("--j-max", type=float, default=1.4)
    ap.add_argument("--j-step", type=float, default=0.05)
    args = ap.parse_args()

    grid = build_grid(args)
    samples = []
    for z in grid["Z"]:
        for pd in grid["P_D"]:
            for ae in grid["Ae_Ao"]:
                for j in grid["J"]:
                    body = {"series": f"B{z}-70", "P_D": pd, "Z": z, "Ae_Ao": ae, "J": j}
                    status, payload = call(args.base, ENDPOINT, body)
                    rec = {"Z": z, "P_D": pd, "Ae_Ao": ae, "J": j, "http_status": status}
                    if isinstance(payload, dict):
                        for key in ("KT", "KQ", "eta0", "method"):
                            if key in payload:
                                rec[key] = payload[key]
                        if "error" in payload:
                            rec["error"] = payload["error"]
                    samples.append(rec)

    doc = {
        "base": args.base,
        "endpoint": ENDPOINT,
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "grid": grid,
        "n_samples": len(samples),
        "samples": samples,
    }
    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    ok = sum(1 for s in samples if s.get("http_status") == 200)
    print(f"[probe] {ok}/{len(samples)} HTTP 200 -> {out}")
    return 0 if ok == len(samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())