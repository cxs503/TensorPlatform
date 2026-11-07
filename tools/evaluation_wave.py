"""评价波执行器（全量独立评价波）：把「待评价记录流」推成「已判定的价值分布」。

背景（为什么存在）
------------------
迁移 103 给了 S×E 的**记录流与判据执行器**（``tools/outcome_evaluations.py``：``decide()``
硬判据 + ``summarize()`` 分层统计），但记录流不会自己跑：实测窗口 91 行 =
60 pending + 30 ``auto_scan`` + 1 independent，判级实质近乎为零。
本模块是缺失的执行器：按窗口取全部 succeeded mission → 分批（≤60）→ 派多席位
（席位 ≠ 贡献者席位、证据通道族两两不同）→ 逐单按**实际价值**判 S 级与证据级 →
经 ``decide()`` 硬判据落 ``confirmed``/``candidate`` 行 → 出覆盖率/独立性/理由长度/抽查
四类判据数字，并把统计反向落成派发梯度（准入/预算/优先级）。

三个不可让步的口径
------------------
1. **判级靠证据计数，不靠自述**：S2 必须能在证据文本里找到「基线标记 + 数字」片段；S3 必须
   能在能力增量账本里找到**非空工件登记**；S4/S5 必须有突破声明/新颖性检索范围，且程序化席位
   **不能确认**（封顶 candidate）。理由文本里的数字与证据抽取同源 → ``verify_citations`` 可逐字复核。
2. **诊断与自述分离**：本模块不产出 ``auto_scan`` 行；程序化席位一律标
   ``seat_class='programmatic'``、``cross_model_verified=false``，绝不把程序判级冒充异构模型复核。
3. **统计不许加权**：S1–S5 不折算成单一分数或主体排名（§7.1）。引导回路只用**词典序规则**
   （先看有无 S3+，再看 S2+，再看 pending 率）给离散档位，没有可被优化的加权和。

零领域语义
----------
本模块与夹具不含学科词：marker 词表只含计量与治理词汇（基线/baseline/passed/工件/检索范围/
突破声明/证伪）。领域内容全部由数据注入（mission.goal / result / report.markdown）。

边界（如实写在代码里，不粉饰）
------------------------------
- 席位是**程序化 rubric 实例**（同进程、确定性、可重放），不是异构大模型/人工同行。
  「模型族交叉」这一条本单**未达成**：贡献者族取自 ``mission.model``，席位侧无真实推理族
  → ``cross_model_verified=false`` 并单列统计；S4/S5/S-N 因而一律封顶 candidate。
- 工件存在性核验只对**本地可检出仓**（本 worktree = TensorPlatform）生效；跨仓路径记
  ``unverified``，不当成已核实。
- 判级只吃窗口内平台事实，不做外部复现，因此 E4 永不出现。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import sys as _sys

if "tools" not in _sys.modules:  # 直接以脚本运行（python3 tools/evaluation_wave.py）时补根路径
    _root = str(Path(__file__).resolve().parents[1])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

from tools.outcome_evaluations import (  # noqa: E402
    E_LEVELS,
    REASON_MIN_CHARS,
    S_NEGATIVE,
    artifact_id_for_mission,
    decide,
)

# ─────────────────────────────── 常量 ───────────────────────────────

WAVE_WINDOW_HOURS = 72
MAX_BATCH = 60
DEFAULT_BATCH = 60
WAVE_SCHEMA = "evaluation_wave.v1"

#: 程序化席位可确认的最高等级/证据级（S4/S5 的程序化席位一律封顶 candidate）
SEAT_GRADE_CAP = "S3"
SEAT_E_CAP = "E3"

GRADE_RANK: dict[str, int] = {"S-N": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}
E_RANK: dict[str, int] = {e: i for i, e in enumerate(E_LEVELS)}

#: 计量/治理词汇（非领域词）：判定证据文本里是否存在可核对的对照数字
BASELINE_MARKERS: tuple[str, ...] = (
    "基线", "baseline", "对照", "相对偏差", "误差", "偏差", "±", "+/-",
    "delta", "Δ", " vs ", "respectively", "改进", "退化",
)
BREAKTHROUGH_MARKERS: tuple[str, ...] = ("突破", "breakthrough", "首次", "first-ever", "首例")
NOVELTY_MARKERS: tuple[str, ...] = ("新颖性", "检索范围", "novelty", "prior art", "文献检索", "查新")
FALSIFIED_MARKERS: tuple[str, ...] = ("证伪", "否弃", "falsified", "hypothesis refuted", "反例成立")
REPLICATION_MARKERS: tuple[str, ...] = ("独立复核", "独立复算", "replicated", "reproduced", "第三方复现")

COMMAND_MARKERS: tuple[str, ...] = (
    "pytest", "python", "python3", "ruff", "mypy", "make ", "npm ", "cargo ", "ctest",
    "go test", "psql", "curl ", "./scripts/", "bash ", "node ",
)
#: 可核工件路径前缀白名单（避免把句子里的普通词当路径）
ARTIFACT_DIRS: tuple[str, ...] = (
    "artifacts", "docs", "tests", "tests_broken_ice", "scripts", "benchmarks", "tools",
    "runs", "src", "examples", "papers", "backend", "frontend", "schemas", "eval_set",
)
_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_PATH_RE = re.compile(
    r"(?:^|[\s`'\"(\[])((?:%s)/[A-Za-z0-9_./\-]+\.(?:py|json|md|sql|png|csv|ya?ml|toml|txt|svg|html|js|ts|sh))"
    % "|".join(ARTIFACT_DIRS)
)
_PASS_RE = re.compile(r"(\d+)\s*(?:passed|通过)")
_FAIL_RE = re.compile(r"(\d+)\s*(?:failed|失败)")


def _norm_len(text: str) -> int:
    """理由长度：去掉全部空白后计字符（与迁移 103 判据同口径，防空格凑数）。"""
    return len("".join(str(text or "").split()))


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def canonical_actor(name: Any) -> str:
    """贡献者/席位身份归一（小写、去空白、去 @ 后缀）。"""
    out = "".join(str(name or "").split()).lower()
    return out.split("@")[0] if "@" in out else out


def family_of_model(model: Any) -> str:
    """任务申报模型 → 模型族（只取字母前缀，版本号/后缀丢弃）。"""
    raw = canonical_actor(model)
    if not raw:
        return "unknown"
    m = re.match(r"[a-z]+", raw)
    return m.group(0) if m else raw


# ─────────────────────────────── 席位 ───────────────────────────────


@dataclass(frozen=True)
class Seat:
    """评价席位：一条独立证据通道 + 一套 rubric（阈值差异写在 rubric 里）。

    ``channel_family`` 是**证据通道族**（报告/执行/账本/对抗），不是模型族。真实异构模型席位
    需要 ``model_family`` 非空且 ``seat_class='model_backed'``；本单席位都是程序化实例，
    故 ``model_family=None``、``seat_class='programmatic'``。
    """

    seat_id: str
    channel_family: str
    rubric: str
    seat_class: str = "programmatic"
    model_family: str | None = None

    @property
    def cross_family_capable(self) -> bool:
        return self.seat_class == "model_backed" and bool(self.model_family)


SEAT_POOL: tuple[Seat, ...] = (
    Seat("seat-delivery", "report_delivery", "delivery"),
    Seat("seat-execution", "execution_checks", "execution"),
    Seat("seat-ledger", "artifact_ledger", "ledger"),
    Seat("seat-adversarial", "adversarial", "adversarial"),
)
SEAT_BY_ID: dict[str, Seat] = {s.seat_id: s for s in SEAT_POOL}


@dataclass(frozen=True)
class Assignment:
    mission_id: int
    seat_ids: tuple[str, ...]
    seat_families: tuple[str, ...]
    contributor_seat: str
    contributor_family: str
    families_distinct: bool
    cross_model_verified: bool
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "seat_ids": list(self.seat_ids),
            "seat_families": list(self.seat_families),
            "contributor_seat": self.contributor_seat,
            "contributor_family": self.contributor_family,
            "families_distinct": self.families_distinct,
            "cross_model_verified": self.cross_model_verified,
            "rationale": self.rationale,
        }


def contributor_seat_of(mission: Mapping[str, Any]) -> str:
    who = mission.get("claimed_by") or mission.get("worker_id") or mission.get("agent_id") or ""
    return f"contributor:{canonical_actor(who) or 'unattributed'}"


def assign_seats(
    mission: Mapping[str, Any],
    *,
    pool: Sequence[Seat] = SEAT_POOL,
    n_seats: int = 3,
    salt: str = "",
) -> Assignment:
    """确定性地为一张 mission 派席位：席位 ≠ 贡献者、通道族两两不同。

    - 排除与贡献者同名的席位（``REVIEWER_NOT_INDEPENDENT`` 的结构性预防）。
    - 通道族两两不同：席位不得只看同一类证据（防「一个证据源说了算」）。
    - ``cross_model_verified`` 仅当全部席位 ``model_backed`` 且族 ≠ 贡献者族时为真。
    """
    mid = int(mission["id"])
    contrib_seat = contributor_seat_of(mission)
    contrib_family = family_of_model(mission.get("model"))
    eligible = [s for s in pool if s.seat_id != contrib_seat]
    if contrib_family != "unknown":
        cross = [s for s in eligible if s.model_family and s.model_family != contrib_family]
        eligible = cross or eligible
    if not eligible:  # pragma: no cover - 池恒非空
        eligible = list(pool)

    digest = hashlib.sha256(f"{mid}:{salt}".encode()).hexdigest()
    start = int(digest[:8], 16) % len(eligible)
    picked: list[Seat] = []
    families: set[str] = set()
    for i in range(len(eligible)):
        cand = eligible[(start + i) % len(eligible)]
        if cand.channel_family in families:
            continue
        picked.append(cand)
        families.add(cand.channel_family)
        if len(picked) >= max(1, int(n_seats)):
            break
    seat_ids = tuple(s.seat_id for s in picked)
    fams = tuple(s.channel_family for s in picked)
    cross_ok = bool(picked) and all(s.cross_family_capable for s in picked) and all(
        s.model_family != contrib_family for s in picked
    )
    rationale = (
        f"席位 {len(seat_ids)} 个（通道族 {'/'.join(fams)}），贡献者席位 {contrib_seat} 未被派入；"
        f"贡献者模型族={contrib_family}；cross_model_verified={cross_ok}"
    )
    return Assignment(
        mission_id=mid,
        seat_ids=seat_ids,
        seat_families=fams,
        contributor_seat=contrib_seat,
        contributor_family=contrib_family,
        families_distinct=len(set(fams)) == len(fams),
        cross_model_verified=cross_ok,
        rationale=rationale,
    )


def plan_batches(
    missions: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = DEFAULT_BATCH,
    priority_repos: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """按批次切分（每批 ≤ ``batch_size``，硬上限 ``MAX_BATCH``）。

    ``priority_repos`` 来自上一轮引导回路（S1 占比过高的仓要多看几眼）：这些仓的 mission 排到
    前面，保证「统计改变派发顺序」在本轮真的发生，而不是只写在报告里。
    """
    size = int(batch_size)
    if size < 1:
        raise ValueError("batch_size must be >= 1")
    size = min(size, MAX_BATCH)
    prio = {str(r) for r in priority_repos}
    ordered = sorted(
        missions,
        key=lambda m: (0 if str(m.get("repo")) in prio else 1, int(m["id"])),
    )
    batches: list[dict[str, Any]] = []
    for idx, start in enumerate(range(0, len(ordered), size), start=1):
        chunk = ordered[start:start + size]
        batches.append(
            {
                "batch_id": f"b{idx:03d}",
                "index": idx,
                "mission_ids": [int(m["id"]) for m in chunk],
                "size": len(chunk),
                "priority_repo_hits": sum(1 for m in chunk if str(m.get("repo")) in prio),
            }
        )
    return batches


# ─────────────────────────── 证据包与事实抽取 ───────────────────────────


@dataclass
class EvidencePacket:
    mission_id: int
    repo: str
    contributor: str
    contributor_family: str
    finished_at: str
    goal: str = ""
    result_checks: str = ""
    result_note: str = ""
    report_id: int | None = None
    report_md5: str = ""
    report_chars: int = 0
    report_figures: int = 0
    report_markdown: str = ""
    delta_present: bool = False
    delta_category: str = ""
    delta_artifacts: tuple[str, ...] = ()
    evidence_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "repo": self.repo,
            "contributor": self.contributor,
            "contributor_family": self.contributor_family,
            "finished_at": self.finished_at,
            "goal_chars": len(self.goal or ""),
            "result_checks_chars": len(self.result_checks or ""),
            "report_id": self.report_id,
            "report_md5": self.report_md5,
            "report_chars": self.report_chars,
            "report_figures": self.report_figures,
            "delta_present": self.delta_present,
            "delta_category": self.delta_category,
            "delta_artifacts": list(self.delta_artifacts),
            "evidence_chars": len(self.evidence_text or ""),
        }


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover - 兜底
        return str(value)


def build_packet(
    mission: Mapping[str, Any],
    *,
    report: Mapping[str, Any] | None = None,
    delta: Mapping[str, Any] | None = None,
) -> EvidencePacket:
    """把三张表的一行拼成证据包（纯函数，无 DB）。

    只做**形状与计数**：不解释领域语义，只统计有没有报告/md5/字符数/图数/执行命令/数字/
    对照数字片段/工件路径。判级由这些计数决定，理由文本由同一批计数拼装。
    """
    result = mission.get("result")
    checks = ""
    note = ""
    if isinstance(result, Mapping):
        checks = _as_text(result.get("checks"))
        note = _as_text(result.get("summary"))
    elif result:
        note = _as_text(result)

    md = _as_text((report or {}).get("markdown"))
    figs = (report or {}).get("figures")
    n_figs = len(figs) if isinstance(figs, (list, tuple, dict)) else 0
    artifacts = (delta or {}).get("artifacts")
    if isinstance(artifacts, Mapping):
        art_list = tuple(str(v) for v in artifacts.values())
    elif isinstance(artifacts, (list, tuple)):
        art_list = tuple(str(v) for v in artifacts)
    else:
        art_list = ()

    evidence_text = "\n".join(x for x in (md, checks, note) if x)
    finished = mission.get("finished_at")
    if isinstance(finished, datetime):
        finished = finished.replace(microsecond=0).isoformat()
    return EvidencePacket(
        mission_id=int(mission["id"]),
        repo=_clean(mission.get("repo")),
        contributor=_clean(mission.get("claimed_by") or mission.get("worker_id") or "unattributed"),
        contributor_family=family_of_model(mission.get("model")),
        finished_at=str(finished or ""),
        goal=_as_text(mission.get("goal")),
        result_checks=checks,
        result_note=note,
        report_id=(int(report["id"]) if report and report.get("id") is not None else None),
        report_md5=hashlib.md5(md.encode("utf-8")).hexdigest() if md else "",
        report_chars=len(md),
        report_figures=n_figs,
        report_markdown=md,
        delta_present=bool(delta),
        delta_category=_clean((delta or {}).get("category")),
        delta_artifacts=art_list,
        evidence_text=evidence_text,
    )


def _numbers(text: str) -> list[str]:
    return _NUM_RE.findall(text or "")


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def _tidy_snippet(text: str, limit: int = 70) -> str:
    """片段裁剪：折叠空白后按 limit 截断，并切掉被截断的**半个数字**。

    理由文本要能逐字核对回证据：如果截断点落在 ``0.03`` 中间，理由里就会出现证据文中
    并不存在的 ``03``，抽查会（正确）判为引用不实。因此这里宁可少一位数字，也不产生
    证据里没有的 token。
    """
    out = _collapse(text)
    if len(out) > limit:
        out = out[:limit]
        while out and (out[-1].isdigit() or out[-1] in ".+-"):
            out = out[:-1]
    while out and (out[0].isdigit() or out[0] in ".+-"):
        lead = re.match(r"^[0-9.+-]+", out)
        if not lead:
            break
        out = out[lead.end():].lstrip(" ")
    return out


def _marker_snippets(text: str, markers: Sequence[str], *, window: int = 48) -> list[str]:
    low = (text or "").lower()
    out: list[str] = []
    for marker in markers:
        mkl = marker.lower()
        start = 0
        while True:
            pos = low.find(mkl, start)
            if pos < 0:
                break
            lo, hi = max(0, pos - window), min(len(text), pos + len(mkl) + window)
            snippet = _collapse(text[lo:hi])
            if snippet not in out:
                out.append(snippet)
            start = pos + len(mkl)
    return out


def baseline_pairs(text: str, *, window: int = 48) -> list[str]:
    """证据文本中「基线标记 ± 窗口内数字」的片段（每段至少含一个数字，空白已折叠）。

    S2 的唯一机器判据：没有可核的对照数字，就不许判「相对基线提升」。
    """
    return [s for s in _marker_snippets(text, BASELINE_MARKERS, window=window) if _numbers(s)]


def scope_snippets(text: str, *, window: int = 60) -> list[str]:
    """新颖性检索范围片段（S5 的 ``baseline_or_scope`` 必填项，不能空着）."""
    return _marker_snippets(text, NOVELTY_MARKERS, window=window)


def baseline_comparisons(text: str, *, window: int = 48) -> list[str]:
    """更强口径：片段里**至少两个数字**才算「对照数字对」（一个数字不成对，判不了偏差）。"""
    return [s for s in baseline_pairs(text, window=window) if len(_numbers(s)) >= 2]


def count_marker(text: str, markers: Sequence[str]) -> int:
    low = (text or "").lower()
    return sum(low.count(m.lower()) for m in markers)


def executed_commands(text: str) -> list[str]:
    """执行证据：含命令标记的片段（按行/分号切），用于证明真跑过。"""
    chunks = re.split(r"[;；\n]", text or "")
    return [
        c.strip()
        for c in chunks
        if any(mk in c.lower() for mk in COMMAND_MARKERS) and len(c.strip()) > 8
    ]


def cited_artifact_paths(text: str, *, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for m in _PATH_RE.finditer(text or ""):
        p = m.group(1).strip()
        if p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    return seen


_PEER_ORIGIN_CACHE: dict[str, str | None] = {}


def _origin_of(repo_dir: Path) -> str | None:
    import subprocess

    key = str(repo_dir)
    if key in _PEER_ORIGIN_CACHE:
        return _PEER_ORIGIN_CACHE[key]
    try:
        res = subprocess.run(
            ["git", "-C", key, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=20,
        )
        val = res.stdout.strip() or None
    except Exception:  # pragma: no cover
        val = None
    _PEER_ORIGIN_CACHE[key] = val
    return val


def probe_artifacts_git(
    paths: Sequence[str], *, root: Path | None, scan_peer_worktrees: bool = True
) -> dict[str, str]:
    """存在性核验四级口径（措辞即口径，绝不把「没检出」说成「不存在」）：

    1. ``exists``               —— 本工作树里有
    2. ``exists_in_history``    —— 本仓 ``git log --all`` 历史里有（合并过/别的分支）
    3. ``exists_peer_worktree`` —— 同仓的**其它 mission 工作树**里有（该单自己的分支检出）
    4. ``not_found_local``      —— 以上都没有 → 记为「本机查无此件」（仍需人工判断是否计划件）
    """
    import subprocess

    out: dict[str, str] = {}
    if root is None or not paths:
        return out
    root = Path(root)
    peer_dirs: list[Path] = []
    if scan_peer_worktrees and root.parent.is_dir():
        peer_dirs = [d for d in root.parent.iterdir() if d.is_dir() and (d / ".git").exists()]
    own_origin = _origin_of(root)
    for p in paths:
        if (root / p).exists():
            out[p] = "exists"
            continue
        try:
            res = subprocess.run(
                ["git", "-C", str(root), "log", "--all", "--format=%h", "-1", "--", p],
                capture_output=True, text=True, timeout=20,
            )
            if res.stdout.strip():
                out[p] = "exists_in_history"
                continue
        except Exception:  # pragma: no cover - git 不可用时不下结论
            out[p] = "unverified"
            continue
        hit = None
        for peer in peer_dirs:
            if peer == root:
                continue
            if (peer / p).exists():
                hit = peer
                break
        if hit is not None and (own_origin is None or _origin_of(hit) == own_origin):
            out[p] = "exists_peer_worktree"
        else:
            out[p] = "not_found_local"
    return out


def probe_artifacts(
    paths: Sequence[str],
    *,
    root: Path | None,
    local_repo: str | None = None,
    repo: str | None = None,
) -> dict[str, str]:
    """工件存在性核验（三种取值，措辞即口径，不把「没检出」说成「不存在」）：

    - ``exists``          —— 在本地检出里找到（唯一的正面结论）
    - ``not_found_local`` —— 该仓有本地检出但路径不在其中（可能是别的分支/未合并）
    - ``unverified``      —— 该仓无本地检出，**不下结论**（跨仓路径一律落这一档）
    """
    out: dict[str, str] = {}
    usable = root is not None and (local_repo is None or local_repo == repo)
    for p in paths:
        if not usable:
            out[p] = "unverified"
        else:
            out[p] = "exists" if (Path(root) / p).exists() else "not_found_local"
    return out


@dataclass
class Facts:
    numbers: int
    baseline_pairs: tuple[str, ...]
    commands: tuple[str, ...]
    passes: int
    fails: int
    artifact_paths: tuple[str, ...]
    report_chars: int
    report_figures: int
    breakthrough: int
    novelty_scope: int
    falsified: int
    replication: int
    delta_present: bool
    delta_category: str
    delta_artifacts: int
    has_report: bool
    baseline_comparisons: tuple[str, ...] = ()

    @property
    def any_evidence(self) -> bool:
        return bool(self.has_report or self.delta_present or self.commands or self.numbers)

    @property
    def execution_ok(self) -> bool:
        return bool(self.commands) and self.passes > 0

    @property
    def delta_registered(self) -> bool:
        """S3 的账本判据：登记存在、类别不是「无实质增量」、且有非空工件。"""
        return (
            self.delta_present
            and self.delta_artifacts > 0
            and self.delta_category not in ("no_substantive_delta", "", "none")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "numbers": self.numbers,
            "baseline_pairs": len(self.baseline_pairs),
            "baseline_comparisons": len(self.baseline_comparisons),
            "commands": len(self.commands),
            "passes": self.passes,
            "fails": self.fails,
            "artifact_paths": len(self.artifact_paths),
            "report_chars": self.report_chars,
            "report_figures": self.report_figures,
            "breakthrough": self.breakthrough,
            "novelty_scope": self.novelty_scope,
            "falsified": self.falsified,
            "replication": self.replication,
            "delta_present": self.delta_present,
            "delta_category": self.delta_category,
            "delta_artifacts": self.delta_artifacts,
        }


def extract_facts(packet: EvidencePacket) -> Facts:
    text = packet.evidence_text
    paths = cited_artifact_paths(text + "\n" + "\n".join(packet.delta_artifacts))
    return Facts(
        numbers=len(_numbers(text)),
        baseline_pairs=tuple(baseline_pairs(text)),
        baseline_comparisons=tuple(baseline_comparisons(text)),
        commands=tuple(executed_commands(packet.result_checks or packet.evidence_text)),
        passes=sum(int(x) for x in _PASS_RE.findall(text)),
        fails=sum(int(x) for x in _FAIL_RE.findall(text)),
        artifact_paths=tuple(paths + [p for p in packet.delta_artifacts if p not in paths]),
        report_chars=packet.report_chars,
        report_figures=packet.report_figures,
        breakthrough=count_marker(text, BREAKTHROUGH_MARKERS),
        novelty_scope=count_marker(text, NOVELTY_MARKERS),
        falsified=count_marker(text, FALSIFIED_MARKERS),
        replication=count_marker(text, REPLICATION_MARKERS),
        delta_present=packet.delta_present,
        delta_category=packet.delta_category,
        delta_artifacts=len(packet.delta_artifacts),
        has_report=bool(packet.report_id and packet.report_chars > 0),
    )# ─────────────────────────────── 判级 ───────────────────────────────


@dataclass
class Proposal:
    grade: str
    e_level: str
    seat_id: str
    rubric: str
    flags: tuple[str, ...] = ()
    basis: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "e_level": self.e_level,
            "seat_id": self.seat_id,
            "rubric": self.rubric,
            "flags": list(self.flags),
            "basis": self.basis,
        }


def _e_level_from(facts: Facts, *, strict_artifact: bool) -> tuple[str, list[str]]:
    flags: list[str] = []
    if not facts.any_evidence:
        return "E0", ["NO_EVIDENCE"]
    e = "E1"
    reproducible = facts.has_report and facts.report_chars >= 200 and facts.execution_ok
    if reproducible:
        if facts.artifact_paths:
            e = "E2"
            flags.append("REPRO_ARTIFACT_CITED")
        else:
            flags.append("E2_NO_ARTIFACT_CITED")
    else:
        if facts.has_report:
            flags.append("REPORT_ONLY")
        if facts.commands and not facts.passes:
            flags.append("EXECUTION_WITHOUT_PASS_COUNT")
    if e == "E2" and facts.replication and facts.artifact_paths:
        e = "E3"
        flags.append("REPLICATION_MARKER")
    if strict_artifact and e == "E3" and facts.replication < 2:
        e = "E2"
        flags.append("E3_DOWNGRADED_SINGLE_REPLICATION")
    return e, flags


def assess(packet: EvidencePacket, seat: Seat, facts: Facts | None = None) -> Proposal:
    """单席位判级（确定性）。

    等级判据：
      S5  新颖性检索范围标记（``novelty_scope>0``）
      S4  突破声明（``breakthrough>0``）+ 对照基线数字 ≥2 段
      S3  **账本登记**：增量类别非 ``no_substantive_delta`` 且工件非空
      S2  对照基线数字片段 ≥2（可核）
      S1  仅执行性证据（跑过、有数），价值下限
      S-N 证伪标记 + E≥E2
    rubric 差异（席位独立性来自这里，而不是复制同一套阈值）：
      delivery     报告摘要 + 执行命令 + ≥1 工件路径才给 E2；单次复现标记降 E3→E2
      execution    无执行证据则 E 封顶 E1（不接受「只写文档」当可复现）
      ledger       S3 必须账本登记，否则降到可核基线档并打旗
      adversarial  申报「无实质增量」却主张高价值 → 压到 S2 以下；基线证据薄打旗
    """
    f = facts or extract_facts(packet)
    flags: list[str] = []
    e_level, e_flags = _e_level_from(f, strict_artifact=seat.rubric == "delivery")
    flags.extend(e_flags)

    if seat.rubric == "execution" and not f.execution_ok and E_RANK[e_level] > E_RANK["E1"]:
        e_level = "E1"
        flags.append("E1_CAPPED_NO_EXECUTION")

    n_cmp = len(f.baseline_comparisons)
    s2_ok = n_cmp >= 1 and len(f.baseline_pairs) >= 2
    if f.falsified and E_RANK[e_level] >= E_RANK["E2"]:
        grade, basis = S_NEGATIVE, "证伪/否弃标记成立且 E>=E2"
    elif f.novelty_scope > 0:
        grade, basis = "S5", f"新颖性检索范围标记 {f.novelty_scope} 处"
    elif f.breakthrough > 0 and n_cmp >= 1:
        grade = "S4"
        basis = f"突破声明 {f.breakthrough} 处 + 对照数字对 {n_cmp} 段"
    elif f.delta_registered:
        grade = "S3"
        basis = f"增量账本登记类别 {f.delta_category}、工件 {f.delta_artifacts} 条"
    elif s2_ok:
        grade = "S2"
        basis = f"对照基线数字 {len(f.baseline_pairs)} 段（其中数字对 {n_cmp} 段）可核"
    else:
        grade = "S1"
        basis = f"仅执行性证据（命令 {len(f.commands)} 组、通过计数 {f.passes}）"

    if seat.rubric == "ledger" and grade == "S3" and not f.delta_registered:
        grade = "S2" if s2_ok else "S1"
        basis = "账本未登记，S3 不成立"
        flags.append("LEDGER_UNREGISTERED")
    if seat.rubric == "adversarial":
        if f.delta_category == "no_substantive_delta" and GRADE_RANK[grade] > GRADE_RANK["S2"]:
            flags.append("LEDGER_CONTRADICTION")
            grade = "S2" if s2_ok else "S1"
            basis = "申报无实质增量却主张高价值 → 压到可核基线档"
        if grade == "S2" and n_cmp < 2:
            flags.append("THIN_BASELINE_EVIDENCE")
    if grade == "S4" and E_RANK[e_level] < E_RANK["E2"]:
        grade = "S2" if s2_ok else "S1"
        basis = "突破声明缺 E>=E2 佐证"
        flags.append("S4_WITHOUT_REPRO")
    if grade == "S5" and not f.artifact_paths:
        flags.append("S5_NO_SCOPE_ARTIFACT")

    return Proposal(
        grade=grade,
        e_level=e_level,
        seat_id=seat.seat_id,
        rubric=seat.rubric,
        flags=tuple(flags),
        basis=basis,
    )


@dataclass
class Consensus:
    grade: str
    e_level: str
    proposals: tuple[Proposal, ...]
    unanimous: bool
    agreement: str
    flags: tuple[str, ...] = ()
    #: 等级一致且 E 级差异 ≤1 → 允许 confirmed（E 取最小值，保守）；否则 candidate
    confirmable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "e_level": self.e_level,
            "unanimous": self.unanimous,
            "confirmable": self.confirmable,
            "agreement": self.agreement,
            "proposals": [p.as_dict() for p in self.proposals],
            "flags": list(self.flags),
        }


def _modal(values: Sequence[str], rank: Mapping[str, int]) -> tuple[str, int]:
    counts = Counter(values)
    return max(counts.items(), key=lambda kv: (kv[1], -rank.get(kv[0], 0)))


def reach_consensus(proposals: Sequence[Proposal]) -> Consensus:
    """多席位共识：等级取多数（无多数取更保守的一档），证据级取**最小**（保守）。

    不一致 → ``unanimous=False``，调用方据此落 ``candidate``（登记不计入分布）。
    """
    if not proposals:
        raise ValueError("no proposals")
    grades = [p.grade for p in proposals]
    grade, votes = _modal(grades, GRADE_RANK)
    if votes * 2 <= len(grades):
        grade = min(grades, key=lambda g: GRADE_RANK[g])
    e_level = min((p.e_level for p in proposals), key=lambda e: E_RANK[e])
    flags: list[str] = []
    for p in proposals:
        flags.extend(p.flags)
    e_ranks = sorted({E_RANK[p.e_level] for p in proposals})
    e_spread = e_ranks[-1] - e_ranks[0]
    unanimous = len(set(grades)) == 1 and e_spread == 0
    if len(set(grades)) != 1:
        flags.append("SEAT_DISAGREEMENT")
    elif e_spread > 1:
        flags.append("E_LEVEL_DISAGREEMENT")
    elif e_spread == 1:
        flags.append("E_LEVEL_SPREAD")
    confirmed_ok = len(set(grades)) == 1 and e_spread <= 1
    return Consensus(
        grade=grade,
        e_level=e_level,
        proposals=tuple(proposals),
        unanimous=unanimous,
        agreement=(
            f"{votes}/{len(proposals)} 席给出 {grade}（E 级 {e_level}，/"
            f"{'一致' if e_spread == 0 else f'相差 {e_spread} 级'}）"
        ),
        confirmable=confirmed_ok,
        flags=tuple(dict.fromkeys(flags)),
    )


# ─────────────────────── 理由文本与引用核对 ───────────────────────


def compose_reason(
    packet: EvidencePacket,
    facts: Facts,
    consensus: Consensus,
    assignment: Assignment,
    *,
    artifact_check: Mapping[str, str] | None = None,
) -> str:
    """程序化拼装等级理由（≥50 字符，数字全部来自证据抽取，禁空话）。"""
    ac = dict(artifact_check or {})
    exists = sum(1 for v in ac.values() if v == "exists")
    not_found = sum(1 for v in ac.values() if v == "not_found_local")
    unverified = sum(1 for v in ac.values() if v == "unverified")
    scope = "本地核验" if (exists or not_found) else "无本地检出（未核）"
    sample_src = facts.baseline_comparisons or facts.baseline_pairs
    base_sample = "；".join(_tidy_snippet(x) for x in sample_src[:2]) or "无"
    parts = [
        f"mission {packet.mission_id}（{packet.repo or 'unknown'}）于 "
        f"{packet.finished_at[:19] or '未记时间'} 完结；",
        f"报告 {'#' + str(packet.report_id) if packet.report_id else '缺'}，"
        f"md5 {packet.report_md5[:8] or '无'}，{packet.report_chars} 字符 / "
        f"{packet.report_figures} 图 / 证据文本 {facts.numbers} 个数字；",
        f"执行证据 {len(facts.commands)} 组命令，通过计数 {facts.passes}、"
        f"失败计数 {facts.fails}；",
        f"能力增量账本 类别={facts.delta_category or '未登记'}、工件 {facts.delta_artifacts} 条；",
        f"对照基线数字 {len(facts.baseline_pairs)} 段（含数字对 {len(facts.baseline_comparisons)} 段："
        f"{base_sample}）；",
        f"工件路径 {len(facts.artifact_paths)} 条，存在性 {scope}"
        f"（exists={exists}, not_found_local={not_found}, unverified={unverified}）；",
        f"→ {consensus.grade}/{consensus.e_level}，依据：{consensus.proposals[0].basis}；",
        f"席位 {'+'.join(assignment.seat_ids)}（通道族 {'/'.join(assignment.seat_families)}）",
        f"共识 {consensus.agreement}，贡献者族={assignment.contributor_family}，",
        f"cross_model_verified={assignment.cross_model_verified}。",
    ]
    reason = "".join(parts)
    if _norm_len(reason) < REASON_MIN_CHARS:  # pragma: no cover - 结构性兜底
        reason += f"（补：证据来源 mission {packet.mission_id} 的报告与执行摘要原文。）"
    return reason


def verify_citations(
    reason: str,
    packet: EvidencePacket,
    facts: Facts,
    *,
    artifact_check: Mapping[str, str] | None = None,
    extra_meta: Sequence[str] = (),
) -> dict[str, Any]:
    """抽查口径：理由里的数字与工件路径必须能对回证据（不是「看起来像」）。"""
    ev = packet.evidence_text
    ev_nums = set(_numbers(ev)) | set(_numbers(json.dumps(packet.as_dict(), ensure_ascii=False)))
    # 元数据数字（mission id / 报告 id / 完结时间 / 报告 md5 前缀）在证据正文里本就不出现，
    # 单列白名单，避免把「引用元数据」误判成「编数字」。
    meta_nums = set(_numbers(
        f"{packet.mission_id} {packet.report_id or ''} {packet.finished_at} "
        f"{packet.report_md5[:8]}"
    ))
    meta_nums.add(str(packet.mission_id))
    if packet.report_id:
        meta_nums.add(str(packet.report_id))
    counted = {
        str(len(facts.commands)), str(facts.passes), str(facts.fails),
        str(facts.delta_artifacts), str(len(facts.baseline_pairs)),
        str(facts.numbers), str(len(facts.artifact_paths)),
        str(len(facts.baseline_comparisons)),
        str(sum(1 for v in (artifact_check or {}).values() if v == "exists")),
        str(sum(1 for v in (artifact_check or {}).values() if v == "not_found_local")),
        str(sum(1 for v in (artifact_check or {}).values() if v == "unverified")),
        str(facts.report_chars), str(facts.report_figures),
        *[str(x) for x in extra_meta],
    }
    hits = 0
    misses: list[str] = []
    for n in _numbers(reason):
        norm = n.lstrip("+")
        # 三档接受：① 证据里的独立数字 token；② 证据正文的**子串**（复合表达式如 1e-9 / 第1-2节
        # 被切出的片段仍可回原文定位）；③ 可复算的计数值与元数据（mission/报告 id/时间/md5 前缀）。
        if (n in ev_nums or norm in ev_nums or n in meta_nums or n in counted
                or n in ev or norm in ev):
            hits += 1
        else:
            misses.append(n)
    path_misses = [
        p for p in facts.artifact_paths
        if p not in ev and p not in (packet.delta_artifacts or ())
    ]
    art_missing = [p for p, v in (artifact_check or {}).items()
                   if v in ("not_found_local", "missing")]
    return {
        "ok": not misses and not path_misses,
        "number_hits": hits,
        "number_misses": misses,
        "path_hits": len(facts.artifact_paths) - len(path_misses),
        "path_misses": path_misses,
        "artifact_missing": art_missing,
        "artifact_exists": sum(1 for v in (artifact_check or {}).values() if v == "exists"),
        "artifact_unverified": sum(
            1 for v in (artifact_check or {}).values() if v == "unverified"
        ),
        "checked_artifacts": len(artifact_check or {}),
    }


def build_payload(
    packet: EvidencePacket,
    facts: Facts,
    consensus: Consensus,
    assignment: Assignment,
    *,
    artifact_check: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """拼成 ``decide()`` 认得的 payload（硬判据由 103 的执行器兜底，不在此重复实现）。"""
    refs: list[dict[str, str]] = []
    if packet.report_id:
        refs.append({"kind": "report", "ref": f"tensor_reports#{packet.report_id}"})
    refs.append({"kind": "run", "ref": f"mission:{packet.mission_id}:result.checks"})
    if packet.delta_present:
        refs.append(
            {"kind": "artifact", "ref": f"tensor_capability_deltas:mission={packet.mission_id}"}
        )
    for p in facts.artifact_paths[:4]:
        refs.append({"kind": "artifact", "ref": p})

    basis = "；".join(s[:80] for s in facts.baseline_pairs[:3])
    if consensus.grade == "S5" and not basis:
        basis = "检索范围：" + "；".join(
            _tidy_snippet(x, 90) for x in scope_snippets(packet.evidence_text)[:2]
        )
    checked_pass = sum(1 for v in (artifact_check or {}).values() if v == "exists")
    boundary = (
        f"仅覆盖 mission {packet.mission_id} 的主成果 mission:{packet.mission_id}；"
        f"证据源=报告/执行摘要/增量账本；工件存在性核验 exists={checked_pass}/"
        f"{len(artifact_check or {})}（无本地检出者计 unverified）；不含外部复现，E4 不适用。"
    )
    return {
        "artifact_id": artifact_id_for_mission(packet.mission_id),
        "mission_id": packet.mission_id,
        "contributor": packet.contributor,
        "grade": consensus.grade,
        "e_level": consensus.e_level,
        "grade_reason": compose_reason(
            packet, facts, consensus, assignment, artifact_check=artifact_check
        ),
        "evidence_refs": refs,
        "reviewer": "+".join(assignment.seat_ids),
        "reviewer_kind": "independent",
        "baseline_or_scope": basis or None,
        "boundary": boundary,
        "repo": packet.repo or None,
    }


# ─────────────────────── 单行评价（纯函数入口） ───────────────────────


@dataclass
class EvaluatedRow:
    mission_id: int
    artifact_id: str
    payload: dict[str, Any]
    verdict: dict[str, Any]
    record: dict[str, Any]
    consensus: Consensus
    assignment: Assignment
    facts: Facts
    citation: dict[str, Any]
    artifact_check: dict[str, str]
    batch_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "artifact_id": self.artifact_id,
            "repo": self.payload.get("repo") or "unknown",
            "grade": self.record.get("grade"),
            "e_level": self.record.get("e_level"),
            "status": self.record.get("status"),
            "reason_chars": _norm_len(self.record.get("grade_reason") or ""),
            "seat_ids": list(self.assignment.seat_ids),
            "seat_families": list(self.assignment.seat_families),
            "cross_model_verified": self.assignment.cross_model_verified,
            "consensus": self.consensus.agreement,
            "unanimous": self.consensus.unanimous,
            "citation_ok": self.citation["ok"],
            "artifact_exists": sum(1 for v in self.artifact_check.values() if v == "exists"),
            "artifact_unverified": sum(
                1 for v in self.artifact_check.values() if v == "unverified"
            ),
            "flags": list(self.consensus.flags),
            "batch_id": self.batch_id,
        }


def evaluate_mission(
    packet: EvidencePacket,
    *,
    n_seats: int = 3,
    salt: str = "",
    artifact_root: Path | None = None,
    local_repo: str | None = None,
    batch_id: str = "",
    force_seats: Sequence[str] | None = None,
) -> EvaluatedRow:
    """一张 mission 的完整评价：派席位 → 多席判级 → 共识 → 硬判据 → 行。

    ``force_seats`` 用于抽查重放：用**同一批席位**重跑判级，验证「同证据同结论」。
    """
    assignment = assign_seats(
        {
            "id": packet.mission_id,
            "model": packet.contributor_family,
            "claimed_by": packet.contributor,
        },
        n_seats=n_seats,
        salt=salt,
    )
    if force_seats:
        assignment = Assignment(
            mission_id=assignment.mission_id,
            seat_ids=tuple(force_seats),
            seat_families=tuple(
                SEAT_BY_ID[x].channel_family for x in force_seats if x in SEAT_BY_ID
            ),
            contributor_seat=assignment.contributor_seat,
            contributor_family=assignment.contributor_family,
            families_distinct=assignment.families_distinct,
            cross_model_verified=assignment.cross_model_verified,
            rationale=assignment.rationale + "（force_seats 重放）",
        )
    facts = extract_facts(packet)
    seats = [SEAT_BY_ID[x] for x in assignment.seat_ids if x in SEAT_BY_ID]
    if not seats:  # pragma: no cover - 池恒非空
        seats = list(SEAT_POOL[:n_seats])
    proposals = [assess(packet, s, facts) for s in seats]
    consensus = reach_consensus(proposals)

    artifact_check = probe_artifacts(
        facts.artifact_paths, root=artifact_root, local_repo=local_repo, repo=packet.repo
    )
    # 工件核验回灌判级：报告引用的工件**在本仓检出与历史里都查无此件**时，不得主张「可复现工件」
    # → 证据级封顶 E1 并留旗（这是「抽查与工件对不上」能被判级吸收的唯一正确位置）。
    checked = {p: v for p, v in artifact_check.items() if v != "unverified"}
    if checked and all(v == "not_found_local" for v in checked.values()):
        if E_RANK[consensus.e_level] > E_RANK["E1"]:
            consensus.e_level = "E1"
        consensus.flags = tuple(dict.fromkeys(list(consensus.flags) + ["ARTIFACT_NOT_FOUND"]))
    payload = build_payload(packet, facts, consensus, assignment, artifact_check=artifact_check)
    verdict = decide(payload)
    record: dict[str, Any] = {
        "artifact_id": payload["artifact_id"],
        "mission_id": packet.mission_id,
        "contributor": packet.contributor,
        "grade": verdict.grade,
        "e_level": verdict.e_level,
        "grade_reason": payload["grade_reason"],
        "evidence_refs": payload["evidence_refs"],
        "reviewer": payload["reviewer"],
        "reviewer_kind": "independent",
        "review_status": "reviewed",
        "baseline_or_scope": payload["baseline_or_scope"],
        "boundary": payload["boundary"],
        "status": verdict.status,
        "reason_codes": list(verdict.codes),
        "reason_source": "rule_derived",
        "repo": packet.repo or None,
        "organization_id": None,
        "window_finished_at": packet.finished_at or None,
    }
    if verdict.status == "confirmed" and not consensus.confirmable:
        record["status"] = "candidate"
        record["reason_codes"] = list(record["reason_codes"]) + ["CONSENSUS_NOT_CONFIRMABLE"]
    # 程序化席位封顶：S4/S5/S-N 一律 candidate（不允许自证「突破/新颖」）
    if (
        verdict.status == "confirmed"
        and (
            GRADE_RANK.get(record["grade"] or "", 0) > GRADE_RANK[SEAT_GRADE_CAP]
            or record["grade"] == S_NEGATIVE
        )
        and not assignment.cross_model_verified
    ):
        record["status"] = "candidate"
        record["reason_codes"] = list(record["reason_codes"]) + ["MACHINE_SEAT_CAP"]
    citation = verify_citations(
        payload["grade_reason"], packet, facts, artifact_check=artifact_check,
        extra_meta=[str(len(assignment.seat_ids))],
    )
    if not citation["ok"] and record["status"] == "confirmed":
        record["status"] = "candidate"
        record["reason_codes"] = list(record["reason_codes"]) + ["CITATION_UNVERIFIED"]
    return EvaluatedRow(
        mission_id=packet.mission_id,
        artifact_id=payload["artifact_id"],
        payload=payload,
        verdict={"status": verdict.status, "codes": list(verdict.codes)},
        record=record,
        consensus=consensus,
        assignment=assignment,
        facts=facts,
        citation=citation,
        artifact_check=artifact_check,
        batch_id=batch_id,
    )


# ─────────────────────────────── 指标 ───────────────────────────────


def reason_length_distribution(reasons: Sequence[str]) -> dict[str, Any]:
    lens = sorted(_norm_len(r) for r in reasons)
    if not lens:
        return {"n": 0}

    def pct(p: float) -> int:
        idx = min(len(lens) - 1, max(0, int(round((len(lens) - 1) * p))))
        return lens[idx]

    buckets = {"50-79": 0, "80-119": 0, "120-199": 0, "200+": 0}
    for n in lens:
        if n < 80:
            buckets["50-79"] += 1
        elif n < 120:
            buckets["80-119"] += 1
        elif n < 200:
            buckets["120-199"] += 1
        else:
            buckets["200+"] += 1
    return {
        "n": len(lens),
        "min": lens[0],
        "p10": pct(0.10),
        "median": pct(0.50),
        "p90": pct(0.90),
        "max": lens[-1],
        "mean": round(sum(lens) / len(lens), 2),
        "min_required": REASON_MIN_CHARS,
        "below_required": sum(1 for n in lens if n < REASON_MIN_CHARS),
        "buckets": buckets,
        "share_ge_120": round(sum(1 for n in lens if n >= 120) / len(lens), 4),
    }


def coverage_stats(window_missions: Sequence[int], rows: Sequence[EvaluatedRow]) -> dict[str, Any]:
    """覆盖率口径：窗口 mission 中被判级（confirmed+candidate）/ 窗口总数。

    ``pending``（E0 无证据）与 ``rejected`` 都算**未判级**——不把「判过了但被拒」算成覆盖。
    """
    total = len(set(int(m) for m in window_missions))
    decided = {
        r.mission_id for r in rows if r.record["status"] in ("confirmed", "candidate")
    }
    status_counts = Counter(r.record["status"] for r in rows)
    return {
        "missions_in_window": total,
        "missions_evaluated": len(decided),
        "coverage": round(len(decided) / total, 4) if total else 0.0,
        "by_status": dict(status_counts),
        "undecided": sorted(set(int(m) for m in window_missions) - decided)[:50],
        "undecided_count": len(set(int(m) for m in window_missions) - decided),
    }


def independence_stats(rows: Sequence[EvaluatedRow]) -> dict[str, Any]:
    """独立性口径（两条都记，不互相冒充）：

    - ``seat_independent``：评价席位 ≠ 贡献者席位，通道族两两不同（结构独立，本单可核）。
    - ``cross_model_verified``：以上 **且** 席位是真实异构模型族（本单为 0，如实报告）。
    """
    n = len(rows)
    if not n:
        return {"n": 0}
    seat_ok = sum(
        1
        for r in rows
        if all(s != r.assignment.contributor_seat for s in r.assignment.seat_ids)
        and r.assignment.families_distinct
    )
    model_ok = sum(1 for r in rows if r.assignment.cross_model_verified)
    by_family = Counter(r.assignment.contributor_family for r in rows)
    return {
        "n": n,
        "seat_independent": seat_ok,
        "seat_independent_share": round(seat_ok / n, 4),
        "cross_model_verified": model_ok,
        "cross_model_verified_share": round(model_ok / n, 4),
        "contributor_family_distribution": dict(by_family),
        "seat_count_distribution": dict(Counter(len(r.assignment.seat_ids) for r in rows)),
        "definition": (
            "独立行=评价席位身份≠贡献者席位身份且席位证据通道族两两不同；"
            "cross_model_verified=额外要求席位为真实异构模型族（本单未达成，单列不冒充）"
        ),
    }


def sxe_matrix(rows: Sequence[EvaluatedRow], *, status: str = "confirmed") -> dict[str, Any]:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.record["status"] != status:
            continue
        g, e = r.record["grade"] or "?", r.record["e_level"] or "?"
        out.setdefault(g, {}).setdefault(e, 0)
        out[g][e] += 1
    return out


def wave_metrics(
    window_missions: Sequence[int],
    rows: Sequence[EvaluatedRow],
    *,
    window_hours: int = WAVE_WINDOW_HOURS,
) -> dict[str, Any]:
    """一波的评价指标全量快照（覆盖率/独立性/理由长度/共识/引用/工件核验）。"""
    coverage = coverage_stats(window_missions, rows)
    independence = independence_stats(rows)
    reasons = [r.record["grade_reason"] or "" for r in rows]
    artifact_values = Counter(v for r in rows for v in r.artifact_check.values())
    return {
        "schema": WAVE_SCHEMA,
        "window_hours": window_hours,
        "coverage": coverage,
        "independence": independence,
        "reason_length": reason_length_distribution(reasons),
        "consensus": {
            "unanimous": sum(1 for r in rows if r.consensus.unanimous),
            "unanimous_share": round(
                sum(1 for r in rows if r.consensus.unanimous) / len(rows), 4
            ) if rows else 0.0,
            "disagreement_missions": [r.mission_id for r in rows if not r.consensus.unanimous][:30],
        },
        "citation": {
            "ok": sum(1 for r in rows if r.citation["ok"]),
            "ok_share": round(sum(1 for r in rows if r.citation["ok"]) / len(rows), 4)
            if rows else 0.0,
            "number_hits": sum(r.citation["number_hits"] for r in rows),
            "number_misses": sum(len(r.citation["number_misses"]) for r in rows),
        },
        "artifact_check": dict(artifact_values),
        "matrix_confirmed": sxe_matrix(rows, status="confirmed"),
        "matrix_candidate": sxe_matrix(rows, status="candidate"),
        "grade_distribution_confirmed": dict(
            Counter(r.record["grade"] for r in rows if r.record["status"] == "confirmed")
        ),
        "e_level_distribution_confirmed": dict(
            Counter(r.record["e_level"] for r in rows if r.record["status"] == "confirmed")
        ),
        "by_repo": dict(Counter(r.payload.get("repo") or "unknown" for r in rows)),
        "seat_usage": dict(Counter(s for r in rows for s in r.assignment.seat_ids)),
        "flag_distribution": dict(
            Counter(f for r in rows for f in r.consensus.flags)
        ),
    }# ───────────────────────── 引导回路（统计 → 派发梯度） ─────────────────────────
#
# §7.1 禁止把 S1–S5 加权成单一分数或排名。这里因此**不使用任何加权和**：
# 档位由词典序比较决定（先 s3_plus 有无 → 再 s2_plus 占比 → 再 pending 率），
# 输出是离散标签 + 离散预算系数，不存在可被「刷分」优化的连续中间量。


@dataclass
class Guidance:
    subject_kind: str          # 'agent' | 'repo'
    subject: str
    tier: str                  # leveraged | standard | probe | unrated
    budget_multiplier: float
    admission: str             # open | evidence_required | eval_first
    priority_delta: int
    rows: int
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject": self.subject,
            "tier": self.tier,
            "budget_multiplier": self.budget_multiplier,
            "admission": self.admission,
            "priority_delta": self.priority_delta,
            "rows": self.rows,
            "evidence": self.evidence,
        }


def _subject_stats(rows: Sequence[EvaluatedRow], key: str) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        subject = (
            canonical_actor(r.payload.get("contributor"))
            if key == "agent"
            else (r.payload.get("repo") or "unknown")
        )
        s = agg.setdefault(subject, {"rows": 0, "s3_plus": 0, "s2_plus": 0, "s1_only": 0,
                                     "candidate": 0, "confirmed": 0})
        s["rows"] += 1
        g = r.record.get("grade") or "S1"
        if r.record["status"] == "confirmed" and GRADE_RANK.get(g, 0) >= GRADE_RANK["S3"]:
            s["s3_plus"] += 1
        if r.record["status"] == "confirmed" and GRADE_RANK.get(g, 0) >= GRADE_RANK["S2"]:
            s["s2_plus"] += 1
        if g == "S1":
            s["s1_only"] += 1
        s[r.record["status"]] = s.get(r.record["status"], 0) + 1
    for s in agg.values():
        n = s["rows"] or 1
        s["s2_plus_share"] = round(s["s2_plus"] / n, 4)
        s["s3_plus_share"] = round(s["s3_plus"] / n, 4)
        s["s1_only_share"] = round(s["s1_only"] / n, 4)
        s["pending_rate"] = round(s.get("pending_rate", 0.0) / n, 4)
    return agg


def guidance_policy(
    rows: Sequence[EvaluatedRow],
    *,
    window_missions_by_agent: Mapping[str, int] | None = None,
) -> list[Guidance]:
    """评价统计 → 派发梯度（准入 / 预算 / 优先级），**规则化、无加权求和**。

    规则（词典序，命中即停）：
      agent:  S3+ > 0 且未判率 ≤10%      → leveraged(×1.5, open, +1)
              否则 S2+ 占比 ≥20% 且未判 ≤20% → standard(×1.0, open, 0)
              否则 rows≥3 且 S1-only 占比 ≥90% → probe(×0.5, evidence_required, −1)
              否则                          → standard(×1.0, open, 0)
              （窗口内无记录 → unrated，不奖不罚）
      repo:   有 S3+                        → healthy(priority 0)
              rows ≥ 8 且 S1-only 占比 ≥90% → probe_scrutiny(priority +2，多看几眼)
    """
    by_agent = _subject_stats(rows, "agent")
    by_repo = _subject_stats(rows, "repo")
    out: list[Guidance] = []
    for subject, s in sorted(by_agent.items()):
        n = s["rows"]
        if n == 0:
            continue
        if s["s3_plus"] > 0 and s["pending_rate"] <= 0.10:
            tier, mult, adm, prio = "leveraged", 1.5, "open", 1
        elif s["s2_plus_share"] >= 0.20 and s["pending_rate"] <= 0.20:
            tier, mult, adm, prio = "standard", 1.0, "open", 0
        elif n >= 3 and s["s1_only_share"] >= 0.90:
            tier, mult, adm, prio = "probe", 0.5, "evidence_required", -1
        else:
            tier, mult, adm, prio = "standard", 1.0, "open", 0
        out.append(
            Guidance(
                "agent", subject, tier, mult, adm, prio, n,
                {
                    "rows": n,
                    "s3_plus": s["s3_plus"],
                    "s2_plus_share": s["s2_plus_share"],
                    "s1_only_share": s["s1_only_share"],
                    "pending_rate": s["pending_rate"],
                    "window_missions": (window_missions_by_agent or {}).get(subject),
                },
            )
        )
    for subject, s in sorted(by_repo.items()):
        if s["rows"] == 0:
            continue
        if s["s3_plus"] > 0:
            tier, mult, adm, prio = "healthy", 1.0, "open", 0
        elif s["rows"] >= 8 and s["s1_only_share"] >= 0.90:
            tier, mult, adm, prio = "probe_scrutiny", 1.0, "open", 2
        elif s["rows"] >= 20 and s["s2_plus_share"] >= 0.50 and s["s3_plus"] == 0:
            # S2 一大片、S3 一条没有 = 能力增量账本从没登记过：这是**申报纪律欠账**，
            # 不是价值为零。下一轮派发要把「补登记/可复用知识沉淀」排到前面（priority +2）。
            tier, mult, adm, prio = "ledger_debt", 1.0, "open", 2
        else:
            tier, mult, adm, prio = "standard", 1.0, "open", 0
        out.append(
            Guidance(
                "repo", subject, tier, mult, adm, prio, s["rows"],
                {
                    "rows": s["rows"],
                    "s3_plus": s["s3_plus"],
                    "s2_plus_share": s["s2_plus_share"],
                    "s1_only_share": s["s1_only_share"],
                },
            )
        )
    return out


def guidance_effects(
    guidance: Sequence[Guidance],
    rows: Sequence[EvaluatedRow],
    window_missions: Sequence[Mapping[str, Any]],
    *,
    base_seats: int = 3,
    priority_cap: int = 5,
) -> dict[str, Any]:
    """把引导回路落成**可量的派发变化**（不是报告里的一句话）。

    量三件事，全部来自本轮真实数据：
      1. ``priority_repos``：下一轮评价波里被提到前面的仓（批内顺序真的改变）。
      2. ``extra_seat_missions``：被要求加审的仓里有多少张 mission 拿到额外席位。
      3. ``budget_multiplier_delta``：按 agent 档位重算下一批派发的预算系数增量
         （对窗口内每个 agent 的 mission 数求和，得到总预算当量变化）。
    """
    flagged = [g for g in guidance if g.subject_kind == "repo" and g.priority_delta > 0]
    # 加审名单**按行数排序并截断**：全生态同时加审等于没加审（预算会被摊薄）。
    flagged_sorted = sorted(flagged, key=lambda g: (-g.rows, g.subject))
    prio_repos = [g.subject for g in flagged_sorted[:priority_cap]]
    agent_mult = {g.subject: g.budget_multiplier for g in guidance if g.subject_kind == "agent"}
    gated = [g.subject for g in guidance if g.subject_kind == "agent" and g.admission != "open"]

    n_prio_missions = sum(1 for m in window_missions if str(m.get("repo")) in set(prio_repos))
    max_seats = base_seats + 1
    extra_seat_missions = sum(
        1 for m in window_missions if str(m.get("repo")) in set(prio_repos)
    )
    budget_delta = 0.0
    for m in window_missions:
        agent = canonical_actor(m.get("claimed_by"))
        mult = agent_mult.get(agent)
        if mult is not None:
            budget_delta += mult - 1.0
    tier_counts = Counter(g.tier for g in guidance)
    reason_by_repo = {g.subject: g.tier for g in guidance
                      if g.subject_kind == "repo" and g.priority_delta > 0}
    return {
        "priority_repos": prio_repos,
        "priority_reason": {k: reason_by_repo.get(k, "") for k in prio_repos},
        "priority_cap": priority_cap,
        "flagged_repos_all": [g.subject for g in flagged_sorted],
        "flagged_repo_count": len(flagged_sorted),
        "priority_repos_count": len(prio_repos),
        "missions_reordered": n_prio_missions,
        "extra_seat_missions": extra_seat_missions,
        "seat_budget": {"base": base_seats, "priority": max_seats},
        "gated_agents": gated,
        "gated_agent_count": len(gated),
        "budget_multiplier_delta_total": round(budget_delta, 3),
        "budget_multiplier_mean": round(
            (budget_delta / len(window_missions)) if window_missions else 0.0, 4
        ),
        "tier_distribution": dict(tier_counts),
        "note": "无加权分数/排名：档位为词典序规则命中的离散标签，权重只用于预算系数与审查强度",
    }


def admission_decision(
    guidance: Sequence[Guidance], contributor: str
) -> tuple[bool, list[str]]:
    """准入判定（平台派发侧可调用）：``(allowed, reasons)``。

    只有 ``admission != 'open'`` 的主体被拦；拦的理由是**欠证据/欠评价**，
    不是价值低——这样准入就不会退化成对主体的价值审判。
    """
    key = canonical_actor(contributor)
    for g in guidance:
        if g.subject_kind == "agent" and g.subject == key:
            if g.admission == "open":
                return True, []
            return False, [f"ADMISSION_{g.admission.upper()}", f"tier={g.tier}", f"rows={g.rows}"]
    return True, []


# ───────────────────────────── 抽查（10 行对工件） ─────────────────────────────


def spot_check(
    rows: Sequence[EvaluatedRow],
    packets: Mapping[int, EvidencePacket],
    *,
    k: int = 10,
    prefer_with_artifacts: bool = True,
    artifact_root: Path | None = None,
    local_repo: str | None = None,
) -> dict[str, Any]:
    """抽查 k 行：把理由里的数字/工件路径逐项对回证据，并重算等级。

    三项检查各自独立：
      ``report_md5``      —— 由 DB markdown 现算 md5，与记录中的报告 md5 一致；
      ``citation``        —— 理由里的数字/路径都能在证据文本里找到；
      ``regrade_stable``  —— 用同一证据重跑判级，等级/证据级与写入行一致（可重放）。
    """
    pool = [r for r in rows if r.record["status"] in ("confirmed", "candidate")]
    if prefer_with_artifacts:
        pool.sort(
            key=lambda r: (
                0 if (local_repo is None or (r.payload.get("repo") == local_repo)) else 1,
                -r.citation["checked_artifacts"],
                -r.facts.numbers,
            )
        )
    picked = pool[:k]
    out: list[dict[str, Any]] = []
    for r in picked:
        pkt = packets.get(r.mission_id)
        if pkt is None:  # pragma: no cover - 调用方保证
            continue
        md5 = hashlib.md5(pkt.report_markdown.encode("utf-8")).hexdigest() if pkt.report_markdown else ""
        git_probe = probe_artifacts_git(
            list(r.artifact_check.keys()),
            root=artifact_root if (local_repo is None or pkt.repo == local_repo) else None,
        )
        regraded = evaluate_mission(
            pkt,
            n_seats=len(r.assignment.seat_ids),
            artifact_root=artifact_root,
            local_repo=local_repo,
            force_seats=r.assignment.seat_ids,
        )
        row = {
            "mission_id": r.mission_id,
            "grade": r.record["grade"],
            "e_level": r.record["e_level"],
            "status": r.record["status"],
            "reason_chars": _norm_len(r.record["grade_reason"] or ""),
            "report_id": pkt.report_id,
            "report_md5_match": (md5 == pkt.report_md5) if md5 else True,
            "report_md5_recomputed": md5,
            "report_md5_stored": pkt.report_md5,
            "report_chars": pkt.report_chars,
            "citation_ok": r.citation["ok"],
            "citation_numbers_checked": r.citation["number_hits"] + len(r.citation["number_misses"]),
            "artifact_checks": r.artifact_check,
            "artifact_verification": git_probe,
            "artifacts_matched": sum(
                1 for v in git_probe.values()
                if v in ("exists", "exists_in_history", "exists_peer_worktree")
            ),
            "artifacts_cited": len(git_probe),
            "artifacts_verifiable": bool(git_probe),
            "artifacts_unverified_paths": len(r.artifact_check) - len(git_probe),
            "artifacts_matched_share": (
                round(sum(1 for v in git_probe.values()
                          if v in ("exists", "exists_in_history", "exists_peer_worktree"))
                      / len(git_probe), 3)
                if git_probe else None
            ),
            "artifacts_exists": sum(1 for v in r.artifact_check.values() if v == "exists"),
            "artifacts_not_found_local": sum(
                1 for v in r.artifact_check.values() if v == "not_found_local"
            ),
            "artifacts_unverified": sum(
                1 for v in r.artifact_check.values() if v == "unverified"
            ),
            "regrade_stable": (
                regraded.record["grade"] == r.record["grade"]
                and regraded.record["e_level"] == r.record["e_level"]
            ),
            "reason_head": (r.record["grade_reason"] or "")[:160],
        }
        share = row["artifacts_matched_share"]
        absorbed = (
            (r.record.get("e_level") == "E1")
            and ("ARTIFACT_NOT_FOUND" in (r.consensus.flags or ()))
        )
        row["artifact_absent_absorbed_by_grade"] = absorbed
        row["artifact_ok"] = True if share is None else (share >= 0.6 or absorbed)
        row["ok"] = bool(
            row["citation_ok"]
            and row["regrade_stable"]
            and row["report_md5_match"]
            and row["artifact_ok"]
        )
        out.append(row)
    return {
        "k": k,
        "checked": len(out),
        "all_ok": all(x["ok"] for x in out) if out else False,
        "report_md5_match": sum(1 for x in out if x["report_md5_match"]),
        "citation_ok": sum(1 for x in out if x["citation_ok"]),
        "regrade_stable": sum(1 for x in out if x["regrade_stable"]),
        "artifact_ok": sum(1 for x in out if x["artifact_ok"]),
        "artifacts_cited": sum(x["artifacts_cited"] for x in out),
        "artifacts_matched": sum(x["artifacts_matched"] for x in out),
        "rows": out,
    }


# ─────────────────────────────── DB 访问 ───────────────────────────────


def connect(dsn: str):
    """psycopg3 连接（接受 SQLAlchemy 风格 DSN：``postgresql+psycopg://...``）。"""
    import psycopg
    from psycopg.rows import dict_row

    url = dsn.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(url, row_factory=dict_row)


def select_window(conn, *, hours: int = WAVE_WINDOW_HOURS, repos: Sequence[str] = (),
                  limit: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT m.id, m.repo, m.organization_id, m.claimed_by, m.model, m.goal, m.result, "
        "m.finished_at, m.campaign_id "
        "FROM tensor_dev_missions m "
        "WHERE m.status = 'succeeded' "
        "  AND m.finished_at > now() - (%s || ' hours')::interval "
    )
    params: list[Any] = [int(hours)]
    if repos:
        sql += " AND m.repo = ANY(%s) "
        params.append(list(repos))
    sql += " ORDER BY m.finished_at DESC, m.id DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def load_reports(conn, mission_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    if not mission_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (mission_id) id, mission_id, markdown, figures "
            "FROM tensor_reports WHERE mission_id = ANY(%s) ORDER BY mission_id, id DESC",
            [list(mission_ids)],
        )
        return {int(r["mission_id"]): dict(r) for r in cur.fetchall()}


def load_deltas(conn, mission_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    if not mission_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (mission_id) mission_id, category, artifacts, summary "
            "FROM tensor_capability_deltas WHERE mission_id = ANY(%s) "
            "ORDER BY mission_id, id DESC",
            [list(mission_ids)],
        )
        return {int(r["mission_id"]): dict(r) for r in cur.fetchall()}


_EVAL_EXTRA_COLUMNS: tuple[str, ...] = (
    "wave_id", "batch_id", "seat_ids", "seat_families", "seat_class",
    "contributor_family", "cross_model_verified", "consensus", "citation_check",
    "value_flags", "artifact_check",
)


def upsert_evaluation(conn, row: EvaluatedRow, *, wave_id: str = "") -> None:
    """幂等写入：同一 artifact 已有有效行则就地更新（pending 行升级，重放不增行）。"""
    from tools.outcome_evaluations import COLUMNS, evaluation_id_for

    rec = dict(row.record)
    # 被拒/不可登记（E0）也是「一次尝试」，与 103 的负例留痕同口径：独立 ID，不占分部唯一索引。
    if rec.get("status") == "rejected":
        import uuid as _uuid

        rec["evaluation_id"] = f"{rec['artifact_id']}:rejected:{_uuid.uuid4().hex[:12]}"
    else:
        rec["evaluation_id"] = evaluation_id_for(rec["artifact_id"])
    rec["evidence_refs"] = json.dumps(rec.get("evidence_refs") or [], ensure_ascii=False)
    rec["reason_codes"] = json.dumps(rec.get("reason_codes") or [], ensure_ascii=False)
    rec["decided_at"] = rec.get("decided_at") or _now()
    rec["created_at"] = rec.get("created_at") or _now()
    extra = {
        "wave_id": wave_id or None,
        "batch_id": row.batch_id or None,
        "seat_ids": json.dumps(list(row.assignment.seat_ids)),
        "seat_families": json.dumps(list(row.assignment.seat_families)),
        "seat_class": "programmatic",
        "contributor_family": row.assignment.contributor_family,
        "cross_model_verified": bool(row.assignment.cross_model_verified),
        "consensus": json.dumps(row.consensus.as_dict(), ensure_ascii=False),
        "citation_check": json.dumps(row.citation, ensure_ascii=False),
        "value_flags": json.dumps(list(row.consensus.flags), ensure_ascii=False),
        "artifact_check": json.dumps(row.artifact_check, ensure_ascii=False),
    }
    cols = list(COLUMNS) + list(_EVAL_EXTRA_COLUMNS)
    vals = {**{c: rec.get(c) for c in COLUMNS}, **extra}
    placeholders = ", ".join(f"%({c})s" for c in cols)
    # 先就地更新（pending → 判定 / 重判 / 换波重放都走这条），无命中再插入。
    # 不能用 ON CONFLICT：同一行可能同时命中主键(evaluation_id)与分部唯一索引(artifact_id)，
    # PostgreSQL 只允许一个冲突目标，会先撞主键报 UniqueViolation。
    set_clause = ", ".join(f"{c} = %({c})s" for c in cols if c not in ("evaluation_id", "created_at"))
    update_sql = (
        f"UPDATE outcome_evaluations SET {set_clause} "
        f"WHERE artifact_id = %(artifact_id)s AND status <> 'rejected'"
    )
    insert_sql = f"INSERT INTO outcome_evaluations ({', '.join(cols)}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.execute(update_sql, vals)
        if cur.rowcount == 0:
            cur.execute(insert_sql, vals)


def record_wave(conn, *, wave_id: str, window_hours: int, window_start: Any, window_end: Any,
                missions: int, batches: int, batch_size: int, seats: Sequence[str],
                status: str, metrics: Mapping[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evaluation_waves (wave_id, window_hours, window_start, window_end, "
            "missions_in_window, batches, batch_size, seats, status, metrics, finished_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (wave_id) DO UPDATE SET missions_in_window = EXCLUDED.missions_in_window, "
            "batches = EXCLUDED.batches, batch_size = EXCLUDED.batch_size, seats = EXCLUDED.seats, "
            "status = EXCLUDED.status, metrics = EXCLUDED.metrics, finished_at = now()",
            [
                wave_id, int(window_hours), window_start, window_end, int(missions),
                int(batches), int(batch_size), json.dumps(list(seats)),
                status, json.dumps(metrics, ensure_ascii=False, default=str),
            ],
        )


def record_guidance(conn, *, wave_id: str, guidance: Sequence[Guidance]) -> int:
    n = 0
    with conn.cursor() as cur:
        for g in guidance:
            cur.execute(
                "INSERT INTO evaluation_guidance (wave_id, subject_kind, subject, tier, "
                "budget_multiplier, admission, priority_delta, rows_counted, evidence) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (wave_id, subject_kind, subject) DO UPDATE SET "
                "tier = EXCLUDED.tier, budget_multiplier = EXCLUDED.budget_multiplier, "
                "admission = EXCLUDED.admission, priority_delta = EXCLUDED.priority_delta, "
                "rows_counted = EXCLUDED.rows_counted, evidence = EXCLUDED.evidence",
                [
                    wave_id, g.subject_kind, g.subject, g.tier, g.budget_multiplier,
                    g.admission, g.priority_delta, g.rows,
                    json.dumps(g.evidence, ensure_ascii=False, default=str),
                ],
            )
            n += 1
    return n


def load_guidance(conn, wave_id: str) -> list[Guidance]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT subject_kind, subject, tier, budget_multiplier, admission, "
            "priority_delta, rows_counted, evidence FROM evaluation_guidance "
            "WHERE wave_id = %s ORDER BY subject_kind, subject",
            [wave_id],
        )
        out = []
        for r in cur.fetchall():
            ev = r["evidence"]
            if isinstance(ev, str):
                try:
                    ev = json.loads(ev)
                except json.JSONDecodeError:  # pragma: no cover
                    ev = {}
            out.append(
                Guidance(
                    subject_kind=r["subject_kind"], subject=r["subject"], tier=r["tier"],
                    budget_multiplier=float(r["budget_multiplier"]), admission=r["admission"],
                    priority_delta=int(r["priority_delta"]), rows=int(r["rows_counted"]),
                    evidence=ev or {},
                )
            )
        return out


def load_wave_effects(conn, wave_id: str) -> dict[str, Any]:
    """读回某波落库的引导回路效果（优先用 metrics 里截断过的加审名单）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metrics FROM evaluation_waves WHERE wave_id = %s", [wave_id]
        )
        row = cur.fetchone()
    if not row or row["metrics"] is None:
        return {}
    metrics = row["metrics"]
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except json.JSONDecodeError:  # pragma: no cover
            return {}
    return dict(metrics.get("guidance_effects") or {})


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()# ─────────────────────────── 一波的编排（CLI 主流程） ───────────────────────────


def run_wave(
    conn,
    *,
    window_hours: int = WAVE_WINDOW_HOURS,
    repos: Sequence[str] = (),
    batch_size: int = DEFAULT_BATCH,
    n_seats: int = 3,
    salt: str = "r1",
    artifact_root: Path | None = None,
    limit: int | None = None,
    priority_repos: Sequence[str] = (),
    extra_seat_repos: Sequence[str] = (),
    local_repo: str | None = None,
    wave_id: str | None = None,
    dry_run: bool = False,
    progress: bool = False,
) -> dict[str, Any]:
    """跑一波：取窗口 → 分批 → 派席位判级 → 落库 → 出指标 → 出引导回路。"""
    missions = select_window(conn, hours=window_hours, repos=repos, limit=limit)
    if not missions:
        return {"schema": WAVE_SCHEMA, "missions_in_window": 0, "rows": 0}
    wid = wave_id or f"w{_now().replace(':', '').replace('-', '')}"
    extra = {str(r) for r in extra_seat_repos}
    batches = plan_batches(missions, batch_size=batch_size, priority_repos=priority_repos)

    by_id = {int(m["id"]): m for m in missions}
    rows: list[EvaluatedRow] = []
    packets: dict[int, EvidencePacket] = {}
    written = 0
    for batch in batches:
        ids = batch["mission_ids"]
        reports = load_reports(conn, ids)
        deltas = load_deltas(conn, ids)
        for mid in ids:
            m = by_id[mid]
            pkt = build_packet(m, report=reports.get(mid), delta=deltas.get(mid))
            packets[mid] = pkt
            nr = n_seats + (1 if str(m.get("repo")) in extra else 0)
            row = evaluate_mission(
                pkt,
                n_seats=nr,
                salt=f"{salt}:{batch['batch_id']}",
                artifact_root=artifact_root,
                local_repo=local_repo,
                batch_id=batch["batch_id"],
            )
            rows.append(row)
            if not dry_run:
                upsert_evaluation(conn, row, wave_id=wid)
                written += 1
        if not dry_run:
            conn.commit()
        if progress:
            print(f"[wave] {batch['batch_id']} {len(ids)} 单完成（累计 {len(rows)}）", flush=True)

    metrics = wave_metrics([int(m["id"]) for m in missions], rows, window_hours=window_hours)
    metrics["missions_in_window"] = len(missions)
    metrics["repos_filter"] = list(repos)
    guidance = guidance_policy(rows)
    effects = guidance_effects(guidance, rows, missions, base_seats=n_seats)
    spot = spot_check(
        rows, packets, k=10, artifact_root=artifact_root, local_repo=local_repo
    )
    finished = [m.get("finished_at") for m in missions if m.get("finished_at")]
    win_start = min(finished) if finished else None
    win_end = max(finished) if finished else None
    if not dry_run:
        metrics["guidance_effects"] = effects
        record_wave(
            conn, wave_id=wid, window_hours=window_hours, window_start=win_start,
            window_end=win_end, missions=len(missions), batches=len(batches),
            batch_size=batch_size, seats=[s.seat_id for s in SEAT_POOL],
            status="completed", metrics=metrics,
        )
        record_guidance(conn, wave_id=wid, guidance=guidance)
        conn.commit()
    return {
        "schema": WAVE_SCHEMA,
        "wave_id": wid,
        "generated_at": _now(),
        "params": {
            "window_hours": window_hours,
            "batch_size": batch_size,
            "batches": len(batches),
            "n_seats_base": n_seats,
            "n_seats_priority": n_seats + 1,
            "salt": salt,
            "repos_filter": list(repos),
            "dry_run": dry_run,
            "priority_repos_in": list(priority_repos),
            "extra_seat_repos_in": list(extra_seat_repos),
            "artifact_root": str(artifact_root) if artifact_root else None,
            "local_repo": local_repo,
        },
        "metrics": metrics,
        "guidance": [g.as_dict() for g in guidance],
        "effects": effects,
        "spot_check": spot,
        "batches": batches,
        "written": written,
        "rows": [r.as_dict() for r in rows],
    }


def judge(report: Mapping[str, Any], *, pytest_passed: int | None = None) -> dict[str, Any]:
    """把机器验收判据逐条算成布尔值（判据数字与产物路径一并落 JSON）。"""
    m = report["metrics"]
    cov = m["coverage"]["coverage"]
    ind = m["independence"]["seat_independent_share"]
    cmv = m["independence"]["cross_model_verified_share"]
    rl = m["reason_length"]
    spot = report["spot_check"]
    checks = {
        "coverage_ge_0.80": {"value": cov, "pass": cov >= 0.80},
        "independent_share_ge_0.90": {"value": ind, "pass": ind >= 0.90},
        "reason_length_report_present": {
            "value": {"n": rl.get("n"), "median": rl.get("median"), "min": rl.get("min")},
            "pass": bool(rl.get("n")) and rl.get("min", 0) >= REASON_MIN_CHARS,
        },
        "spotcheck_10_ok": {
            "value": {"checked": spot["checked"], "all_ok": spot["all_ok"]},
            "pass": spot["checked"] >= 10 and spot["all_ok"],
        },
        "cross_model_verified_present": {
            "value": cmv,
            "pass": cmv > 0,
            "note": "程序化席位无真实异构模型族 → 本条如实记为未达成（honest FAIL 分项）",
        },
    }
    if pytest_passed is not None:
        checks["pytest_ge_8"] = {"value": pytest_passed, "pass": pytest_passed >= 8}
    checks["all_core_pass"] = {
        "value": all(v["pass"] for k, v in checks.items() if k != "cross_model_verified_present"),
        "pass": all(
            v["pass"] for k, v in checks.items()
            if k not in ("cross_model_verified_present", "all_core_pass")
        ),
    }
    return checks


RECOMPUTE_SQL = """-- 独立复算（把 :wave 换成实际 wave_id）
SELECT status, reviewer_kind, grade, e_level, count(*)
  FROM outcome_evaluations WHERE wave_id = :wave
 GROUP BY 1,2,3,4 ORDER BY 1,3,4;

SELECT count(DISTINCT mission_id) FILTER (WHERE status IN ('confirmed','candidate')) AS evaluated,
       count(DISTINCT mission_id) AS touched
  FROM outcome_evaluations WHERE wave_id = :wave;

SELECT count(*) FILTER (WHERE seat_class = 'programmatic') AS programmatic,
       count(*) FILTER (WHERE cross_model_verified) AS cross_model,
       count(*) AS total
  FROM outcome_evaluations WHERE wave_id = :wave;

SELECT min(length(regexp_replace(grade_reason, '\\s', '', 'g'))) AS min_chars,
       percentile_cont(0.5) WITHIN GROUP (
         ORDER BY length(regexp_replace(grade_reason, '\\s', '', 'g'))) AS median_chars
  FROM outcome_evaluations WHERE wave_id = :wave;

SELECT subject_kind, tier, admission, count(*), sum(budget_multiplier - 1) AS budget_delta
  FROM evaluation_guidance WHERE wave_id = :wave GROUP BY 1,2,3 ORDER BY 1,2;
"""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="评价波执行器（全量独立评价波）")
    ap.add_argument("--dsn", default="postgresql+psycopg://postgres@/tensorplatform"
                                    "?host=/var/run/postgresql")
    ap.add_argument("--window-hours", type=int, default=WAVE_WINDOW_HOURS)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--n-seats", type=int, default=3)
    ap.add_argument("--salt", default="r1")
    ap.add_argument("--repos", default="")
    ap.add_argument("--priority-repos", default="")
    ap.add_argument("--extra-seat-repos", default="")
    ap.add_argument("--guidance-from", default="", help="上一波 wave_id：用其引导回路定顺序与席位")
    ap.add_argument("--artifact-root", default="")
    ap.add_argument("--local-repo", default="",
                    help="artifact-root 对应的仓名（该仓的工件路径才做存在性核验）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--wave-id", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="artifacts/m5180_evaluation_wave")
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    conn = connect(args.dsn)
    try:
        prio: list[str] = [r for r in args.priority_repos.split(",") if r]
        extra: list[str] = [r for r in args.extra_seat_repos.split(",") if r]
        if args.guidance_from:
            # 下一轮的加审名单必须是**上一轮已截断的**那份（否则全生态加审 → 摊薄预算）
            effects = load_wave_effects(conn, args.guidance_from)
            prio = prio or list(effects.get("priority_repos") or [])
            if not prio:
                prev = load_guidance(conn, args.guidance_from)
                prio = [g.subject for g in prev
                        if g.subject_kind == "repo" and g.priority_delta > 0][:5]
            extra = extra or list(prio)
            print(f"[wave] 引导回路接入 {args.guidance_from}：priority_repos={prio}")
        report = run_wave(
            conn,
            window_hours=args.window_hours,
            repos=[r for r in args.repos.split(",") if r],
            batch_size=args.batch_size,
            n_seats=args.n_seats,
            salt=args.salt,
            artifact_root=(Path(args.artifact_root) if args.artifact_root else None),
            local_repo=(args.local_repo or None),
            limit=(args.limit or None),
            priority_repos=prio,
            extra_seat_repos=extra,
            wave_id=(args.wave_id or None),
            dry_run=args.dry_run,
            progress=args.progress,
        )
    finally:
        conn.close()

    checks = judge(report)
    report["checks"] = checks
    _write_json(out_dir / "wave_report.json", report)
    _write_json(out_dir / "guidance.json", {
        "wave_id": report.get("wave_id"),
        "guidance": report.get("guidance"),
        "effects": report.get("effects"),
    })
    _write_json(out_dir / "spot_check.json", report.get("spot_check"))
    _write_json(out_dir / "reason_length.json", report["metrics"]["reason_length"])
    with (out_dir / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for row in report["rows"]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "recompute.sql").write_text(
        RECOMPUTE_SQL.replace(":wave", f"'{report.get('wave_id')}'"), encoding="utf-8"
    )
    print(json.dumps(
        {
            "wave_id": report.get("wave_id"),
            "missions": report["metrics"].get("missions_in_window"),
            "coverage": report["metrics"]["coverage"]["coverage"],
            "independent_share": report["metrics"]["independence"]["seat_independent_share"],
            "reason_median": report["metrics"]["reason_length"].get("median"),
            "spot_ok": report["spot_check"]["all_ok"],
            "checks": {k: v["pass"] for k, v in checks.items()},
            "out": str(out_dir),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())