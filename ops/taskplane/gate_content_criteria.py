#!/usr/bin/env python3
"""gate_content_criteria —— 把门判据从「工件存在」升级为「工件内容满足声明的数字判据」。

## 缺陷（对照基线）

``gate_auto_release.evaluate_gate()`` 的 C2 判据只看三件事：路径能解析到文件、
文件字节数 > 0、``.json`` 能 json 解析。于是**空洞工件也能放门**：

    echo '{}' > artifacts/x/verdict.json     # 旧门 → release（reason=satisfied）

「判据数字落报告 JSON」（节点任务书的常见表述）在旧门里完全不可机验：数字可以是
任意值、可以根本不存在、也可以被手抄进报告而没有任何溯源——门都放。

## 本模块做什么（平台共性机制，零领域语义）

门 metadata 里可增量声明 ``gate_criteria.checks``（或 ``acceptance_checks``）：
每条 check = 「某个工件里的某个值，必须满足某个算子阈值」。本模块提供**纯函数**
解析 + 求值 + 三态裁决：

* ``PASS``     —— 每条声明判据都被实测值满足（附 path/size/sha256/实测值作证据）；
* ``FAIL``     —— 判据可判且**被违反**（阈值不达标 / 摘要不符 / 逃逸路径 / 类型冲突）；
* ``WITHHELD`` —— 证据不足，**绝不折算成 PASS**（文件缺失 / JSON 损坏 / 指针不存在 /
                  值为 null）：工件可能正在写，门继续等即可；

（``检查集为空`` → ``NOT_DECLARED``，调用方保持旧行为，向后兼容。）

判定只依赖文件内容与 metadata，不读库、不联网、不看时钟，因此同一输入逐字节同结论；
``digest`` = 全部 check 结果规范化 JSON 的 sha256，落审计后即可复算比对。

## metadata 形状

.. code-block:: json

    "gate_criteria": {
      "artifacts": ["artifacts/demo/verdict.json"],
      "checks": [
        {"id": "residual",  "kind": "json_pointer",
         "path": "artifacts/demo/verdict.json", "pointer": "/checks/residual",
         "op": "<=", "value": 0.03},
        {"id": "fig_bytes", "kind": "min_bytes",
         "path": "artifacts/demo/plot.png", "value": 4096},
        {"id": "fig_digest","kind": "sha256",
         "path": "artifacts/demo/plot.png", "digest": "9f86d0…"},
        {"id": "verdict",   "kind": "json_pointer",
         "path": "artifacts/demo/verdict.json", "pointer": "/verdict",
         "op": "in", "value": ["PASS", "FAIL"]},
        {"id": "no_todo",   "kind": "text_contains",
         "path": "docs/report.md", "pattern": "TODO", "negate": true}
      ]
    }

支持的 ``kind``：``exists`` / ``min_bytes`` / ``max_bytes`` / ``sha256`` /
``json_pointer`` / ``json_count`` / ``text_contains`` / ``text_regex``。
支持的 ``op``：``== != < <= > >= ~= in contains``（``~=`` 为带 ``tolerance`` 的近似等）。

CLI::

    python3 ops/taskplane/gate_content_criteria.py --metadata m.json --roots . \
        --json-out artifacts/gate_content_criteria/verdict.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# 三态 + 「未声明」
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_WITHHELD = "WITHHELD"
VERDICT_NOT_DECLARED = "NOT_DECLARED"

KINDS = frozenset({
    "exists", "min_bytes", "max_bytes", "sha256",
    "json_pointer", "json_count", "text_contains", "text_regex",
})
OPS = frozenset({"==", "!=", "<", "<=", ">", ">=", "~=", "in", "contains"})

_REQUIRED = {
    "exists": ("path",),
    "min_bytes": ("path", "value"),
    "max_bytes": ("path", "value"),
    "sha256": ("path", "digest"),
    "json_pointer": ("path", "pointer", "op", "value"),
    "json_count": ("path", "pointer", "op", "value"),
    "text_contains": ("path", "pattern"),
    "text_regex": ("path", "pattern"),
}


class SpecError(ValueError):
    """metadata 里声明的 check 不合法（声明错误必须显式报错，不可静默跳过）。"""


# ─────────────────────────── 声明解析 ───────────────────────────

@dataclasses.dataclass(frozen=True)
class CheckSpec:
    id: str
    kind: str
    path: str
    pointer: str = ""
    op: str = ""
    value: Any = None
    digest: str = ""
    pattern: str = ""
    negate: bool = False
    tolerance: float = 1e-9
    note: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _spec_id(raw: Mapping[str, Any]) -> str:
    explicit = str(raw.get("id") or "").strip()
    if explicit:
        return explicit
    bits = [str(raw.get("kind") or ""), str(raw.get("path") or ""),
            str(raw.get("pointer") or ""), str(raw.get("op") or "")]
    return "@".join(b for b in bits if b)


def parse_check(raw: Any, index: int = 0) -> CheckSpec:
    """把一条原始声明校验成 CheckSpec（未知字段忽略、未知 kind/op 显式报错）。"""
    if not isinstance(raw, Mapping):
        raise SpecError(f"check#{index}: 必须是对象，得到 {type(raw).__name__}")
    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        raise SpecError(f"check#{index}: 未知 kind={kind!r}，合法值={sorted(KINDS)}")
    for field in _REQUIRED[kind]:
        if raw.get(field) in (None, ""):
            raise SpecError(f"check#{index}({kind}): 缺少必填字段 {field!r}")
    op = str(raw.get("op") or "").strip()
    if kind in ("json_pointer", "json_count"):
        if op not in OPS:
            raise SpecError(f"check#{index}({kind}): 未知 op={op!r}，合法值={sorted(OPS)}")
    tolerance = raw.get("tolerance", 1e-9)
    try:
        tolerance = float(tolerance)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"check#{index}: tolerance 非数值 {tolerance!r}") from exc
    if not math.isfinite(tolerance) or tolerance < 0:
        raise SpecError(f"check#{index}: tolerance 必须是非负有限数，得到 {tolerance!r}")
    digest = str(raw.get("digest") or raw.get("sha256") or "").strip().lower()
    if kind == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SpecError(f"check#{index}: digest 必须是 64 位十六进制")
    return CheckSpec(
        id=_spec_id(raw), kind=kind, path=str(raw.get("path")),
        pointer=str(raw.get("pointer") or ""), op=op, value=raw.get("value"),
        digest=digest, pattern=str(raw.get("pattern") or ""),
        negate=bool(raw.get("negate", False)), tolerance=tolerance,
        note=str(raw.get("note") or ""),
    )


def parse_checks(metadata: Mapping[str, Any] | None) -> tuple[list[CheckSpec], str]:
    """从门 metadata 解析全部内容判据；返回 (specs, 声明来源)。"""
    meta = dict(metadata or {})
    for source, key in (("metadata.gate_criteria", "gate_criteria"),
                        ("metadata.acceptance_checks", "acceptance_checks")):
        raw = meta.get(key)
        if isinstance(raw, Mapping):
            checks = raw.get("checks")
            if checks is not None:
                if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
                    raise SpecError(f"{source}.checks 必须是数组")
                sealed = [parse_check(item, i) for i, item in enumerate(checks)]
                seen: set[str] = set()
                for spec in sealed:
                    if spec.id in seen:
                        raise SpecError(f"{source}: check id 重复 {spec.id!r}")
                    seen.add(spec.id)
                return sealed, source
        elif key == "acceptance_checks" and raw is not None:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise SpecError("metadata.acceptance_checks 必须是数组")
            sealed = [parse_check(item, i) for i, item in enumerate(raw)]
            return sealed, "metadata.acceptance_checks"
    return [], "none"


# ─────────────────────────── 通用算子 ───────────────────────────

def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def compare(op: str, observed: Any, expected: Any, tolerance: float = 1e-9) -> tuple[bool, str]:
    """通用比较：返回 (是否满足, 说明)。类型不可比时抛 ValueError（调用方判 FAIL）。"""
    if op == "in":
        options = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return (observed in options), f"{observed!r} in {list(options)!r}"
    if op == "contains":
        return (str(expected) in str(observed)), f"{expected!r} in observed"
    left, right = _num(observed), _num(expected)
    if left is None or right is None:
        # 非数值只允许 == / !=
        if op == "==":
            return observed == expected, f"{observed!r} == {expected!r}"
        if op == "!=":
            return observed != expected, f"{observed!r} != {expected!r}"
        raise ValueError(
            f"op={op!r} 需要数值，得到 observed={observed!r} expected={expected!r}")
    if op == "==":
        return left == right, f"{left} == {right}"
    if op == "!=":
        return left != right, f"{left} != {right}"
    if op == "<":
        return left < right, f"{left} < {right}"
    if op == "<=":
        return left <= right, f"{left} <= {right}"
    if op == ">":
        return left > right, f"{left} > {right}"
    if op == ">=":
        return left >= right, f"{left} >= {right}"
    if op == "~=":
        ok = math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)
        return ok, f"|{left}-{right}| <= tol({tolerance})"
    raise ValueError(f"未知 op={op!r}")


def json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """RFC6901 取值：返回 (是否存在, 值)。"""
    if pointer in ("", "/"):
        return True, document
    if not pointer.startswith("/"):
        raise ValueError(f"pointer 必须以 '/' 开头: {pointer!r}")
    cur = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, Mapping):
            if token not in cur:
                return False, None
            cur = cur[token]
        elif isinstance(cur, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return False, None
            idx = int(token)
            if idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            return False, None
    return True, cur


# ─────────────────────────── 工件读取（含逃逸防护） ───────────────────────────

def _contained(path: Path, roots: Sequence[str]) -> bool:
    if not roots:
        return True
    real = os.path.realpath(path)
    for root in roots:
        base = os.path.realpath(root)
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def resolve_file(pattern: str, roots: Sequence[str], repo: str | None = None) -> tuple[Path | None, str]:
    """解析工件路径（与 gate_auto_release.resolve_artifact 同语义，单一真相源）。

    返回 (path|None, detail)。逃逸 roots 的路径一律拒绝（detail=escapes_roots）。
    """
    roots = list(roots or [])
    if os.path.isabs(pattern):
        candidates = [pattern]
    else:
        if roots:
            candidates = [os.path.join(root, pattern) for root in roots]
        else:
            candidates = [pattern]
    for cand in candidates:
        if ".." in Path(cand).parts:
            return None, "escapes_roots"
    hits = sorted((c for c in candidates if os.path.isfile(c) and os.path.getsize(c) > 0),
                  key=len)
    if not hits:
        return None, "not_found"
    if repo:
        scoped = [h for h in hits if f"/{repo}/" in h]
        if not scoped:
            return None, f"repo_mismatch(expect {repo})"
        hits = scoped
    hit = hits[0]
    if not _contained(Path(hit), roots):
        return None, "escapes_roots"
    return Path(hit), "ok"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> tuple[Any, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), "ok"
    except Exception as exc:  # noqa: BLE001 - 损坏/半写工件按证据不足处理
        return None, f"json_invalid: {type(exc).__name__}"


def _read_text(path: Path) -> tuple[str | None, str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), "ok"
    except Exception as exc:  # noqa: BLE001
        return None, f"read_error: {type(exc).__name__}"


# ─────────────────────────── 求值 ───────────────────────────

@dataclasses.dataclass
class CheckResult:
    spec: CheckSpec
    verdict: str
    observed: Any = None
    detail: str = ""
    evidence: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.spec.id,
            "kind": self.spec.kind,
            "path": self.spec.path,
            "pointer": self.spec.pointer or None,
            "op": self.spec.op or None,
            "expected": self.spec.value if self.spec.value is not None else (
                self.spec.digest or self.spec.pattern or None),
            "negate": self.spec.negate,
            "verdict": self.verdict,
            "observed": self.observed,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclasses.dataclass
class ContentVerdict:
    declared: bool
    source: str
    verdict: str
    checks: list[CheckResult] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    @property
    def blocking(self) -> bool:
        """是否需要拦住放门（NOT_DECLARED 不拦）。"""
        return self.declared and self.verdict != VERDICT_PASS

    def digest(self) -> str:
        """全部 check 结果的规范化 sha256。

        **路径无关**：剔除 ``evidence.resolved_path``（绝对路径随机器/roots 变化），
        保留 ``size_bytes`` / ``sha256`` 等内容证据——同一内容在任何检出位置得同一
        digest，跨机复核才有意义。
        """
        projected = []
        for c in self.checks:
            item = c.to_dict()
            item["evidence"] = {k: v for k, v in item["evidence"].items()
                                if k != "resolved_path"}
            projected.append(item)
        payload = json.dumps(projected, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        counts = {v: sum(1 for c in self.checks if c.verdict == v)
                  for v in (VERDICT_PASS, VERDICT_FAIL, VERDICT_WITHHELD)}
        return {
            "declared": self.declared,
            "source": self.source,
            "verdict": self.verdict,
            "counts": counts,
            "digest": self.digest(),
            "warnings": list(self.warnings),
            "checks": [c.to_dict() for c in self.checks],
        }


def _evaluate_one(spec: CheckSpec, roots: Sequence[str], repo: str | None) -> CheckResult:
    path, detail = resolve_file(spec.path, roots, repo)
    if path is None:
        verdict = VERDICT_FAIL if detail == "escapes_roots" else VERDICT_WITHHELD
        return CheckResult(spec, verdict, None, detail)
    evidence = {"resolved_path": str(path), "size_bytes": path.stat().st_size}

    if spec.kind == "exists":
        return CheckResult(spec, VERDICT_PASS, True, "ok", evidence)
    if spec.kind in ("min_bytes", "max_bytes"):
        size = path.stat().st_size
        op = ">=" if spec.kind == "min_bytes" else "<="
        try:
            ok, why = compare(op, size, spec.value)
        except ValueError as exc:
            return CheckResult(spec, VERDICT_FAIL, size, f"compare_error: {exc}", evidence)
        return CheckResult(spec, VERDICT_PASS if ok else VERDICT_FAIL, size, why, evidence)
    if spec.kind == "sha256":
        actual = _sha256_of(path)
        evidence["sha256"] = actual
        ok = actual == spec.digest
        return CheckResult(spec, VERDICT_PASS if ok else VERDICT_FAIL, actual,
                           "digest_match" if ok else "digest_mismatch", evidence)

    if spec.kind in ("json_pointer", "json_count"):
        document, status = _load_json(path)
        if status != "ok":
            return CheckResult(spec, VERDICT_WITHHELD, None, status, evidence)
        try:
            present, value = json_pointer(document, spec.pointer)
        except ValueError as exc:
            return CheckResult(spec, VERDICT_FAIL, None, f"bad_pointer: {exc}", evidence)
        if not present or value is None:
            return CheckResult(spec, VERDICT_WITHHELD, value,
                               "pointer_absent" if not present else "value_null", evidence)
        observed = value
        if spec.kind == "json_count":
            if not isinstance(value, (list, tuple, Mapping, str)):
                return CheckResult(spec, VERDICT_FAIL, value,
                                   f"not_countable: {type(value).__name__}", evidence)
            observed = len(value)
        try:
            ok, why = compare(spec.op, observed, spec.value, spec.tolerance)
        except ValueError as exc:
            return CheckResult(spec, VERDICT_FAIL, observed, f"compare_error: {exc}", evidence)
        return CheckResult(spec, VERDICT_PASS if ok else VERDICT_FAIL, observed, why, evidence)

    text, status = _read_text(path)
    if status != "ok":
        return CheckResult(spec, VERDICT_WITHHELD, None, status, evidence)
    hit = (spec.pattern in text) if spec.kind == "text_contains" else \
        bool(re.search(spec.pattern, text))
    ok = (not hit) if spec.negate else hit
    return CheckResult(spec, VERDICT_PASS if ok else VERDICT_FAIL, hit,
                       f"hit={hit} negate={spec.negate}", evidence)


def aggregate(results: Iterable[CheckResult]) -> str:
    """三态合取：任一 FAIL → FAIL；否则任一 WITHHELD → WITHHELD；否则 PASS。

    FAIL 优先于 WITHHELD：已被实测否证的判据不会因为别处证据不足而被「挂着」。
    """
    results = list(results)
    if not results:
        return VERDICT_NOT_DECLARED
    if any(r.verdict == VERDICT_FAIL for r in results):
        return VERDICT_FAIL
    if any(r.verdict == VERDICT_WITHHELD for r in results):
        return VERDICT_WITHHELD
    return VERDICT_PASS


def evaluate_content(metadata: Mapping[str, Any] | None,
                     roots: Sequence[str] = (),
                     repo: str | None = None) -> ContentVerdict:
    """解析并求值门的内容判据（纯函数：只读工件，不写任何东西）。"""
    specs, source = parse_checks(metadata)
    if not specs:
        return ContentVerdict(False, source, VERDICT_NOT_DECLARED)
    results = [_evaluate_one(spec, roots, repo) for spec in specs]
    return ContentVerdict(True, source, aggregate(results), results)


def render_markdown(verdict: ContentVerdict, title: str = "gate content criteria") -> str:
    """把裁决渲染成可贴报告的 markdown 表（数字全部取自 CheckResult，不手抄）。"""
    lines = [f"# {title}", "",
             f"- verdict: **{verdict.verdict}**（source={verdict.source}, "
             f"digest=`{verdict.digest()}`）"]
    counts = verdict.to_dict()["counts"]
    lines.append(f"- counts: PASS={counts[VERDICT_PASS]} "
                 f"FAIL={counts[VERDICT_FAIL]} WITHHELD={counts[VERDICT_WITHHELD]}")
    lines += ["", "| id | kind | path | expected | observed | verdict | detail |",
              "|---|---|---|---|---|---|---|"]
    for c in verdict.checks:
        expected = c.spec.value if c.spec.value is not None else (
            c.spec.digest or c.spec.pattern or "-")
        lines.append("| {id} | {kind} | {path} | {exp} | {obs} | {verdict} | {detail} |".format(
            id=c.spec.id, kind=c.spec.kind, path=c.spec.path,
            exp=str(expected).replace("|", "\\|"),
            obs=str(c.observed).replace("|", "\\|"), verdict=c.verdict,
            detail=c.detail.replace("|", "\\|")))
    return "\n".join(lines) + "\n"


# ─────────────────────────── CLI ───────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="门内容判据求值（纯函数 + CLI 封装）")
    ap.add_argument("--metadata", required=True, help="含 gate_criteria 的 metadata JSON 文件")
    ap.add_argument("--roots", nargs="*", default=["."], help="工件根目录（默认 .）")
    ap.add_argument("--repo", default=None, help="仓归属约束（/repo/ 子串匹配）")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    args = ap.parse_args(argv)

    meta = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    verdict = evaluate_content(meta, args.roots, args.repo)
    payload = verdict.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.md_out:
        out = Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(verdict), encoding="utf-8")
    return 0 if payload["verdict"] in (VERDICT_PASS, VERDICT_NOT_DECLARED) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())