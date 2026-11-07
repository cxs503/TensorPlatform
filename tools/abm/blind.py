"""预测盲登记器（commit–reveal）：先承诺、后观测，事后不可改预测。

机制
----
1. ``register``：登记假设卡（``card_id``/``hypothesis``/``predictions``/``tolerances``），
   立即算出 **承诺哈希** = sha256(canonical({card_id, hypothesis, predictions,
   tolerances, rationale, salt}))。``salt`` 由调用方提供（可空串），用于避免猜测式穷举。
2. ``seal``：封存，之后才允许观测。封存时把承诺哈希写进账本。
3. ``observe``：登记实测值。**未封存即观测 = 违规**，直接抛错（防「先跑后编预测」）。
4. ``score``：逐项算相对误差，与承诺时给出的容差比对，输出命中表。

盲性可机器审计：账本中 ``card.seal`` 的 seq 必须严格小于同一 card 首个
``card.observe`` 的 seq（``blind_integrity`` 复算）。预测值只接受有限实数，
拒绝字符串/None/NaN，避免「预测写成范围后再挑选」。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .ledger import TraceLedger, canonical_json, sha256_hex  # noqa: F401


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


class BlindRegistry:
    """预测盲登记器。``ledger`` 与 ``ts`` 可选：给出则自动留痕。"""

    def __init__(self, ledger: Optional[TraceLedger] = None, ts: Optional[str] = None):
        self.ledger = ledger
        self.ts = ts
        self._cards: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ register
    def register(
        self,
        card_id: str,
        hypothesis: str,
        predictions: Dict[str, float],
        tolerances: Dict[str, float],
        *,
        rationale: str = "",
        actor_role: str = "theorist",
        salt: str = "",
        falsifiable_by: str = "",
    ) -> Dict[str, Any]:
        cid = str(card_id)
        if cid in self._cards:
            raise ValueError(f"假设卡已存在（预测盲不允许覆盖）: {cid}")
        if not isinstance(predictions, dict) or not predictions:
            raise ValueError("predictions 必须是非空 dict")
        for k, v in predictions.items():
            if not _is_num(v):
                raise ValueError(f"预测项 {k} 必须是有限实数（禁止区间/文字后再挑选）")
        missing_tol = [k for k in predictions if k not in tolerances]
        if missing_tol:
            raise ValueError(f"缺少容差: {missing_tol}")
        for k, v in tolerances.items():
            if not _is_num(v) or float(v) <= 0:
                raise ValueError(f"容差 {k} 必须是正实数")
        payload = {
            "card_id": cid,
            "hypothesis": str(hypothesis),
            "predictions": {k: float(v) for k, v in predictions.items()},
            "tolerances_rel": {k: float(v) for k, v in tolerances.items()},
            "rationale": str(rationale),
            "falsifiable_by": str(falsifiable_by),
            "salt": str(salt),
        }
        commitment = sha256_hex(payload)
        card = {
            "card_id": cid,
            "commitment_sha256": commitment,
            "payload": payload,
            "sealed": False,
            "observations": {},
            "scored": None,
        }
        self._cards[cid] = card
        if self.ledger is not None:
            self.ledger.append(
                "blind.card.register",
                actor_role,
                {"card_id": cid, "commitment_sha256": commitment,
                 "predictions": payload["predictions"],
                 "tolerances_rel": payload["tolerances_rel"],
                 "hypothesis": payload["hypothesis"]},
                gate="hypotheses_blind",
                ts=self.ts,
            )
        return dict(card)

    # ------------------------------------------------------------------ seal
    def seal(self, card_id: str, *, actor_role: str = "verifier") -> Dict[str, Any]:
        cid = str(card_id)
        card = self._require(cid)
        if card["sealed"]:
            raise ValueError(f"重复封存: {cid}")
        if card["observations"]:
            raise ValueError(f"{cid} 已有观测，无法再封存（预测盲被破坏）")
        card["sealed"] = True
        if self.ledger is not None:
            self.ledger.append(
                "blind.card.seal",
                actor_role,
                {"card_id": cid, "commitment_sha256": card["commitment_sha256"]},
                gate="hypotheses_blind",
                ts=self.ts,
            )
        return {"card_id": cid, "sealed": True, "commitment_sha256": card["commitment_sha256"]}

    # ------------------------------------------------------------------ observe
    def observe(self, card_id: str, observations: Dict[str, float], *,
                actor_role: str = "verifier", source: str = "") -> Dict[str, Any]:
        cid = str(card_id)
        card = self._require(cid)
        if not card["sealed"]:
            raise ValueError(f"{cid} 未封存即观测：预测盲违规")
        unknown = [k for k in observations if k not in card["payload"]["predictions"]]
        if unknown:
            raise ValueError(f"{cid} 观测含未预测项: {unknown}（禁止事后补靶）")
        for k, v in observations.items():
            if not _is_num(v):
                raise ValueError(f"观测项 {k} 必须是有限实数")
        card["observations"].update({k: float(v) for k, v in observations.items()})
        if self.ledger is not None:
            self.ledger.append(
                "blind.card.observe",
                actor_role,
                {"card_id": cid, "observations": {k: float(v) for k, v in observations.items()},
                 "source": str(source)},
                gate="experiment",
                ts=self.ts,
                evidence_refs=[str(source)] if source else [],
            )
        return {"card_id": cid, "observations": dict(card["observations"])}

    # ------------------------------------------------------------------ score
    def score(self, card_id: str) -> Dict[str, Any]:
        cid = str(card_id)
        card = self._require(cid)
        preds = card["payload"]["predictions"]
        tols = card["payload"]["tolerances_rel"]
        items: List[Dict[str, Any]] = []
        for k, predicted in preds.items():
            if k not in card["observations"]:
                items.append({"metric": k, "predicted": predicted, "observed": None,
                              "rel_error": None, "tolerance_rel": tols[k], "hit": None})
                continue
            observed = card["observations"][k]
            denom = abs(observed) if abs(observed) > 1e-30 else 1e-30
            rel = abs(observed - predicted) / denom
            items.append({
                "metric": k,
                "predicted": predicted,
                "observed": observed,
                "rel_error": round(float(rel), 8),
                "tolerance_rel": tols[k],
                "hit": bool(rel <= tols[k]),
            })
        scored_items = [i for i in items if i["hit"] is not None]
        n_hit = sum(1 for i in scored_items if i["hit"])
        card["scored"] = {
            "card_id": cid,
            "hypothesis": card["payload"]["hypothesis"],
            "items": items,
            "n_scored": len(scored_items),
            "n_hit": n_hit,
            "hit_rate": round(n_hit / len(scored_items), 6) if scored_items else None,
            "falsified": bool(scored_items) and n_hit == 0,
        }
        if self.ledger is not None:
            self.ledger.append(
                "blind.card.score",
                "verifier",
                {"card_id": cid, "n_hit": n_hit, "n_scored": len(scored_items),
                 "falsified": card["scored"]["falsified"]},
                gate="challenge",
                ts=self.ts,
            )
        return dict(card["scored"])

    # ------------------------------------------------------------------ export
    def _require(self, card_id: str) -> Dict[str, Any]:
        card = self._cards.get(str(card_id))
        if card is None:
            raise KeyError(f"未知假设卡: {card_id}")
        return card

    @property
    def cards(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self._cards.items()}

    def export(self) -> Dict[str, Any]:
        cards = []
        for cid in sorted(self._cards):
            c = self._cards[cid]
            cards.append({
                "card_id": cid,
                "hypothesis": c["payload"]["hypothesis"],
                "commitment_sha256": c["commitment_sha256"],
                "sealed": c["sealed"],
                "predictions": c["payload"]["predictions"],
                "tolerances_rel": c["payload"]["tolerances_rel"],
                "rationale": c["payload"]["rationale"],
                "falsifiable_by": c["payload"]["falsifiable_by"],
                "observations": c["observations"],
                "scored": c["scored"],
            })
        return {"n_cards": len(cards), "n_sealed": sum(1 for c in cards if c["sealed"]),
                "n_scored": sum(1 for c in cards if c["scored"]), "cards": cards}


def blind_integrity(ledger: TraceLedger) -> Dict[str, Any]:
    """复算盲性：每张卡的首个 observe 必须晚于其 seal（且 seal 存在）。"""
    per_card: Dict[str, Dict[str, Optional[int]]] = {}
    for rec in ledger.records:
        cid = (rec.get("payload") or {}).get("card_id")
        if not cid:
            continue
        slot = per_card.setdefault(str(cid), {"register": None, "seal": None, "first_observe": None})
        if rec["record_type"] == "blind.card.register" and slot["register"] is None:
            slot["register"] = int(rec["seq"])
        elif rec["record_type"] == "blind.card.seal" and slot["seal"] is None:
            slot["seal"] = int(rec["seq"])
        elif rec["record_type"] == "blind.card.observe" and slot["first_observe"] is None:
            slot["first_observe"] = int(rec["seq"])
    violations = []
    for cid, slot in sorted(per_card.items()):
        if slot["seal"] is None:
            violations.append({"card_id": cid, "reason": "未封存"})
        elif slot["first_observe"] is not None and slot["first_observe"] < slot["seal"]:
            violations.append({"card_id": cid, "reason": "观测早于封存",
                               "seal_seq": slot["seal"], "first_observe_seq": slot["first_observe"]})
    return {"ok": not violations, "n_cards": len(per_card), "violations": violations,
            "per_card": per_card}