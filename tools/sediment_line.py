#!/usr/bin/env python3
"""sediment_line.py — 被动进水口常设守护（锚点 FEAT-TK-GOV-INFRA）。

定位（owner 2026-11-10）：**「每次 agent 干完活形成知识积累进来」的平台侧执行器**。
本进程不生产知识，只做三件事：

1. **看**：轮询平台 API（默认 5 min 一轮），扫 ``succeeded``（含已合并）任务；
2. **筛**：按知识族过滤器判断这条 result 有没有「可沉淀面」——
   物理验证 / 基准工装 / QR 或仲裁定谳 / 根因判例 / 校准数据，
   外加 **falsification 负知识必收**；infra 杂务与纯文案一律不收（防噪）；
3. **派**：命中即派生提取子单 ``task_key=tk-sediment-m<mid>``（服务端 task_key 幂等 +
   本地账本双保险），goal 模板要求从源 mission 的 result/report 提取 ≥3 条四契约知识，
   写入 TensorKnowledge ``knowledge/sediment/m<mid>/``，**draft 态、生产者不得自审**。

子命令::

    scan     常设守护主体（daemon 每轮调一次）；--dry-run 只判定不派单
    replay   近 N 天回放验收（扫满窗口 → 判定 → 派单 → 出验收报告）
    verify   抽样 N 张已派生提取单，机器核验证据可回溯性（防伪造）

设计纪律：

- **零领域语义**（TensorPlatform 铁律）：过滤器关键词全是科研过程词（验证/基准/校准/
  仲裁/根因/否证…），不含任何物理域词条；域数据只经 mission 的 result 注入。
- **诚实优先**：源 mission 没记录的 sha 一律写 ``PENDING`` 并给出解析命令，
  绝不生成假 sha；判定为 skip 的单也落账（可审计为什么没收）。
- **幂等**：``task_key`` 服务端去重 + ``state.json`` 本地去重 + 源单自身是本守护派生的
  单不再递归沉淀（避免自噬）。

CLI 例::

    python3 tools/sediment_line.py scan --dry-run
    python3 tools/sediment_line.py replay --days 7 --max-per-run 24
    python3 tools/sediment_line.py verify --sample 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

MARKER = "[SEDIMENT-LINE]"
TASK_KEY_PREFIX = "tk-sediment-m"
TARGET_REPO = "TensorKnowledge"
SEDIMENT_DIR = "knowledge/sediment"
MIN_CLAIMS = 3
DEFAULT_DAYS = 7
DEFAULT_MAX_PER_RUN = 20
DEFAULT_DAILY_CAP = 40
DEFAULT_PER_REPO_DAILY = 6
DECISIONS_ROTATE_MB = 32
DEFAULT_API_BASE = "http://127.0.0.1:8010/api/v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = REPO_ROOT / "runs" / "sediment_line"
LOG_FILE = REPO_ROOT / "logs" / "daemon_sediment.log"

FOUR_CONTRACTS = (
    ("schemas/claim-record.schema.json", "claim"),
    ("schemas/evidence-record.schema.json", "evidence"),
    ("schemas/provenance-record.schema.json", "provenance"),
    ("schemas/review-event.schema.json", "review"),
)


# ───────────────────────────── 过滤器 ─────────────────────────────
@dataclass(frozen=True)
class Rule:
    """一条可审计的判定规则。

    family  — 知识族（写进派生单，决定提取提示语的重点）；
    weight  — 该族命中一次的得分（同族多次命中只计一次）；
    strong  — 单族命中即可派生（定谳/根因/否证这类高价值面）；
    patterns— 正则；判定时记录命中片段原文，供人复核。
    """

    family: str
    label: str
    weight: int
    patterns: tuple[str, ...]
    strong: bool = False


# 知识族（family → 中文标签 / 权重 / 是否强命中）。权重只用于排序与账本，不是概率。
# 铁律：这里全是「科研过程词」，零物理域语义——域内容只能经 mission 的 result 注入。
FAMILIES: dict[str, dict[str, Any]] = {
    "falsification": {"label": "负知识/否证（falsification，高价值必收）", "weight": 6, "strong": True, "order": 0},
    "verdict": {"label": "QR / 仲裁定谳", "weight": 5, "strong": True, "order": 1},
    "root_cause": {"label": "根因判例（根因→修复→回归护栏）", "weight": 4, "strong": True, "order": 2},
    "calibration": {"label": "校准 / 标定数据（含误差量）", "weight": 3, "strong": False, "order": 3},
    "physical_validation": {"label": "物理验证面（定量误差 vs 外部基准）", "weight": 3, "strong": False, "order": 4},
    "benchmark_tooling": {"label": "基准工装 / 对标工件", "weight": 3, "strong": False, "order": 5},
}

# 强命中族（结果侧出现即「一定值得沉淀」的判据面）
STRONG_FAMILIES = tuple(f for f, m in FAMILIES.items() if m["strong"])

# 正向信号：只读 **result 侧**（headline / checks / decision_note），不读 goal。
# 原因：战役 goal 是模板化长文，「验证/基准/判据」在全仓 goal 里遍地都是，读 goal 会把
# 命中率顶到 90%+（实测 4000 单 3754 命中）——那既不是沉淀也不是防噪。
_SIG_FALSIFICATION = re.compile(
    r"(falsif|否证|证伪|反例|counter[-\s]?example|honest[-\s]?fail|推翻|假设被否定)", re.I)
_SIG_VERDICT = re.compile(r"(\bQR\b|仲裁|定谳|裁定|裁决|arbitrat|adjudicat)", re.I)
_SIG_ROOT_CAUSE = re.compile(r"(根因|root[-\s]?cause|判例|post[-\s]?mortem|\bRCA\b)", re.I)
_SIG_CALIBRATION = re.compile(r"(校准|标定|calibrat)", re.I)
# 「定量误差」= 误差词后紧跟数字（"误差 1.06%" / "相对误差<3%" / "rel_err=0.8"）
_SIG_ERROR_NUM = re.compile(
    r"(误差|偏差|rel_err|relative error|deviation)[^。；;\n]{0,30}?\d", re.I)
# 「外部基准」= 明确的对标物，不是自说自话
_SIG_REFERENCE = re.compile(r"(基准|参考值|解析解|analytic|对标|观测|实验值|判据|参考文献|真值)", re.I)
_SIG_BENCHMARK = re.compile(r"(基准|benchmark|对标|baseline|工装)", re.I)
_SIG_BENCH_TOOLING = re.compile(r"(基准工装|benchmark\s*(?:suite|case|工装|集)|对标工装)", re.I)
_BENCH_ARTIFACT_RE = re.compile(r"(benchmark|baseline|基准|对标)", re.I)
_SIG_DIGIT = re.compile(r"\d")

# 噪点族：infra 杂务 / 纯文案 / 元层报表。噪声分 ≥ 正向前提时不收（防噪 > 贪多）。
NOISE_RULES: tuple[Rule, ...] = (
    Rule(
        "infra_chore",
        "infra 杂务",
        3,
        (
            r"\blint\b",
            r"格式化",
            r"依赖(升级|更新|锁定)",
            r"\bbump\b",
            r"\bdocker\b",
            r"kubernetes|\bk8s\b",
            r"healthcheck|健康检查",
            r"心跳|heartbeat",
            r"daemon|定时任务|轮询",
            r"分支清理|清理分支",
            r"\bretry\b|重试",
            r"deploy|部署脚本",
            r"workflow|CI 配置|ci 配置",
            r"迁移脚本|migration",
            r"refresh token|续期",
            r"pre-commit",
        ),
    ),
    Rule(
        "pure_prose",
        "纯文案",
        3,
        (
            r"README",
            r"CHANGELOG",
            r"文案",
            r"文档重写|重写.{0,6}文档",
            r"补报|回填报告|post_report",
            r"措辞|排版",
        ),
    ),
    Rule(
        "meta_reporting",
        "元层/报表杂务",
        2,
        (r"看板|dashboard", r"报表渲染", r"周报|日报"),
    ),
)

# 证据工件闸：result 必须留下**可回溯的工件**（测试/验证/报告/基准类文件，或已发布报告 id）。
# 没有工件的「自述式通过」不发单——否则提取子单会拿不到 path:line 引句。
_ARTIFACT_RE = re.compile(
    r"(test_|_test\.|tests/|validation|verif|benchmark|baseline|report|anchor|calib|核查|机检)",
    re.I)

_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_WORKTREE_RE = re.compile(r"/root/tp-agent/work/m(\d+)/")
_DIGIT_RE = re.compile(r"\d")

_VERIFY_CHECKS = ("source_id_cited", "repo_cited", "files_traceable", "sha_present",
                  "four_contracts_present", "quotes_verbatim")


def _result_text(mission: dict[str, Any]) -> str:
    """只取 result 侧文本（headline/checks/decision_note）——判据面的唯一依据。"""
    result = mission.get("result") or {}
    summary = (result.get("evidence") or {}).get("summary") or {}
    parts = [
        str(result.get("headline") or ""),
        str(summary.get("headline") or ""),
        str(result.get("checks") or ""),
        str(summary.get("checks") or ""),
        str(result.get("decision_note") or ""),
    ]
    return "\n".join(p for p in parts if p)


def _text_blob(mission: dict[str, Any]) -> str:
    """全文本（含 goal / 文件清单）——只用于噪点判定与自噬检测。"""
    result = mission.get("result") or {}
    parts = [
        str(mission.get("goal") or ""),
        _result_text(mission),
        str(result.get("git_status") or ""),
        " ".join(str(x) for x in (result.get("changed_files") or [])),
        " ".join(str(x) for x in (result.get("on_disk_changes") or [])[:40]),
    ]
    return "\n".join(p for p in parts if p)


def _snippet(text: str, start: int, end: int, pad: int = 46) -> str:
    lo, hi = max(0, start - pad), min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def _quotes(text: str, pattern: re.Pattern[str], limit: int = 2) -> list[str]:
    out: list[str] = []
    for m in pattern.finditer(text):
        q = _snippet(text, m.start(), m.end())
        if q not in out:
            out.append(q)
        if len(out) >= limit:
            break
    return out


def _match_rules(rules: Sequence[Rule], text: str, limit_per_rule: int = 2) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for rule in rules:
        quotes: list[str] = []
        for pat in rule.patterns:
            quotes.extend(q for q in _quotes(text, re.compile(pat, re.I), limit_per_rule)
                          if q not in quotes)
            if len(quotes) >= limit_per_rule:
                quotes = quotes[:limit_per_rule]
                break
        if quotes:
            hits.append({"family": rule.family, "label": rule.label,
                         "weight": rule.weight, "quotes": quotes})
    return hits


def detect_signals(mission: dict[str, Any]) -> list[dict[str, Any]]:
    """抽 result 侧正向信号（知识族），每条附「原文摘录」供人复核。"""
    text = _result_text(mission)
    files = [str(x) for x in ((mission.get("result") or {}).get("changed_files") or [])]
    result = mission.get("result") or {}
    exhibits = any(_ARTIFACT_RE.search(f) for f in files) or bool(result.get("report_id"))
    found: list[dict[str, Any]] = []

    def _add(family: str, quotes: list[str]) -> None:
        meta = FAMILIES[family]
        found.append({"family": family, "label": meta["label"], "weight": meta["weight"],
                      "strong": meta["strong"], "quotes": quotes})

    q = _quotes(text, _SIG_FALSIFICATION)
    if q:
        _add("falsification", q)
    q = _quotes(text, _SIG_VERDICT)
    if q:
        _add("verdict", q)
    q = _quotes(text, _SIG_ROOT_CAUSE)
    if q:
        _add("root_cause", q)
    if _SIG_CALIBRATION.search(text) and _SIG_ERROR_NUM.search(text):
        _add("calibration", _quotes(text, _SIG_CALIBRATION) + _quotes(text, _SIG_ERROR_NUM))
    if _SIG_ERROR_NUM.search(text) and _SIG_REFERENCE.search(text):
        _add("physical_validation", _quotes(text, _SIG_ERROR_NUM) + _quotes(text, _SIG_REFERENCE))
    # 基准工装族再加一道闸：必须真的留下 benchmark/基准类**工件**或声明「基准工装」，
    # 否则「基线/baseline」这种口径词会把普通回归单全吸进来（实测 713 单里大半是噪声）。
    bench_artifact = bool(_BENCH_ARTIFACT_RE.search(" ".join(files))) or bool(
        _SIG_BENCH_TOOLING.search(text))
    if exhibits and bench_artifact and _SIG_BENCHMARK.search(text) and _SIG_DIGIT.search(text):
        _add("benchmark_tooling", _quotes(text, _SIG_BENCHMARK))
    return found


@dataclass
class Decision:
    mission_id: int
    repo: str
    hit: bool
    families: list[str]
    positive_score: int
    noise_score: int
    positive: list[dict[str, Any]] = field(default_factory=list)
    noise: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    anchors: dict[str, Any] = field(default_factory=dict)
    exhibits: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id, "repo": self.repo, "hit": self.hit,
            "families": self.families, "positive_score": self.positive_score,
            "noise_score": self.noise_score, "reason": self.reason,
            "positive": self.positive, "noise": self.noise, "anchors": self.anchors,
            "exhibits": self.exhibits,
        }


def git_rev_parse(path: str, ref: str = "HEAD") -> str | None:
    """在源工作树里解析 commit sha（拿不到就返回 None——绝不编造）。"""
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", ref],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (out.stdout or "").strip()
    return sha if out.returncode == 0 and _SHA_RE.match(sha) else None


def extract_anchors(mission: dict[str, Any]) -> dict[str, Any]:
    """抽出可用于「可回溯证据」的锚点：源单 id / 仓 / 分支 / PR / 工作树 / sha / 文件清单。"""
    mid = int(mission.get("id") or 0)
    result = mission.get("result") or {}
    evidence = result.get("evidence") or {}
    summary = evidence.get("summary") or {}
    files = [str(p) for p in (result.get("changed_files") or [])]
    on_disk = [str(p) for p in (result.get("on_disk_changes") or [])]
    worktree = None
    for p in files + on_disk:
        m = _WORKTREE_RE.match(p)
        if m:
            worktree = f"/root/tp-agent/work/m{m.group(1)}"
            break
    branch = evidence.get("branch") or f"agent/m{mid}"
    head_sha = evidence.get("head_commit")
    sha_source = "api" if isinstance(head_sha, str) and _SHA_RE.match(head_sha) else "missing"
    if sha_source == "missing" and worktree:
        resolved = git_rev_parse(worktree)
        if resolved:
            head_sha, sha_source = resolved, "worktree"
    pr_gate = result.get("pr_gate") if isinstance(result.get("pr_gate"), dict) else {}
    checks = str(result.get("checks") or "")
    traceable = bool(files or on_disk or _DIGIT_RE.search(checks) or result.get("report_id"))
    return {
        "source_mission_id": mid,
        "repo": str(mission.get("repo") or ""),
        "branch": branch,
        "pr": pr_gate.get("pr"),
        "pr_state": pr_gate.get("state"),
        "merged": pr_gate.get("state") == "merged",
        "worktree": worktree,
        "head_sha": head_sha,
        "sha_source": sha_source,
        "files": files[:20],
        "report_id": result.get("report_id"),
        "model": result.get("model") or summary.get("model"),
        "finished_at": mission.get("finished_at"),
        "status": mission.get("status"),
        "evidence_traceable": traceable,
        "headline": result.get("headline") or summary.get("headline"),
        "checks": checks,
    }


def classify(mission: dict[str, Any]) -> Decision:
    """判定一条 succeeded mission 是否值得派生知识提取单，并给出可审计理由。

    判定顺序（每一步都是可失败的闸）：
      0. 自噬闸：源单本身是本守护派生的提取单 → skip；
      1. 噪点闸：infra 杂务 / 纯文案 / 元层报表得分 ≥ 正向得分 → skip；
      2. 证据闸：result 无工件（无测试/验证/报告类改动、无 report_id）→ skip；
      3. 知识面闸：result 侧无任何知识族信号 → skip；
      4. 强度闸：强命中族（否证/定谳/根因）任一即可；否则非强信号需 ≥1 个定量信号
         （物理验证=误差数字+外部基准 / 校准 / 基准工装）。
    """
    mid = int(mission.get("id") or 0)
    repo = str(mission.get("repo") or "")
    full_text = _text_blob(mission)
    anchors = extract_anchors(mission)
    result = mission.get("result") or {}
    files = [str(x) for x in (result.get("changed_files") or [])]
    exhibits = any(_ARTIFACT_RE.search(f) for f in files) or bool(result.get("report_id"))

    pos = detect_signals(mission)
    noise = _match_rules(NOISE_RULES, full_text)
    pos_score = sum(h["weight"] for h in pos)
    noise_score = sum(h["weight"] for h in noise)
    families = [h["family"] for h in pos]
    strong = any(h["strong"] for h in pos)

    def _d(hit: bool, reason: str) -> Decision:
        return Decision(mid, repo, hit, families, pos_score, noise_score,
                        pos, noise, reason, anchors, exhibits)

    if MARKER in full_text:
        return _d(False, "self_loop: 源单本身就是沉淀提取单（防自噬，不再递归沉淀）")
    if noise and noise_score >= pos_score:
        return _d(False, "noise_dominant: 噪声得分 %d ≥ 正向得分 %d（%s）"
                  % (noise_score, pos_score, ", ".join(h["family"] for h in noise)))
    if not anchors["evidence_traceable"] or not exhibits:
        return _d(False, "no_traceable_evidence: result 无工件（无测试/验证/报告改动，无 report_id）"
                         "→ 提取单无 path:line 可引")
    if not pos:
        return _d(False, "no_sediment_surface: result 侧未命中任何知识族"
                         "（验证/基准/校准/定谳/根因/否证）")
    if not strong and pos_score < 3:
        return _d(False, "weak_signal: 仅 %d 分且无强命中族（%s）" % (pos_score, ", ".join(families)))
    tag = "strong" if strong else "quant"
    return _d(True, "sediment(%s): 命中 %s（+%d / 噪声 %d）"
              % (tag, ", ".join(families), pos_score, noise_score))


# ───────────────────────────── 派生单模板 ─────────────────────────────
def task_key_for(mission_id: int) -> str:
    return f"{TASK_KEY_PREFIX}{mission_id}"


def build_goal(mission: dict[str, Any], decision: Decision, *, min_claims: int = MIN_CLAIMS) -> str:
    """生成提取子单 goal。所有引用逐字来自源 mission 的 result（不新增信息）。"""
    a = decision.anchors
    mid = decision.mission_id
    if a.get("head_sha"):
        sha_line = str(a["head_sha"])
    else:
        sha_line = ("PENDING — 源单未记录 sha，提取时自行解析："
                    f"git -C {a.get('worktree') or '<源仓检出>'} rev-parse {a.get('branch')}；"
                    "解析不到就写 null 并在 notes 说明")
    fam = "\n".join("- `%s` — %s（+%d）" % (h["family"], h["label"], h["weight"])
                    for h in decision.positive) or "- (none)"
    quotes: list[str] = []
    seen: set[str] = set()
    for h in decision.positive:
        for q in h["quotes"][:2]:
            # 同一句摘录会被多个知识族命中（如 headline 同时含「否证」和「基准」），
            # 全局去重后只引一次，避免子单里堆重复引文。
            if q in seen:
                continue
            seen.add(q)
            quotes.append("> [%s] %s" % (h["family"], q))
    if a.get("headline"):
        quotes.append("> [headline] %s" % str(a["headline"])[:280])
    if a.get("checks"):
        quotes.append("> [checks] %s" % str(a["checks"])[:280])
    for p in (a.get("files") or [])[:4]:
        quotes.append("> [file] %s" % p)

    lines = [
        f"{MARKER} 被动进水口·知识沉淀提取单（源 mission m{mid}）",
        f"【锚点=FEAT-TK-GOV-INFRA】本单由平台守护 tools/sediment_line.py 自动派生（非人工工单），"
        f"task_key={task_key_for(mid)}（幂等键，勿改）。",
        "",
        "## 0 来源（provenance，逐字取自平台 API，禁止改写或臆补）",
        f"- source_mission_id: {mid}",
        f"- source_repo: {a.get('repo')}",
        f"- source_branch: {a.get('branch')}",
        f"- source_pr: {a.get('pr') or 'n/a'}  state: {a.get('pr_state') or 'n/a'}  merged: {a.get('merged')}",
        f"- source_worktree: {a.get('worktree') or 'n/a'}",
        f"- source_head_sha: {sha_line}",
        f"- source_model: {a.get('model') or 'n/a'}",
        f"- source_status: {a.get('status')}  finished_at: {a.get('finished_at')}",
        f"- task_key: {task_key_for(mid)}",
        "",
        "## 1 为什么收它（过滤器命中，规则族可审计）",
        fam,
        "",
        "命中证据摘录（原文，逐字；提取时以它们为线索回源仓核对）：",
        *quotes,
        "",
        f"## 2 任务：从源 mission 的 result/report 提取 ≥{min_claims} 条四契约知识",
        f"目标仓 = {TARGET_REPO}；落盘目录 = {SEDIMENT_DIR}/m{mid}/。每条知识必须四契约齐备：",
        f"1. claim（{FOUR_CONTRACTS[0][0]}）= statement + claim_type + scope + epistemic_status=draft",
        f"2. evidence（{FOUR_CONTRACTS[1][0]}）= source{{repo, ref, sha}} + "
        "locator{path, line_start, line_end, quote} + relation",
        f"3. provenance（{FOUR_CONTRACTS[2][0]}）= generated_entity + activity{{mission_id, task_key}} + "
        f"agents[{{mission_id, model}}] + derived_from[{mid}]",
        f"4. review（{FOUR_CONTRACTS[3][0]}）= 不写。生产者不得自审；只留 review_status=pending，"
        "评审由独立席位另派。",
        "",
        "硬约束（缺失即 honest-FAIL，不许凑数）：",
        "- 每条 evidence.locator 必须给：repo + 仓内相对路径 + 行号区间 + 逐字引句"
        "（quote ≥20 字符，且能在该 sha 的文件里 grep 到）。",
        "- sha：用 §0 的 source_head_sha；若为 PENDING 则按其中命令解析后写实际 sha；"
        "解析失败写 null 并在 claim.notes 注明「sha 未解析」。禁止编造 sha。",
        "- 数值型 claim 必须带数值 + 对标基准 + 误差项，并显式回答「是否 <3%」；无基准须说明原因。",
        "- 负知识（falsification）单：必须写明「哪条假设被否证 + 反证工件路径 + 复现命令」，"
        "不得写成经验总结。",
        f"- 输出：{SEDIMENT_DIR}/m{mid}/claim-*.json、evidence-*.json、provenance-*.json + INDEX.md"
        "（每条一行：claim id / statement 摘要 / path:line / sha）；JSON 必须过 jsonschema 校验。",
        "- 平台仓零领域语义铁律照旧：域内容只落 TensorKnowledge，不得反向硬编码进平台仓。",
        "",
        "## 3 交付验收（机器可检）",
        f"- [ ] {SEDIMENT_DIR}/m{mid}/ 下 ≥{min_claims} 组 claim/evidence/provenance JSON，"
        "全部通过 jsonschema 校验（列出校验命令与输出）",
        "- [ ] 每条 evidence 的 path:line 能按 sha 复现引句（附 grep 命令 + 命中行输出）",
        "- [ ] INDEX.md 与 JSON 内容一致（条目数、ID、locator 逐项一致）",
        "- [ ] 全部记录 epistemic_status=draft，无 review-event、无 promoted 状态（生产者不自审）",
        "",
        "## 4 参考（在目标仓内）",
        f"- {FOUR_CONTRACTS[0][0]} 等四份 schema；ke/ 最小闭环（ingest/query/validate）可直接复用做校验",
        f"- 源单 result 原文可用 API 复核：GET /api/v1/tensor/missions/{mid}",
    ]
    return "\n".join(lines)[:11900]


# ───────────────────────────── API 客户端 ─────────────────────────────
class PlatformAPI:
    def __init__(self, base: str | None = None, user: str | None = None,
                 password: str | None = None, timeout: int = 60):
        self.base = (base or os.environ.get("TENSOR_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.user = user or os.environ.get("TENSOR_AGENT_USER") or ""
        self.password = password or os.environ.get("TENSOR_AGENT_PASS") or ""
        self.timeout = timeout
        self._token: str | None = None

    def token(self) -> str:
        if self._token:
            return self._token
        status, data = self._raw("POST", "/auth/login",
                                 {"username": self.user, "password": self.password}, auth=False)
        if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
            raise RuntimeError(f"login failed: http {status} {str(data)[:200]}")
        self._token = str(data["access_token"])
        return self._token

    def _raw(self, method: str, path: str, body: Any = None, auth: bool = True) -> tuple[int, Any]:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if auth:
            req.add_header("Authorization", "Bearer " + self.token())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return exc.code, raw.decode(errors="replace")[:500]

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            path = path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        status, data = self._raw("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} -> {status} {str(data)[:300]}")
        return data

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self._raw("POST", path, body)


def fetch_succeeded(api: PlatformAPI, days: float, *, page: int = 200, max_pages: int = 40,
                    now: dt.datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """拉取窗口内的 succeeded 单（满窗停止：遇到 finished_at 早于 cutoff 即收手）。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    out: list[dict[str, Any]] = []
    offset = 0
    total = None
    pages = 0
    stopped = "exhausted"
    while pages < max_pages:
        data = api.get("/tensor/missions", {"status": "succeeded", "limit": page, "offset": offset})
        items = data.get("items") or []
        total = data.get("total", total)
        pages += 1
        if not items:
            stopped = "no_items"
            break
        hit_cutoff = False
        for m in items:
            ts = m.get("finished_at")
            if not ts:
                continue
            if dt.datetime.fromisoformat(ts) < cutoff:
                hit_cutoff = True
                break
            out.append(m)
        if hit_cutoff:
            stopped = "cutoff"
            break
        offset += page
        if total is not None and offset >= total:
            stopped = "total"
            break
    return out, {"cutoff": cutoff.isoformat(), "scanned_pages": pages, "stopped": stopped,
                 "total_succeeded_in_db": total, "in_window": len(out)}


# ───────────────────────────── 账本 ─────────────────────────────
class Ledger:
    """append-only 判定账本 + 派生单状态表（幂等第二道闸）。"""

    def __init__(self, ledger_dir: str | Path = LEDGER_DIR):
        self.dir = Path(ledger_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                pass
        return {"derived": {}, "rounds": 0, "last_run": None}

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def already_derived(self, mission_id: int) -> dict[str, Any] | None:
        return self.state["derived"].get(str(mission_id))

    def record(self, mission_id: int, payload: dict[str, Any]) -> None:
        self.state["derived"][str(mission_id)] = payload
        self.save()

    def append(self, path_name: str, records: Iterable[dict[str, Any]],
               rotate_mb: float | None = None) -> Path:
        path = self.dir / path_name
        if rotate_mb and path.exists() and path.stat().st_size > rotate_mb * 1024 * 1024:
            path.replace(path.with_suffix(path.suffix + ".1"))  # 只留一代，防无界膨胀
        with path.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return path


def log(event: str, level: str = "INFO", log_file: str | Path | None = LOG_FILE, **fields: Any) -> None:
    line = json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "level": level, "event": event, **fields}, ensure_ascii=False)
    sys.stderr.write(line + "\n")
    target = Path(log_file) if log_file is not None else LOG_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ───────────────────────────── 派单 ─────────────────────────────
DISPATCH_BUDGET = {"max_tool_calls": 60, "max_wall_seconds": 5400, "max_tokens": 2_000_000}


def dispatch(api: PlatformAPI | None, mission: dict[str, Any], decision: Decision, *,
             dry_run: bool = False, min_claims: int = MIN_CLAIMS) -> dict[str, Any]:
    """派提取子单（幂等：服务端 task_key 去重；本函数不重试、不吞错）。"""
    key = task_key_for(decision.mission_id)
    body = {
        "goal": build_goal(mission, decision, min_claims=min_claims),
        "repo": TARGET_REPO,
        "base_ref": "main",
        "runner": "openhands",
        "origin": "agent",
        "task_key": key,
        "budget": dict(DISPATCH_BUDGET),
    }
    if dry_run or api is None:
        return {"task_key": key, "status": "dry_run", "mission_id": None, "goal_chars": len(body["goal"])}
    status, data = api.post("/tensor/missions", body)
    if status in (200, 201):
        return {"task_key": key, "status": "derived", "mission_id": data.get("id"),
                "idempotent": bool(data.get("idempotent")), "goal_chars": len(body["goal"])}
    if status == 409:
        return {"task_key": key, "status": "conflict", "error": str(data)[:200]}
    return {"task_key": key, "status": f"http_{status}", "error": str(data)[:300]}


# ───────────────────────────── scan / replay ─────────────────────────────
def run_round(api: PlatformAPI | None, *, days: float = DEFAULT_DAYS,
              max_per_run: int = DEFAULT_MAX_PER_RUN, dry_run: bool = False,
              ledger: Ledger | None = None, log_file: str | Path | None = LOG_FILE,
              missions: list[dict[str, Any]] | None = None,
              min_claims: int = MIN_CLAIMS, daily_cap: int = DEFAULT_DAILY_CAP,
              per_repo_daily: int = DEFAULT_PER_REPO_DAILY) -> dict[str, Any]:
    """一轮：取窗 → 逐条判定 → 按知识面价值排序派单（幂等 + 配额）。

    守护是**进水管**不是洪水：判定（hit）与派单（derive）分离——命中全部落账，
    派单受三道配额约束（本轮上限 / 当日总上限 / 单仓当日上限），被挡下的记 deferred，
    下一轮按价值序继续消化。这样积压可见（不藏），核爆炸不会发生（有闸）。
    """
    ledger = ledger or Ledger()
    if missions is None:
        if api is None:
            raise ValueError("missions or api required")
        missions, window = fetch_succeeded(api, days)
    else:
        window = {"cutoff": None, "scanned_pages": 0, "stopped": "injected",
                  "total_succeeded_in_db": None, "in_window": len(missions)}
    decisions = [classify(m) for m in missions]

    pending = [d for d in decisions if d.hit and not ledger.already_derived(d.mission_id)]
    already = [d.mission_id for d in decisions if d.hit and ledger.already_derived(d.mission_id)]
    pending.sort(key=lambda d: (_family_order(d.families), -d.positive_score, -d.mission_id))
    by_id = {int(m.get("id") or 0): m for m in missions}

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    used_today = _derived_on(ledger, today)
    repo_used = {r: used_today["repos"].get(r, 0) for r in set(used_today["repos"])}
    quota = max(0, min(max_per_run, daily_cap - used_today["total"]))

    dispatched: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for d in pending:
        if len(dispatched) >= quota:
            deferred.append({"mission_id": d.mission_id, "families": d.families,
                             "why": "round_quota"})
            continue
        if repo_used.get(d.repo, 0) >= per_repo_daily:
            deferred.append({"mission_id": d.mission_id, "families": d.families,
                             "why": "repo_daily_quota"})
            continue
        res = dispatch(api, by_id[d.mission_id], d, dry_run=dry_run, min_claims=min_claims)
        res.update({"source_mission_id": d.mission_id, "source_repo": d.repo,
                    "families": d.families, "positive_score": d.positive_score,
                    "noise_score": d.noise_score, "reason": d.reason,
                    "anchors": d.anchors,
                    "round_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        dispatched.append(res)
        if res["status"] == "derived":
            repo_used[d.repo] = repo_used.get(d.repo, 0) + 1
        if not dry_run and res["status"] == "derived":
            ledger.record(d.mission_id, {
                "task_key": res["task_key"], "extraction_mission_id": res["mission_id"],
                "families": d.families, "derived_at": res["round_at"], "source_repo": d.repo,
                "head_sha": d.anchors.get("head_sha"), "sha_source": d.anchors.get("sha_source")})
        log("derive", mission_id=d.mission_id, task_key=res["task_key"], status=res["status"],
            extraction_mission_id=res.get("mission_id"), families=d.families, log_file=log_file)

    skipped = [d.to_dict() for d in decisions if not d.hit]
    summary = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "days": days, "dry_run": dry_run, "window": window,
        "quota": {"round_cap": max_per_run, "daily_cap": daily_cap,
                  "per_repo_daily": per_repo_daily, "used_today_before": used_today["total"],
                  "granted_this_round": quota},
        "scanned": len(decisions),
        "hits": len(pending) + len(already),
        "new_hits": len(pending),
        "already_derived": len(already),
        "derived": sum(1 for r in dispatched if r["status"] == "derived"),
        "dry_run_decisions": sum(1 for r in dispatched if r["status"] == "dry_run"),
        "failed": sum(1 for r in dispatched if r["status"] not in ("derived", "dry_run")),
        "deferred_over_cap": len(deferred),
        "family_counts": _family_counts(decisions),
        "skip_reason_counts": _skip_counts(skipped),
        "dispatched": dispatched,
        "deferred": deferred,
    }
    # 判定账只记「本轮新见」的单（按 mission id 高水位），否则 5min 守护每轮重写整个
    # 7 天窗口 → decisions.jsonl 每小时膨胀十几 MB（实测 3 轮 59MB）。高水位 + 轮转。
    high = int(ledger.state.get("seen_max_id") or 0)
    fresh = [dict(d.to_dict(), at=summary["at"]) for d in decisions if d.mission_id > high]
    if decisions:
        ledger.state["seen_max_id"] = max([d.mission_id for d in decisions] + [high])
    summary["decisions_logged"] = len(fresh)
    ledger.append("decisions.jsonl", fresh, rotate_mb=DECISIONS_ROTATE_MB)
    ledger.append("dispatched.jsonl", dispatched)
    ledger.state["rounds"] = int(ledger.state.get("rounds", 0)) + 1
    ledger.state["last_run"] = summary["at"]
    ledger.state["last_summary"] = {k: v for k, v in summary.items() if k not in ("dispatched", "deferred")}
    ledger.save()
    if not dry_run:
        stamp = summary["at"].replace(":", "").replace("-", "")
        (ledger.dir / f"round_{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    log("round", scanned=summary["scanned"], derived=summary["derived"],
        already=summary["already_derived"], deferred=len(deferred), dry_run=dry_run, log_file=log_file)
    return summary


def _family_order(families: Sequence[str]) -> int:
    return min((FAMILIES.get(f, {}).get("order", 99) for f in families), default=99)


def _derived_on(ledger: Ledger, day: str) -> dict[str, Any]:
    """当日已派生计数（总 + 按源仓）——配额账。"""
    total = 0
    repos: dict[str, int] = {}
    for rec in ledger.state.get("derived", {}).values():
        if str(rec.get("derived_at") or "").startswith(day):
            total += 1
            repo = str(rec.get("source_repo") or "")
            repos[repo] = repos.get(repo, 0) + 1
    return {"total": total, "repos": repos}


def _family_counts(decisions: Sequence[Decision]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in decisions:
        for f in d.families:
            out[f] = out.get(f, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _skip_counts(skipped: Sequence[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in skipped:
        reason = str(d.get("reason") or "")
        key = reason.split(":", 1)[0] or "unknown"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ───────────────────────────── 抽样核验 ─────────────────────────────
def verify_derived(api: PlatformAPI, ledger: Ledger, sample: int = 5) -> dict[str, Any]:
    """抽样核验派生提取单的「证据可回溯性」——防伪造（六项机器检查）。"""
    derived = [(int(mid), rec) for mid, rec in ledger.state.get("derived", {}).items()
               if rec.get("extraction_mission_id")]
    if not derived:
        return {"sample": 0, "items": [], "all_pass": False, "note": "no derived missions in ledger"}
    derived.sort(key=lambda kv: -kv[0])
    items: list[dict[str, Any]] = []
    for src_mid, rec in derived[:sample]:
        item: dict[str, Any] = {"source_mission_id": src_mid,
                                "extraction_mission_id": rec.get("extraction_mission_id"),
                                "task_key": rec.get("task_key"), "checks": {}, "failures": []}
        try:
            child = api.get(f"/tensor/missions/{rec['extraction_mission_id']}")
            parent = api.get(f"/tensor/missions/{src_mid}")
        except RuntimeError as exc:
            item["checks"] = {k: False for k in _VERIFY_CHECKS}
            item["failures"] = [f"api: {exc}"]
            items.append(item)
            continue
        goal = str(child.get("goal") or "")
        parent_text = _parent_search_text(parent)
        checks = {
            "source_id_cited": (f"source_mission_id: {src_mid}" in goal
                                and task_key_for(src_mid) in goal),
            "repo_cited": bool(parent.get("repo")) and str(parent.get("repo")) in goal,
            "files_traceable": _files_traceable(goal, parent),
            "sha_present": _sha_present(goal),
            "four_contracts_present": _four_contracts_present(goal),
            "quotes_verbatim": _quotes_verbatim(goal, parent_text),
        }
        item["checks"] = checks
        item["failures"] = [k for k, v in checks.items() if not v]
        items.append(item)
    return {"sample": len(items), "items": items,
            "all_pass": all(not it["failures"] for it in items),
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def _parent_search_text(parent: dict[str, Any]) -> str:
    """核验用的父单检索文本 = 字段值拼接（与 _result_text 同口径）。

    不能用 json.dumps：JSON 语法（``", "checks": "``）会把跨字段引句切断，
    造成「忠实引文被判伪造」的假阴性。
    """
    files = " ".join(str(x) for x in ((parent.get("result") or {}).get("changed_files") or []))
    return "\n".join([_result_text(parent), str(parent.get("goal") or ""), files])


def _files_traceable(goal: str, parent: dict[str, Any]) -> bool:
    """goal 里引用的每个文件路径都必须逐字出现在父单 result 里（不得臆造路径）。"""
    cited = set(re.findall(r"^> \[file\] (\S+)$", goal, flags=re.MULTILINE))
    if not cited:
        return False
    blob = json.dumps(parent.get("result") or {}, ensure_ascii=False)
    return all(p in blob for p in cited)


def _sha_present(goal: str) -> bool:
    m = re.search(r"^- source_head_sha: (.+)$", goal, flags=re.MULTILINE)
    if not m:
        return False
    val = m.group(1).strip()
    return bool(_SHA_RE.match(val)) or ("PENDING" in val and "rev-parse" in goal)


def _four_contracts_present(goal: str) -> bool:
    has_schemas = all(path in goal for path, _ in FOUR_CONTRACTS)
    draft = "epistemic_status=draft" in goal or 'epistemic_status="draft"' in goal
    no_self_review = "生产者不得自审" in goal or "不得自审" in goal
    min_claims = re.search(r"≥\s*(\d+)\s*条四契约知识", goal) is not None
    return bool(has_schemas and draft and no_self_review and min_claims)


def _quotes_verbatim(goal: str, parent_text: str) -> bool:
    """goal 中「命中证据摘录」的每一条都必须能在父单 result 里逐字找到（防伪造引文）。"""
    quotes = re.findall(r"^> \[(?:falsification|verdict|root_cause|benchmark_tooling|"
                        r"physical_validation|calibration|headline|checks)\] (.+)$", goal,
                        flags=re.MULTILINE)
    if not quotes:
        return False
    norm_parent = re.sub(r"\s+", " ", parent_text)
    ok = 0
    for q in quotes:
        q_norm = re.sub(r"\s+", " ", q).strip()
        core = q_norm[:48]
        if core and core in norm_parent:
            ok += 1
    return ok == len(quotes)


# ───────────────────────────── 报告 ─────────────────────────────
def render_report(summary: dict[str, Any], verify: dict[str, Any] | None,
                  ledger: Ledger, *, title: str = "被动进水口 sediment_line 回放验收") -> str:
    w = summary.get("window") or {}
    lines = [f"# {title}", "",
             f"- 生成时间：{summary.get('at')}",
             f"- 窗口：近 {summary.get('days')} 天（cutoff {w.get('cutoff')}，停止原因 {w.get('stopped')}，"
             f"扫描页 {w.get('scanned_pages')}）",
             f"- 扫描 succeeded 单：**{summary.get('scanned')}**",
             f"- 命中过滤器：**{summary.get('hits')}**（本轮新增可派 {summary.get('new_hits')}，"
             f"已派生过 {summary.get('already_derived')}）",
             f"- 实际派生提取单：**{summary.get('derived')}**"
             f"（dry-run 判定 {summary.get('dry_run_decisions')}，失败 {summary.get('failed')}，"
             f"超 cap 顺延 {summary.get('deferred_over_cap')}）",
             "", "## 命中知识族分布", "",
             "| 知识族 | 单数 |", "|---|---|"]
    for fam, cnt in (summary.get("family_counts") or {}).items():
        lines.append(f"| {fam} | {cnt} |")
    lines += ["", "## 未收原因分布（防噪证据）", "", "| 原因 | 单数 |", "|---|---|"]
    for reason, cnt in (summary.get("skip_reason_counts") or {}).items():
        lines.append(f"| {reason} | {cnt} |")
    if verify:
        lines += ["", f"## 抽样核验（{verify.get('sample')} 张派生单，六项机器检查）", "",
                  f"- 全通过：**{verify.get('all_pass')}**"]
        for it in verify.get("items", []):
            mark = "PASS" if not it["failures"] else "FAIL:" + ",".join(it["failures"])
            lines.append(f"- m{it['source_mission_id']} → 提取单 #{it['extraction_mission_id']}"
                         f"（{it['task_key']}）：{mark}")
    lines += ["", "## 派生清单（前 25 条）", "",
              "| 源单 | 仓 | 知识族 | 提取单 | task_key |", "|---|---|---|---|---|"]
    for r in (summary.get("dispatched") or [])[:25]:
        lines.append(f"| m{r['source_mission_id']} | {r['source_repo']} | "
                     f"{','.join(r['families'])} | {r.get('mission_id') or r['status']} | {r['task_key']} |")
    lines += ["", f"- 账本：`{ledger.dir}/state.json`（派生、幂等键）、`dispatched.jsonl`、"
              "`decisions.jsonl`（含每条 skip 的理由）"]
    return "\n".join(lines) + "\n"


# ───────────────────────────── CLI ─────────────────────────────
def _ledger(args: argparse.Namespace) -> Ledger:
    return Ledger(args.ledger_dir or LEDGER_DIR)


def cmd_scan(args: argparse.Namespace) -> int:
    api = PlatformAPI(args.api_base, args.user, args.password)
    summary = run_round(api, days=args.days, max_per_run=args.max_per_run,
                        dry_run=args.dry_run, ledger=_ledger(args), log_file=args.log_file,
                        daily_cap=args.daily_cap, per_repo_daily=args.per_repo_daily)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("dispatched", "deferred")},
                     ensure_ascii=False, indent=1))
    return 0 if summary["failed"] == 0 else 1


def cmd_replay(args: argparse.Namespace) -> int:
    api = PlatformAPI(args.api_base, args.user, args.password)
    ledger = _ledger(args)
    summary = run_round(api, days=args.days, max_per_run=args.max_per_run,
                        dry_run=args.dry_run, ledger=ledger, log_file=args.log_file,
                        daily_cap=args.daily_cap, per_repo_daily=args.per_repo_daily)
    verify = None
    if not args.dry_run and args.sample > 0:
        verify = verify_derived(api, ledger, sample=args.sample)
    out_dir = Path(args.out_dir) if args.out_dir else (REPO_ROOT / "artifacts" / "sediment")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["at"][:10]
    (out_dir / f"replay_{stamp}.json").write_text(
        json.dumps({"round": summary, "verify": verify}, ensure_ascii=False, indent=1))
    report = render_report(summary, verify, ledger)
    (out_dir / f"replay_{stamp}.md").write_text(report)
    sys.stdout.write(report)
    ok = summary["derived"] >= args.require_derived and (verify is None or verify["all_pass"])
    if not ok:
        sys.stderr.write(f"\n[replay] 验收未达：derived={summary['derived']} "
                         f"(require {args.require_derived}), verify_all_pass="
                         f"{(verify or {}).get('all_pass')}\n")
    return 0 if ok else 2


def cmd_verify(args: argparse.Namespace) -> int:
    api = PlatformAPI(args.api_base, args.user, args.password)
    ledger = _ledger(args)
    result = verify_derived(api, ledger, sample=args.sample)
    out = Path(args.out or (REPO_ROOT / "artifacts" / "sediment" / "verify.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["all_pass"] else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="被动进水口 sediment_line：γ 常设守护")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = dict()
    for name, fn, helptext in (("scan", cmd_scan, "常设守护主体：判定 + 派单"),
                               ("replay", cmd_replay, "近 N 天回放验收"),
                               ("verify", cmd_verify, "抽样核验证据可回溯性")):
        sp = sub.add_parser(name, help=helptext)
        sp.set_defaults(func=fn)
        sp.add_argument("--days", type=float, default=DEFAULT_DAYS)
        sp.add_argument("--max-per-run", dest="max_per_run", type=int, default=DEFAULT_MAX_PER_RUN)
        sp.add_argument("--daily-cap", dest="daily_cap", type=int, default=DEFAULT_DAILY_CAP)
        sp.add_argument("--per-repo-daily", dest="per_repo_daily", type=int,
                        default=DEFAULT_PER_REPO_DAILY)
        sp.add_argument("--dry-run", dest="dry_run", action="store_true")
        sp.add_argument("--api-base", dest="api_base", default=None)
        sp.add_argument("--user", default=None)
        sp.add_argument("--password", default=None)
        sp.add_argument("--ledger-dir", dest="ledger_dir", default=None)
        sp.add_argument("--log-file", dest="log_file", default=str(LOG_FILE))
        sp.add_argument("--sample", type=int, default=5)
        sp.add_argument("--out-dir", dest="out_dir", default=None)
        sp.add_argument("--out", default=None)
        sp.add_argument("--require-derived", dest="require_derived", type=int, default=20)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())