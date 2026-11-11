#!/usr/bin/env python3
"""能力台账 pin 自愈闭环（capability ledger pin self-heal）。

为什么需要它（根因）
--------------------
``tools/audit/ledger_paths.py`` 的契约门有 ``--write-pin``，但**没有**「检测缺失 →
自动修复」的路径。于是部署世界里只要 pin 文件 ``artifacts/capability_ledger/
ledger_contract.json`` 丢过一次（换树/清仓/只拷了三个产物文件），``/health`` 就会
永久停在::

    capability_ledger.status = "DEGRADED", reasons = ["PIN_MISSING"]

没有人被叫醒、没有机制会重试——这正是 owner「一切依赖写成机制」要消灭的形态：
**红是永久的、修是手动的**。本模块补上闭环的另一半：机器可判「红是否可自愈」，
可自愈就自愈，不可自愈就指名要人。

判据分层（关键安全设计：只修能修的那一种）
------------------------------------------
* **可自动修复**：仅 ``PIN_MISSING``。写 pin 是**合成动作**（把台账当前 sha256
  盖章），不改变台账内容本身；台账合法的世界里它等价于「补签」。
* **一律只报不修**：``NO_LEDGER`` / ``NO_PARSEABLE_LEDGER`` / ``SCHEMA_VERSION_MISMATCH``
  / ``LEDGER_DRIFT`` / ``DERIVED_MISSING`` / ``DERIVED_UNREADABLE`` /
  ``FALLBACK_NON_CANONICAL``。对 ``LEDGER_DRIFT``（台账被人改过、pin 还是旧值）
  写 pin 会**把篡改洗白**——这是本模块最危险的失败模式，因此叠加了第二道闸：
  **只要 reasons 里有任何一条不可自动修的原因，``PIN_MISSING`` 也不再修**
  （``fixable=[]``，退出码 1）。写 pin 只允许发生在「台账本体已合法、只差图章」时。
* 重定位（``TP_CAP_LEDGER`` 指向仓外台账）→ ``LEDGER_RELOCATED`` 只报不修：
  此时仓内 pin 位置已不代表真值来源，自动写章会掩盖来源问题。
* 四消费者层（``contract_report``）的**指纹分歧/导入错误**同样只报不修：
  消费者访问器签名无 ``root``（契约冻结），所以演练世界（``--root``）下它们仍解析
  真仓；因此 ``--root != repo_root`` 时消费者层只登记为 ``scope="ledger-only"``，
  不参与可修性裁决（否则演练世界会被真仓状态污染）。

用法
----
    python3 tools/audit/ledger_selfheal.py --check            # 只体检，红 → 退出码 1
    python3 tools/audit/ledger_selfheal.py --check --json     # 完整 JSON（含契约层）
    python3 tools/audit/ledger_selfheal.py --fix              # 可自愈则补章，再复检
    python3 tools/audit/ledger_selfheal.py --fix --root /tmp/world   # 演练世界
    python3 tools/audit/ledger_selfheal.py --fix --json       # 修复前后 reasons 对照

退出码：``0`` = 已健康或已修复；``1`` = 需人工介入（含不可自动修原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402
from tools.audit import ledger_contract_gate as GATE  # noqa: E402

#: 唯一允许自动修复的缺陷：图章缺失（写 pin 只补签、不改台账内容）
AUTO_FIXABLE = ("PIN_MISSING",)
#: 绝不自动修复的原因（写 pin 会掩盖真实缺陷，尤其 LEDGER_DRIFT = 篡改洗白）
NEVER_FIX = ("NO_LEDGER", "NO_PARSEABLE_LEDGER", "SCHEMA_VERSION_MISMATCH",
             "LEDGER_DRIFT", "DERIVED_MISSING", "DERIVED_UNREADABLE",
             "FALLBACK_NON_CANONICAL")

ACTION_NONE = "none"                    # 只体检 / 已健康无需动作
ACTION_WROTE_PIN = "wrote_pin"          # 补盖图章
ACTION_REFUSED = "refused_needs_human"  # 有不可自动修原因，只报不修
ACTION_FAILED = "fix_failed"            # 尝试修复但仍红 / 写 pin 抛异常

SCOPE_REPO = "repo"           # 真仓：四消费者层参与裁决
SCOPE_LEDGER = "ledger-only"  # 演练世界（--root）：消费者层只登记不裁决


def _now() -> str:
    """UTC ISO 时间戳（与 ledger_paths 同格式；不耦合其私有函数）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_repo_root(root: Path) -> bool:
    try:
        return root.resolve() == LP.repo_root().resolve()
    except Exception:
        return False


def _gate_view(root: Path, scope: str, ref: dict) -> dict:
    """gate 判据视图。真仓用 ``ledger_contract_gate.run``，演练世界用台账层判据。"""
    if scope == SCOPE_REPO:
        try:
            res = GATE.run(str(root))
            return {"verdict": res["verdict"], "failed": list(res.get("failed") or []),
                    "checks": res.get("checks"), "scope": SCOPE_REPO}
        except Exception as exc:  # gate 自身故障：如实登记，不假装绿
            return {"verdict": "FAIL", "failed": [f"GATE_ERROR:{type(exc).__name__}"],
                    "checks": None, "scope": SCOPE_REPO}
    reasons = list(ref.get("reasons") or [])
    return {"verdict": "PASS" if ref.get("status") == LP.STATUS_OK else "FAIL",
            "failed": [f"LEDGER:{r}" for r in reasons], "checks": None,
            "scope": SCOPE_LEDGER}


def evaluate(root: str | Path | None = None) -> dict:
    """体检：**永不抛异常**，把可修/不可修/动作全部写成字段。

    返回 ``{status, reasons, fixable, unfixable, action_taken, ...}``；
    ``--check`` 只读本函数的输出（``action_taken="none"``）。
    """
    root_p = Path(root or LP.repo_root())
    scope = SCOPE_REPO if _is_repo_root(root_p) else SCOPE_LEDGER
    try:
        ref = LP.resolve_ledger(root=str(root_p))
    except Exception as exc:  # 解析器自崩：登记为不可修，绝不静默
        ref = {"status": LP.STATUS_DEGRADED,
               "reasons": [f"RESOLVER_ERROR:{type(exc).__name__}"],
               "sha256": None, "path_rel": None, "pinned": None, "relocated": False,
               "schema_version": None, "capabilities": 0}
    reasons = list(ref.get("reasons") or [])
    status = ref.get("status") or LP.STATUS_DEGRADED

    # ── 不可自动修的原因（除 PIN_MISSING 之外一律只报） ─────────────
    unfixable: list[str] = [r for r in reasons if r not in AUTO_FIXABLE]
    if ref.get("relocated"):
        unfixable.append("LEDGER_RELOCATED")

    contract: dict | None = None
    if scope == SCOPE_REPO:
        try:
            rep = LP.contract_report(str(root_p))
            contract = {"contract_ok": rep.get("contract_ok"),
                        "violations": list(rep.get("violations") or []),
                        "canonical_sha256": rep.get("canonical_sha256"),
                        "consumers": {n: {"path": c.get("path"), "sha256": c.get("sha256"),
                                          "status": c.get("status"), "error": c.get("error")}
                                      for n, c in (rep.get("consumers") or {}).items()}}
            for name, c in contract["consumers"].items():
                if c.get("error"):
                    unfixable.append(f"CONSUMER_ERROR:{name}")
            for v in contract["violations"]:
                if v.startswith(("CONSUMER_SHA256_DIVERGENCE", "CONSUMER_PATH_DIVERGENCE")):
                    unfixable.append(v)
        except Exception as exc:
            contract = {"error": f"{type(exc).__name__}: {exc}"}
            unfixable.append("CONTRACT_REPORT_ERROR")

    # 去重保序
    seen: set[str] = set()
    unfixable = [u for u in unfixable if not (u in seen or seen.add(u))]

    # ── 可修性子句：reasons 非空且只含可修原因，且无任何不可修项 ──────
    fixable = [r for r in reasons if r in AUTO_FIXABLE] if (reasons and not unfixable) else []

    pinned = ref.get("pinned") or {}
    sha = ref.get("sha256")
    return {
        "root": str(root_p),
        "scope": scope,
        "status": status,
        "reasons": reasons,
        "fixable": fixable,
        "unfixable": unfixable,
        "action_taken": ACTION_NONE,
        "healthy": status == LP.STATUS_OK,
        "ledger_path": ref.get("path_rel"),
        "sha256": sha,
        "schema_version": ref.get("schema_version"),
        "pin_sha256": pinned.get("sha256"),
        "pin_records_ledger_path": pinned.get("path_rel"),
        "pin_matches_ledger": bool(sha and pinned.get("sha256") == sha),
        "relocated": bool(ref.get("relocated")),
        "contract": contract,
        "gate": _gate_view(root_p, scope, ref),
        "checked_at": _now(),
    }


def _summary(res: dict) -> dict:
    """修复前后对照用的精简视图（reason 列表 + 图章身份 + gate 判据）。"""
    return {"status": res["status"], "reasons": list(res["reasons"]),
            "pin_sha256": res["pin_sha256"], "sha256": res["sha256"],
            "pin_matches_ledger": res["pin_matches_ledger"],
            "gate": res["gate"]["verdict"], "action_taken": res["action_taken"]}


def check(root: str | Path | None = None) -> dict:
    """体检（只读，绝不落盘）。"""
    res = evaluate(root)
    res["action_taken"] = ACTION_NONE
    return res


def fix(root: str | Path | None = None) -> dict:
    """自愈：**仅当**唯一失败原因是 ``PIN_MISSING`` 时补写图章，然后复检。"""
    root_p = Path(root or LP.repo_root())
    before = evaluate(root_p)

    err = None
    if before["healthy"]:
        # 已健康 → 严格 no-op（不重写 pin，保留原 pinned_at 与字节）
        action = ACTION_NONE
    elif not before["fixable"]:
        action = ACTION_REFUSED
    else:
        action = ACTION_WROTE_PIN
        try:
            LP.write_pin(str(root_p))
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            action = ACTION_FAILED

    after = evaluate(root_p)
    fixed = (action == ACTION_WROTE_PIN and after["status"] == LP.STATUS_OK
             and not after["reasons"] and after["pin_matches_ledger"])
    if action == ACTION_WROTE_PIN and not fixed:
        action = ACTION_FAILED

    out = {
        "root": after["root"],
        "scope": after["scope"],
        "action_taken": action,
        "status": after["status"],
        "reasons": list(after["reasons"]),
        "fixable": list(before["fixable"]),
        "unfixable": list(after["unfixable"]),
        "fixed": fixed,
        "before": _summary(before),
        "after": _summary(after),
        "pin_sha256": after["pin_sha256"],
        "sha256": after["sha256"],
        "pin_matches_ledger": after["pin_matches_ledger"],
        "ledger_path": after["ledger_path"],
        "gate": after["gate"],
        "contract": after["contract"],
        "checked_at": after["checked_at"],
    }
    if err:
        out["error"] = err
    return out


def _exit_code(res: dict, fixing: bool) -> int:
    if res["status"] == LP.STATUS_OK and not res["reasons"]:
        return 0
    if fixing and res.get("fixed"):
        return 0
    return 1


def _compact(res: dict) -> dict:
    out = {k: res.get(k) for k in ("status", "reasons", "fixable", "action_taken")}
    if "unfixable" in res:
        out["unfixable"] = res["unfixable"]
    if "before" in res:
        out["before"] = res["before"]
        out["after"] = res["after"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="能力台账 pin 自愈闭环（检测→修复→复检）")
    ap.add_argument("--root", default=None, help="覆盖仓库根（演练世界用）")
    ap.add_argument("--check", action="store_true", help="只体检；红 → 退出码 1")
    ap.add_argument("--fix", action="store_true", help="可自愈（仅 PIN_MISSING）则补写图章")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON（含契约层与前后对照）")
    ap.add_argument("--out", default=None,
                    help="把完整结果落盘（审计留痕；如 artifacts/capability_ledger/selfheal_run.json）")
    args = ap.parse_args(argv)

    res = fix(args.root) if args.fix else check(args.root)
    if args.out:  # 审计留痕（可选；不影响退出码）
        try:
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        except Exception as exc:
            print(f"[warn] --out 落盘失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(json.dumps(res if args.json else _compact(res), ensure_ascii=False, indent=1))
    return _exit_code(res, fixing=args.fix)


if __name__ == "__main__":
    raise SystemExit(main())