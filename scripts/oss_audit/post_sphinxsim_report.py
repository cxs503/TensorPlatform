#!/usr/bin/env python3
"""Post the SPHinXsim wave-2 deep-dive report (mission 8665) to the tensor platform.

Figures are embedded as base64 PNG read from disk (no hand-copied numbers in the body:
the markdown itself is the artifact produced by scripts/oss_audit/sphinxsim_inventory.py).
"""
import base64
import json
import os
import sys
import urllib.request

API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MD = "docs/oss_inventory_sph/REPORT_oss-sph-w2-sphinxsim.md"
FIGS = [
    ("fig1_sphinxsim_method_matrix.png",
     "图1 SPHinXsim 方法族源码级支持矩阵（绿=REAL 实现/黄=部分/红=NOT_SUPPORTED，数字为 file:line 证据条数）："
     "WCSPH+Riemann+密度正则化+多相+TLSPH/FSI+Eulerian/FVM 全绿；ISPH/PCISPH/DFSPH/IISPH、δ-SPH、PBD 全红（0 命中）。",
     "docs/oss_inventory_sph/figures/fig1_sphinxsim_method_matrix.png"),
    ("fig2_sphinxsim_scale_validation.png",
     "图2 左：代码构成 84847 行 vendored Apache-2.0 上游核心 vs 6783 行 C++ 胶水 vs 12966 行 Python；"
     "右：验证资产 70×2D + 44×3D 算例、857 回归台文件(/10)、15 Python 测试、0 数值容差断言。",
     "docs/oss_inventory_sph/figures/fig2_sphinxsim_scale_validation.png"),
]


def main() -> int:
    data = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=data,
                                headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]

    with open(os.path.join(ROOT, MD), encoding="utf-8") as fh:
        markdown = fh.read()
    figures = []
    for name, caption, rel in FIGS:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            figures.append({"name": name, "caption": caption,
                            "png_base64": base64.b64encode(fh.read()).decode()})

    report = {
        "title": "【OSS-SPH 深盘 w2】Xiangyu-Hu/SPHinXsim 源码级盘点：MIT 外壳 + Apache-2.0 全量 SPHinXsys 内核",
        "summary": ("SPHinXsim 实为 SPHinXsys 的 agent-ready 外围平台（MIT 胶水 12966 行）并整棵 vendor 上游 "
                    "Apache-2.0 核心（608 文件/84847 行）：WCSPH+Riemann 耗散+密度正则化+多相+TLSPH/FSI+Eulerian/FVM "
                    "全部 REAL；ISPH/PCISPH/DFSPH/IISPH、δ-SPH、PBD 零命中（NOT_SUPPORTED）；并行仅 TBB，SYCL 默认关且 "
                    "wheel 强制 OFF，无 MPI；验证资产 70×2D+44×3D 算例 + 857 回归台文件但上游算例库构建被关闭、数值容差断言 0。"
                    "更正 m8638「简化/教学版」初判。产出 6 条可迁移方案（GAP-SPH-WCSPH-VALIDATION / DELTA-SPH / "
                    "MULTIPHASE / GPU-SCALING / FSI-BENCHMARK / DIFFERENTIABLE），license 合规逐项判定。"),
        "scenario_key": "osssph-w2",
        "mission_id": 8665,
        "tags": ["oss-inventory", "sph", "sphinxsim", "wcsph", "riemann", "multiphase", "license-audit",
                 "gAP-SPH", "wave-2"],
        "markdown": markdown,
        "figures": figures,
    }
    body = json.dumps(report).encode()
    req2 = urllib.request.Request(f"{API}/tensor/reports", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    try:
        out = json.loads(urllib.request.urlopen(req2, timeout=60).read())
        print("Report posted:", json.dumps(out)[:400])
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Post failed: {exc}", file=sys.stderr)
        if hasattr(exc, "read"):
            print(exc.read().decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())