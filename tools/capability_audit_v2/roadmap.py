"""升级路线图：把「台账里不是 REAL 的能力」变成**机器可校验**的行动工件。

两类条目
========
* ``items``：MOCK / STUB 能力 → REAL 的升级路径。每条必须给
  - ``upgrade_class`` ∈ {algorithm_replacement, parameterization, solver_integration}
  - ≥1 条 ``algorithm_refs``（citation + locator：文献/规范/仓内实现路径）
  - ≥3 个 ``steps``，每步含 action + verification + acceptance，
    且 verification / acceptance 里必须出现**仓内真实存在的路径或选择器**
    （否则就是"写了一句话"，不是可执行路线）。
* ``evidence_completion``：WITHHELD 能力 → REAL 需要补齐的证据动作（模板 + 配置覆盖）。

缺少 MOCK/STUB 的升级路径 = 路线图不完整 = CLI 退出码 2（不做静默遗漏）。
升级路径的具体算法/文献内容属于**领域数据**，全部放在
``config/capability_audit_v2/upgrade_paths.json``，本模块只做结构校验与编排。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "capability_audit_v2.roadmap.v1"

#: 升级类别（通用工程语义，与领域无关）
UPGRADE_CLASSES = ("algorithm_replacement", "parameterization", "solver_integration")

#: 证据补齐动作模板：只依赖理由码，不含域词条
_COMPLETION_TEMPLATES: dict[str, str] = {
    "NO_EXEC_BINDING": "在 exec spec 为该能力声明 pytest 选择器或不变量探针，然后重跑 audit 取得可执行证据",
    "EVIDENCE_NOT_BOUND": "在基础 spec 里把该能力绑定到实现/测试路径（或显式声明不支持），重跑 audit",
    "NO_BENCHMARK": "落盘对标基准工件（json_cases/json_values）并在 spec.benchmark 绑定，误差 ≤ REAL 容差",
    "BENCHMARK_OVER_TOLERANCE": "补齐/复核对标数据并重算误差；越界项保持 WITHHELD 并记录原因",
    "BELOW_REAL_TOLERANCE": "把对标误差压到 REAL 容差内（算法或数据修正）后重跑",
    "EXEC_ENV_ERROR": "修复宿主环境（provider 注册/依赖/采集）后重跑执行证据，不得据此降级能力",
    "EXEC_TEST_FAILED": "定位失败用例：若是能力缺陷→转 MOCK 升级路径；若是测试陈旧→修正测试并重跑",
    "EXEC_NON_NUMERIC": "给执行证据补数值断言（至少 1 条），否则测试通过不构成 REAL 证据",
    "EXEC_EVIDENCE_MISSING": "补齐与静态 REAL 相匹配的执行证据并重跑",
    "TESTS_MISSING": "补写覆盖关键量的数值断言测试，并绑定到该能力",
    "TESTS_NON_NUMERIC": "把存在性测试升级为数值断言测试（关键量 vs 参考值）",
    "INSUFFICIENT": "补齐任一证据面（实现/测试/基准/探针）后重跑",
}

_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|json|md|ya?ml)")
_SELECTOR_TOKEN = re.compile(r"[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+){0,2}")


class RoadmapError(ValueError):
    """路线图数据不合格（CLI 退出码 2）。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise RoadmapError(msg)


def _token_exists(token: str, root: Path) -> bool:
    head = token.split("::")[0]
    return (root / head).exists()


def load_paths(path: str | Path, known_ids: set[str]) -> dict[str, dict]:
    p = Path(path)
    _require(p.is_file(), f"升级路径数据缺失: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    _require(isinstance(doc, dict), "升级路径数据顶层必须是 object")
    paths = doc.get("paths") or {}
    _require(isinstance(paths, dict), "paths 必须是 object")
    orphans = sorted(set(paths) - known_ids)
    _require(not orphans, f"paths 声明了未知能力 id: {orphans}")
    return paths


def _validate_item(cap_id: str, item: dict, root: Path) -> dict:
    _require(isinstance(item, dict), f"{cap_id} 的升级路径必须是 object")
    klass = item.get("upgrade_class")
    _require(klass in UPGRADE_CLASSES, f"{cap_id}.upgrade_class 必须是 {UPGRADE_CLASSES}")
    refs = item.get("algorithm_refs")
    _require(isinstance(refs, list) and refs, f"{cap_id}.algorithm_refs 至少 1 条")
    out_refs = []
    for i, ref in enumerate(refs):
        _require(isinstance(ref, dict), f"{cap_id}.algorithm_refs[{i}] 必须是 object")
        citation = str(ref.get("citation") or "").strip()
        locator = str(ref.get("locator") or "").strip()
        _require(bool(citation), f"{cap_id}.algorithm_refs[{i}] 缺 citation")
        _require(bool(locator), f"{cap_id}.algorithm_refs[{i}] 缺 locator（文献标识或仓内路径）")
        if "/" in locator or locator.endswith((".py", ".json", ".md")):
            _require(_token_exists(locator, root),
                     f"{cap_id}.algorithm_refs[{i}].locator 指向不存在的仓内路径: {locator}")
        out_refs.append({"citation": citation, "locator": locator,
                         "role": str(ref.get("role") or "")})
    steps = item.get("steps")
    _require(isinstance(steps, list) and len(steps) >= 3,
             f"{cap_id}.steps 至少 3 步（当前 {len(steps) if isinstance(steps, list) else 0}）")
    out_steps = []
    for i, step in enumerate(steps):
        _require(isinstance(step, dict), f"{cap_id}.steps[{i}] 必须是 object")
        action = str(step.get("action") or "").strip()
        verification = str(step.get("verification") or "").strip()
        acceptance = str(step.get("acceptance") or "").strip()
        _require(bool(action), f"{cap_id}.steps[{i}] 缺 action")
        _require(bool(verification), f"{cap_id}.steps[{i}] 缺 verification")
        _require(bool(acceptance), f"{cap_id}.steps[{i}] 缺 acceptance")
        tokens = _SELECTOR_TOKEN.findall(verification) + _PATH_TOKEN.findall(verification)
        _require(bool(tokens),
                 f"{cap_id}.steps[{i}].verification 未给出可执行路径或选择器")
        for tok in tokens:
            _require(_token_exists(tok, root),
                     f"{cap_id}.steps[{i}] 引用了不存在的路径/选择器: {tok}")
        out_steps.append({"action": action, "verification": verification,
                          "acceptance": acceptance,
                          "artifact": str(step.get("artifact") or "")})
    return {
        "capability": cap_id,
        "upgrade_class": klass,
        "blocking_summary": str(item.get("blocking_summary") or "").strip(),
        "algorithm_refs": out_refs,
        "steps": out_steps,
        "risks": [str(r) for r in (item.get("risks") or [])],
        "effort_days": item.get("effort_days"),
    }


def _completion(verdict: dict, paths: dict[str, dict]) -> dict:
    cap_id = verdict["capability"]
    reason = str(verdict.get("reason_code") or "INSUFFICIENT")
    override = (paths.get(cap_id) or {}).get("next_action")
    return {
        "capability": cap_id,
        "level": verdict["level"],
        "reason_code": reason,
        "next_action": str(override or _COMPLETION_TEMPLATES.get(
            reason, _COMPLETION_TEMPLATES["INSUFFICIENT"])),
        "override": bool(override),
    }


def build_roadmap(verdicts: list[dict], paths: dict[str, dict], root: str | Path = ".",
                  ruleset_version: str = "") -> dict:
    """生成并校验路线图；缺 MOCK/STUB 路径 ⇒ :class:`RoadmapError`。"""
    root = Path(root)
    items: list[dict] = []
    missing: list[str] = []
    for v in verdicts:
        if v["level"] in ("MOCK", "STUB"):
            item = paths.get(v["capability"])
            if not item:
                missing.append(v["capability"])
                continue
            built = _validate_item(v["capability"], item, root)
            built.update({"from_level": v["level"], "to_level": "REAL",
                          "reason_code": v.get("reason_code")})
            items.append(built)
    _require(not missing, f"MOCK/STUB 能力缺升级路径（禁止静默遗漏）: {missing}")

    declared_withheld = {cid for cid, item in paths.items()
                         if isinstance(item, dict) and item.get("next_action")}
    completions = [_completion(v, paths) for v in verdicts
                   if v["level"] == "WITHHELD"]
    level_by_id = {v["capability"]: v["level"] for v in verdicts}
    stale = sorted(cid for cid in declared_withheld
                   if cid in level_by_id and level_by_id[cid] != "WITHHELD")
    _require(not stale, f"next_action 指向未处于 WITHHELD 的能力: {stale}")

    return {
        "schema_version": SCHEMA,
        "ruleset_version": ruleset_version,
        "items": sorted(items, key=lambda x: x["capability"]),
        "evidence_completion": sorted(completions, key=lambda x: x["capability"]),
        "summary": {
            "upgrade_items": len(items),
            "algorithm_refs": sum(len(i["algorithm_refs"]) for i in items),
            "steps": sum(len(i["steps"]) for i in items),
            "completion_actions": len(completions),
            "classes": sorted({i["upgrade_class"] for i in items}),
        },
    }