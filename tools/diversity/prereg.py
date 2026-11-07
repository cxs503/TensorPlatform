"""盲预测锁定（prereg）：开跑前锁预测，防"看着结果写预测"。

**为什么必须机器实现而不是纪律要求**：可复盘的盲预测是多样性的**度量前提**——
若预测可在结果之后补齐，"哪一族猜对了"就退化成事后叙事，锦标赛的奖励信号可被
反演（先看分数再写假设）。本模块把三条时序约束做成硬门：

1. **锁先于跑**：`seal()` 发现结果文件已存在且非空 → `PreregError`（拒锁）。
2. **锁内容不可改**：锁工件里存 `predictions_sha256` 与 `lock_hash`（对锁主体自哈希），
   `verify()` 任一处被改动即 False，并指出改的是哪一个字段。
3. **结果不得早于锁**：`score_blind()` 要求每条结果的 `recorded_utc` 晚于 `locked_utc`；
   时间倒挂即拒评（这是"抄"最直接的可机验指纹）。

另外两条与多样性直接相关的约束：
- **预测必须覆盖全部席位**（缺席 → 拒锁）：给没预测的格子事后记分等于放水；
- **同文本预测只允许出现一次**：不同族给出逐字相同的预测 = 同骰子的一个强指纹，
  在锁的阶段就拒掉（与 dedup 的方案相似度门互补：一个管"预测同质"，一个管"方案同质"）。

零平台域语义：预测载荷只是 (seat_id, 数值区间, 一句话断言, 证伪条件)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

SCHEMA = "in25-t2.prereg.v1"
MIN_CLAIM_CHARS = 12


class PreregError(ValueError):
    """盲预测时序/完整性约束被违反。"""


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(obj) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Prediction:
    seat_id: str
    band_lo: float
    band_hi: float
    claim: str
    falsifier: str

    def as_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "band_lo": float(self.band_lo),
            "band_hi": float(self.band_hi),
            "claim": self.claim,
            "falsifier": self.falsifier,
        }


def plan_digest(seats: Sequence) -> str:
    """席位矩阵摘要：锁的是"跑什么"，结果必须回报同一摘要（换了席位矩阵即对不上）。"""
    rows = []
    for s in seats:
        d = s.as_dict() if hasattr(s, "as_dict") else dict(s)
        rows.append([d[k] for k in ("seat_id", "family", "model_id", "framing", "role", "seed")])
    rows.sort()
    return _sha(rows)


def _validate_predictions(predictions: Sequence[Prediction], seats: Sequence, seat_ids: Sequence[str]) -> List[str]:
    errs: List[str] = []
    got = [p.seat_id for p in predictions]
    if len(got) != len(set(got)):
        dup = sorted({x for x in got if got.count(x) > 1})
        errs.append(f"重复席位预测: {dup}")
    missing = sorted(set(seat_ids) - set(got))
    extra = sorted(set(got) - set(seat_ids))
    if missing:
        errs.append(f"未覆盖席位 {len(missing)} 个（首 3: {missing[:3]}）")
    if extra:
        errs.append(f"预测含未知席位: {extra[:3]}")
    if len(predictions) < len(seat_ids):
        errs.append(f"预测数 {len(predictions)} < 席位 {len(seat_ids)}")
    texts: Dict[str, str] = {}
    for p in predictions:
        if len(p.claim.strip()) < MIN_CLAIM_CHARS:
            errs.append(f"{p.seat_id}: 断言语义过短({len(p.claim.strip())}<{MIN_CLAIM_CHARS})")
        if not p.falsifier or not p.falsifier.strip():
            errs.append(f"{p.seat_id}: 缺证伪条件（不可证伪的预测不计分）")
        if not (p.band_lo <= p.band_hi):
            errs.append(f"{p.seat_id}: 区间非法 [{p.band_lo},{p.band_hi}]")
        key = _canon([p.claim.strip(), p.falsifier.strip()])
        if key in texts:
            errs.append(f"{p.seat_id} 与 {texts[key]} 预测逐字相同（同骰子指纹）")
        else:
            texts[key] = p.seat_id
    return errs


def seal(
    predictions: Sequence[Prediction],
    seats: Sequence,
    *,
    results: Optional[Sequence[Mapping]] = None,
    now: Optional[str] = None,
    goal: str = "",
) -> dict:
    """锁定盲预测工件（纯函数：不写盘，返回可序列化 dict；写盘由调用方决定）。

    - `results` 传入且非空 → 拒锁（先跑后锁不可接受）；
    - 预测未覆盖全部席位 / 文本重复 / 缺证伪条件 → 拒锁；
    - 锁工件自带 `lock_hash`，`verify()` 可机械验伪。
    """
    if results:
        raise PreregError(f"结果已存在 {len(results)} 条，拒绝事后锁定预测（先跑后锁=抄）")
    seat_ids = []
    for s in seats:
        d = s.as_dict() if hasattr(s, "as_dict") else dict(s)
        seat_ids.append(d["seat_id"])
    errs = _validate_predictions(predictions, seats, seat_ids)
    if errs:
        raise PreregError("预测不合法: " + " | ".join(errs))

    pred_rows = sorted((p.as_dict() for p in predictions), key=lambda r: r["seat_id"])
    body = {
        "schema": SCHEMA,
        "locked_utc": now or utcnow(),
        "goal": goal,
        "plan_digest": plan_digest(seats),
        "n_seats": len(seat_ids),
        "n_predictions": len(pred_rows),
        "band_median_width": _median([r["band_hi"] - r["band_lo"] for r in pred_rows]),
        "predictions_sha256": _sha(pred_rows),
        "developers": "predictions written before any seat result existed",
    }
    body["lock_hash"] = _sha(body)
    body["predictions"] = pred_rows
    return body


def _median(xs: Iterable[float]) -> float:
    ys = sorted(xs)
    if not ys:
        return 0.0
    n = len(ys)
    return ys[n // 2] if n % 2 else 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def verify(lock: Mapping) -> dict:
    """验锁：schema / 预测摘要 / 自哈希三面独立复算，返回 {"ok": bool, "reason": str}。"""
    if lock.get("schema") != SCHEMA:
        return {"ok": False, "reason": f"schema 不符: {lock.get('schema')}"}
    preds = lock.get("predictions")
    if not isinstance(preds, list) or not preds:
        return {"ok": False, "reason": "锁内无预测"}
    if _sha(list(preds)) != lock.get("predictions_sha256"):
        return {"ok": False, "reason": "predictions_sha256 不符（预测被改动）"}
    body = {k: v for k, v in lock.items() if k not in ("lock_hash", "predictions")}
    if _sha(body) != lock.get("lock_hash"):
        return {"ok": False, "reason": "lock_hash 不符（锁主体被改动）"}
    if int(lock.get("n_predictions", -1)) != len(preds):
        return {"ok": False, "reason": "n_predictions 与预测条数不符"}
    return {"ok": True, "reason": "ok"}


def score_blind(
    lock: Mapping,
    results: Sequence[Mapping],
    seats: Optional[Sequence] = None,
) -> dict:
    """用锁 + 结果算盲预测成绩，并执行"结果不得早于锁"的时序门。

    结果条目：{"seat_id": str, "value": float, "recorded_utc": iso8601, "plan_digest"?: str}
    返回：命中率（预测区间覆盖实测值）、区间宽度、每族命中、时序违规数、错误清单。
    """
    v = verify(lock)
    if not v["ok"]:
        raise PreregError(f"锁无效: {v['reason']}")
    locked = lock["locked_utc"]
    late_violations: List[str] = []
    for r in results:
        rec = r.get("recorded_utc")
        if not rec:
            raise PreregError(f"{r.get('seat_id')}: 结果缺 recorded_utc（不可证时序）")
        if _parse(rec) <= _parse(locked):
            late_violations.append(f"{r.get('seat_id')}@{rec}")
    if late_violations:
        raise PreregError(f"结果时间早于/等于锁定时间: {late_violations[:3]}（时序倒挂=事后预测）")

    if seats is not None:
        d = plan_digest(seats)
        if d != lock["plan_digest"]:
            raise PreregError("席位矩阵与锁不一致（plan_digest 不符）")
        for r in results:
            if r.get("plan_digest") and r["plan_digest"] != d:
                raise PreregError(f"{r.get('seat_id')}: 结果的 plan_digest 与锁不符")

    preds = {p["seat_id"]: p for p in lock["predictions"]}
    by_family: Dict[str, List[bool]] = {}
    hits, width, rows = 0, [], []
    fam_of: Dict[str, str] = {}
    if seats is not None:
        for s in seats:
            dd = s.as_dict() if hasattr(s, "as_dict") else dict(s)
            fam_of[dd["seat_id"]] = dd["family"]
    for r in results:
        sid = r.get("seat_id")
        p = preds.get(sid)
        if p is None:
            raise PreregError(f"结果 {sid} 无对应预测（未预测的格子不得记分）")
        value = float(r["value"])
        hit = p["band_lo"] <= value <= p["band_hi"]
        hits += int(hit)
        width.append(p["band_hi"] - p["band_lo"])
        rows.append({"seat_id": sid, "value": value, "band": [p["band_lo"], p["band_hi"]], "hit": hit})
        by_family.setdefault(fam_of.get(sid, "unknown"), []).append(hit)
    n = len(rows)
    return {
        "n_results": n,
        "n_hits": hits,
        "hit_rate": round(hits / n, 6) if n else 0.0,
        "band_median_width": _median(width),
        "locked_utc": locked,
        "earliest_result_utc": min((r["recorded_utc"] for r in results), default=None),
        "time_order_ok": True,
        "family_hit_rate": {f: round(sum(v) / len(v), 6) for f, v in sorted(by_family.items())},
        "rows": sorted(rows, key=lambda x: x["seat_id"]),
        "lock_verified": True,
    }


def _parse(ts: str) -> datetime:
    t = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(t)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)