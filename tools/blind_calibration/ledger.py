#!/usr/bin/env python3
"""预测计分账本：把「谁在什么时候承诺了什么概率、事后结局是什么」钉成哈希链。

为什么不能只存一张 CSV
======================
校准结论的可信度全在**预测记录不可事后修改**。散装 JSON 里改一个概率，
任何下游统计都会跟着变且无法察觉。故本账本复用平台的通用哈希链原语
（``tools.abm.ledger.TraceLedger``：``hash = sha256(canonical(body + prev))``），
每条预测记录一入账即被链住：

* 改任一字段 → ``verify()`` 复算在首个断点处报错；
* 删/插记录 → 同样被链断裂暴露（后续记录的 ``prev`` 对不上）；
* 账本摘要 ``digest`` 随产物一起落盘，报告里的每个数字都能用摘要溯源到这批记录。

记录口径（刻意极简）
====================
``case_id``（案例标识）· ``prob``（承诺概率）· ``outcome``（0/1）· ``source``
（预测来源标签，自由字符串）· ``group``（分组标签，自由字符串）· ``evidence_refs``
（证据引用，如工件 sha256）。**没有**任何领域字段：物理量名、算例名、阈值都不进账本，
需要时由调用方经 ``source``/``group``/``evidence_refs`` 注入。

纪律
====
* 同一 ``case_id`` 不允许重复入账（覆盖 = 事后改预测，直接抛错）。
* 概率必须 ∈ [0,1] 的有限实数；结局必须 ∈ {0,1}，否则抛错（不静默丢弃）。
* 账本是**只追加**的：``reset=False`` 续写，``reset=True`` 清空重建。
* 账本证明「记录未被事后修改」，**不证明**预测是盲的——盲性由
  ``tools.abm.blind.blind_integrity``（seal 早于 observe）独立复算。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

from tools.abm.ledger import TraceLedger, canonical_json, sha256_hex

from . import metrics

RECORD_TYPE = "calibration.forecast"
SCHEMA = "blind_calibration/ledger/v1"


@dataclass(frozen=True)
class ForecastRecord:
    """一条概率预测（承诺概率 + 事后二元结局）。"""

    case_id: str
    prob: float
    outcome: int
    source: str = ""
    group: str = ""
    evidence_refs: tuple[str, ...] = ()
    seq: int | None = None
    record_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "prob": float(self.prob),
            "outcome": int(self.outcome),
            "source": self.source,
            "group": self.group,
            "evidence_refs": list(self.evidence_refs),
            "seq": self.seq,
            "record_hash": self.record_hash,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ForecastRecord":
        refs = d.get("evidence_refs") or ()
        return ForecastRecord(
            case_id=str(d["case_id"]),
            prob=float(d["prob"]),
            outcome=int(d["outcome"]),
            source=str(d.get("source") or ""),
            group=str(d.get("group") or ""),
            evidence_refs=tuple(str(x) for x in refs),
            seq=d.get("seq"),
            record_hash=d.get("record_hash"),
        )


class CalibrationLedger:
    """只追加、哈希链的预测计分账本（JSONL）。"""

    def __init__(self, path: str | os.PathLike[str], reset: bool = True,
                 ts: str | None = None):
        self.path = str(path)
        self._ts = ts
        self._chain = TraceLedger(self.path, reset=reset)
        self._records: List[ForecastRecord] = []
        self._seen: set[str] = set()
        if not reset:
            for rec in self._chain.records:
                if rec.get("record_type") == RECORD_TYPE:
                    fr = self._from_chain(rec)
                    self._records.append(fr)
                    self._seen.add(fr.case_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _from_chain(rec: dict[str, Any]) -> ForecastRecord:
        p = rec.get("payload") or {}
        return ForecastRecord(
            case_id=str(p["case_id"]),
            prob=float(p["prob"]),
            outcome=int(p["outcome"]),
            source=str(p.get("source") or ""),
            group=str(p.get("group") or ""),
            evidence_refs=tuple(str(x) for x in (p.get("evidence_refs") or ())),
            seq=int(rec["seq"]),
            record_hash=str(rec["hash"]),
        )

    # ------------------------------------------------------------------ append
    def append(self, case_id: str, prob: float, outcome: int, *,
               source: str = "", group: str = "",
               evidence_refs: Iterable[str] = (), ts: str | None = None) -> ForecastRecord:
        cid = str(case_id)
        if not cid:
            raise ValueError("case_id 不能为空")
        if cid in self._seen:
            raise ValueError(f"case_id 已入账（预测不允许覆盖）: {cid}")
        p = float(prob)
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"概率必须在 [0,1]: {prob!r}")
        if outcome not in (0, 1, 0.0, 1.0) or isinstance(outcome, bool):
            raise ValueError(f"结局必须是 0 或 1: {outcome!r}")
        payload = {
            "case_id": cid,
            "prob": p,
            "outcome": int(outcome),
            "source": str(source),
            "group": str(group),
            "evidence_refs": sorted(str(x) for x in evidence_refs),
        }
        rec = self._chain.append(RECORD_TYPE, "forecaster", payload,
                                 gate="calibration", ts=ts or self._ts)
        fr = self._from_chain(rec)
        self._records.append(fr)
        self._seen.add(cid)
        return fr

    def append_many(self, records: Sequence[ForecastRecord | dict[str, Any]]) -> int:
        n = 0
        for r in records:
            fr = r if isinstance(r, ForecastRecord) else ForecastRecord.from_dict(r)
            self.append(fr.case_id, fr.prob, fr.outcome, source=fr.source,
                        group=fr.group, evidence_refs=fr.evidence_refs)
            n += 1
        return n

    # ------------------------------------------------------------------ read
    @property
    def records(self) -> List[ForecastRecord]:
        return list(self._records)

    def arrays(self) -> tuple[list[float], list[int]]:
        return [r.prob for r in self._records], [r.outcome for r in self._records]

    def verify(self) -> dict[str, Any]:
        v = self._chain.verify()
        return {
            "path": self.path,
            "n_records": len(self._records),
            "chain_ok": bool(v.get("ok")),
            "chain_digest": v.get("digest"),
            "first_bad_seq": v.get("first_bad_seq"),
        }

    def dataset_digest(self) -> str:
        """账本内预测集合的内容摘要（与链摘要互为独立校验）。"""
        return sha256_hex([r.to_dict() for r in self._records])

    # ------------------------------------------------------------------ score
    def score(self, n_bins: int = metrics.DEFAULT_N_BINS, **kw: Any) -> dict[str, Any]:
        p, y = self.arrays()
        out = metrics.score(p, y, n_bins, **kw)
        out["provenance"] = self.provenance()
        return out

    def provenance(self) -> dict[str, Any]:
        v = self.verify()
        return {
            "ledger_schema": SCHEMA,
            "ledger_path": self.path,
            "ledger_n": len(self._records),
            "ledger_chain_ok": v["chain_ok"],
            "ledger_chain_digest": v["chain_digest"],
            "ledger_dataset_digest": self.dataset_digest(),
            "sources": sorted({r.source for r in self._records}),
            "groups": sorted({r.group for r in self._records}),
        }


# --------------------------------------------------------------------------- #
# 载入 / 落盘（数据经工件注入，代码不内嵌任何具体案例）
# --------------------------------------------------------------------------- #
def load_forecasts(path: str | os.PathLike[str]) -> List[ForecastRecord]:
    """读 JSON（``{"forecasts":[…]}``或列表）或 JSONL → 记录表。"""
    text = open(path, encoding="utf-8").read()
    if not text.strip():
        raise ValueError(f"预测文件为空: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = data.get("forecasts") if isinstance(data, dict) else data
        if rows is None:
            raise ValueError(f"缺少 forecasts 键: {path}")
    return [ForecastRecord.from_dict(r) for r in rows]


def write_forecasts(path: str | os.PathLike[str], records: Sequence[ForecastRecord],
                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "schema": "blind_calibration/forecasts/v1",
        "n": len(records),
        "meta": meta or {},
        "forecasts": [r.to_dict() for r in records],
    }
    os.makedirs(os.path.dirname(os.path.abspath(str(path))), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "n": len(records), "sha256": _file_sha256(str(path))}


def _file_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# ABM 预测盲桥：把「承诺的置信度 + 容差命中」变成可计分的概率预测
# --------------------------------------------------------------------------- #
def forecasts_from_blind_cards(registry_export: dict[str, Any],
                               confidences: dict[str, dict[str, float]],
                               *, source: str = "abm.blind",
                               evidence_refs: Sequence[str] = ()) -> List[ForecastRecord]:
    """ABM 预测盲 → 计分记录。

    ``registry_export`` 是 ``tools.abm.blind.BlindRegistry.export()`` 的产物；
    ``confidences`` 形如 ``{card_id: {item: p}}``，是**封存前**登记的「该项落在
    容差内的概率」。每张卡的每个已评分项变成一条记录：``outcome = 1`` 当且仅当
    该项命中容差（``scored.items`` 里的 ``hit``）。

    只在项被评分（``scored`` 非空）且给出了置信度时产出记录；两者缺一即跳过并
    在返回的 ``skipped`` 里如实登记原因（不静默丢弃）。
    """
    out: List[ForecastRecord] = []
    for card in registry_export.get("cards", []):
        cid = str(card.get("card_id"))
        scored = card.get("scored") or {}
        items = scored.get("items") or []
        conf = confidences.get(cid) or {}
        for item in items:
            key = str(item.get("item") or item.get("key") or "")
            if not key:
                continue
            if key not in conf:
                continue
            out.append(ForecastRecord(
                case_id=f"{cid}:{key}",
                prob=float(conf[key]),
                outcome=1 if item.get("hit") else 0,
                source=source,
                group=str(card.get("card_id")),
                evidence_refs=tuple(str(x) for x in evidence_refs),
            ))
    return out


def blind_skips(registry_export: dict[str, Any],
                confidences: dict[str, dict[str, float]]) -> List[dict[str, Any]]:
    """如实列出「有卡有项但无法计分」的原因（缺置信度 / 未评分）。"""
    skips: List[dict[str, Any]] = []
    for card in registry_export.get("cards", []):
        cid = str(card.get("card_id"))
        scored = card.get("scored") or {}
        items = scored.get("items") or []
        conf = confidences.get(cid) or {}
        if not items:
            skips.append({"card_id": cid, "reason": "card_not_scored"})
            continue
        for item in items:
            key = str(item.get("item") or item.get("key") or "")
            if key and key not in conf:
                skips.append({"card_id": cid, "item": key,
                              "reason": "no_declared_confidence"})
    return skips