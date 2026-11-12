"""package_schema —— 干货包的结构契约与校验（fail-open，永不抛异常）。

两份契约
--------
1. **包体** ``context-package/v1``：assembler 的产物，落到注入审计与 A/B 语料。
2. **声明** ``context_package``：工作包（派单契约）里新增的字段，声明"本单要哪几个槽、
   多少预算、哪些槽必需"。校验器与 ``tools/dispatch_contract`` 对接（见 patch_contract）。
"""

from __future__ import annotations

from typing import Any, Mapping

REQUIRED_PACKAGE_KEYS = ("schema_version", "package_id", "task_class", "as_of",
                         "budget", "slots", "audit")
REQUIRED_ITEM_KEYS = ("id", "text", "tokens", "decision")


def validate_package(pkg: Any, decisions: Mapping[str, Any] | None = None) -> list[str]:
    """校验包体；返回错误码列表（空=合法）。密度门违规是最重的一类（DENSITY_*）。"""
    errs: list[str] = []
    try:
        if not isinstance(pkg, Mapping):
            return ["PACKAGE_NOT_OBJECT"]
        for k in REQUIRED_PACKAGE_KEYS:
            if k not in pkg:
                errs.append(f"PACKAGE_MISSING_KEY:{k}")
        if pkg.get("schema_version") not in (None, "context-package/v1"):
            errs.append(f"PACKAGE_VERSION_UNSUPPORTED:{pkg.get('schema_version')}")
        budget = pkg.get("budget") or {}
        try:
            used, total = int(budget.get("used_tokens", 0)), int(budget.get("total_tokens", 0))
            if total <= 0:
                errs.append("BUDGET_NOT_POSITIVE")
            if used > total:
                errs.append(f"BUDGET_EXCEEDED:{used}>{total}")
        except Exception:
            errs.append("BUDGET_NOT_INT")
        slots = pkg.get("slots")
        if not isinstance(slots, (list, tuple)):
            errs.append("SLOTS_NOT_LIST")
            slots = []
        seen_ids: set[str] = set()
        sum_used = 0
        for i, s in enumerate(slots):
            if not isinstance(s, Mapping):
                errs.append(f"SLOT[{i}]:NOT_OBJECT")
                continue
            for k in ("slot", "status", "items"):
                if k not in s:
                    errs.append(f"SLOT[{i}]:MISSING_{k.upper()}")
            for j, it in enumerate(s.get("items") or []):
                if not isinstance(it, Mapping):
                    errs.append(f"SLOT[{i}].item[{j}]:NOT_OBJECT")
                    continue
                for k in REQUIRED_ITEM_KEYS:
                    if k not in it:
                        errs.append(f"SLOT[{i}].item[{j}]:MISSING_{k.upper()}")
                dec = it.get("decision")
                if not dec:
                    errs.append(f"SLOT[{i}].item[{j}]:DENSITY_NO_DECISION")
                elif decisions is not None and dec not in decisions:
                    errs.append(f"SLOT[{i}].item[{j}]:DENSITY_UNKNOWN_DECISION:{dec}")
                iid = str(it.get("id"))
                if iid in seen_ids:
                    errs.append(f"SLOT[{i}].item[{j}]:DUPLICATE_ID:{iid}")
                seen_ids.add(iid)
                try:
                    sum_used += int(it.get("tokens", 0))
                except Exception:
                    errs.append(f"SLOT[{i}].item[{j}]:TOKENS_NOT_INT")
        audit = pkg.get("audit") or {}
        if isinstance(audit, Mapping) and "density_yield" in audit:
            dy = audit.get("density_yield")
            if isinstance(dy, (int, float)) and dy < 1.0:
                errs.append(f"DENSITY_YIELD_LT_1:{dy}")
        return errs
    except Exception as exc:  # noqa: BLE001
        return [f"PACKAGE_VALIDATE_ERROR:{type(exc).__name__}"]


def validate_decl(decl: Any) -> list[str]:
    """校验工作包契约里的 ``context_package`` 声明字段。"""
    errs: list[str] = []
    try:
        if decl is None:
            return []
        if not isinstance(decl, Mapping):
            return ["CTXPKG_DECL_NOT_OBJECT"]
        if "modes" in decl and decl["modes"] not in (None,):
            if not isinstance(decl["modes"], (list, tuple)):
                errs.append("CTXPKG_DECL_MODES_NOT_LIST")
        slots = decl.get("slots")
        if slots is not None:
            if not isinstance(slots, (list, tuple)):
                errs.append("CTXPKG_DECL_SLOTS_NOT_LIST")
            else:
                for i, s in enumerate(slots):
                    if not isinstance(s, str) or not s.strip():
                        errs.append(f"CTXPKG_DECL_SLOTS[{i}]:NOT_STRING")
        budget = decl.get("budget_tokens")
        if budget is not None:
            try:
                if int(budget) <= 0:
                    errs.append("CTXPKG_DECL_BUDGET_NOT_POSITIVE")
            except Exception:
                errs.append("CTXPKG_DECL_BUDGET_NOT_INT")
        if decl.get("task_class") is not None and not isinstance(decl.get("task_class"), str):
            errs.append("CTXPKG_DECL_CLASS_NOT_STRING")
        return errs
    except Exception as exc:  # noqa: BLE001
        return [f"CTXPKG_DECL_VALIDATE_ERROR:{type(exc).__name__}"]