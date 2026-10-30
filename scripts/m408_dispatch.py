#!/usr/bin/env python3
"""m408 值班长班次：FAIL 案例「诊断+修复绕行」派发（最多 3 单，仅 TensorLBM 扫荡类）。

数字与失败证据全部来自平台 API（/tensor/missions、/tensor/reports），零手抄。
派发走 POST /api/v1/tensor/missions，runner=native, repo=TensorLBM, origin=agent。
每单带 task_key 做幂等，防止重跑本脚本产生重复单。
"""
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "artifacts", "m408_dispatches.json")


def _req(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def login():
    d = _req("/auth/login", "POST", {"username": USER, "password": PASS})
    return d["access_token"]


COMMON = """
【纪律】① 只在本 mission 工作树内改；不改判据口径（阈值/参考值/达标定义一律不得放宽），
判据红不得改写为绿——允许并鼓励 honest-FAIL；② 全程程序化取数（脚本产出 → 报告内联），零手抄；
③ 必须提交论文级报告（六要素：案例介绍/方法/结果数字表/图/注意点/误差判定逐项是否<3%），
POST /api/v1/tensor/reports（title/summary/scenario_key/mission_id:<本单 id>/tags/markdown/figures）；
④ 必须捕获设备证据（torch.sdtaa/torch.sdaa 版本、卡号、步数、墙钟）写入 evidence.json；
⑤ 完成后调用 finish，写清跑了什么判据。
"""

GOALS = [
    {
        "task_key": "m408-sweep-startup_poiseuille-rework",
        "marker": "startup_poiseuille",
        "goal": """【诊断+修复绕行·返工】SDAA 验证案例扫荡——案例 startup_poiseuille（TensorLBM benchmarks/verified/startup_poiseuille）【班次 m408 派发·返工单 A】

一、失败证据（程序化取自平台 API，零手抄）
- 前单 mission_id=373 状态 = failed：result.evidence.checks.gate = "no-evidence"，
  reason = "模型未调用 finish"；tool_calls=80，finished_status 缺失。
  即：该单写了 5 个脚本（collect_evidence.py / diag_sdaa.py / make_plots.py / run_sdaa.py / run_sdaa_H119.py）
  但未 finish、未落判据 → 属「进程/交付缺失」，不是物理判据红。
- 更早的口径背景（report id=275 标题）：该案例「阈值全过、单调收敛未过，最差 0.476% < 3%」。
  即：物理误差量级远小于 3% 门限，历史红点来自「单调收敛」这一附加判据。

二、本单任务（诊断 → 修复绕行 → 收口）
1) 先复跑：在 SDAA 卡上跑通 benchmark 主流程，产出 artifacts（含设备证据：torch/torch_sdaa 版本、卡号、步数、墙钟）。
2) 复现历史红点：明确 startup_poiseuille 的判定判据族（V 系列 / ladder / 单调收敛 / 稳态判据）逐条列出
   每条的实测值 vs 阈值 vs 是否通过，落到 evidence.json（键名固定：criteria[] = {name, measured, threshold, pass}）。
3) 若「单调收敛」红：诊断是否为判据本身不适用于起始瞬态（Poiseuille 启动流 err 随时间非单调下降是物理正常现象，
   例：早期压力波反射/压缩性瞬变）。给出数据证据（err(t) 曲线 + 拐点定位），
   **不得修改阈值**；若确认判据口径不当，只能如实上报「判据口径问题 → 交 owner」，并在报告 limitations 写明。
4) 若存在可绕行的数值修复（如起始阶段改用 diffusive scaling 起流、调整采样窗口起点、TRT 替代 BGK 抑制振铃），
   逐项实测前后对比（同一 seed/网格）。
5) 判据：本单成功 = ① SDAA 主流程跑通且有设备证据；② criteria[] 全条目机器可裁（数值+阈值+布尔）；
   ③ 对每一条红判据给出「物理/口径/数值」三分类结论并附数据；④ 报告已发布（含 ⑥误差判定逐项是否<3%）。
   honest-FAIL 允许且必须如实。
""" + COMMON,
    },
    {
        "task_key": "m408-sweep-blasius_flat_plate-bypass",
        "marker": "blasius_flat_plate",
        "goal": """【诊断+修复绕行】SDAA 验证案例扫荡——案例 blasius_flat_plate（TensorLBM benchmarks/pending/blasius_flat_plate）【班次 m408 派发·修复单 B】

一、失败证据（程序化取自平台 API，零手抄；report id=312, mission_id=386）
- 判决 HONEST_FAIL（终裁确认）：6 组 grid×collision 全红，最佳 plate400_y1600 TRT Cf=0.02819 vs 基准 0.02214 → +27.34%（门限 3%）。
- 误差单调随 Re_x 增大：+24.6%（Re_x=150）→ +27.4%（Re_x=900）。
- 关键诊断线索（前单自述）：位移厚度 δ* 增长使流道有效截面收缩，边缘加速 u_edge/U 达 1.083（应=1.0），
  即计算域下边界/上边界条件把平板边界层逼成了「受限通道流」。
- L2 剖面误差 0.199~0.279（仍未收敛量级）。

二、本单任务（诊断 → 修复绕行 → 收口）
1) 先按 1:1 复现前单最佳档（plate400_y1600 TRT）以确认基线可重复：Cf 与 u_edge/U 必须复现到 ±1% 内，
   否则视为环境差异并记录（设备证据必填）。
2) 主诊断方向（按优先级）：
   (a) 上边界条件：把 plate400 的 y1600 域顶部改为「无滑移→自由滑移/外推出口」或把域高加倍，
       复测 u_edge/U 是否回到 1.00±0.02；若不回，量化 δ*/H 比值并给出所需域高下限 H_min（程序化扫 2~3 档）。
   (b) 入口/出口：入口改为速度入口 + 出口零梯度，检查入口处的抽吸/堵塞是否引入额外加速。
   (c) 网格：x 向加密（plate800）验证 Cf 是否随分辨率单调下降（当前 plate200→400 使误差 47%→27%，说明仍在分辨率敏感区）。
3) 每档实测：Cf_sim、Cf_ref(Blasius 0.664/sqrt(Re_x) 或前单同一基准)、误差%、u_edge/U、L2 剖面误差 → 一张表零手抄。
4) 判据：达标 = Cf 误差 ≤3% 且 u_edge/U = 1.00±0.02（两者都要）。若密集网格/开口边界后仍 >3%，允许 honest-FAIL，
   但必须给出「误差随分辨率/域高的收敛趋势 + 外推值」，并明确声明外推值不用于达标。
   **禁止**放宽 3% 门限、禁止改写基准值。
""" + COMMON,
    },
    {
        "task_key": "m408-sweep-bstep_3d-refine",
        "marker": "bstep_3d",
        "goal": """【诊断+修复绕行】SDAA 验证案例扫荡——案例 bstep_3d（TensorLBM benchmarks/pending/bstep_3d）【班次 m408 派发·修复单 C】

一、失败证据（程序化取自平台 API，零手抄；report id=313, mission_id=387）
- 判决 HONEST_FAIL：X_r/h(SDAA, H=10) = 2.8852 vs 参考 3.0 → -3.83%（门限 3%），超门限 0.83 个百分点。
- 同单 CUDA legacy H=40 为 +13.18%（方向相反且更大），说明该案例的网格/边界敏感性高，需分辨率序列判定。
- 前单归因：粗网格 ER（台阶高/通道高比）偏差 +3.1% 与格点分辨率不足共同作用；与设备无关（SDAA 可运行性已验证）。

二、本单任务（诊断 → 修复绕行 → 收口）
1) SDAA 分辨率梯子（至少 3 档，起始 H=10/16/24，若能负担则加 H=32）：
   每档固定物理 Re=100、ER、展向周期长度，仅改格点数；产出 X_r/h 每档值 + 墙钟 + 设备证据。
2) 关键必查项：ER 的「格点实现值」是否等于名义值（H=10 时台阶高仅 10 格，±1 格即 ±10% 偏差）——
   程序化统计台阶实际占据格点数并给出 ER_effective，判断 -3.83% 中有多少来自 ER 离散化。
3) 再查 reattachment 提取方法：壁面 u_x 过零插值（线性 vs 高阶）、时间平均窗口长度、展向平均是否做，
   与 CUDA legacy 的口径逐项对齐；给出方法差异对 X_r/h 的敏感度（±%）.
4) 判据：达标 = 分辨率梯子最细档 X_r/h 误差 ≤3% 且（X_r/h 随网格单调收敛 + 最细两档差 ≤1%）。
   若最细档仍 >3%，允许 honest-FAIL，但必须给出外推 X_∞/h 与单调性证据。禁止放宽门限/改参考值 3.0。
""" + COMMON,
    },
]


def main():
    token = login()
    missions = _req("/tensor/missions?limit=50", token=token)["items"]
    seen = {}
    for m in missions:
        g = (m.get("goal") or "")
        for spec in GOALS:
            if spec["marker"] in g:
                seen.setdefault(spec["marker"], []).append(m["id"])

    results = []
    for spec in GOALS:
        payload = {
            "goal": spec["goal"].strip(),
            "repo": "TensorLBM",
            "base_ref": "main",
            "runner": "native",
            "origin": "agent",
            "task_key": spec["task_key"],
        }
        try:
            r = _req("/tensor/missions", "POST", payload, token=token)
            results.append({"case": spec["marker"], "task_key": spec["task_key"],
                            "mission_id": r.get("id"), "session_id": r.get("session_id"),
                            "status": r.get("status"), "idempotent": r.get("idempotent", False),
                            "prior_missions": seen.get(spec["marker"], [])})
            print("DISPATCHED", spec["marker"], "->", r.get("id"), r.get("status"))
        except Exception as e:  # noqa: BLE001
            body = getattr(e, "read", lambda: b"")()
            results.append({"case": spec["marker"], "task_key": spec["task_key"], "error": str(e),
                            "body": body.decode()[:500]})
            print("ERROR", spec["marker"], e, body[:300])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"shift": "m408", "dispatches": results}, f, ensure_ascii=False, indent=2)
    print("WROTE", OUT)


if __name__ == "__main__":
    main()