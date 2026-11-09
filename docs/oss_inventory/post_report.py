#!/usr/bin/env python3
"""Post the lbmpy OSS-inventory report to the platform (tensor_reports).

Reads docs/oss_inventory/REPORT_lbmpy.md and the three measured figures, embeds
figures as base64 and POSTs to $TENSOR_API_BASE/tensor/reports with
scenario_key=oss-inventory, mission_id=8291.
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
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def fig(name, caption):
    with open(os.path.join(HERE, name), "rb") as f:
        return {"name": name, "caption": caption,
                "png_base64": base64.b64encode(f.read()).decode()}


def main():
    tok = post("/auth/login", {"username": USER, "password": PASS})
    tok = tok.get("access_token") or tok.get("data", {}).get("access_token")
    md = open(os.path.join(HERE, "REPORT_lbmpy.md"), encoding="utf-8").read()

    payload = {
        "title": "【盘点】lbmpy —— SymPy 符号式 LBM 前端：方法谱系最全、多相只有 Shan-Chen 教程与相场研究码、自由面/spurious-current 全空白（GPL-3.0 只可参考重写）",
        "summary": (
            "对 lssfau/lbmpy（HEAD 0afc9d5，2026-07-22）做代码级深盘：13 个碰撞工厂 / 9 个力项 / 12 个边界 / 5 个格子 + "
            "相场 CH/AC 与自动 Chapman-Enskog；多相 = SCMP/MCMP Shan-Chen（指数势，密度比~14，配 64 点逐点回归）"
            "+ 相场（EOS+Maxwell 高密度比，声明到 1000）；自由面与 spurious-current 抑制经全仓 grep 核实为 0 命中；"
            "许可证 GPL-3.0-or-later 强 copyleft，对我们只可参考重写不可复制；本环境与集群均无 pystencils（不在 PyPI）"
            "故全部结论为静态源码证据，190 个 file:line 指针经脚本全量校验 0 失败。"
        ),
        "scenario_key": "oss-inventory",
        "mission_id": 8291,
        "tags": ["oss-inventory", "lbmpy", "LBM", "Shan-Chen", "MCMP", "phase-field", "code-generation", "GPL-3.0"],
        "markdown": md,
        "figures": [
            fig("fig1_surface.png",
                "图1 lbmpy 方法面计数（程序化采集自 src/lbmpy）：格子 6 个类(5 具体)、碰撞工厂 13、力项 10 个类(9 具体)、"
                "边界条件 12 个具体类。数据源 docs/oss_inventory/lbmpy_stats.json"),
            fig("fig2_modules.png",
                "图2 模块 LOC 分布（wc -l 实测，src/lbmpy 105 文件 23365 行）：边界与矩变换、LBMConfig 工厂、"
                "Chapman-Enskog 为三大重心，多相相关模块规模明显偏小（研究代码量级）"),
            fig("fig3_multiphase.png",
                "图3 多相/自由面能力覆盖矩阵：绿=有代码锚点，红=全仓 grep 0 命中（已核实缺失）。"
                "自由面与 spurious-current 抑制为 0 —— 这两项是真空区，不是可抄作业"),
        ],
    }
    r = post("/tensor/reports", payload, tok)
    rid = r.get("id") or r.get("data", {}).get("id") or r
    print("posted report id:", rid)
    with open("/tmp/lbmpy_report_post.log", "w") as f:
        json.dump({"resp": r}, f, ensure_ascii=False, indent=1)[:0] if False else None
        f.write(json.dumps({"resp_id": str(rid)}, ensure_ascii=False))


if __name__ == "__main__":
    main()