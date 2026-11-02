"""声明-证据台账：把「方法学声明」与「实现证据」机器配对，压住证据分级漂移。

为什么需要它
------------
平台的能力证据分级（REAL / MOCK / STUB / WITHHELD）历史上靠人眼读代码 + 读报告：
m1523 靠人眼发现回转要素落在 0.5L 整数栅格，m1547 靠人眼发现垂荡 RAO 恒为 1.260，
m1679 靠人眼发现 `_METHOD` 宣称 "Lewis-section 2D coefficients" 而实现是拟合频率
因子。**同一个失效模式换一个模块就要重新靠人眼发现一次**：docstring 或端点
provenance 字符串（`{"method": ...}`，会被下游报告/审计工具当作证据消费）宣称某个
具名方法，而代码体里没有该方法的实现足迹（系数表、共形映射、积分核、Bessel 函数…）。

本工具把这件事机器化，三个动作：

1. **抽声明**（claim）：模块 docstring 中的具名方法提及；以及 **provenance 字符串**
   （字典字面量里 ``method/model/basis/formula/source/theory`` 等键的字符串值，
   含模块级 `_METHOD = "..."` 常量的解析）。
2. **抽证据**（evidence）：只在**代码体**找该方法特有的实现足迹——docstring 与注释
   先被剥离（否则声明自己就是证据，判据自证）。是否允许**委派**到被本模块 import
   的同语料模块，由 ``allow_delegation`` 控制。
3. **判级**（verdict）：

   ==================  ============================================================
   判级                 含义
   ==================  ============================================================
   EVIDENCED            本模块代码体有该方法特征
   DELEGATED            本模块没有，但其 import 的同语料模块有（真实现被复用）
   QUALIFIED            无特征，但同一句内带限定语（简化/近似/拟合/占位/仅用于/
                        等效/heuristic/proxy）⇒ **诚实披露**，不算缺陷
   REFERENCE_MENTION    同句是引用/否定/对比语境（参考文献、pseudo、no/not、
                        参见、不适用…）⇒ 只是提及，不算实现声明
   UNBACKED             docstring 声明具名方法、无特征、无限定语、非引用语境 ⇒ 待裁
   PROVENANCE_DRIFT     **会被下游当证据消费**的 provenance 字符串宣称具名方法而
                        无实现特征 ⇒ 缺陷本体（消费者无法分辨真假）
   ==================  ============================================================

纪律
----
* 判决只是**线索分级**，不是最终证据分级；UNBACKED 仍需人工裁决（可能是命名沿用）。
  工具的价值：把「要人看的地方」从 1600+ 篇报告压到一张清单，并让**机器消费的
  provenance 字符串**不再能单方面宣称方法学。
* 判据只读本文本，不引用外部参考值 ⇒ 判据本身不需要被判决。
* ``--gate``：存在 PROVENANCE_DRIFT（``--strict`` 时含 UNBACKED）则退出码 1，
  可直接接 CI / pytest 作反漂移闸门。
* 纯标准库，离线可跑，不联网、不写平台状态。

用法
----
    python3 tools/audit/claim_evidence_ledger.py                      # 全树台账
    python3 tools/audit/claim_evidence_ledger.py --root backend/api --gate
    python3 tools/audit/claim_evidence_ledger.py --file backend/api/seakeeping_physics.py
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- 词典
# 每个具名方法 = 声明正则 + 代码体实现足迹正则（任一命中即算有证据）。
# 足迹必须是**该方法特有**的东西；禁止用通用词（如 mapping/table）当证据，否则
# 判据退化为恒真（m1962 自测：'mapping' 曾把 Lewis 判成 EVIDENCED）。
LEXICON: Dict[str, Dict[str, object]] = {
    "ITTC-1957": {
        "claim": r"ITTC[\s\-]?(?:1957|57)\b",
        # 「0.075 与 log10 同在代码体」= 这条线的实现足迹（写成 denom 变量也算）
        "evidence": [[r"0\.075", r"log10"]],
    },
    "ITTC-1978": {
        "claim": r"ITTC[\s\-]?(?:1978|78)\b",
        "evidence": [[r"1\.067", r"log10"],
                     [r"105", r"0\.64"],          # C_A = (105 (ks/L)^(1/3) - 0.64)e-3
                     [r"0\.006\s*\(", r"0\.00205"]],
    },
    "Holtrop-Mennen": {
        "claim": r"Holtrop",
        "evidence": [[r"\bc1[0-7]\b"], [r"m1", r"\bfn\b"],
                     [r"0\.453", r"0\.4425"],    # Holtrop-Mennen 湿表面积回归式
                     [r"1\s*\+\s*k1|one_k1"]],
    },
    "Michell": {
        "claim": r"Michell",
        "evidence": [r"1\.0\s*/\s*math\.cosh|sech", r"michell_integral|integrand"],
    },
    "Lewis-section": {
        "claim": r"Lewis[\s\-]?(?:form|section|形|截面|剖面)",
        "evidence": [r"\ba_?1\b[^\n]{0,60}\ba_?3\b", r"conformal|multi_?pole|"
                     r"half[\s_]?beam[\s_/]draft"],
    },
    "Salvesen-Tuck-Faltinsen": {
        "claim": r"Salvesen|Tuck[\s\-]?Faltinsen",
        "evidence": [r"\bf_a\s*\(|\bf_b\s*\("],
    },
    "Tasai": {
        "claim": r"Tasai",
        "evidence": [r"multi_?pole|conformal|zeta"],
    },
    "Frank-close-fit": {
        "claim": r"Frank[\s\-]?(?:close|close[\s\-]?fit)",
        "evidence": [r"close[\s_]?fit|green_function|pulsating[\s_]?source"],
    },
    "Ikeda": {
        "claim": r"Ikeda",
        "evidence": [r"eddy|bilge[\s_]?keel|hull[\s_]?friction"],
    },
    "Hess-Smith": {
        "claim": r"Hess[\s\-]?Smith",
        "evidence": [r"dipole|source[\s_]?panel|panel[\s_]?strength"],
    },
    "Wagner": {
        "claim": r"\bWagner\b",
        "evidence": [r"2\.0\s*/\s*\(\s*1\.0\s*\+|sqrt\(\s*1\.0\s*\+\s*2\.0"],
    },
    "Stefan": {
        "claim": r"\bStefan\b",
        "evidence": [r"stefan_growth|ICE_LATENT_HEAT|l_f\b"],
    },
    "NSGA-II": {
        "claim": r"NSGA[\s\-]?II",
        "evidence": [r"crowding|non[\s_]?dominated|sbx"],
    },
    "CMA-ES": {
        "claim": r"CMA[\s\-]?ES",
        "evidence": [r"covariance|sigma[\s_]?path|pc\b"],
    },
}

#: 「诚实披露」限定语：同一句内出现即判 QUALIFIED（不算缺陷）。
QUALIFIERS = re.compile(
    r"简化|近似|拟合|占位|示意|等效|仅用于|经验式|沿用|粗略|形式|形（|形\(|"
    r"\bform\b|\bapproximat|\bfit(?:ted)?\b|heuristic|proxy|estimate|lumped|"
    r"placeholder|schematic|未验证|not\s+verified",
    re.I,
)

#: 引用 / 否定 / 对比语境：同一句内出现即判 REFERENCE_MENTION（不是实现声明）。
REFERENCE_MARKERS = re.compile(
    r"pseudo|引用|参见|参考|文献|原文|paper|report|§|见\s*docs|see\s+docs|"
    r"\bnot\b|\bno\s+\w+\s+term|\bfalse\b|不可能|未实现|不适用|"
    r"缺失|并非|不是|instead\s+of|rather\s+than|对照",
    re.I,
)

#: 会被下游消费的 provenance 键（端点回执/报告引用它们当证据）。
PROVENANCE_KEYS = {"method", "model", "model_name", "basis", "formula", "source",
                   "theory", "reference", "solver", "kernel", "approach", "method_name"}

DEFAULT_ROOTS = ("backend/api", "backend/services", "src")

#: 台账分级顺序。
VERDICT_ORDER = ("EVIDENCED", "DELEGATED", "QUALIFIED", "REFERENCE_MENTION",
                 "UNBACKED", "PROVENANCE_DRIFT")


# --------------------------------------------------------------------- 文本预处理
def strip_docstrings_and_comments(source: str) -> str:
    """返回只含**可执行代码**的文本：docstring / 注释全部剥离。

    证据判据必须作用在代码体上，否则「声明本身」就是证据（判据自证）。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    spans: List[Tuple[int, int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(getattr(body[0], "value", None), ast.Constant) and \
                    isinstance(body[0].value.value, str):
                d = body[0].value
                spans.append((d.lineno, d.col_offset, d.end_lineno, d.end_col_offset))
    lines = source.splitlines(keepends=True)
    out: List[str] = []
    for i, line in enumerate(lines, start=1):
        for (l0, c0, l1, c1) in spans:
            if l0 <= i <= l1:
                if l0 == l1:
                    line = line[:c0] + line[c1:]
                elif i == l0:
                    line = line[:c0] + "\n"
                elif i == l1:
                    line = line[c1:]
                else:
                    line = ""
                break
        out.append(line)
    text = "".join(out)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
        return "\n".join(t.string for t in toks if t.type != tokenize.COMMENT)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return re.sub(r"#[^\n]*", "", text)


def _segments(doc: str) -> Iterable[str]:
    """把 docstring 切成句子级片段（限定语/引用语境只在**同句**生效）。"""
    for seg in re.split(r"[。；;\n]+", doc):
        s = seg.strip()
        if s:
            yield s


def _claim_segments(doc: str, pattern: str) -> List[str]:
    rx = re.compile(pattern, re.I)
    return [seg for seg in _segments(doc) if rx.search(seg)]


# ----------------------------------------------------------------------- 抽声明
def iter_python_files(roots: Sequence[str]) -> List[str]:
    files: List[str] = []
    for root in roots:
        if os.path.isfile(root) and root.endswith(".py"):
            files.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in sorted(dirnames)
                           if d not in {".git", "__pycache__", "node_modules", ".venv"}]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    files.append(os.path.join(dirpath, fn))
    return files


def _module_string_constants(tree: ast.AST) -> Dict[str, str]:
    """模块级字符串常量（`_METHOD = "..."`，含 `+` 常量拼接），供 provenance 解析。"""
    out: Dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        text: Optional[str] = None
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
        elif isinstance(value, ast.JoinedStr):
            text = "".join(p.value for p in value.values
                           if isinstance(p, ast.Constant) and isinstance(p.value, str))
        elif isinstance(value, ast.BinOp):
            lhs = getattr(value.left, "value", None)
            rhs = getattr(value.right, "value", None)
            if isinstance(lhs, str) and isinstance(rhs, str):
                text = lhs + rhs
        if text is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = text
    return out


def _string_expr_value(node: ast.AST, consts: Dict[str, str]) -> str:
    """尽力把字符串表达式取值（常量 / f-string / 常量+变量 / 模块级常量名）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(p.value for p in node.values
                       if isinstance(p, ast.Constant) and isinstance(p.value, str))
    if isinstance(node, ast.BinOp):
        return _string_expr_value(node.left, consts) + _string_expr_value(node.right, consts)
    return ""


def extract_provenance_strings(tree: ast.AST,
                               consts: Optional[Dict[str, str]] = None
                               ) -> List[Dict[str, object]]:
    """抽出会被下游消费的 provenance 字符串（``{"method": "..."}`` 一类）。"""
    consts = consts or {}
    found: List[Dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            key = k.value.strip().lower()
            if key not in PROVENANCE_KEYS:
                continue
            text = _string_expr_value(v, consts).strip()
            if len(text) < 8:
                continue
            found.append({"key": key, "text": text, "line": node.lineno})
    return found


def _imports(tree: ast.AST) -> List[str]:
    """本模块 import 的同语料模块（按模块 basename 记录，供委派解析）。"""
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.append(a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[-1])
            for a in node.names:
                names.append(a.name.split(".")[-1])
    return names


def scan_module(path: str) -> Dict[str, object]:
    """单模块原始扫描结果（判级在 build_ledger 里跨模块完成后进行）。"""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"module": path, "skipped": "syntax-error", "claims": [],
                "provenance": [], "imports": [], "evidence": {}}
    doc = ast.get_docstring(tree, clean=False) or ""
    code = strip_docstrings_and_comments(source)
    consts = _module_string_constants(tree)
    evidence: Dict[str, int] = {}
    for method, spec in LEXICON.items():
        evidence[method] = _count_alternatives(spec["evidence"], code)
    claims: List[Dict[str, object]] = []
    for method, spec in LEXICON.items():
        segs = _claim_segments(doc, str(spec["claim"]))
        if segs:
            claims.append({"method": method, "segments": segs,
                           "evidence_hits": evidence[method]})
    prov = extract_provenance_strings(tree, consts)
    return {"module": path, "claims": claims, "provenance": prov,
            "imports": _imports(tree), "evidence": evidence}


def _alt_matches(alt, code: str) -> bool:
    """一个证据备选：str = 单条正则；list = 全部正则须命中（合取）。"""
    if isinstance(alt, str):
        return bool(re.search(alt, code, re.I))
    return all(re.search(str(p), code, re.I) for p in alt)


def _count_alternatives(alternatives, code: str) -> int:
    return sum(1 for alt in alternatives if _alt_matches(alt, code))


def _judge(text: str, has_local_evidence: bool, delegated: bool) -> str:
    if has_local_evidence:
        return "EVIDENCED"
    if QUALIFIERS.search(text):
        return "QUALIFIED"
    if REFERENCE_MARKERS.search(text):
        return "REFERENCE_MENTION"
    if delegated:
        return "DELEGATED"
    return "UNBACKED"


def build_ledger(roots: Sequence[str] = DEFAULT_ROOTS,
                 files: Optional[Sequence[str]] = None,
                 allow_delegation: bool = True) -> Dict[str, object]:
    paths = list(files) if files is not None else iter_python_files(roots)
    raw = [scan_module(p) for p in paths]

    # 证据索引：方法 → 有该实现足迹的模块 basename 集合
    by_basename: Dict[str, set] = {}
    for rec in raw:
        base = os.path.basename(str(rec["module"]))[:-3]
        for method, hits in (rec.get("evidence") or {}).items():
            if hits:
                by_basename.setdefault(base, set()).add(method)

    claims: List[Dict[str, object]] = []
    provs: List[Dict[str, object]] = []
    for rec in raw:
        mod = str(rec["module"])
        imp = set(rec.get("imports") or [])
        local = rec.get("evidence") or {}
        for c in rec.get("claims") or []:
            method = str(c["method"])
            delegated = allow_delegation and any(
                method in by_basename.get(b, set()) for b in imp)
            verdicts = [_judge(seg, bool(local.get(method)),
                               delegated and not QUALIFIERS.search(seg)
                               and not REFERENCE_MARKERS.search(seg))
                        for seg in c["segments"]]
            # 取最弱（最需人看）的分级
            order = {v: i for i, v in enumerate(VERDICT_ORDER)}
            verdict = max(verdicts, key=lambda v: order[v])
            claims.append({"module": mod, "method": method, "verdict": verdict,
                           "evidence_hits": int(local.get(method) or 0),
                           "delegated": delegated,
                           "n_segments": len(c["segments"]),
                           "sample": str(c["segments"][0])[:200]})
        for p in rec.get("provenance") or []:
            text = str(p["text"])
            for method, spec in LEXICON.items():
                if not re.search(str(spec["claim"]), text, re.I):
                    continue
                delegated = allow_delegation and any(
                    method in by_basename.get(b, set()) for b in imp)
                has_local = bool(local.get(method))
                if has_local:
                    verdict = "EVIDENCED"
                elif QUALIFIERS.search(text):
                    verdict = "QUALIFIED"
                elif delegated:
                    verdict = "DELEGATED"
                else:
                    verdict = "PROVENANCE_DRIFT"
                provs.append({"module": mod, "method": method, "key": p["key"],
                              "line": int(p["line"]), "verdict": verdict,
                              "evidence_hits": int(local.get(method) or 0),
                              "text": text[:220]})

    by_verdict: Dict[str, int] = {v: 0 for v in VERDICT_ORDER}
    for c in claims + provs:
        by_verdict[str(c["verdict"])] = by_verdict.get(str(c["verdict"]), 0) + 1
    return {
        "n_modules_scanned": len(paths),
        "n_modules_with_claims": sum(1 for r in raw
                                     if r.get("claims") or r.get("provenance")),
        "n_claim_entries": len(claims),
        "n_provenance_entries": len(provs),
        "by_verdict": by_verdict,
        "provenance_drift": sorted([p for p in provs if p["verdict"] == "PROVENANCE_DRIFT"],
                                   key=lambda r: (r["module"], r["line"])),
        "unbacked_claims": sorted([c for c in claims if c["verdict"] == "UNBACKED"],
                                  key=lambda r: (r["module"], r["method"])),
        "claims": claims,
        "provenance": provs,
    }


# ----------------------------------------------------------------------- 输出
def render_markdown(ledger: Dict[str, object], limit: int = 60) -> str:
    bv = ledger["by_verdict"]
    lines = ["# 声明-证据台账（claim → evidence ledger）", "",
             f"- 扫描模块 **{ledger['n_modules_scanned']}**"
             f"（含声明 {ledger['n_modules_with_claims']}）",
             f"- 声明条目 **{ledger['n_claim_entries']}**（docstring 具名方法）"
             f" + **{ledger['n_provenance_entries']}**（provenance 字符串）",
             "- 分级：" + " · ".join(f"{k} {bv.get(k, 0)}" for k in VERDICT_ORDER), "",
             "## PROVENANCE_DRIFT（下游当证据消费却无实现足迹）", "",
             "| 模块 | 行 | 键 | 方法 | 字符串 |", "|---|---|---|---|---|"]
    for r in ledger["provenance_drift"][:limit]:
        lines.append(f"| `{r['module']}` | {r['line']} | `{r['key']}` | "
                     f"{r['method']} | {str(r['text'])[:120]} |")
    if not ledger["provenance_drift"]:
        lines.append("| — | — | — | — | 无 |")
    lines += ["", "## UNBACKED（docstring 宣称具名方法，无足迹、无限定语、非引用语境）", "",
              "| 模块 | 方法 | 片段 |", "|---|---|---|"]
    for r in ledger["unbacked_claims"][:limit]:
        lines.append(f"| `{r['module']}` | {r['method']} | {str(r['sample'])[:120]} |")
    if not ledger["unbacked_claims"]:
        lines.append("| — | — | 无 |")
    return "\n".join(lines) + "\n"


def gate_failed(ledger: Dict[str, object], strict: bool = False) -> bool:
    if ledger["provenance_drift"]:
        return True
    return bool(strict and ledger["unbacked_claims"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="声明-证据台账 / 反漂移闸门")
    ap.add_argument("--root", action="append", default=None,
                    help=f"扫描根目录，可重复（默认 {', '.join(DEFAULT_ROOTS)}）")
    ap.add_argument("--file", action="append", default=None, help="只扫指定文件")
    ap.add_argument("--json", default="artifacts/claim_ledger/ledger.json")
    ap.add_argument("--md", default="artifacts/claim_ledger/ledger.md")
    ap.add_argument("--no-delegation", action="store_true",
                    help="不把 import 来的实现在台账里算作本模块证据")
    ap.add_argument("--gate", action="store_true", help="有 PROVENANCE_DRIFT 则退出码 1")
    ap.add_argument("--strict", action="store_true", help="闸门同时卡 UNBACKED")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    roots = args.root or list(DEFAULT_ROOTS)
    ledger = build_ledger(roots, files=args.file,
                          allow_delegation=not args.no_delegation)
    for path in (args.json, args.md):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1)
    with open(args.md, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(ledger))
    if not args.quiet:
        bv = ledger["by_verdict"]
        print(f"modules={ledger['n_modules_scanned']} claims={ledger['n_claim_entries']} "
              f"provenance={ledger['n_provenance_entries']}")
        print("verdicts: " + " ".join(f"{k}={v}" for k, v in bv.items()))
        for r in ledger["provenance_drift"]:
            print(f"DRIFT  {r['module']}:{r['line']} [{r['key']}] {r['method']} :: "
                  f"{str(r['text'])[:80]}")
    if args.gate and gate_failed(ledger, args.strict):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())