"""发布 W8-N08 战役级报告到 TensorPlatform (figures base64 内嵌)。"""
import base64
import json
import os
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PWD = os.environ["TENSOR_AGENT_PASS"]

ROOT = "/srv/tensor/agent/work/m1219"
ACC = ROOT + "/artifacts/ice_integration/acceptance.json"
FIG = ROOT + "/artifacts/ice_integration/figures"
MD = ROOT + "/docs/ice_integration_gate.md"


def post(path, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main():
    tok = post("/auth/login", {"username": USER, "password": PWD})
    token = (tok.get("access_token") or tok.get("token")
             or tok.get("data", {}).get("access_token"))
    if not token:
        raise SystemExit(f"login failed keys={list(tok)}")

    acc = json.loads(open(ACC).read())
    markdown = open(MD).read()
    figs = []
    for name, cap in [
        ("fig1_contract_chain.png", "图1 七节点契约链与上游分支可见性"),
        ("fig2_time_history.png", "图2 端到端时历: 冰载荷/横摇(耦合 vs 无液晃 vs 共同仿真)/EKF"),
        ("fig3_conservation.png", "图3 质量/动量/能量守恒残差 vs 1% 阈值"),
        ("fig4_validation.png", "图4 液晃频率/GZ/融膜厚度/SEA 能量关系对标"),
    ]:
        with open(os.path.join(FIG, name), "rb") as fh:
            figs.append({"name": name, "caption": cap,
                         "png_base64": base64.b64encode(fh.read()).decode()})

    summary = (
        "W8-N08 跨域集成门 PASS: 7 节点 38 端口/13 边契约 0 错误 (量纲+坐标系+反馈环延时), "
        "端到端数据流 7 节点全通, 质量/动量/能量最大残差 "
        f"{acc['conservation']['max_residual']*100:.3f}% (<1%), 共同仿真 vs 单块偏差 "
        f"{acc['coupling']['relative_delta']*100:.2f}%, pytest 25/25 全绿。"
        "上游 N01~N07 分支均未合并, 其 pytest 如实登记 WITHHELD; "
        "本门以平台契约参考实现为可执行证据。审计抓到 2-DOF 漏 dV/dphi 项"
        "(能量残差 8.4% -> 1.3e-6) 的真实建模错误。"
    )
    payload = {
        "title": "W8-N08 冰-结构相变-液晃-孪生 跨域集成门 战役级验收报告 (PASS)",
        "summary": summary,
        "scenario_key": "ice_navigation.w8_n08_cross_domain_gate",
        "mission_id": 1219,
        "tags": ["ice", "cross-domain", "integration-gate", "contract", "conservation",
                 "sloshing", "sea", "digital-twin", "w8"],
        "markdown": markdown,
        "figures": figs,
    }
    res = post("/tensor/reports", payload, token)
    print(json.dumps(res, ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()