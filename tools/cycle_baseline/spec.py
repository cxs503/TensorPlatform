"""cycle_baseline.spec —— 口径冻结层（预注册即代码）。

为什么单独一层
--------------
战役节点 IN55W3-N08 的验收要求「预注册冻结周期口径，后续只许按 prereg 测量」。为了让
口径**机器可读、可对拍、不可被后续改动悄悄漂移**，这里把段定义、口径字符串、覆盖率门限、
准入常数一次性冻结成常量 + ``SPEC_SHA256``（对 canonical JSON 求 sha256）。prereg 文档
（``prereg/cycle-baseline-<date>.md``）与 ``artifacts/cycle_baseline/baseline.json`` 都嵌
同一个 ``SPEC_SHA256``；两者不一致即视为口径漂移，测试会红。

平台级纪律（TensorPlatform 铁律：零领域语义）
--------------------------------------------
本模块只谈 mission / worker / 段时长 / 队列，不出现任何领域词条；域数据一律经 DB/配置/
工件注入。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "cycle_baseline.v1"
PREREG_ID = "cycle-baseline-2026-11-10"

# ── 四段定义（口径的唯一真相） ────────────────────────────────────────────
SEGMENTS = ("t_queue", "t_exec", "t_merge", "t_report")

DEFINITIONS: dict[str, str] = {
    "cohort": "cohort = 时间窗内终态 mission（status ∈ 契约 terminal 集合），按 created_at 归窗；"
              "分母固定为 cohort 全量，不因缺证据缩小（缩分母=自欺）",
    "t_queue": "首个 mission_claimed 事件 − mission.created_at（排队等待；无领取证据 → 缺失）",
    "t_exec": "mission.finished_at − 首个 mission_claimed 事件（领取到终态的墙钟；无领取证据 → 缺失）",
    "t_merge": "首次落地 commit 时间 − mission.finished_at（git 侧按 mission id 反查；"
               "无匹配 commit → 缺失）。原值可为负：先落地后收尾的 push-merge 路径",
    "t_report": "报告行 created_at − mission.finished_at（tensor_reports.mission_id 关联；"
                "无报告行 → 缺失）。原值可为负：先发报告再 finish 的执行路径",
    "cycle": "周期 = created_at → finished_at（预注册主口径）。原战役假设『created→report』"
             "与实测相位相反（>95% 报告早于 finished_at），故本口径按实测修正并在此冻结",
    "segment_share": "份额 = 段时长 / Σ 已测段时长（仅在四段齐备的 complete-case 子集上"
                     "可加；缺证据时分子分母同时收缩并在 coverage 字段显式给出）",
    "clamped_lag": "t_merge / t_report 原值为负时，非执行开销口径取 max(0, 原值) 并单列"
                   "negative_share（负值比例），避免用 clamp 掩盖相位事实",
    "coverage": "段覆盖率 = 该段有证据的 mission 数 / cohort mission 数",
    "jain": "Jain 公平指数 J(x) = (Σx)² / (n Σx²)，x 为 worker 维度负载（计数或忙时秒）；"
            "J ∈ [1/n, 1]，1 = 完全均分",
    "gini": "Gini = Σ_i Σ_j |x_i − x_j| / (2 n² μ)；μ=0 时取 0 并标记 degenerate",
    "starvation": "饥饿事件 = 排队时长 > 同 cohort 排队 p95 且终态非 succeeded"
                  "（等待超阈却未正常完成）",
    "priority_inversion": "优先级反转 = 同战役内 mission A 的 planned_start 早于 B，"
                          "但 A 的领取时间晚于 B（调度序与计划序倒置），阈值 gap 秒",
    "fairness_gate": f"分段覆盖率 < {0.80:.2f} → 该段结论降级 WITHHELD（不得宣称总体结论）",
}

# ── 门限 / 常数（预注册，事后不得下调） ──────────────────────────────────
COVERAGE_MIN = 0.80            # 判据 (d)
JAIN_RECOVERY_TOL = 0.02       # 判据 (a)
DETECTOR_F1_MIN = 0.90         # 判据 (b)
POLICY_THEORY_TOL = 0.05       # 判据 (e)，Jain/Gini 相对误差
STARVATION_PCTL = 0.95         # 饥饿阈值：排队 p95
INVERSION_GAP_S = 60.0         # 反转判定的 planned_start 间隔下限（秒）
DEFAULT_WINDOW_DAYS = 8
DEFAULT_SEED = 20261110

# 终态词表兜底（与 backend/contracts/mission_status_vocab.v1.json 保持一致；
# 运行时优先读契约文件，测试断言两者相等，防止第二份真相漂移）
FALLBACK_TERMINAL = ("succeeded", "failed", "stopped", "revoked", "orphan")


def _canonical_spec() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "prereg_id": PREREG_ID,
        "segments": list(SEGMENTS),
        "definitions": DEFINITIONS,
        "coverage_min": COVERAGE_MIN,
        "jain_recovery_tol": JAIN_RECOVERY_TOL,
        "detector_f1_min": DETECTOR_F1_MIN,
        "policy_theory_tol": POLICY_THEORY_TOL,
        "starvation_pctl": STARVATION_PCTL,
        "inversion_gap_s": INVERSION_GAP_S,
        "seed": DEFAULT_SEED,
    }


def canonical_json(obj: Any) -> str:
    """稳定序列化：键排序、无多余空格、浮点用 repr（跨进程同值同串）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


SPEC_SHA256 = sha256_of(_canonical_spec())