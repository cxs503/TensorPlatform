#!/usr/bin/env python3
"""Post the waLBerla OSS-inventory report to the platform (tensor_reports).

Reads docs/oss_inventory/REPORT_walberla.md and the three measured figures, embeds
figures as base64 and POSTs to $TENSOR_API_BASE/tensor/reports with
scenario_key=oss-inventory, mission_id=8551.
"""
import base64
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
HERE = os.path.dirname(os.path.abspath(__file__))


def post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def fig(name, caption):
    with open(os.path.join(HERE, name), "rb") as f:
        return {"name": name, "caption": caption,
                "png_base64": base64.b64encode(f.read()).decode()}


def main():
    tok = post("/auth/login", {"username": USER, "password": PASS})
    tok = tok.get("access_token") or tok.get("data", {}).get("access_token")
    md = open(os.path.join(HERE, "REPORT_walberla.md"), encoding="utf-8").read()

    payload = {
        "title": "【盘点】walberla（lssfau/walberla）—— 块森林多物理 HPC 框架：碰撞/受力谱系齐全但缺 KBC/正则化/Zou-He，"
                 "多相只有相场 Allen-Cahn（rho 比至 1000）与 C++ 手写自由面，SCMP/MCMP/颜色梯度/自由能全 0 命中（GPL-3.0 只可参考重写）",
        "summary": (
            "waLBerla（HEAD dc16f31，2026-08-04；clone 与 trees API 同 sha 校验）代码级深盘：src 1103 文件 322410 行，"
            "LBM 子集 54126 行、自由面 15063 行。碰撞 SRT/TRT/MRT(D3Q19)/Cumulant(D3Q27)，力项 EDM/Luo/Guo/Latt，"
            "边界反弹/曲边 BB/UBB/压力/PAB + IBM 三套，缺 KBC/正则化/Zou-He。多相仅相场 Allen-Cahn（lbmpy codegen，"
            "rho 比实跑 1000/744）与 C++ 手写自由面；SCMP/MCMP/自由能/颜色梯度/CH grep 全 0。GPL-3.0 只可参考重写。"
            "出 13 条 OSS-SOL，143 条 file:line 指针脚本回读 0 失败。"
        ),
        "scenario_key": "oss-inventory",
        "mission_id": 8551,
        "tags": ["oss-inventory", "walberla", "LBM", "free-surface", "phase-field", "Allen-Cahn",
                 "cumulant", "Guo-forcing", "block-forest", "GPL-3.0"],
        "markdown": md,
        "figures": [
            fig("walberla_fig1_modules.png",
                "图1 src/ 模块规模分布（LOC，程序化采集自 make_walberla_stats.py）：core 111k / lbm 54k / "
                "lbm_generated 21k / free_surface 15k。LBM 相关模块标红——LBM 是这个框架里的一个大模块，不是它的全部"),
            fig("walberla_fig2_multiphase_matrix.png",
                "图2 多相能力覆盖矩阵：8 个目标族 × 3 条实现通道。绿色=有实现锚点（相场 AC 与热毛细仅 lbmpy codegen；"
                "自由面仅 C++ 手写），灰色=全仓 grep 0 命中（SCMP/MCMP/自由能/颜色梯度/CH）。数据源 walberla_stats.json"),
            fig("walberla_fig3_freesurface_pipeline.png",
                "图3 自由面时间步 8 段流水（行号由脚本从 SurfaceDynamicsHandler.h 的 38 个 sweep 注册点归并而来）："
                "红色三段 = 质量守恒关键段（cell conversion → PDF refilling → excess-mass redistribution）"),
        ],
    }
    r = post("/tensor/reports", payload, tok)
    rid = r.get("id") or r.get("data", {}).get("id") or r
    print("posted report id:", rid)
    with open("/tmp/walberla_report_post.log", "w") as f:
        f.write(json.dumps({"resp_id": str(rid)}, ensure_ascii=False))


if __name__ == "__main__":
    main()