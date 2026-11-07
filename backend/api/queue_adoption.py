"""Queued-backlog adoption audit — pure logic (no DB, no I/O).

背景（IN27 影子诊断单 a151356df79cb336，器官 `adoption_stall`）
---------------------------------------------------------------
影子器官的指标是 `min(created_at) WHERE status='queued'`，2026-11-07 读到
8820 min（≥240 min 阈值）。复算后该数字由**两类互不相同的缺陷**叠加而成，
本模块把两件事拆开，让每个数字都能单独被证伪：

1. **口径缺陷（parked ≠ adoptable）**
   `status='queued'` 里混着**显式停挂**的单（`suspended_at IS NOT NULL`）：
   11-01 的 propose-p9-w1-2 被 owner 冻结令停挂，但状态被改回 queued 而
   `suspended_at` 没清。它既不可被 `/resume`（要求 status='suspended'），
   又能被 native worker 领走（=停挂单自己写下的“冻结洞”）。
   于是「收养停滞」的年龄被一张**按设计不该被收养**的单顶着。

2. **引脚缺陷（orphan pin）**
   一批 queued 单被钉在某个 model 上（`model='glm-5.1'`），而该 model 的
   车道没有**可工作的**认领者：注册表里挂着 glm-5.1 的驻留会话只在心跳，
   实际进程呈递的是别的 model（会话-注册漂移），于是它每一次会话级 claim
   都被 409 挡回、静默退化成公共池车道——被钉住的单永远无人认领。
   判定「车道活」必须看**近期认领证据**，不能看心跳：心跳恰好是掩盖该洞的
   信号（tp-gen-4 心跳新鲜、state=idle，却 10 小时没认领过任何 glm-5.1 单）。

判据（机器可裁）
----------------
* `split_backlog(...)["violating"]` —— 只用 **adoptable**（非停挂）的单算年龄，
  这才是「入池无人领」的真实语义；停挂量单独报，不参与越界判定。
* `pin_audit(...)["orphan_pins"]` —— 被钉 model 上「超阈值排队数 > 0 且车道
  无近期认领证据」的 model 列表；空列表 = 无孤儿引脚。
* `heal_plan(...)` —— 只给出两类**可逆**修复：把停挂单恢复成 status='suspended'
  （恢复 /resume 可逆性、关掉冻结洞）；把孤儿引脚解钉成 model=NULL（原始
  model 由调用方写回 result 审计字段，可回滚）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

THRESHOLD_MINUTES = 240.0
LANE_LIVENESS_HOURS = 6.0


def parse_ts(value):
    """接受 datetime / ISO-8601 字符串；空值返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minutes_since(now, value):
    ts = parse_ts(value)
    if ts is None:
        return None
    return (now - ts).total_seconds() / 60.0


def _truthy(value) -> bool:
    """psql -t -A 的 boolean 出来是 't'/'f'，别让 bool('f') 骗成 True。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y", "on")


def _queued(rows):
    return [r for r in rows if str(r.get("status") or "") == "queued"]


def is_parked(row) -> bool:
    """停挂 = 有 suspended_at 的单，与 status 无关（两者可能不一致）。"""
    return parse_ts(row.get("suspended_at")) is not None


def split_backlog(rows, now, threshold_minutes: float = THRESHOLD_MINUTES) -> dict:
    """把 queued 池拆成 adoptable / parked 两部分并各自报最老年龄。

    只有 adoptable 部分的年龄参与越界判定：停挂单是按设计不参与收养的，
    把它算进「无人认领」会让指标永远贴着冻结期的最老一单。
    """
    now = parse_ts(now)
    queued = _queued(rows)
    parked = [r for r in queued if is_parked(r)]
    adoptable = [r for r in queued if not is_parked(r)]

    def _oldest(subset):
        ages = [a for a in (_minutes_since(now, r.get("created_at")) for r in subset) if a is not None]
        return max(ages) if ages else 0.0

    oldest_adoptable = _oldest(adoptable)
    oldest_parked = _oldest(parked)
    return {
        "threshold_minutes": float(threshold_minutes),
        "queued_total": len(queued),
        "adoptable_count": len(adoptable),
        "parked_count": len(parked),
        "oldest_adoptable_minutes": round(oldest_adoptable, 1),
        "oldest_parked_minutes": round(oldest_parked, 1),
        # 影子器官原口径（会把停挂单算进来），留作对照，不作为判据
        "oldest_any_minutes": round(max(oldest_adoptable, oldest_parked), 1),
        "violating": bool(adoptable) and oldest_adoptable > float(threshold_minutes),
        "parked_but_queued_ids": sorted(int(r["id"]) for r in parked),
        "adoptable_ids": sorted(int(r["id"]) for r in adoptable),
    }


def _lane_liveness(claims, model, now, liveness_hours: float):
    """车道是否活着 = 窗口内真的有人领到过该 model 的单（不看心跳）。"""
    window = timedelta(hours=float(liveness_hours))
    alive, last = False, None
    for c in claims:
        if str(c.get("model") or "") != str(model):
            continue
        ts = parse_ts(c.get("claimed_at"))
        if ts is None:
            continue
        if last is None or ts > last:
            last = ts
        if now - ts <= window:
            alive = True
    return alive, last


def pin_audit(rows, residents, claims, now,
              threshold_minutes: float = THRESHOLD_MINUTES,
              liveness_hours: float = LANE_LIVENESS_HOURS) -> dict:
    """按 model 引脚体检：谁排着队、这条车道还活着吗、注册表是否漂移。"""
    now = parse_ts(now)
    adoptable = [r for r in _queued(rows) if not is_parked(r)]

    by_model: dict[str, list] = {}
    for r in adoptable:
        model = r.get("model")
        if not model:
            continue
        by_model.setdefault(str(model), []).append(r)

    lanes, orphans, drift = [], [], []
    for model in sorted(by_model):
        group = by_model[model]
        ages = [a for a in (_minutes_since(now, r.get("created_at")) for r in group) if a is not None]
        oldest = max(ages) if ages else 0.0
        registrants = [w for w in residents
                       if str(w.get("model") or "") == model and _truthy(w.get("enabled", True))]
        lane_alive, last_claim = _lane_liveness(claims, model, now, liveness_hours)
        orphan = bool(group) and oldest > float(threshold_minutes) and not lane_alive
        lanes.append({
            "model": model,
            "queued_count": len(group),
            "oldest_minutes": round(oldest, 1),
            "registered_claimant_ids": sorted(str(w.get("worker_id")) for w in registrants),
            "lane_alive": lane_alive,
            "last_claim_at": last_claim.isoformat() if last_claim else None,
            "orphan_pin": orphan,
        })
        if orphan:
            orphans.append(model)
            for w in registrants:
                wid = str(w.get("worker_id"))
                mine = [c for c in claims
                        if str(c.get("claimed_by") or "") == wid
                        and str(c.get("model") or "") not in ("", "None")]
                last_model = None
                if mine:
                    newest = max(mine, key=lambda c: parse_ts(c.get("claimed_at")) or now)
                    last_model = str(newest.get("model"))
                drift.append({
                    "worker_id": wid,
                    "registered_model": model,
                    "last_claimed_model": last_model,
                    "state": w.get("state"),
                    # 注册 glm-5.1 却只领到过别的 model 的单 = 会话/注册漂移
                    "drift": bool(last_model) and last_model != model,
                })
    return {"lanes": lanes, "orphan_pins": orphans, "drift_suspects": drift}


def heal_plan(rows, residents, claims, now,
              threshold_minutes: float = THRESHOLD_MINUTES,
              liveness_hours: float = LANE_LIVENESS_HOURS) -> dict:
    """只输出可逆的修复动作；不碰阈值、不改指标口径。"""
    split = split_backlog(rows, now, threshold_minutes)
    audit = pin_audit(rows, residents, claims, now, threshold_minutes, liveness_hours)
    unpin = []
    for model in audit["orphan_pins"]:
        for r in _queued(rows):
            if not is_parked(r) and str(r.get("model") or "") == model:
                unpin.append({"mission_id": int(r["id"]), "from_model": model})
    return {
        "unpark": [{"mission_id": mid, "action": "restore_status_suspended"}
                   for mid in split["parked_but_queued_ids"]],
        "unpin": sorted(unpin, key=lambda d: d["mission_id"]),
    }