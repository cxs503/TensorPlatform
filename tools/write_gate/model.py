#!/usr/bin/env python3
"""工作树写入边界门 —— 数据模型与规范化工具。

三条平台铁律
------------
1. **零领域语义**：本包只认「路径 / 仓库边界 / 凭证形态 / 文件大小」这四类平台通用概念。
   白名单、仓边界规则、凭证模式**全部外置**到 ``data/write_gate/policy.json``；
   代码里不写任何学科/业务词条（由 ``tests/test_write_gate.py::test_package_carries_no_domain_terms``
   机器校验）。
2. **可复算**：判决是纯函数输出——同一输入必得逐字节相同的 JSON；摘要由内容派生，
   不含墙钟、不含随机、不含绝对时间。
3. **fail-closed**：判不了的情形（策略缺失、路径非法、仓根不可读）记 ``invalid`` 类
   违规并给 ``PASS`` 之外的判决，绝不"默认放行"。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "tp.write_gate.verdict/v2"
POLICY_SCHEMA = "write_gate/policy/v1"
ACCEPTANCE_SCHEMA = "tp.write_gate.acceptance/v1"

# 五类判决分类：边界类（cross_repo）优先归因——越界路径不再按白名单未命中重复计
CATEGORIES = ("whitelist", "cross_repo", "secret", "binary", "invalid")


class WriteGateError(ValueError):
    """输入/配置不合法（调用方按 fail-closed 处理）。"""


def canonical_json(obj: Any) -> str:
    """规范化 JSON：键排序、紧凑分隔、保留非 ASCII（中文可读且稳定）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(obj: Any) -> str:
    """内容摘要：同一逻辑内容必得同一摘要。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Change:
    """一条待写入变更（路径必填；``content`` 为该文件最终文本，可选）。"""

    path: str
    kind: str = "MODIFY"          # ADD | MODIFY | DELETE
    content: Optional[str] = None
    repo: Optional[str] = None    # 变更声称所属仓（跨仓污染判据）
    source: str = "paths"         # paths | staged | json
    # 内容判据的范围：None=整份内容都在范围内（默认，最严）；
    # 元组=只判这些新文件行号（``--content-scope added``），其余行命中的凭证降级为 advisory。
    added_lines: Optional[Tuple[int, ...]] = None

    def to_json(self) -> Dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "repo": self.repo,
                "source": self.source, "has_content": self.content is not None}


@dataclass
class Violation:
    """一条拦截记录。``excerpt`` 永远经过脱敏，绝不回显凭证原文。

    ``evidence_sha256`` 为 patch 片段摘要（16 字符截断），用于审计溯源。
    """

    code: str
    category: str
    path: str
    rule_id: str = ""
    detail: str = ""
    line: Optional[int] = None
    excerpt: Optional[str] = None
    evidence_sha256: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"code": self.code, "category": self.category,
                              "path": self.path, "rule_id": self.rule_id, "detail": self.detail}
        if self.line is not None:
            out["line"] = self.line
        if self.excerpt is not None:
            out["excerpt"] = self.excerpt
        if self.evidence_sha256 is not None:
            out["evidence_sha256"] = self.evidence_sha256
        return out


@dataclass
class Verdict:
    """一次门判决。``verdict`` ∈ {PASS, FAIL}；``decision`` ∈ {allow, block, unknown}。"""

    verdict: str = "PASS"
    decision: str = "allow"
    violations: List[Violation] = field(default_factory=list)
    advisories: List[Dict[str, Any]] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    policy_ref: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def add(self, violation: Violation) -> None:
        self.violations.append(violation)
        if violation.category == "invalid":
            self.decision = "unknown"
        elif self.decision != "unknown":
            # 判决字段必须与 ``verdict`` 同向：``FAIL`` 却写 ``decision=allow`` 会让下游
            # （钩子/账本/面板）把拦截读成放行，等于给审计留了一条自相矛盾的口子
            self.decision = "block"
        self.verdict = "FAIL"

    @property
    def blocked(self) -> int:
        return len(self.violations)

    def counts(self) -> Dict[str, int]:
        out = {name: 0 for name in CATEGORIES}
        for v in self.violations:
            out[v.category] = out.get(v.category, 0) + 1
        return out

    def to_json(self) -> Dict[str, Any]:
        violations = sorted(
            (v.to_json() for v in self.violations),
            key=lambda v: (v["category"], v["path"], v.get("line") or 0, v["code"], v["rule_id"]),
        )
        files = sorted(self.files, key=lambda f: f["path"])
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "decision": self.decision if self.verdict == "FAIL" else "allow",
            "violations": violations,
            "advisories": sorted(self.advisories, key=lambda a: (a["path"], a.get("rule_id", ""))),
            "files": files,
            "totals": {
                "changes": len(files),
                "blocked": len(violations),
                "by_category": self.counts(),
            },
            "policy": self.policy_ref,
            "meta": self.meta,
        }
        payload["digest"] = digest(payload)
        return payload
