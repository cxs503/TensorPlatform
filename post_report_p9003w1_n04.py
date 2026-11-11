#!/usr/bin/env python3
"""Publish the p9003w1_n04 report to the platform (figures embedded as base64).

Numbers are read from ``artifacts/bench_norm/`` (criteria.json / real_analysis.json);
the markdown body is the rendered ``docs/reports/P9003W1_N04_cross_arch_normalization.md``.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
ART = REPO / "artifacts/bench_norm"
MD = REPO / "docs/reports/P9003W1_N04_cross_arch_normalization.md"
MISSION_ID = 13840


def _env() -> dict:
    env = {}
    for line in Path("/root/tp-agent/agent.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    env.setdefault("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    return env


def _post(base: str, path: str, payload: dict, token: str | None = None, timeout: int = 180) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode()[:600]}


def main() -> int:
    env = _env()
    base = env["TENSOR_API_BASE"].rstrip("/")
    tok = _post(base, "/auth/login", {"username": env["TENSOR_AGENT_USER"],
                                      "password": env["TENSOR_AGENT_PASS"]})
    token = tok.get("access_token") or tok.get("token")

    crit = json.loads((ART / "criteria.json").read_text())
    real = json.loads((ART / "real_analysis.json").read_text())
    inj = json.loads(sorted((ART / "injection").glob("*.json"))[0].read_text())
    arm = [a for a in real["arms"]][0]
    diff = real["diffs"][arm]

    summary = (
        f"注入层判据 (a)-(e) 全 PASS（α p95 误差 {100*inj['alpha_rel_err']['p95_abs']:.2f}%、"
        f"R² {inj['r2_log']['mean']:.4f}、覆盖率 {100*inj['coverage']['rate']:.0f}%、"
        f"负控 {inj['diff_raw_pct']['min_abs']:.0f}%）；实测同主机 -O2/-O0 raw 差异 "
        f"{diff['raw']['difference_pct']:.1f}%，α(x) 归一化后留出残差 "
        f"{diff['normalised']['difference_pct']:.2f}%，常数标量 α 为 "
        f"{diff['normalised_scalar']['difference_pct']:.2f}%（如实记 FAIL）。"
        f"真实 loong64-vs-x86 臂因无 x86 主机 WITHHELD。"
    )

    figs = []
    captions = {
        "fig_alpha_bars.png": "α_arch 柱状图（注入臂已知真值 + 实测臂，95% CI 误差棒）",
        "fig_fit_scatter.png": "线性混合效应模型拟合散点（观测 log T vs 拟合值，1:1 线）",
        "fig_before_after.png": "归一化前后性能差异箱线图（±50% 负控带 / ±5% 判据带）",
    }
    for name, cap in captions.items():
        p = ART / "figs" / name
        if p.exists():
            figs.append({"name": name, "caption": cap,
                         "png_base64": base64.b64encode(p.read_bytes()).decode()})
    for p in sorted((ART / "figs").glob("fig_alpha_by_workload_*.png")):
        figs.append({"name": p.name,
                     "caption": "逐 workload 的 T_arm/T_ref：检验 α 是否为常数",
                     "png_base64": base64.b64encode(p.read_bytes()).decode()})

    payload = {
        "title": "p9003w1_n04 跨架构基准归一化：α 因子 + 线性混合效应模型 + 置信区间",
        "summary": summary,
        "scenario_key": "cross_arch_benchmark_normalisation",
        "mission_id": MISSION_ID,
        "tags": ["cross-arch", "benchmark", "normalisation", "mixed-effects", "loongarch64",
                 "lbm", "honest-withheld"],
        "markdown": MD.read_text(encoding="utf-8"),
        "figures": figs,
    }
    res = _post(base, "/tensor/reports", payload, token=token)
    if res.get("status") == 422 and len(figs) > 3:
        print("[post] 422 with all figures, retrying with the three required figures")
        payload["figures"] = [f for f in figs
                              if f["name"] in ("fig_alpha_bars.png", "fig_fit_scatter.png",
                                               "fig_before_after.png")]
        res = _post(base, "/tensor/reports", payload, token=token)
    if res.get("status") == 422:
        print("[post] 422 body:", res.get("body"))
        payload["figures"] = payload["figures"][:1]
        res = _post(base, "/tensor/reports", payload, token=token)
    print(json.dumps({"id": res.get("id"), "title": res.get("title"),
                      "status": res.get("status"), "n_figures": len(figs),
                      "summary": summary}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())