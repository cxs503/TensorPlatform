"""提案去重证据的可复算门（dedup evidence replay gate）。

**要防的东西**：提案者声称「我做过三重检索了」——纯自由文本、无法复核。本模块把
「检索证据」升级为**机器可复算的工件**：

1. **快照指纹**：被检索的三张表（``campaign_nodes`` / ``campaign_plans`` / ``proposals``，
   表名与文本列全部来自配置）在取证时刻的 ``(行数, max(id), 内容 sha256)`` 三元组，
   合成为快照指纹 ``snapshot.sha256``；
2. **查询规格**：每次检索写成结构化 :class:`QuerySpec`（表 + 模式 ``ilike`` /
   ``keyword_set`` / ``vector`` + 显式词项集 + top_k + 阈值），可序列化、可重放；
   规格自身的 ``spec_sha256`` 也进证据，改一个词项即失配；
3. **证据工件**：:class:`DedupEvidence` = 快照指纹 + 规格集 + 逐规格的命中计数与 top-k
   近邻（含分数），整体 ``evidence_sha256`` 覆盖全部内容；
4. **复算器** :func:`verify_evidence`：收养时用**同一份快照**重跑同样的规格，
   命中计数**精确**比对、top-k 集合比对（集合相等或 Jaccard ≥ ``topk_min_jaccard``，
   默认 0.8）、分数比对（1e-9 容差）；任一超界即 ``REJECT``（fail-closed），
   绝不静默放行；
5. **动作分级**：证据不一致 → ``REJECT`` + 分类原因码；证据一致但近邻过近
   （top-1 相似度 ≥ ``near_neighbor_threshold``）→ ``DEEPEN``（建议深化/合并到既有条目，
   而非新建孤儿提案）；
6. **只读**：所有数据源只执行 ``SELECT``；:class:`DbapiSource` 记录执行过的语句，
   非 ``SELECT``/``WITH`` 前缀直接抛错，配合 ``verify`` 前后的快照指纹对照证明无副作用。

复算口径的两个刻意选择（都是为了让复算**跨方言、跨进程可重现**）：

* 相似度计算全部在**进程内**用确定性的纯函数完成（NFKC + casefold 归一、字符 3-gram、
  blake2b 签名特征哈希投射），不依赖数据库排序规则/分词器/扩展——否则 PG 与 SQLite
  的 ``ILIKE``/``to_tsvector`` 语义差异会让「同一证据」复算出不同数字；
* 数据源只负责**按 id 有序取行**，行文本由配置声明（不写死在代码里）。

零领域语义：本模块只认识「表名/列名/阈值/词项」这些结构量，词项由调用方注入；
测试夹具全部用合成域名（foo/bar/widget）。
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

SCHEMA_VERSION = "dedup-evidence/1.0"

MODES: tuple[str, ...] = ("ilike", "keyword_set", "vector")
VERDICT_ACCEPT = "ACCEPT"
VERDICT_DEEPEN = "DEEPEN"
VERDICT_REJECT = "REJECT"

# 原因码（分类进报告与事件；新增只追加不重命名）
R_MISSING = "MISSING_EVIDENCE"
R_MALFORMED = "MALFORMED_EVIDENCE"
R_TAMPERED = "EVIDENCE_TAMPERED"
R_SPEC_TAMPERED = "SPEC_TAMPERED"
R_SPEC_COVERAGE = "SPEC_COVERAGE_INCOMPLETE"
R_SNAPSHOT_STALE = "SNAPSHOT_MISMATCH"
R_COUNT = "COUNT_MISMATCH"
R_TOPK = "TOPK_MISMATCH"
R_SCORE = "SCORE_MISMATCH"
R_SOURCE = "SOURCE_UNAVAILABLE"
R_NEAR = "NEAR_NEIGHBOR"

_NUM_RE = re.compile(r"[0-9]+")
_LATIN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")
_WS_RE = re.compile(r"\s+")
_SELECT_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


# ─────────────────────────── 规范化与哈希 ───────────────────────────


def normalize(text: str) -> str:
    """NFKC + casefold + 空白折叠（跨方言/跨语言一致的归一）。纯函数，带进程内缓存。"""
    if not isinstance(text, str):
        raise TypeError("text 必须是 str")
    return _normalize_cached(text)


@functools.lru_cache(maxsize=200_000)
def _normalize_cached(text: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def canonical_bytes(obj: Any) -> bytes:
    """确定性 JSON 序列化（排序键、无多余空白、保留非 ASCII）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    """对象 → 确定性 JSON → sha256（十六进制）。"""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def char_shingles(text: str, size: int = 3) -> tuple[str, ...]:
    """字符 n-gram（去空白后切分；n > len 时退化为整串）。带进程内缓存（纯函数）。"""
    return _char_shingles_cached(text, size)


@functools.lru_cache(maxsize=200_000)
def _char_shingles_cached(text: str, size: int) -> tuple[str, ...]:
    flat = _WS_RE.sub("", normalize(text))
    if not flat:
        return ()
    if len(flat) <= size:
        return (flat,)
    return tuple(flat[i : i + size] for i in range(len(flat) - size + 1))


def hashed_vector(text: str, dims: int) -> tuple[float, ...]:
    """确定性签名特征哈希投射：字符 3-gram → dims 维单位向量（带缓存，纯函数）。"""
    return _hashed_vector_cached(text, dims)


@functools.lru_cache(maxsize=200_000)
def _hashed_vector_cached(text: str, dims: int) -> tuple[float, ...]:
    """确定性签名特征哈希投射：字符 3-gram → dims 维单位向量。

    用 blake2b 取代 ``hash()``（后者受 ``PYTHONHASHSEED`` 影响，不可跨进程复现）。
    这是**确定性投影**，不是学习式嵌入：无需外部模型，任何进程任何时候算同一串得同一向量。
    """
    if dims <= 0:
        raise ValueError("dims 必须 > 0")
    vec = [0.0] * dims
    grams = char_shingles(text)
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return tuple(vec)
    return tuple(v / norm for v in vec)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("维度不一致")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def derive_terms(text: str, k: int) -> tuple[str, ...]:
    """从文本确定性抽取关键词项集（拉丁词 len>=3 + CJK 3-gram，按首次出现序取前 k）。"""
    if k <= 0:
        return ()
    norm = normalize(text)
    out: list[str] = []
    seen: set[str] = set()
    for word in _LATIN_RE.findall(norm):
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= k:
            return tuple(out)
    for gram in char_shingles(norm, 3):
        if gram in seen or gram.isdigit():
            continue
        seen.add(gram)
        out.append(gram)
        if len(out) >= k:
            return tuple(out)
    return tuple(out)


def derive_terms_by_rarity(
    text: str,
    rows: Sequence[Mapping[str, Any]],
    k: int,
    *,
    max_candidates: int = 240,
) -> tuple[str, ...]:
    """按**池内稀有度**挑关键词项：df 升序取前 k（df 相同则取更长的词项）。

    只挑高频泛词（"提案/节点/机制"）会让无关文本也轻松过匹配率阈值——那是误报的主源；
    反之，池内一次都没出现的词项对本次检索零判别力（永远匹配不到任何行），一并剔除。
    稀有度口径必须相对**被检索的池**定义，而池由快照指纹钉住、词项又显式写进规格，
    因此复算仍是逐字确定的（同一指纹 ⇒ 同一词项集 ⇒ 同一命中）。
    """
    if k <= 0:
        return ()
    candidates: list[str] = []
    seen: set[str] = set()
    norm = normalize(text)
    for gram in char_shingles(norm, 3):
        if gram.isdigit() or gram in seen:
            continue
        seen.add(gram)
        candidates.append(gram)
        if len(candidates) >= max_candidates:
            break
    if not candidates:
        return derive_terms(text, k)
    haystacks = [normalize(str(row.get("text") or "")) for row in rows]
    scored: list[tuple[int, int, int, str]] = []
    for order, term in enumerate(candidates):
        df = sum(1 for hay in haystacks if term in hay)
        if df == 0:
            continue  # 池内一次都没出现：对本次检索毫无判别力，剔除
        scored.append((df, -len(term), order, term))
    if not scored:
        return derive_terms(text, k)
    scored.sort()
    return tuple(term for _, _, _, term in scored[:k])


# ─────────────────────────── 配置 ───────────────────────────


@dataclass(frozen=True)
class TableSpec:
    """一张被检索的表：名字 + 组成行文本的列（不写死在代码里）。"""

    name: str
    text_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "text_fields": list(self.text_fields)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TableSpec":
        name = data.get("name")
        fields = data.get("text_fields")
        if not isinstance(name, str) or not name:
            raise ValueError("TableSpec.name 非法")
        if not isinstance(fields, Sequence) or not fields or not all(isinstance(f, str) and f for f in fields):
            raise ValueError(f"TableSpec[{name}].text_fields 非法")
        return cls(name=name, text_fields=tuple(fields))


@dataclass(frozen=True)
class EvidenceConfig:
    """复算门的全部可调参数（唯一来源 = ``config/dedup_evidence.json``）。"""

    tables: tuple[TableSpec, ...]
    required_tables: tuple[str, ...]
    required_modes: tuple[str, ...]
    topk_min_jaccard: float = 0.8
    near_neighbor_threshold: float = 0.8
    neighbor_min_votes: int = 2
    neighbor_mode_thresholds: Mapping[str, float] = None  # type: ignore[assignment]
    max_recompute_seconds: float = 2.0
    default_top_k: int = 5
    max_top_k: int = 50
    keyword_top_terms: int = 12
    keyword_match_ratio: float = 0.5
    min_query_chars: int = 20
    vector_dims: int = 64
    vector_threshold: float = 0.35
    score_abs_tolerance: float = 1e-9
    fail_closed: bool = True

    # ── 装载 ──
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceConfig":
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known - {"schema_version", "purpose"}
        if unknown:
            raise ValueError(f"未知配置项: {sorted(unknown)}")
        tables_raw = data.get("tables")
        if not isinstance(tables_raw, Sequence) or not tables_raw:
            raise ValueError("配置缺少 tables")
        kwargs = {k: v for k, v in data.items() if k in known and k != "tables"}
        if "neighbor_mode_thresholds" in kwargs:
            raw = kwargs["neighbor_mode_thresholds"]
            if not isinstance(raw, Mapping):
                raise ValueError("neighbor_mode_thresholds 必须是对象")
            bad = set(raw) - set(MODES)
            if bad:
                raise ValueError(f"neighbor_mode_thresholds 含未知模式: {sorted(bad)}")
            kwargs["neighbor_mode_thresholds"] = {str(k): float(v) for k, v in raw.items()}
        for key in ("required_tables", "required_modes"):
            if key in kwargs:
                kwargs[key] = tuple(kwargs[key])
        cfg = cls(tables=tuple(TableSpec.from_dict(t) for t in tables_raw), **kwargs)  # type: ignore[arg-type]
        known_names = {t.name for t in cfg.tables}
        missing = set(cfg.required_tables) - known_names
        if missing:
            raise ValueError(f"required_tables 未在 tables 中声明: {sorted(missing)}")
        bad_modes = set(cfg.required_modes) - set(MODES)
        if bad_modes:
            raise ValueError(f"required_modes 含未知模式: {sorted(bad_modes)}")
        return cfg

    @classmethod
    def load(cls, path: str | Path | None) -> "EvidenceConfig":
        if path is None:
            raise ValueError("dedup_evidence 配置路径缺失（fail-closed，不给默认阈值）")
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def mode_threshold(self, mode: str) -> float:
        """近邻判定用的模式阈值（配置未给则该模式回退到 near_neighbor_threshold）。"""
        thresholds = self.neighbor_mode_thresholds or {}
        return float(thresholds.get(mode, self.near_neighbor_threshold))

    def table(self, name: str) -> TableSpec:
        for spec in self.tables:
            if spec.name == name:
                return spec
        raise KeyError(f"未声明的表: {name}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if key == "tables":
                out[key] = [t.to_dict() for t in value]
            elif isinstance(value, tuple):
                out[key] = list(value)
            else:
                out[key] = value
        return out


DEFAULT_CONFIG_PATH = "config/dedup_evidence.json"


# ─────────────────────────── 数据源 ───────────────────────────


class TableSource(Protocol):
    """只读表源：按 id 有序返回 ``[{id, text}]``。"""

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:  # pragma: no cover - 协议
        ...

    def statements(self) -> Sequence[str]:
        """本次会话执行过的语句（只读审计用）。"""
        ...


def _row_pairs(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in rows:
        if "id" not in row or "text" not in row:
            raise ValueError("行必须含 id/text 字段")
        pairs.append((str(row["id"]), str(row["text"])))
    pairs.sort(key=lambda p: (len(p[0]), p[0]))
    return pairs


@dataclass(frozen=True)
class MemorySource:
    """内存表源（测试夹具/离线复算）。"""

    tables: Mapping[str, Sequence[Mapping[str, Any]]]

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:
        if table not in self.tables:
            raise KeyError(f"源中无此表: {table}")
        return self.tables[table]

    def statements(self) -> Sequence[str]:
        return ()


class JsonSnapshotSource:
    """JSON 快照文件源（``{"tables": {name: [{id, text}]}}``）。"""

    def __init__(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tables = data.get("tables")
        if not isinstance(tables, Mapping):
            raise ValueError("快照文件缺少 tables")
        self.path = Path(path)
        self._tables: dict[str, Sequence[Mapping[str, Any]]] = {
            str(k): list(v) for k, v in tables.items()
        }

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:
        if table not in self._tables:
            raise KeyError(f"快照中无此表: {table}")
        return self._tables[table]

    def statements(self) -> Sequence[str]:
        return (f"-- json snapshot {self.path.name}",)


class DbapiSource:
    """DB-API 只读表源（sqlite3 / psycopg2 均可）：只允许 SELECT/WITH。

    ``queries`` 由配置/调用方给出，形如
    ``{"campaign_nodes": "SELECT id, name AS text FROM campaign_nodes ORDER BY id"}``——
    表名列名不在代码里。
    """

    def __init__(self, connection: Any, queries: Mapping[str, str]) -> None:
        for table, sql in queries.items():
            if not _SELECT_RE.match(sql):
                raise ValueError(f"只读源拒绝非 SELECT 语句: {table}")
        self.connection = connection
        self.queries = dict(queries)
        self._statements: list[str] = []
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:
        if table in self._cache:
            return self._cache[table]
        sql = self.queries.get(table)
        if sql is None:
            raise KeyError(f"无此表的查询定义: {table}")
        cur = self.connection.execute(sql)
        rows = [{"id": r[0], "text": "" if r[1] is None else str(r[1])} for r in cur.fetchall()]
        self._statements.append(sql)
        self._cache[table] = rows
        return rows

    def statements(self) -> Sequence[str]:
        return tuple(self._statements)


# ─────────────────────────── 快照指纹 ───────────────────────────


@dataclass(frozen=True)
class TableFingerprint:
    table: str
    row_count: int
    max_id: str
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "max_id": self.max_id,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TableFingerprint":
        return cls(
            table=str(data["table"]),
            row_count=int(data["row_count"]),
            max_id=str(data["max_id"]),
            content_sha256=str(data["content_sha256"]),
        )


@dataclass(frozen=True)
class SnapshotFingerprint:
    tables: tuple[TableFingerprint, ...]
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": [t.to_dict() for t in sorted(self.tables, key=lambda t: t.table)],
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SnapshotFingerprint":
        raw = data.get("tables")
        if not isinstance(raw, Sequence) or not raw:
            raise ValueError("快照指纹缺少 tables")
        tables = tuple(TableFingerprint.from_dict(t) for t in raw)
        claim = str(data.get("sha256") or "")
        if not claim:
            raise ValueError("快照指纹缺少 sha256")
        return cls(tables=tables, sha256=claim)

    def table(self, name: str) -> TableFingerprint:
        for fp in self.tables:
            if fp.table == name:
                return fp
        raise KeyError(f"快照指纹无此表: {name}")

    def recomputed_sha256(self) -> str:
        """由表指纹重算聚合 sha256（用于检出「改表指纹但保留旧聚合哈希」的伪造）。"""
        payload = [fp.to_dict() for fp in sorted(self.tables, key=lambda t: t.table)]
        return sha256_hex(payload)


def table_fingerprint(table: str, rows: Sequence[Mapping[str, Any]]) -> TableFingerprint:
    """``(行数, max(id), 内容 sha256)`` 三元组（内容哈希覆盖 (id, text) 全量）。"""
    pairs = _row_pairs(rows)
    max_id = ""
    for pid, _ in pairs:
        if len(pid) > len(max_id) or (len(pid) == len(max_id) and pid > max_id):
            max_id = pid
    return TableFingerprint(
        table=table,
        row_count=len(pairs),
        max_id=max_id,
        content_sha256=sha256_hex([list(p) for p in pairs]),
    )


def snapshot_fingerprint(source: TableSource, cfg: EvidenceConfig) -> SnapshotFingerprint:
    """对整个被检索快照取指纹（表集合来自配置）。"""
    fps = tuple(table_fingerprint(spec.name, source.rows(spec.name)) for spec in cfg.tables)
    payload = [fp.to_dict() for fp in sorted(fps, key=lambda f: f.table)]
    return SnapshotFingerprint(tables=fps, sha256=sha256_hex(payload))


# ─────────────────────────── 查询规格 ───────────────────────────


@dataclass(frozen=True)
class QuerySpec:
    """一条可序列化、可重放的检索规格（禁止自由文本「我查过了」）。"""

    query_id: str
    table: str
    mode: str
    text: str
    terms: tuple[str, ...]
    top_k: int
    threshold: float
    match_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "table": self.table,
            "mode": self.mode,
            "text": self.text,
            "terms": list(self.terms),
            "top_k": self.top_k,
            "threshold": round(float(self.threshold), 12),
            "match_ratio": round(float(self.match_ratio), 12),
        }

    def sha256(self) -> str:
        return sha256_hex(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, cfg: EvidenceConfig) -> "QuerySpec":
        required = {"query_id", "table", "mode", "text", "terms", "top_k", "threshold", "match_ratio"}
        missing = required - set(data)
        if missing:
            raise ValueError(f"QuerySpec 缺字段: {sorted(missing)}")
        unknown = set(data) - required
        if unknown:
            raise ValueError(f"QuerySpec 未知字段: {sorted(unknown)}")
        mode = str(data["mode"])
        if mode not in MODES:
            raise ValueError(f"未知检索模式: {mode}")
        table = str(data["table"])
        try:
            cfg.table(table)  # 未声明的表直接拒
        except KeyError as exc:
            raise ValueError(f"未声明的表: {table}") from exc
        text = data["text"]
        if not isinstance(text, str) or len(normalize(text)) < cfg.min_query_chars:
            raise ValueError(f"查询文本过短（<{cfg.min_query_chars} 字符）")
        terms = data["terms"]
        if not isinstance(terms, Sequence) or isinstance(terms, str):
            raise ValueError("terms 必须是字符串序列")
        term_tuple = tuple(str(t) for t in terms)
        if not term_tuple:
            raise ValueError("terms 为空（空词项集 = 没有检索，拒）")
        top_k = int(data["top_k"])
        if not 1 <= top_k <= cfg.max_top_k:
            raise ValueError(f"top_k 越界: {top_k}")
        return cls(
            query_id=str(data["query_id"]),
            table=table,
            mode=mode,
            text=text,
            terms=term_tuple,
            top_k=top_k,
            threshold=float(data["threshold"]),
            match_ratio=float(data["match_ratio"]),
        )


@dataclass(frozen=True)
class Hit:
    key: str
    table: str
    row_id: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "table": self.table, "row_id": self.row_id, "score": round(self.score, 12)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Hit":
        return cls(
            key=str(data["key"]),
            table=str(data["table"]),
            row_id=str(data["row_id"]),
            score=float(data["score"]),
        )


@dataclass(frozen=True)
class QueryOutcome:
    query_id: str
    spec_sha256: str
    hit_count: int
    topk: tuple[Hit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "spec_sha256": self.spec_sha256,
            "hit_count": self.hit_count,
            "topk": [h.to_dict() for h in self.topk],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueryOutcome":
        return cls(
            query_id=str(data["query_id"]),
            spec_sha256=str(data["spec_sha256"]),
            hit_count=int(data["hit_count"]),
            topk=tuple(Hit.from_dict(h) for h in data.get("topk") or ()),
        )


def _score(spec: QuerySpec, row_text: str, row_vec: Sequence[float] | None, cfg: EvidenceConfig) -> float:
    """单行打分（纯函数，确定性）。"""
    if spec.mode == "vector":
        vec = hashed_vector(row_text, cfg.vector_dims)
        return cosine(hashed_vector(spec.text, cfg.vector_dims), vec)
    if spec.mode == "ilike":
        hay = normalize(row_text)
        matched = sum(1 for term in spec.terms if normalize(term) in hay)
        return matched / len(spec.terms) if spec.terms else 0.0
    if spec.mode == "keyword_set":
        hay = set(char_shingles(row_text, 3))
        hay_norm = normalize(row_text)
        matched = 0
        for term in spec.terms:
            t = normalize(term)
            if t in hay or t in hay_norm:
                matched += 1
        return matched / len(spec.terms) if spec.terms else 0.0
    raise ValueError(f"未知模式: {spec.mode}")


def run_query(source: TableSource, spec: QuerySpec, cfg: EvidenceConfig) -> QueryOutcome:
    """执行一条规格：返回命中计数 + top-k 近邻。"""
    rows = source.rows(spec.table)
    scored: list[tuple[float, str, str]] = []
    hits = 0
    for row in rows:
        text = str(row.get("text") or "")
        score = _score(spec, text, None, cfg)
        threshold = spec.threshold if spec.mode == "vector" else spec.match_ratio
        if score >= threshold and score > 0.0:
            hits += 1
            scored.append((score, str(row.get("id")), text))
    scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
    topk = tuple(
        Hit(key=f"{spec.table}:{row_id}", table=spec.table, row_id=row_id, score=score)
        for score, row_id, _ in scored[: spec.top_k]
    )
    return QueryOutcome(
        query_id=spec.query_id,
        spec_sha256=spec.sha256(),
        hit_count=hits,
        topk=topk,
    )


# ─────────────────────────── 证据工件 ───────────────────────────


@dataclass(frozen=True)
class DedupEvidence:
    snapshot: SnapshotFingerprint
    specs: tuple[QuerySpec, ...]
    outcomes: tuple[QueryOutcome, ...]
    schema_version: str = SCHEMA_VERSION

    def content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_dict(),
            "specs": [s.to_dict() for s in self.specs],
            "outcomes": [o.to_dict() for o in self.outcomes],
        }

    def sha256(self) -> str:
        return sha256_hex(self.content_dict())

    def to_dict(self) -> dict[str, Any]:
        out = self.content_dict()
        out["evidence_sha256"] = self.sha256()
        return out

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


def build_evidence(source: TableSource, specs: Sequence[QuerySpec], cfg: EvidenceConfig) -> DedupEvidence:
    """取证：在**当前快照**上执行规格集，落成可复算证据工件。"""
    snap = snapshot_fingerprint(source, cfg)
    outcomes = tuple(run_query(source, spec, cfg) for spec in specs)
    return DedupEvidence(snapshot=snap, specs=tuple(specs), outcomes=outcomes)


def specs_for_proposal(
    text: str,
    cfg: EvidenceConfig,
    *,
    query_prefix: str = "q",
    top_k: int | None = None,
    source: TableSource | None = None,
) -> tuple[QuerySpec, ...]:
    """按「三重检索」纪律为一份提案文本生成规格集（每表一条，模式轮转覆盖）。

    表集合/模式集合来自配置；词项集由文本确定性抽取并**写进规格**（可重放）。
    """
    k = top_k or cfg.default_top_k
    if source is not None:
        pool: list[Mapping[str, Any]] = []
        for table in cfg.required_tables:
            pool.extend(source.rows(table))
        terms = derive_terms_by_rarity(text, pool, cfg.keyword_top_terms)
    else:
        terms = derive_terms(text, cfg.keyword_top_terms)
    if not terms:
        raise ValueError("提案文本过短/无词项，无法生成检索规格")
    specs: list[QuerySpec] = []
    for table in cfg.required_tables:
        for mode in cfg.required_modes:
            specs.append(
                QuerySpec(
                    query_id=f"{query_prefix}:{table}:{mode}",
                    table=table,
                    mode=mode,
                    text=text,
                    terms=terms,
                    top_k=k,
                    threshold=cfg.vector_threshold,
                    match_ratio=cfg.keyword_match_ratio,
                )
            )
    return tuple(specs)


# ─────────────────────────── 复算与判定 ───────────────────────────


@dataclass(frozen=True)
class Verification:
    verdict: str
    reason_codes: tuple[str, ...]
    details: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]
    snapshot_match: bool | None
    elapsed_ms: float
    recommendation: str | None = None
    readonly_statements: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "details": list(self.details),
            "checks": list(self.checks),
            "snapshot_match": self.snapshot_match,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "recommendation": self.recommendation,
            "readonly_statements": list(self.readonly_statements),
        }


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def _reject(reasons: Sequence[str], details: Sequence[str], checks: Sequence[dict[str, Any]],
            started: float, *, snap_match: bool | None, statements: Sequence[str] = ()) -> Verification:
    return Verification(
        verdict=VERDICT_REJECT,
        reason_codes=tuple(dict.fromkeys(reasons)),
        details=tuple(details),
        checks=tuple(checks),
        snapshot_match=snap_match,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        readonly_statements=tuple(statements),
    )


def verify_evidence(
    evidence: Mapping[str, Any] | str | None,
    source: TableSource,
    cfg: EvidenceConfig,
) -> Verification:
    """复算门：收养前调用。fail-closed —— 任何「看不清」都判 REJECT。"""
    started = time.perf_counter()
    statements: Sequence[str] = ()
    if evidence is None:
        return _reject([R_MISSING], ["提案未附 dedup_evidence（自由文本『我查过了』不算证据）"], [], started, snap_match=None)
    if isinstance(evidence, str):
        try:
            parsed = json.loads(evidence)
        except json.JSONDecodeError as exc:
            return _reject([R_MALFORMED], [f"evidence 是字符串且非 JSON: {exc}"], [], started, snap_match=None)
        if not isinstance(parsed, Mapping):
            return _reject([R_MALFORMED], ["evidence JSON 顶层不是对象"], [], started, snap_match=None)
        evidence = parsed
    if not isinstance(evidence, Mapping):
        return _reject([R_MALFORMED], [f"evidence 类型非法: {type(evidence).__name__}"], [], started, snap_match=None)

    # ① 结构校验
    for key in ("schema_version", "snapshot", "specs", "outcomes", "evidence_sha256"):
        if key not in evidence:
            return _reject([R_MALFORMED], [f"evidence 缺字段 {key}"], [], started, snap_match=None)
    if str(evidence["schema_version"]) != SCHEMA_VERSION:
        return _reject([R_MALFORMED], [f"schema_version 不匹配: {evidence['schema_version']} != {SCHEMA_VERSION}"], [], started, snap_match=None)
    try:
        snapshot = SnapshotFingerprint.from_dict(evidence["snapshot"])
        specs = tuple(QuerySpec.from_dict(s, cfg=cfg) for s in evidence["specs"])
        outcomes = tuple(QueryOutcome.from_dict(o) for o in evidence["outcomes"])
    except (KeyError, TypeError, ValueError) as exc:
        return _reject([R_MALFORMED], [f"evidence 结构非法: {exc}"], [], started, snap_match=None)
    if not specs:
        return _reject([R_MISSING], ["specs 为空：没有检索规格 = 没有证据"], [], started, snap_match=None)
    if len({s.query_id for s in specs}) != len(specs):
        return _reject([R_MALFORMED], ["query_id 重复"], [], started, snap_match=None)
    if len(outcomes) != len(specs):
        return _reject([R_MALFORMED], [f"outcomes 数量 {len(outcomes)} != specs 数量 {len(specs)}"], [], started, snap_match=None)

    # ② 证据内容哈希（改计数/删近邻/改指纹而未重算哈希 → 直接判篡改）
    claim = str(evidence.get("evidence_sha256") or "")
    recomputed = DedupEvidence(snapshot=snapshot, specs=specs, outcomes=outcomes).sha256()
    if claim != recomputed:
        return _reject([R_TAMPERED], [f"evidence_sha256 失配: claim={claim[:16]}… recomputed={recomputed[:16]}…"], [], started, snap_match=None)

    # ③ 规格自证（spec_sha256 必须与规格内容一致，且每条 outcome 归属于对应规格）
    by_id = {s.query_id: s for s in specs}
    for outcome in outcomes:
        spec = by_id.get(outcome.query_id)
        if spec is None:
            return _reject([R_MALFORMED], [f"outcome 无对应规格: {outcome.query_id}"], [], started, snap_match=None)
        if outcome.spec_sha256 != spec.sha256():
            return _reject([R_SPEC_TAMPERED], [f"规格哈希失配: {outcome.query_id}"], [], started, snap_match=None)

    # ④ 三重检索覆盖（每张必需表至少一条规格；模式需覆盖)
    covered_tables = {s.table for s in specs}
    missing_tables = [t for t in cfg.required_tables if t not in covered_tables]
    if missing_tables:
        return _reject([R_SPEC_COVERAGE], [f"检索未覆盖必需表: {missing_tables}"], [], started, snap_match=None)
    covered_modes = {s.mode for s in specs}
    missing_modes = [m for m in cfg.required_modes if m not in covered_modes]
    if missing_modes:
        return _reject([R_SPEC_COVERAGE], [f"检索未覆盖必需模式: {missing_modes}"], [], started, snap_match=None)

    # ④' 快照指纹自洽（改表指纹但保留旧聚合哈希 = 伪造）
    if snapshot.recomputed_sha256() != snapshot.sha256:
        return _reject(
            [R_SNAPSHOT_STALE],
            [f"快照指纹内部不自洽: 表指纹重算 {snapshot.recomputed_sha256()[:16]}… != 声明 {snapshot.sha256[:16]}…"],
            [], started, snap_match=False,
        )

    # ⑤ 复算：源不可达 → fail-closed
    try:
        live_snap = snapshot_fingerprint(source, cfg)
        statements = tuple(source.statements())
    except Exception as exc:  # noqa: BLE001 - 任何源错误都 fail-closed
        return _reject([R_SOURCE], [f"数据源不可达/读表失败: {type(exc).__name__}: {exc}"], [], started, snap_match=None)

    snap_match = live_snap.sha256 == snapshot.sha256
    if not snap_match:
        diffs: list[str] = []
        live_by_table = {fp.table: fp for fp in live_snap.tables}
        for fp in snapshot.tables:
            live = live_by_table.get(fp.table)
            if live is None:
                diffs.append(f"{fp.table}: 现网无此表")
            elif live != fp:
                diffs.append(
                    f"{fp.table}: 行数 {fp.row_count}→{live.row_count}, "
                    f"max_id {fp.max_id}→{live.max_id}, 内容哈希 {fp.content_sha256[:12]}…→{live.content_sha256[:12]}…"
                )
        return _reject(
            [R_SNAPSHOT_STALE],
            ["快照指纹与当前库不一致（旧快照冒充/伪造指纹/取证后池已变更）: " + "; ".join(diffs or ["聚合哈希不等"])],
            [], started, snap_match=False, statements=statements,
        )

    checks: list[dict[str, Any]] = []
    reasons: list[str] = []
    details: list[str] = []
    for spec, claim_outcome in zip(specs, outcomes):
        try:
            live_outcome = run_query(source, spec, cfg)
        except Exception as exc:  # noqa: BLE001
            return _reject([R_SOURCE], [f"复算失败 {spec.query_id}: {type(exc).__name__}: {exc}"], checks, started, snap_match=True)
        jac = _jaccard([h.key for h in claim_outcome.topk], [h.key for h in live_outcome.topk])
        score_delta = 0.0
        live_scores = {h.key: h.score for h in live_outcome.topk}
        for hit in claim_outcome.topk:
            if hit.key in live_scores:
                score_delta = max(score_delta, abs(hit.score - live_scores[hit.key]))
        count_ok = claim_outcome.hit_count == live_outcome.hit_count
        topk_ok = jac >= cfg.topk_min_jaccard
        score_ok = score_delta <= cfg.score_abs_tolerance
        if not count_ok:
            reasons.append(R_COUNT)
            details.append(f"{spec.query_id}: 命中计数失配 claim={claim_outcome.hit_count} live={live_outcome.hit_count}")
        if not topk_ok:
            reasons.append(R_TOPK)
            details.append(f"{spec.query_id}: top-k 集合 Jaccard={jac:.3f} < {cfg.topk_min_jaccard}")
        if not score_ok:
            reasons.append(R_SCORE)
            details.append(f"{spec.query_id}: top-k 分数差 {score_delta:.3e} > {cfg.score_abs_tolerance}")
        checks.append(
            {
                "query_id": spec.query_id,
                "table": spec.table,
                "mode": spec.mode,
                "claim_hit_count": claim_outcome.hit_count,
                "live_hit_count": live_outcome.hit_count,
                "count_ok": count_ok,
                "topk_jaccard": round(jac, 6),
                "topk_ok": topk_ok,
                "score_delta": score_delta,
                "score_ok": score_ok,
                "claim_topk": [h.key for h in claim_outcome.topk],
                "live_topk": [h.key for h in live_outcome.topk],
            }
        )

    if reasons:
        return _reject(reasons, details, checks, started, snap_match=True, statements=statements)

    # ⑥ 一致 ⇒ 看近邻是否过近：过近则转「深化/合并」建议（不是放行新建，也不是判死）
    # 判定口径 = 逐表「模式互证」：同一张表里至少 neighbor_min_votes 个模式都给出
    # ≥ 各自阈值的 top-1 近邻，才算「池里确实有近邻」。单模式单表易过杀（合成向量
    # 对无关长文本也给高余弦），互证把误报压下去。
    signal = nearest_signal(outcomes, specs, cfg)
    if signal["deepen"]:
        nearest = signal["nearest"]
        return Verification(
            verdict=VERDICT_DEEPEN,
            reason_codes=(R_NEAR,),
            details=[f"证据复算一致，但近邻互证通过：{nearest['table']} 表 {nearest['votes']}/{len(cfg.required_modes)} "
                     f"个模式给出近邻（top-1 {nearest['key']} @ {nearest['score']:.3f}）"],
            checks=tuple(checks),
            snapshot_match=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            recommendation=f"DEEPEN_OR_MERGE: 建议深化/合并到 {nearest['key']}，不新建孤儿提案",
            readonly_statements=statements,
        )

    return Verification(
        verdict=VERDICT_ACCEPT,
        reason_codes=(),
        details=["证据复算一致：快照指纹相同，命中计数精确相等，top-k 集合一致"],
        checks=tuple(checks),
        snapshot_match=True,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        readonly_statements=statements,
    )


def nearest_signal(
    outcomes: Sequence[QueryOutcome],
    specs: Sequence[QuerySpec],
    cfg: EvidenceConfig,
) -> dict[str, Any]:
    """逐表模式互证：返回 ``{deepen, nearest, per_table}``。

    同一张表内，统计 top-1 相似度 ≥ 该模式阈值的模式个数（votes）；
    任一表 votes ≥ ``neighbor_min_votes`` 即认为池中确有近邻。
    """
    by_query = {o.query_id: o for o in outcomes}
    per_table: dict[str, dict[str, Any]] = {}
    best: dict[str, Any] | None = None
    for spec in specs:
        outcome = by_query.get(spec.query_id)
        if outcome is None:
            continue
        slot = per_table.setdefault(spec.table, {"votes": 0, "modes": {}})
        top = outcome.topk[0] if outcome.topk else None
        score = top.score if top else 0.0
        slot["modes"][spec.mode] = {"top1_score": round(score, 6), "top1_key": top.key if top else None,
                                    "threshold": cfg.mode_threshold(spec.mode)}
        if top is not None and score >= cfg.mode_threshold(spec.mode):
            slot["votes"] += 1
            cand = {"table": spec.table, "key": top.key, "score": score, "votes": 0}
            if best is None or (cand["score"], spec.table) > (best["score"], best["table"]):
                best = cand
    votes = {t: int(v["votes"]) for t, v in per_table.items()}
    deepen = any(v >= cfg.neighbor_min_votes for v in votes.values())
    nearest = None
    if best is not None:
        best["votes"] = int(votes.get(best["table"], 0))
        nearest = best
    return {"deepen": deepen, "nearest": nearest, "per_table": per_table, "votes": votes}


def enforce_adoption(proposal: Mapping[str, Any], source: TableSource, cfg: EvidenceConfig) -> Verification:
    """收养入口：提案（含 ``dedup_evidence`` 字段）→ 复算判定。

    与 ``verify_evidence`` 的唯一区别是取字段位置；缺字段即 ``MISSING_EVIDENCE``。
    """
    return verify_evidence(proposal.get("dedup_evidence"), source, cfg)


# ─────────────────────────── 回放判别力 ───────────────────────────


@dataclass(frozen=True)
class ReplayRecord:
    """一条「检索判死」回放记录：候选文本 + 参照标签 + 参照证据。"""

    record_id: str
    text: str
    label_dup: bool
    label_score: float
    label_against: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "label_dup": self.label_dup,
            "label_score": round(self.label_score, 6),
            "label_against": self.label_against,
            "text_sha256": sha256_hex(self.text),
        }


def confusion(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
    accuracy = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1, "accuracy": accuracy,
        "n": tp + fp + fn + tn,
    }