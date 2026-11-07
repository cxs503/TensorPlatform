"""五角色席位与知识包校验（通用层，零领域语义）。

角色是最小充分集（P1/P2 战役模板）：谁负责、谁出理论、谁实现、谁找反例、谁盲验。
知识包（knowledge pack）由调用方注入：本模块只校验**结构**（每席位有独立焦点、
参考材料不空、挑战者与验证者与实现者非同族/同席），不解释内容。
"""

from __future__ import annotations

from typing import Any, Dict, List

ROLES = ("lead", "theorist", "implementer", "challenger", "verifier")

ROLE_LABELS = {
    "lead": "攻关负责人：拆问题树、排依赖、对门负责",
    "theorist": "理论研究者：机理/文献/量纲与解析基准",
    "implementer": "实现工程师：代码架构/数值离散/性能",
    "challenger": "挑战者：专找反例与失效边界，独立判断权",
    "verifier": "独立验证者：盲序复算，不复用实现者脚本",
}

#: 独立性要求：(被审席位, 审查席位) —— 审查席位不得与被审席位同 agent 或同模型族
INDEPENDENCE_PAIRS = (("implementer", "verifier"), ("implementer", "challenger"))


def validate_knowledge_packs(
    packs: Dict[str, Dict[str, Any]],
    roles=ROLES,
    min_refs: int = 2,
) -> List[str]:
    """校验每个角色都有差异化知识包。返回错误列表（空 = 通过）。"""
    errors: List[str] = []
    for role in roles:
        pack = packs.get(role)
        if not isinstance(pack, dict):
            errors.append(f"缺少角色知识包: {role}")
            continue
        refs = pack.get("pack_refs") or []
        if not isinstance(refs, list) or len([r for r in refs if str(r).strip()]) < min_refs:
            errors.append(f"{role}: pack_refs 少于 {min_refs} 条")
        if not str(pack.get("focus") or "").strip():
            errors.append(f"{role}: focus 为空（知识包必须声明该席位的关注面）")
        if not str(pack.get("agent_id") or "").strip():
            errors.append(f"{role}: agent_id 缺失")
    focuses = [str((packs.get(r) or {}).get("focus") or "").strip() for r in roles if packs.get(r)]
    if len(set(focuses)) != len(focuses):
        errors.append("知识包 focus 重复：角色知识未差异化")
    return errors


def check_independence(packs: Dict[str, Dict[str, Any]]) -> List[str]:
    """自批防护：验证者/挑战者不得与实现者同 agent_id 或同 model_family。"""
    errors: List[str] = []
    for reviewed, reviewer in INDEPENDENCE_PAIRS:
        a = packs.get(reviewed) or {}
        b = packs.get(reviewer) or {}
        if not a or not b:
            errors.append(f"独立性检查缺席位: {reviewed}/{reviewer}")
            continue
        if str(a.get("agent_id")) == str(b.get("agent_id")):
            errors.append(f"{reviewer} 与 {reviewed} 同 agent_id（自批）")
        fa, fb = str(a.get("model_family") or ""), str(b.get("model_family") or "")
        if fa and fb and fa == fb:
            errors.append(f"{reviewer} 与 {reviewed} 同模型族 {fa}（自批）")
    return errors