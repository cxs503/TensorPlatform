"""只减不增门（monotone-decreasing gate）：基线棘轮 + CI 拦截。

问题
----
每个 mission 在工作树上建一条 ``agent/m<id>`` 分支；分支只增不减，未合并数单调上升，
于是「未合并 443 条」这种数字每月刷新一次，却没有任何机制阻止它继续变大。**没有基线就没有
「增」的定义**——反过来，只要基线本身可以随便抬，门就是纸门。

机制（三层，都可被 CI 强制执行）
------------------------------
1. **冻结基线** ``freeze``：把当前扫描结果写成
   ``{repo_label -> {base_sha, baseline_unmerged, baseline_by_class, frozen_at, ceiling, history[]}}``。
2. **棘轮**（只在 freeze 时生效）：新基线 ``ceiling`` 必须 ≤ 旧 ``ceiling - min_reduction``；
   想抬高必须 ``--accept-increase <理由>``，理由与前后数字写进 ``history``（留痕不可覆盖）。
   这样「只减不增」约束的是**基线序列本身**，而不是某一次扫描。
3. **门裁决** ``gate``：``pass = current_unmerged <= ceiling``。
   另附「门牙」：``frozen_at`` 之后新出现的 **C 类** 分支（真未交付）计为
   ``new_undelivered``，默认记 warning（正在做的活允许短暂存在），``--strict-new`` 下直接判失败
   （用于 release 分支门）。

为什么门用 ``B+C`` 作「未合并」
-----------------------------
A/D 类内容已在 base（或被历史包含），可被处置动作自动关闭；把它们计入「未合并」会让门永远红，
逼着人给基线放水。所以门的口径 = 「base 里还没有的内容」（B 的内容在别的分支里也没进 base，
故计入）。这个口径与 ``stats.unmerged`` 严格一致，不允许两处各算一套。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import CLASS_B, CLASS_C, CLASSES, SCHEMA_VERSION

GATE_SCHEMA_VERSION = "1.0"

V_COUNT = "V1-count-exceeds-ceiling"
V_NEW_UNDELIVERED = "V2-new-undelivered-since-freeze"
V_BASELINE_INCREASE = "V3-baseline-increase-without-acceptance"
V_BASE_MOVED = "V4-base-moved-unrebaselined"


def freeze_baseline(ledger: Dict[str, Any], previous: Optional[Dict[str, Any]] = None,
                    min_reduction: int = 0, accept_increase: Optional[str] = None,
                    allowance: Optional[int] = None) -> Dict[str, Any]:
    """生成/更新基线。``previous`` 存在时执行棘轮检查（不通过就抛 ``SystemExit``）。"""
    label = ledger["repo"]
    stats = ledger["stats"]
    current = int(stats["unmerged"])
    prev_entry = copy.deepcopy(((previous or {}).get("repos") or {}).get(label))
    history: List[Dict[str, Any]] = list((prev_entry or {}).get("history") or [])
    prev_ceiling = (prev_entry or {}).get("ceiling")

    if prev_entry is not None:
        bound = int(prev_ceiling) - int(min_reduction)
        if current > bound:
            if not accept_increase:
                raise SystemExit(
                    f"[delivery_hygiene] baseline ratchet violation for {label}: "
                    f"current_unmerged={current} > prev_ceiling({prev_ceiling}) - "
                    f"min_reduction({min_reduction}) = {bound}. "
                    f"只减不增：要么先处置分支，要么显式 --accept-increase <理由> 留痕。")
            history.append({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "accepted-increase",
                "from_ceiling": prev_ceiling,
                "to_ceiling": current,
                "reason": str(accept_increase),
            })
    ceiling = current if allowance is None else current + int(allowance)
    history.append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "freeze",
        "unmerged": current,
        "ceiling": ceiling,
        "base_sha": ledger["base_sha"],
    })
    entry = {
        "base": ledger["base"],
        "base_sha": ledger["base_sha"],
        "pattern": ledger["pattern"],
        "baseline_unmerged": current,
        "baseline_by_class": dict(stats["by_class"]),
        "baseline_total_branches": stats["total_branches"],
        "allowance": int(allowance or 0),
        "ceiling": ceiling,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": history,
    }
    # 深拷贝：棘轮判定必须读**原始**基线；浅拷贝会顺手改掉调用方手里的 previous（实测踩过）
    out = copy.deepcopy(previous or {"schema_version": GATE_SCHEMA_VERSION})
    out["schema_version"] = GATE_SCHEMA_VERSION
    out.setdefault("repos", {})
    out["repos"][label] = entry
    return out


def evaluate_gate(ledger: Dict[str, Any], baseline: Dict[str, Any],
                  strict_new: bool = False, grace_days: int = 0) -> Dict[str, Any]:
    """门裁决：``current_unmerged <= ceiling`` 且无未豁免的冻结后新增 C 类分支。"""
    label = ledger["repo"]
    stats = ledger["stats"]
    entry = ((baseline or {}).get("repos") or {}).get(label)
    violations: List[Dict[str, Any]] = []
    if entry is None:
        return {
            "schema_version": GATE_SCHEMA_VERSION,
            "repo": label,
            "pass": False,
            "reason": "no-baseline-for-repo",
            "hint": "先跑 `delivery_hygiene freeze` 冻结基线",
            "current_unmerged": stats["unmerged"],
            "violations": [{"code": "V0-no-baseline", "repo": label}],
            "exit_code": 1,
        }
    ceiling = int(entry.get("ceiling", entry.get("baseline_unmerged", 0)))
    current = int(stats["unmerged"])
    if current > ceiling:
        violations.append({
            "code": V_COUNT,
            "current_unmerged": current,
            "ceiling": ceiling,
            "overflow": current - ceiling,
            "hint": "先处置 A/D/B 分支或合并 C 类，再重跑 gate",
        })
    if str(ledger["base_sha"]) != str(entry.get("base_sha")):
        violations.append({
            "code": V_BASE_MOVED,
            "baseline_base_sha": entry.get("base_sha"),
            "current_base_sha": ledger["base_sha"],
            "severity": "warning",
        })
    new_undelivered = _new_undelivered(ledger, entry, grace_days)
    if new_undelivered and strict_new:
        violations.append({
            "code": V_NEW_UNDELIVERED,
            "count": len(new_undelivered),
            "branches": [r["branch"] for r in new_undelivered][:20],
            "severity": "error",
        })
    hard = [v for v in violations if v.get("severity", "error") == "error"]
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "repo": label,
        "pass": not hard,
        "current_unmerged": current,
        "ceiling": ceiling,
        "headroom": ceiling - current,
        "by_class": dict(stats["by_class"]),
        "baseline_unmerged": entry.get("baseline_unmerged"),
        "frozen_at": entry.get("frozen_at"),
        "new_undelivered": [r["branch"] for r in new_undelivered],
        "new_undelivered_count": len(new_undelivered),
        "strict_new": strict_new,
        "violations": violations,
        "exit_code": 0 if not hard else 1,
    }


def _new_undelivered(ledger: Dict[str, Any], entry: Dict[str, Any],
                     grace_days: int) -> List[Dict[str, Any]]:
    """``frozen_at`` 之后出现、且 tip 已超过 grace 冻结期仍为 C 类的分支。"""
    frozen_at = str(entry.get("frozen_at") or "")
    out: List[Dict[str, Any]] = []
    for r in ledger["rows"]:
        if r["class"] != CLASS_C:
            continue
        tip = str(r.get("tip_date") or "")
        if not tip or not frozen_at or tip <= frozen_at:
            continue
        if grace_days > 0 and not _older_than(tip, frozen_at, grace_days):
            continue
        out.append(r)
    return out


def _older_than(tip: str, ref: str, days: int) -> bool:
    from datetime import datetime as _dt
    try:
        a = _dt.fromisoformat(tip.replace("Z", "+00:00"))
        b = _dt.fromisoformat(ref.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (a - b).days >= days


def render_gate_summary(result: Dict[str, Any]) -> str:
    cls = result.get("by_class") or {}
    head = "PASS" if result["pass"] else "FAIL"
    lines = [f"[gate] {result['repo']}: {head} — 未合并 {result['current_unmerged']} / "
             f"ceiling {result.get('ceiling')}（余量 {result.get('headroom')}）"]
    if cls:
        lines.append("       A={} B={} C={} D={}".format(
            cls.get(CLASSES[0], 0), cls.get(CLASSES[1], 0),
            cls.get(CLASSES[2], 0), cls.get(CLASSES[3], 0)))
    for v in result.get("violations") or []:
        lines.append(f"       ! {v['code']}: " + ", ".join(
            f"{k}={v[k]}" for k in v if k not in ("code", "hint", "severity")))
    if result.get("hint"):
        lines.append(f"       hint: {result['hint']}")
    return "\n".join(lines)