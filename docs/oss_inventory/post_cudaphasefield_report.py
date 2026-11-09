#!/usr/bin/env python3
"""Post the OSS-inventory progress report to the platform /tensor/reports API.

Reads TENSOR_API_BASE / TENSOR_AGENT_USER / TENSOR_AGENT_PASS from the
environment (never printed), embeds the three PNG figures as base64 and sends
the paper-format payload for mission 8543.
"""
import base64
import json
import os
import sys
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]
HERE = os.path.dirname(os.path.abspath(__file__))


def post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read().decode()[:800]


def get_token():
    for path, body in [
        ("/auth/login", {"username": USER, "password": PASS}),
        ("/api/v1/auth/login", {"username": USER, "password": PASS}),
    ]:
        try:
            st, txt = post(path, body)
        except Exception as exc:  # noqa: BLE001
            print(f"login {path} failed: {exc}")
            continue
        if st == 200:
            d = json.loads(txt if txt.strip().endswith("}") else "{}")
            return d.get("access_token") or d.get("token")
    return None


def fig(name, caption):
    with open(os.path.join(HERE, "figures", name), "rb") as fh:
        return {"name": name, "caption": caption, "png_base64": base64.b64encode(fh.read()).decode()}


def main():
    ev = json.load(open(os.path.join(HERE, "CudaPhaseField_evidence.json"), encoding="utf-8"))
    md = open(os.path.join(HERE, "CudaPhaseField.md"), encoding="utf-8").read()
    c, o, t, tr = ev["constants"], ev["operators"], ev["ternary"], ev["traffic"]
    sp, iso = o["spectral"], o["isotropy"]["k=0.785"]

    payload = {
        "title": "【盘点】CudaPhaseField（终稿：141 指针校验通过，测试面口径已订正）",
        "summary": ev["repo"] + " @ " + (ev["commit"] or "")[:7] +
                   " — GPL-3 三相相场 LBM（守恒 Allen-Cahn + 压力演化流体），公开树无固壁/润湿/多孔、无测试/CI；"
                   "算子核验：9 点拉氏各向异性比 5 点小 49×（5.35e-4 vs 2.62e-2），2 阶；"
                   "GPU 传播流量 768 vs 融合 384 B/node（2× 上界）；物理精度 WITHHELD（未执行）。",
        "scenario_key": "oss-inventory",
        "mission_id": 8543,
        "tags": ["oss-inventory", "lbm", "phase-field", "allen-cahn", "cuda-fortran",
                 "ternary-multiphase", "gpu-kernel", "gpl3-reference-only"],
        "markdown": md,
        "figures": [
            fig("cpf_fig1_init_condition.png",
                "随包初值：两个 r=35 的相切液滴（φ1/φ2/φ3 红绿蓝），右图为 tanh 界面剖面，10–90 宽度 4.39 格（w=4）"),
            fig("cpf_fig2_operator_symbols.png",
                "离散算子符号保真度（脚本复算）：左=45° 对角模上的拉普拉斯符号（9 点 vs 朴素 5 点，"
                "各向异性 5.35e-4 vs 2.62e-2）；右=gradient_cal 符号误差（2 阶，各向异性 1.67e-3）"),
            fig("cpf_fig3_traffic_model.png",
                "传播流量模型（按声明数组推算，非实测）：as-coded 768 B/node/step vs 单 kernel 融合 384 B（2.00× 上界）；"
                "右图设备内存构成：3 个分布 13.5 MiB、24 个传播临时 11.81 MiB、173 个场数组 85.15 MiB"),
        ],
        "metadata": {
            "repo": ev["repo"],
            "commit": ev["commit"],
            "src_sha256": ev["src_sha256"],
            "checks": {
                "laplacian_anisotropy_k_pi_4": iso["lap9_anisotropy"],
                "laplacian_anisotropy_5pt": iso["lap5_anisotropy"],
                "laplacian_order": o["observed_order"]["lap9"],
                "gradient_order": o["observed_order"]["grad"],
                "gradient_anisotropy": iso["grad_anisotropy"],
                "lambda": t["lambda"],
                "bulk_at_pure_max": t["max_abs_bulk_at_pure"],
                "w_c": ev["allen_cahn"]["w_c"],
                "init_sum_dev": ev["init_condition"]["phi_sum_max_dev"],
                "streaming_bytes_as_coded": tr["bytes_per_node_streaming_as_coded"],
                "streaming_bytes_fused": tr["bytes_per_node_streaming_fused"],
                "kernel_launches_per_step": tr["kernel_launches_per_step"],
            },
            "pointer_check": ev.get("pointer_check", {}).get("ok"),
            "pointer_bad": len(ev.get("pointer_check", {}).get("bad", [])),
        },
    }

    token = get_token()
    if not token:
        print("no token; refusing to post")
        return 1
    for path in ("/tensor/reports", "/api/v1/tensor/reports"):
        try:
            st, txt = post(path, payload, token)
            print(path, st, txt[:300])
            if st in (200, 201):
                return 0
        except Exception as exc:  # noqa: BLE001
            print(path, "failed:", exc)
    return 1


if __name__ == "__main__":
    sys.exit(main())