"""蒸馏参数（全部阈值集中在此，逻辑层不写字面量）。

参数以 :class:`DistillParams` 承载，可从 JSON 覆盖（``--config``）；未知字段直接报错
而不是静默忽略——静默忽略会让"我调了阈值"变成假象。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

# 条目类别：解析器只认这三档（外加 unclassified 用于未知字段的显式降级）
ITEM_KINDS = ("limitation", "next_step", "honest_fail")


class ConfigError(ValueError):
    """配置字段非法（未知键 / 类型不符 / 越界）。"""


@dataclass(frozen=True)
class DistillParams:
    """蒸馏引擎阈值与超参。

    Attributes
    ----------
    ngram_min, ngram_max: TF-IDF 与嵌入使用的字符 n-gram 下/上界。
    tfidf_weight: 组合相似度里 TF-IDF 余弦的权重（嵌入权重 = 1 - 该值）。
    emb_dim: 子词哈希嵌入维度。
    eps: DBSCAN 邻域半径（余弦距离，1 - 相似度）；``None`` → 用 ``auto_eps``。
    min_samples: DBSCAN 核心点最小邻居数（含自身）。
    eps_quantile: ``auto_eps`` 取 k-距离分布的哪个分位点做半径。
    min_cluster_size: 成簇后再次过滤，小于该规模的簇不算候选（避免单条成提案）。
    min_intra_similarity: 簇内平均相似度下限（凝聚度门）：低于它说明是单链串联的
        "大杂烩"而不是重复缺口，整簇退回未归属、不出种子。
    novelty_min: 新颖性下限：``novelty = 1 - max_sim`` 必须 ≥ 该值。
    mean_sim_max: 与参考池的平均相似度上限（硬约束）。
    token_min_length_ratio: 词元包含度参与合成的最小长度比（防"短片段被长句完全包含"式假相似）。
    min_item_chars: 条目最短字符数（过滤 "-"、"无" 之类空壳行）。
    title_max_chars: 提案种子标题截断长度。
    markdown_section_aliases: markdown 小节标题关键字（按类别），域无关的元词。
    """

    ngram_min: int = 2
    ngram_max: int = 3
    tfidf_weight: float = 0.5
    emb_dim: int = 256
    eps: float | None = None
    min_samples: int = 2
    eps_quantile: float = 0.6
    min_cluster_size: int = 2
    min_intra_similarity: float = 0.20
    novelty_min: float = 0.55
    mean_sim_max: float = 0.40
    token_min_length_ratio: float = 0.5
    min_item_chars: int = 12
    title_max_chars: int = 96
    markdown_section_aliases: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "limitation": ("局限", "限制", "不足", "注意点", "注意事项", "适用范围", "坑",
                           "limitation", "caveat", "shortcoming"),
            "next_step": ("下一步", "后续", "next step", "next_step", "follow-up", "followup"),
            "honest_fail": ("honest fail", "honest_fail", "honest debt", "失败", "未达成",
                            "未完成", "fail"),
        }
    )

    # ------------------------------------------------------------------ #
    def validate(self) -> "DistillParams":
        errs: list[str] = []
        if not 1 <= self.ngram_min <= self.ngram_max:
            errs.append(f"ngram 区间非法: {self.ngram_min}..{self.ngram_max}")
        if not 0.0 <= self.tfidf_weight <= 1.0:
            errs.append(f"tfidf_weight 越界: {self.tfidf_weight}")
        if self.emb_dim < 8:
            errs.append(f"emb_dim 过小: {self.emb_dim}")
        if self.eps is not None and not 0.0 < self.eps < 2.0:
            errs.append(f"eps 越界: {self.eps}")
        if self.min_samples < 1:
            errs.append(f"min_samples 过小: {self.min_samples}")
        if not 0.0 <= self.eps_quantile <= 1.0:
            errs.append(f"eps_quantile 越界: {self.eps_quantile}")
        if not 0.0 <= self.min_intra_similarity <= 1.0:
            errs.append(f"min_intra_similarity 越界: {self.min_intra_similarity}")
        if self.min_cluster_size < 1:
            errs.append(f"min_cluster_size 过小: {self.min_cluster_size}")
        if not 0.0 <= self.novelty_min <= 1.0:
            errs.append(f"novelty_min 越界: {self.novelty_min}")
        if not 0.0 <= self.mean_sim_max <= 1.0:
            errs.append(f"mean_sim_max 越界: {self.mean_sim_max}")
        if not 0.0 <= self.token_min_length_ratio <= 1.0:
            errs.append(f"token_min_length_ratio 越界: {self.token_min_length_ratio}")
        if self.min_item_chars < 1:
            errs.append(f"min_item_chars 过小: {self.min_item_chars}")
        if self.title_max_chars < 8:
            errs.append(f"title_max_chars 过小: {self.title_max_chars}")
        missing = [k for k in ITEM_KINDS if k not in self.markdown_section_aliases]
        if missing:
            errs.append(f"markdown_section_aliases 缺类别: {missing}")
        if errs:
            raise ConfigError("; ".join(errs))
        return self

    def as_dict(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["markdown_section_aliases"] = {
            k: list(v) for k, v in self.markdown_section_aliases.items()
        }
        return doc

    def digest(self) -> str:
        """参数指纹（进溯源链：报告与种子都可追到"用了哪套阈值"）。"""
        blob = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "DistillParams":
        if not raw:
            return cls().validate()
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ConfigError(f"未知配置字段: {unknown}")
        doc = dict(raw)
        try:
            if "markdown_section_aliases" in doc:
                doc["markdown_section_aliases"] = {
                    str(k): tuple(str(x) for x in v)
                    for k, v in dict(doc["markdown_section_aliases"]).items()
                }
            if "eps" in doc and doc["eps"] is not None:
                doc["eps"] = float(doc["eps"])
            return cls(**doc).validate()
        except (TypeError, ValueError) as exc:  # 类型不符 / 无法转换
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_file(cls, path: str | Path) -> "DistillParams":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ConfigError(f"配置文件必须是 JSON object: {path}")
        return cls.from_dict(raw)


DEFAULT_PARAMS = DistillParams().validate()