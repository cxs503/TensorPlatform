"""real dispatch log 适配层：把平台任务库的记录折成「派单时刻可见特征 + 结局」。

纪律
----
1. **只用派单时刻字段做特征**（repo / budget / goal 文本 / 是否是子单 / co_repos /
   热度滚动窗口）。``result`` 里的任何字段（accepted / headline / fail_stage /
   decision_note / tool_calls …）一律**禁止**进特征，只允许作为标签或成本口径 ——
   这是 :mod:`tools.seed_dispatch.integration` 泄漏审计的可证伪前提。
2. **时序严格性**：repo 通过率、近期失败率、新颖性参照池都只用 *该条之前* 的记录，
   不允许回看；记录按 ``(created_at, id)`` 排序。
3. **确定性**：同一份快照 + 同一参数 → 逐字节相同的特征/标签；凡随机处（自助抽样、
   随机基线）用显式 seed。
4. 零领域语义：repo 名、模型名、领域词一律来自数据；本文件不含任何域词。

结局口径（honest 版）
--------------------
``label`` = 是否**被验收通过**：``result.accepted is True`` 或（无该字段时）``status ==
succeeded``；``result.accepted is False`` 或 ``status == failed`` 记 0。其他终态
（stopped / revoked / orphan）与非终态一律 ``None``（删失，不进训练/评估分母）。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "DISPATCH_FIELDS",
    "POST_HOC_FIELDS",
    "Record",
    "NoveltyStream",
    "build_records",
    "fetch_missions",
    "load_snapshot",
    "snapshot_sha256",
    "summarise",
]

#: 允许进特征的字段（派单时刻快照）
DISPATCH_FIELDS: tuple[str, ...] = (
    "id",
    "repo",
    "goal",
    "budget",
    "co_repos",
    "parent_mission_id",
    "created_at",
    "campaign_phase",
    "runner",
    "origin",
)

#: 事后字段（只能当标签/成本，当特征即为泄漏）
POST_HOC_FIELDS: tuple[str, ...] = (
    "result.accepted",
    "result.decision_note",
    "result.headline",
    "result.fail_stage",
    "result.tool_calls",
    "result.checks",
    "result.evidence",
    "result.pr_gate",
    "result.compactions",
    "result.changed_files",
    "status",
    "finished_at",
)

_ACCEPT_KEYS = ("accepted",)


@dataclass
class Record:
    """一条派单记录（派单时刻特征 + 结局 + 成本）。"""

    mission_id: int
    repo: str
    created_at: str
    ts: float
    features: dict[str, float]
    label: int | None
    cost_tool_calls: float | None
    wall_sec: float | None
    status: str
    posthoc_sha256: str
    extra: dict[str, Any] = field(default_factory=dict)

    # --- 映射式访问：让 policy/integration 能用 rec["features"] / rec["ts"] 统一取数 ---
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:  # pragma: no cover - 防御
            raise KeyError(key) from exc

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "repo": self.repo,
            "created_at": self.created_at,
            "ts": self.ts,
            "features": self.features,
            "label": self.label,
            "cost_tool_calls": self.cost_tool_calls,
            "wall_sec": self.wall_sec,
            "status": self.status,
            "posthoc_sha256": self.posthoc_sha256,
        }


def _parse_ts(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:  # pragma: no cover - 防御
        return 0.0


def _tokenise(text: str, *, token_min_len: int, cjk_ngram: int) -> dict[int, float]:
    """词元 → 稀疏向量（桶号 → 频次）。拉丁词取长度门；CJK 取字符 n-gram。

    哈希用 ``zlib.crc32``（跨进程稳定），不用 ``hash()``（受 PYTHONHASHSEED 影响）。
    """
    tokens: list[str] = []
    buf: list[str] = []
    cjk: list[str] = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "_-./"):
            buf.append(ch.lower())
            if cjk:
                tokens.extend(_cjk_grams(cjk, cjk_ngram))
                cjk = []
        elif "\u4e00" <= ch <= "\u9fff":
            cjk.append(ch)
            if buf:
                word = "".join(buf)
                if len(word) >= token_min_len:
                    tokens.append(word)
                buf = []
        else:
            if buf:
                word = "".join(buf)
                if len(word) >= token_min_len:
                    tokens.append(word)
                buf = []
            if cjk:
                tokens.extend(_cjk_grams(cjk, cjk_ngram))
                cjk = []
    if buf:
        word = "".join(buf)
        if len(word) >= token_min_len:
            tokens.append(word)
    if cjk:
        tokens.extend(_cjk_grams(cjk, cjk_ngram))
    vec: dict[int, float] = {}
    for tok in tokens:
        bucket = zlib.crc32(tok.encode("utf-8")) & 0xFFFFFFFF
        vec[bucket] = vec.get(bucket, 0.0) + 1.0
    return _normalise(vec)


def _cjk_grams(chars: Sequence[str], n: int) -> list[str]:
    if len(chars) < n:
        return ["" .join(chars)] if chars else []
    return ["".join(chars[i:i + n]) for i in range(len(chars) - n + 1)]


def _normalise(vec: dict[int, float]) -> dict[int, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm <= 0.0:
        return {}
    return {k: v / norm for k, v in vec.items()}


class NoveltyStream:
    """流式新颖性：``novelty = 1 - cos(doc, 历史池均值)``（只回看，滑动池）。

    池子 = 最近 ``pool`` 条文档的归一化向量；均值用「加新减旧」的稀疏增量维护（每条 O(词元数)），
    因此不用 O(n·pool) 的两两比较。这是明确记录的近似（报告「注意点」节复述），不是精确近邻检索。
    """

    def __init__(self, *, pool: int = 200, alpha: float = 0.02,
                 token_min_len: int = 3, cjk_ngram: int = 2, hash_bits: int = 13) -> None:
        self.pool = pool
        self.alpha = alpha  # 保留字段以兼容参数面；滑动池实现不再用它衰减
        self.token_min_len = token_min_len
        self.cjk_ngram = cjk_ngram
        self.hash_bits = hash_bits
        self._sum: dict[int, float] = {}
        self._vecs: deque[dict[int, float]] = deque(maxlen=pool)
        self._seen = 0

    def tokenise(self, text: str) -> dict[int, float]:
        vec = _tokenise(text, token_min_len=self.token_min_len, cjk_ngram=self.cjk_ngram)
        if not vec:
            return {}
        mask = (1 << self.hash_bits) - 1
        folded: dict[int, float] = {}
        for k, v in vec.items():
            key = k & mask
            folded[key] = folded.get(key, 0.0) + v
        return _normalise(folded)

    @property
    def centroid(self) -> dict[int, float]:
        return _normalise(self._sum)

    def score(self, text: str) -> tuple[float, dict[int, float]]:
        vec = self.tokenise(text)
        if not vec or not self._vecs:
            return 1.0, vec
        centre = self.centroid
        sim = sum(v * centre.get(k, 0.0) for k, v in vec.items())
        return max(0.0, min(1.0, 1.0 - sim)), vec

    def update(self, vec: Mapping[int, float]) -> None:
        if not vec:
            self._seen += 1
            return
        if len(self._vecs) == self._vecs.maxlen:
            old = self._vecs[0]
            for k, v in old.items():
                remain = self._sum.get(k, 0.0) - v
                if abs(remain) < 1e-9:
                    self._sum.pop(k, None)
                else:
                    self._sum[k] = remain
        self._vecs.append(dict(vec))
        for k, v in vec.items():
            self._sum[k] = self._sum.get(k, 0.0) + v
        self._seen += 1


def _label(status: str, result: Mapping[str, Any]) -> int | None:
    accepted = result.get(_ACCEPT_KEYS[0]) if isinstance(result, Mapping) else None
    if accepted is True:
        return 1
    if accepted is False and status in ("succeeded", "failed", "awaiting_pr_merge"):
        # 显式验收否决：即便 worker 报 success 也算没通过
        return 0
    if status == "succeeded":
        return 1
    if status == "failed":
        return 0
    return None


def _posthoc_sha256(result: Mapping[str, Any], status: str, finished_at: Any) -> str:
    payload = json.dumps(
        {"result": result or {}, "status": status, "finished_at": finished_at},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_records(
    raw: Iterable[Mapping[str, Any]],
    *,
    novelty_pool: int = 200,
    novelty_alpha: float = 0.02,
    token_min_len: int = 3,
    cjk_ngram: int = 2,
    hash_bits: int = 13,
    recent: int = 40,
    prior_strength: float = 5.0,
) -> list[Record]:
    """原始任务记录 → 派单记录（时序严格，确定性）。"""
    rows = sorted(
        (r for r in raw if r.get("repo") and r.get("created_at")),
        key=lambda r: (_parse_ts(r.get("created_at")), int(r.get("id") or 0)),
    )
    stream = NoveltyStream(pool=novelty_pool, alpha=novelty_alpha,
                           token_min_len=token_min_len, cjk_ngram=cjk_ngram,
                           hash_bits=hash_bits)
    repo_total: Counter[str] = Counter()
    repo_ok: Counter[str] = Counter()
    global_recent: deque[int] = deque(maxlen=100)
    repo_recent: dict[str, deque[int]] = {}
    base_rate_ok = 0
    base_rate_n = 0
    out: list[Record] = []
    for r in rows:
        repo = str(r["repo"])
        goal = str(r.get("goal") or "")
        budget = r.get("budget") or {}
        if not isinstance(budget, Mapping):
            budget = {}
        novelty, vec = stream.score(goal)
        prior_pass = (repo_ok[repo] + prior_strength * _ratio(base_rate_ok, base_rate_n)) / (
            repo_total[repo] + prior_strength
        )
        window = repo_recent.get(repo)
        recent_fail = 0.0 if not window else 1.0 - (sum(window) / len(window))
        result = r.get("result") or {}
        if not isinstance(result, Mapping):
            result = {}
        status = str(r.get("status") or "")
        label = _label(status, result)
        tool_calls = result.get("tool_calls")
        cost = float(tool_calls) if isinstance(tool_calls, (int, float)) else None
        wall = None
        if r.get("finished_at"):
            wall = max(0.0, _parse_ts(r["finished_at"]) - _parse_ts(r["created_at"]))
        co_repos = r.get("co_repos") or []
        features = {
            "f_repo_pass": round(float(prior_pass), 6),
            "f_global_recent_fail": round(1.0 - _ratio(sum(global_recent), len(global_recent))
                                          if global_recent else 0.5, 6),
            "f_repo_recent_fail": round(float(recent_fail), 6),
            "f_log_len": round(math.log1p(len(goal)), 6),
            "f_log_budget": round(math.log1p(float(budget.get("max_tool_calls") or 0)), 6),
            "f_novelty": round(float(novelty), 6),
            "f_is_child": 1.0 if r.get("parent_mission_id") else 0.0,
            "f_co_repos": float(len(co_repos)) if isinstance(co_repos, (list, tuple)) else 0.0,
        }
        out.append(
            Record(
                mission_id=int(r.get("id") or 0),
                repo=repo,
                created_at=str(r.get("created_at")),
                ts=_parse_ts(r.get("created_at")),
                features=features,
                label=label,
                cost_tool_calls=cost,
                wall_sec=wall,
                status=status,
                posthoc_sha256=_posthoc_sha256(result, status, r.get("finished_at")),
            )
        )
        # --- 状态更新（严格在该条之后）---
        stream.update(vec)
        repo_total[repo] += 1
        if label is not None:
            base_rate_n += 1
            base_rate_ok += label
            global_recent.append(label)
            repo_ok[repo] += label
            dq = repo_recent.setdefault(repo, deque(maxlen=recent))
            if window is None or len(window) == recent:
                pass
            dq.append(label)
    return out


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.5


def summarise(records: Sequence[Record]) -> dict[str, Any]:
    labels = [r.label for r in records]
    known = [x for x in labels if x is not None]
    return {
        "records": len(records),
        "with_label": len(known),
        "passed": sum(1 for x in known if x == 1),
        "failed": sum(1 for x in known if x == 0),
        "pass_rate": round(sum(known) / len(known), 6) if known else None,
        "censored": len(records) - len(known),
        "repos": len({r.repo for r in records}),
        "first_created_at": records[0].created_at if records else None,
        "last_created_at": records[-1].created_at if records else None,
        "with_cost": sum(1 for r in records if r.cost_tool_calls is not None),
    }


def snapshot_sha256(raw: Sequence[Mapping[str, Any]]) -> str:
    """快照指纹：只对 (id, status, created_at) 三元组做规范化哈希，抵抗字段顺序漂移。"""
    # 按行排序后哈希 → 与输入顺序无关（快照重排不算改动，字段改动才会变指纹）
    lines = sorted(f"{r.get('id')}\t{r.get('status')}\t{r.get('created_at')}" for r in raw)
    payload = "\n".join(lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_snapshot(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """读快照（支持 ``.gz``，与未压缩 JSON 等价；``{"items": [...]}`` 也接受）。"""
    text = (str(path).endswith(".gz"))
    if text:
        import gzip

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    if isinstance(payload, Mapping):
        payload = payload.get("items") or []
    if not isinstance(payload, list):
        raise ValueError("mission snapshot must be a JSON list or {'items': [...]}")
    return payload


def fetch_missions(
    api_base: str | None = None,
    token: str | None = None,
    *,
    page: int = 200,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """从平台 API 拉全量任务记录（只读）。token 缺省走环境变量/登录接口。"""
    base = (api_base or os.environ.get("TENSOR_API_BASE") or "").rstrip("/")
    if not base:
        raise RuntimeError("TENSOR_API_BASE not set")
    tok = token or os.environ.get("TENSOR_API_TOKEN") or _login(base, timeout=timeout)
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        req = urllib.request.Request(
            f"{base}/tensor/missions?limit={page}&offset={offset}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        batch = payload.get("items") or []
        items.extend(batch)
        offset += page
        if not batch or offset >= int(payload.get("total") or 0):
            break
    return items


def _login(base: str, *, timeout: float = 30.0) -> str:  # pragma: no cover - 联网
    user = os.environ.get("TENSOR_AGENT_USER")
    pwd = os.environ.get("TENSOR_AGENT_PASS")
    if not user or not pwd:
        raise RuntimeError("no token and no TENSOR_AGENT_USER/TENSOR_AGENT_PASS")
    body = json.dumps({"username": user, "password": pwd}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/auth/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    tok = payload.get("access_token") or payload.get("token")
    if not tok:
        raise RuntimeError("login returned no token")
    return str(tok)