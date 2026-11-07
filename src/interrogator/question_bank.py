"""question_bank —— 战略质询族的题目库（问对不问错）。

owner 式审计不是「挑错」，而是把注意力压到**结构性失真**上。五族：

  ZERO_FLOW  零流器官为何零流   —— 有器官、有入口、零吞吐：是需求没了，还是管道堵了？
  GRADE_CEIL grade 天花板为何 S2 —— 证据分级停在 S2：是证据不够硬，还是判据不敢问？
  EXP_BUDGET 探索预算是否真被使用 —— budget_planned 写满、budget_actual 全零：预算是账面的还是真的？
  SN_TRACK   S-N 轨是否在积累    —— 失败/负结果是否进了可复用经验账本，还是散成日志？
  CONVERSION 质询自身是否在转化   —— 质询问了不落单、落了单不修，等于没问（元质询）。

每条题目必须自带：
  * evidence      —— 摘自探针的数字（含 source，可回溯到采集快照）
  * branches      —— ≥3 个互斥解释分支 + 各自的判别方法（避免「我觉得」式回答）
  * criteria      —— 回答可机器判定的验收条件
  * convert_rule  —— 若判为真缺陷，该转成什么单（仓 + 类型）

拿不到的信号不补零，而是转成「度量缺失」型质询——那本身就是失真的一种。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .probes import Signal

FAMILIES = ("ZERO_FLOW", "GRADE_CEIL", "EXP_BUDGET", "SN_TRACK", "CONVERSION")

FAMILY_TITLES = {
    "ZERO_FLOW": "零流器官为何零流",
    "GRADE_CEIL": "grade 天花板为何停在 S2",
    "EXP_BUDGET": "探索预算是否真被使用",
    "SN_TRACK": "S-N 轨（失败/负结果）是否在积累",
    "CONVERSION": "质询是否真在转化为修补",
}

FAMILY_RATIONALE = {
    "ZERO_FLOW": "零吞吐要么是需求消失（应关闭器官），要么是管道堵塞（应修），二者处置完全相反。",
    "GRADE_CEIL": "证据分级卡在 S2 意味着结论永不可用于决策；若不是证据不够，就是判据设计太软。",
    "EXP_BUDGET": "预算是自治的燃料表：账面预算而实际为零，等于自治在空转却看不出来。",
    "SN_TRACK": "S-N 轨（成功-负结果）是唯一能让后来者少走弯路的资产；不入账的失败等于白失败。",
    "CONVERSION": "质询的价值只体现在转化率；不转化的质询是注意力税。",
}


@dataclass
class Question:
    key: str
    family: str
    title: str
    text: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    convert_rule: str = ""
    severity: int = 3
    target_repo: str = "TensorPlatform"

    @property
    def fingerprint(self) -> str:
        """稳定的题目指纹（族+题号）；证据变化不改变指纹，用于去重窗口。"""
        return f"{self.family}:{self.key}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint
        d["family_title"] = FAMILY_TITLES.get(self.family, self.family)
        return d


def _val(signals: dict[str, Signal], name: str, default: Any = None) -> Any:
    sig = signals.get(name)
    if sig is None or not sig.usable:
        return default
    return sig.value


def _unusable(signals: dict[str, Signal], name: str) -> Signal | None:
    sig = signals.get(name)
    if sig is None or not sig.usable:
        return sig
    return None


def _ev(signals: dict[str, Signal], name: str) -> dict[str, Any]:
    sig = signals.get(name)
    if sig is None:
        return {"signal": name, "value": None, "source": "(absent)", "status": "unavailable"}
    return {"signal": name, "value": sig.value, "source": sig.source, "status": sig.status,
            "detail": sig.detail}


# ───────────────────────── 族 1：零流器官 ─────────────────────────

def _zero_flow(signals: dict[str, Signal]) -> list[Question]:
    out: list[Question] = []
    by_status = _val(signals, "missions_by_status", {}) or {}
    pending = _val(signals, "missions_pending", 0) or 0
    running = int(by_status.get("running", 0) or 0)
    succeeded = _val(signals, "missions_succeeded_window")
    terminal = _val(signals, "missions_terminal_window")

    if _unusable(signals, "zero_flow_organs") or not _val(signals, "zero_flow_organs", []):
        out.append(Question(
            key="ZF-01", family="ZERO_FLOW",
            title="零流器官没有度量：谁在空转，靠什么知道？",
            text=("平台没有可读的「器官吞吐」事实源（探针返回 unavailable），因此无法回答"
                  "「哪些器官零流」。请判定：零流器官度量缺失，是（a）器官本身不存在，"
                  "（b）存在但无出口（吞吐无落点），还是（c）有出口但没人读？"
                  "并给出一条成本最低的可落地度量方案（字段/端点/采样频率）。"),
            evidence=[_ev(signals, "zero_flow_organs"),
                      _ev(signals, "missions_by_status"),
                      _ev(signals, "missions_pending")],
            branches=[
                "a) 器官未建：列出应存在但不存在的器官名与消费者（判别：签核/入口代码是否存在）",
                "b) 器官在跑但无出口：找写入侧无读者（判别：grep 读取方调用点=0）",
                "c) 有出口无人消费：找读者存在但最近 N 天零调用（判别：访问日志/事件表时间戳）",
            ],
            criteria=[
                "回答必须点名 ≥1 个器官并给出代码或表路径（文件:行 或 table.column）",
                "必须给出零流的判别方法（可复跑的查询/命令），不得仅叙述",
                "必须给出度量方案字段名 + 采集频率 + 消费方",
            ],
            convert_rule="若判为 (a)/(b) → 立 TensorPlatform 修补单（器官接线）；若 (c) → 立度量改进单",
            severity=4,
        ))
    if pending and running == 0:
        out.append(Question(
            key="ZF-02", family="ZERO_FLOW",
            title=f"队列有 {pending} 张待领单、在飞为 0：零流在队列还是舰队？",
            text=(f"当前 pending={pending}、running=0、窗口内终态={terminal}、成功={succeeded}。"
                  "请判定零流位置：是派发器不认领（队列侧），还是 worker 全部退出（舰队侧），"
                  "抑或单的 goal 不可执行（内容侧）？三个分支的判别证据必须来自平台表/日志。"),
            evidence=[_ev(signals, "missions_by_status"), _ev(signals, "missions_pending"),
                      _ev(signals, "missions_terminal_window"),
                      _ev(signals, "missions_succeeded_window")],
            branches=[
                "队列侧：claim 端点被调用但返回 claimed=false（判别：claim 事件时间序列）",
                "舰队侧：heartbeat 静默 > 90s 的 worker 数（判别：resident_workers/heartbeat_at）",
                "内容侧：连续被认领后立刻放弃（判别：同一 goal 的 claim→reclaim 间隔）",
            ],
            criteria=[
                "必须给出 pending→running 的实际转化率数字（窗口内）+ 数据来源路径",
                "必须点名一个最可能的瓶颈并给出可复跑的判别证据",
            ],
            convert_rule="判为队列/舰队侧 → 立 TensorPlatform 修补单；内容侧 → 立派发质量改进单",
            severity=5,
        ))
    return out


# ───────────────────────── 族 2：grade 天花板 ─────────────────────────

def _grade_ceiling(signals: dict[str, Signal]) -> list[Question]:
    out: list[Question] = []
    grades = _val(signals, "mission_grade_counts", {}) or {}
    cap_grades = _val(signals, "capability_grade_counts", {}) or {}
    merged = dict(grades)
    for g, n in cap_grades.items():
        merged[g] = int(merged.get(g, 0) or 0) + int(n or 0)
    high = sum(n for g, n in merged.items() if str(g).upper() in ("S3", "S4", "S5", "REAL"))
    top = sorted(merged.items(), key=lambda kv: -int(kv[1] or 0))
    cov = _val(signals, "capability_coverage") or {}
    if isinstance(cov, dict) and cov.get("total_deltas"):
        total_d = int(cov.get("total_deltas") or 0)
        substantive = int(cov.get("substantive_deltas") or 0)
        if substantive == 0:
            out.append(Question(
                key="GC-03", family="GRADE_CEIL",
                title=f"{total_d} 条能力增量里实质增量=0：grade 天花板是结构性的",
                text=(f"能力中心账本共 {total_d} 条增量，实质增量(substantive_deltas)="
                      f"{substantive}，全部落在 no_substantive_delta；同时 succeeded 单 "
                      f"{cov.get('succeeded')} 件、入账覆盖 {cov.get('covered')} 件"
                      f"（覆盖率 {cov.get('ratio')}）。请判定：这是（a）入账判据把实质增量定义得"
                      "不可达，（b）产出确实无实质增量（质量真实偏低），还是（c）入账调用链断了"
                      "（ctor 参数默认 no_substantive_delta）？三分支分别对应改判据/改产线/改接线。"),
                evidence=[_ev(signals, "capability_coverage"),
                          _ev(signals, "mission_grade_counts")],
                branches=[
                    "a) 判据不可达：读 CATEGORIES 定义与判定条件，看是否存在可达路径",
                    "b) 产出偏低：抽样 ≥3 件 succeeded 单，看是否有可复用/可证伪成果被漏记",
                    "c) 接线断裂：找 delta 写入点的默认参数与调用方是否传 category",
                ],
                criteria=[
                    "必须给出 substantive_deltas 计算口径（SQL/代码路径）与期望值",
                    "必须抽样 ≥3 件 mission 给出「应记何类」的判定与证据路径",
                ],
                convert_rule="a/c → 立 TensorPlatform 入账链修补单；b → 立产出质量标准改进单",
                severity=5,
            ))
    if merged:
        ceiling = "S2" if high == 0 else "S3+"
        out.append(Question(
            key="GC-01", family="GRADE_CEIL",
            title=f"证据分级天花板={ceiling}：是证据不够硬，还是判据不敢问？",
            text=(f"分级分布 {dict(top)}，S3 及以上计数={high}。请判定天花板成因："
                  "（a）物理标定缺失（无实验/参考值可比），（b）方法收敛未证（GCI/网格无关性未做），"
                  "（c）判据过软（自证式验收），或（d）**分级链本身断线**（结果未写 grade 字段）。"
                  "对每个成因给出可否在 7 天内解除的判断与代价。"),
            evidence=[_ev(signals, "mission_grade_counts"),
                      _ev(signals, "capability_grade_counts"),
                      _ev(signals, "capability_coverage")],
            branches=[
                "a) 缺物理标定：指出具体能力缺哪个基准（实验/解析/文献）",
                "b) 收敛未证：指出该能力缺少 GCI/网格无关性证据",
                "c) 判据过软：指出判据里可自证的部分（无外部基准的 PASS）",
                "d) 分级断线：统计 result.grade 缺失比例（判别：SQL 计数=总数减有 grade 数）",
            ],
            criteria=[
                "必须给出 grade 缺失比例数字 + 数据路径（若为 d 分支）",
                "必须点名 ≥1 个可从 S2 提到 S3 的能力，并写出缺口条件（不是计划）",
            ],
            convert_rule="判为 d → 立分级链修补单；a/b → 立标定/收敛证据改进单；c → 立判据加严单",
            severity=5 if high == 0 else 3,
        ))
    elif _unusable(signals, "mission_grade_counts"):
        out.append(Question(
            key="GC-02", family="GRADE_CEIL",
            title="分级数据不可得：天花板结论本身无据",
            text=("探针拿不到 grade 分布（missions/capability 端点均无 grade 字段），"
                  "因此任何「grade 天花板为 S2」的说法都是无据判断。请先判定："
                  "分级是（a）未写入结果，（b）写入但键名不一致，还是（c）只在报告文中而未进结构化字段？"),
            evidence=[_ev(signals, "mission_grade_counts"),
                      _ev(signals, "capability_grade_counts"),
                      _ev(signals, "capability_coverage")],
            branches=["a) 未写入：搜索 result 写入点的 grade 参数",
                      "b) 键名漂移：列出所有 grade/level/evidence_grade 变体出现次数",
                      "c) 仅在文本：统计报告 markdown 中 S1..S5 出现次数 vs 结构化字段"],
            criteria=["必须给出 grade/level/evidence_grade 变体的实际出现计数", "必须给出统一键名的具体补丁位置"],
            convert_rule="任一分支成立 → 立 TensorPlatform 分级字段统一修补单",
            severity=4,
        ))
    return out


# ───────────────────────── 族 3：探索预算 ─────────────────────────

def _exp_budget(signals: dict[str, Signal]) -> list[Question]:
    out: list[Question] = []
    plans = _val(signals, "plans_budget")
    if isinstance(plans, dict) and plans.get("total"):
        total = int(plans.get("total") or 0)
        zero = int(plans.get("zero_actual") or 0)
        ratio = (zero / total) if total else 0.0
        if zero:
            out.append(Question(
                key="EB-01", family="EXP_BUDGET",
                title=f"{zero}/{total} 个主计划 budget_actual 全零（{ratio:.0%}）：预算真的被用了吗？",
                text=(f"扫描到 {total} 个主计划，其中 {zero} 个 budget_actual 所有科目为 0，"
                      f"样例 {plans.get('examples')}。请判定：预算是（a）从未记账（字段没接线），"
                      "（b）记了但不在 plan 层聚合（在 mission 层），还是（c）真的没花（探索预算被闲置）？"
                      "三分支处置相反：a 要接线、b 要聚合、c 要追问为何不花。"),
                evidence=[_ev(signals, "plans_budget")],
                branches=[
                    "a) 未记账：找 budget_actual 的写入点；若不存在写入点即证实",
                    "b) 未聚合：mission.budget 与 plan.budget_actual 的量纲/派生关系是否可计算",
                    "c) 真闲置：已有记账但窗口内预算消耗≈0（判别：tokens/tool_calls 时间序列）",
                ],
                criteria=[
                    "必须给出 budget_actual 写入点存在性结论（文件:行 或「不存在」+grep 证据）",
                    "必须给出至少一个 plan 的 planned vs actual 数字对（可复跑 SQL/端点）",
                ],
                convert_rule="a/b → 立 TensorPlatform 预算记账修补单（含 SQL/视图）；c → 立预算再分配改进单",
                severity=5,
            ))
    elif _unusable(signals, "plans_budget"):
        out.append(Question(
            key="EB-02", family="EXP_BUDGET",
            title="主计划预算不可读：探索预算无从审计",
            text=("/tensor/campaigns/plans 不可读，探索预算是否存在、是否被花都无法审计。"
                  "请判定读失败是（a）鉴权/路由问题，（b）计划表为空，还是（c）字段缺失，并给出最小修复。"),
            evidence=[_ev(signals, "plans_budget")],
            branches=["a) 鉴权/路由：端点返回码与错误体", "b) 空表：plans 计数=0", "c) 字段缺失：返回体 keys 不含 budget_*"],
            criteria=["必须给出端点返回码/错误体证据", "必须给出最小修复补丁位置"],
            convert_rule="任一分支 → 立 TensorPlatform 审计可读性修补单",
            severity=4,
        ))
    return out


# ───────────────────────── 族 4：S-N 轨 ─────────────────────────

def _sn_track(signals: dict[str, Signal]) -> list[Question]:
    out: list[Question] = []
    honest = _val(signals, "missions_honest_fail_window")
    failed = _val(signals, "missions_failed_window")
    succeeded = _val(signals, "missions_succeeded_window")
    playbook = _val(signals, "playbook_entries")
    if honest is not None and failed is not None:
        out.append(Question(
            key="SN-01", family="SN_TRACK",
            title=f"窗口内 honest-FAIL={honest}、failed={failed}、成功={succeeded}：负结果入账了吗？",
            text=(f"窗口内终态单中失败 {failed} 件、其中显式 honest-FAIL {honest} 件，成功 {succeeded} 件；"
                  f"PLAYBOOK 条目数={playbook}。请判定负结果的命运："
                  "（a）入 PLAYBOOK/TRAPS（可复用），（b）只进报告正文（不可检索），"
                  "（c）随 worktree 丢弃（等于白败）。并给出「负结果入账率」的可复跑度量。"),
            evidence=[_ev(signals, "missions_honest_fail_window"),
                      _ev(signals, "missions_failed_window"),
                      _ev(signals, "missions_succeeded_window"),
                      _ev(signals, "playbook_entries")],
            branches=[
                "a) 已入账：PLAYBOOK/TRAPS 中可 grep 到对应 mission_id 或判例关键词",
                "b) 仅正文：报告 markdown 命中但账本无条目（判别：两处计数差）",
                "c) 已丢弃：报告与账本均无（判别：mission.result 里是否有 verdict/FAIL 字段）",
            ],
            criteria=[
                "必须给出「负结果入账率」= 入账条数 / honest-FAIL 件数，带分子分母来源",
                "必须抽查 ≥2 件窗口内失败单，各给出入账位置（文件:行）或判定为未入账",
            ],
            convert_rule="b/c 分支 → 立经验蒸馏修补单（入账管线）；a 分支 → 立判断例质量抽检单",
            severity=4,
        ))
    if playbook is not None and playbook == 0:
        out.append(Question(
            key="SN-02", family="SN_TRACK",
            title="PLAYBOOK 零条：S-N 轨确认为空",
            text="PLAYBOOK 条目数为 0，说明经验积累轨尚未启动。请判定应最先蒸馏哪一类判例，"
                 "并给出判例的机器可判入库条件（≥3 个引用数字/证据路径）。",
            evidence=[_ev(signals, "playbook_entries")],
            branches=["从 succeeded 报告蒸馏", "从 honest-FAIL 蒸馏", "从 shadow 处置日志蒸馏"],
            criteria=["必须给出首个判例的完整草案（含 ≥3 个数字或证据路径）"],
            convert_rule="→ 立经验蒸馏改进单（对接 IN27-H6）",
            severity=3,
        ))
    return out


# ───────────────────────── 族 5：质询自身转化（元） ─────────────────────────

def _conversion(signals: dict[str, Signal], ledger_stats: dict[str, Any] | None) -> list[Question]:
    stats = ledger_stats or {}
    out: list[Question] = []
    asked = int(stats.get("asked", 0) or 0)
    converted = int(stats.get("converted", 0) or 0)
    stale = stats.get("stale", []) or []
    if asked and converted == 0:
        out.append(Question(
            key="CV-01", family="CONVERSION",
            title=f"已问 {asked} 题、转化 0 件：质询在自我娱乐吗？",
            text=(f"账本记录已发出 {asked} 个质询，尚无一件转化为修补/改进单。请判定："
                  "（a）题目不可判（无法证伪）→ 改题目设计；（b）有结论但无人立单 → 改回填管线；"
                  "（c）结论为「无缺陷」→ 记录为负结论并停止重复追问。三分支必须择一并给出下一步机器判据。"),
            evidence=[{"signal": "ledger_stats", "value": {"asked": asked, "converted": converted,
                                                          "stale": stale[:5]},
                       "source": "ledger:interrogations", "status": "ok"}],
            branches=["a) 题目不可判", "b) 回填断线", "c) 负结论"],
            criteria=["必须给出所选分支的可复跑证据", "必须给出下一周期题目或流程的具体改动"],
            convert_rule="b → 立回填管线修补单；a → 立题目模板改进单",
            severity=4,
        ))
    if stale:
        stale_days = stats.get("stale_days", 7)
        out.append(Question(
            key="CV-02", family="CONVERSION",
            title=f"{len(stale)} 个质询超期未答（>{stale_days} 天）：谁欠的账？",
            text=("以下质询超过时限仍无结论：" + "；".join(str(s) for s in stale[:5]) +
                  "。请判定欠账责任在（a）派发未领取，（b）领取后未回填，（c）结论被判为无关。"
                  "每件必须给出终局处置（转化/关闭/重问），不得继续挂账。"),
            evidence=[{"signal": "stale_interrogations", "value": stale[:10],
                       "source": "ledger:interrogations", "status": "ok"}],
            branches=["a) 未领取（claim 记录为空）", "b) 领取未回填（有 heartbeat 无 answer）", "c) 判定无关"],
            criteria=["每件 stale 质询必须给出终局状态与证据路径"],
            convert_rule="a/b → 立派发/回填修补单；c → 关闭并记录理由",
            severity=3,
        ))
    return out


def candidates(signals: dict[str, Signal], ledger_stats: dict[str, Any] | None = None) -> list[Question]:
    """返回当期全部候选质询（按严重度降序、题号升序）。"""
    pool: list[Question] = []
    pool += _zero_flow(signals)
    pool += _grade_ceiling(signals)
    pool += _exp_budget(signals)
    pool += _sn_track(signals)
    pool += _conversion(signals, ledger_stats)
    pool.sort(key=lambda q: (-q.severity, q.fingerprint))
    return pool


def family_coverage(questions: list[Question]) -> dict[str, int]:
    cov = {f: 0 for f in FAMILIES}
    for q in questions:
        cov[q.family] = cov.get(q.family, 0) + 1
    return cov


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()