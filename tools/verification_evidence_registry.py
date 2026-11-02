#!/usr/bin/env python3
"""验证证据登记册（data 席 m2055）：把 docs/ 里的验证证据变成**可机器判级**的记录，并给指针腐烂分类。

动机
----
平台头号 KPI 是「已合并功能复核通过率」，但它长期是**人工口算**（看板写作 `4/4（在飞 0）`），
没有任何工具把「哪条证据算数」变成机器判据。反方向的工作已有一件：
``tools/validation_debt_ledger.py``（m1705）抽报告正文的「局限/待办」=**负空间**；
本工具抽工作树内验证证据文档的**指针**=**正空间**，回答两个问题：

1. 这篇「已验证」声称，凭什么？（外部基准 / 数值容差 / 可重跑的测试指针）
2. 这些指针**还能落地吗**？（验证证据是指针构成的，指针会腐烂：
   测试改名、文件迁到 ``backend/tests/``、工件在别的任务分支里没合进来、或压根没存在过）

第二问是长期盲区：指针一旦失效，「已验证」就退化成无法复核的口号。

指针解析状态（五类，缺一不可区分）
--------------------------------
``EXACT``       相对路径在当前树命中（含 glob 命中 ≥1 个文件）
``MOVED``       原路径不在，同基名在树内唯一命中（典型：测试迁到 ``backend/tests/``）
``AMBIGUOUS``   同基名多处命中，无法判定是否同一件东西（不计腐烂，计不确定性）
``OFFBRANCH``   当前树没有，但 ``git log --all`` 的提交历史里出现过 → 证据在**别的分支**，
                本树无法复核（合并/清理欠账，不等于臆造）
``LOST``        全历史都没出现过 → 真腐烂或被清理的死指针（README 级缺陷）
``SHORTHAND``   花括号/区间缩写（``a/{x,y}.py``、``fig1..fig3.png``）→ 不是单一可解析指针，
                不计入腐烂分母，单独计数（避免用假阳性充数）

文档判级（优先级自上而下）
--------------------------
``ROTTED``              ≥1 条 LOST 或 ≥1 个 nodeid 名在全树同名候选里都找不到 → 声称不可复核
``OFFBRANCH_ONLY``      无 LOST，但 ≥1 条 OFFBRANCH → 证据不在本树
``UNANCHORED``          无任何数值容差声明，也无任何现存测试指针 → 只有散文
``SELF_CONSISTENT_ONLY``有容差与测试，但正文无任何外部基准关键词（只有自洽/互校/双路线）
``REAL_ANCHORED``       外部基准关键词 + 数值容差 + ≥1 现存测试指针 + 无 LOST/OFFBRANCH

设计纪律
--------
* 只读：只写 ``--out`` 目录；git 只用 ``ls-tree`` / ``log --all --name-only`` 这类只读子命令。
* 零第三方依赖：纯标准库；matplotlib 仅在 ``--figures`` 时按需导入。
* 可判定：词表/正则/判级优先级都是显式常量，测试用内联夹具钉死。
* 确定性：输出按路径排序，JSON 不含墙钟时间（``--stamp`` 才写），可逐位比对。
* 不臆造：文档原文只摘命中片段；等级只是"有利于复核"的标签，最终判级仍归审计单。

用法
----
    python3 tools/verification_evidence_registry.py --out artifacts/verification_evidence
    python3 tools/verification_evidence_registry.py --out /tmp/ev --figures
    python3 tools/verification_evidence_registry.py --check --max-rot 0        # 腐烂门禁
    python3 tools/verification_evidence_registry.py --no-git --out /tmp/ev     # 离线（不做 OFFBRANCH 判定）

产物
----
* ``registry.json``：逐文档记录（等级/指针/容差/词表命中）+ 汇总统计
* ``registry.md``：人读摘要（KPI + 判级分布 + LOST/OFFBRANCH 清单）
* ``--figures``：``fig_grade_distribution.png``、``fig_pointer_status.png``
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# 显式常量（测试钉死）
# ---------------------------------------------------------------------------

#: 被视为"仓内路径"的首段目录白名单（防止把 `J=0.5`、`K_T/K_Q` 之类当路径）
PATH_ROOTS = frozenset(
    """
    tests tools src backend docs scripts artifacts contracts examples runs schemas
    config quality optimizer api frontend frontend-console frontend-factory
    frontend-vue deploy ops eval_set agent_work agent_memory tensor_reports
    """.split()
)

#: 被登记为指针的扩展名
PATH_EXTS = (".py", ".json", ".md", ".png", ".csv", ".yaml", ".yml", ".txt", ".sh", ".ipynb")

#: 反引号内联代码
INLINE_RE = re.compile(r"`([^`\n]{2,160})`")

#: 数值容差声明
TOL_RE = re.compile(
    r"(?:误差|偏差|相对误差|最大偏差|relative\s+error|error|deviation|diff)"
    r"[^0-9\n]{0,18}?"
    r"([0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)"
    r"\s*(%|‰)?",
    re.IGNORECASE,
)

#: 外部基准关键词（"这条证据对的是别人"）
REF_KEYWORDS = (
    "解析", "解析解", "闭式", "analytic", "exact", "literature", "文献",
    "benchmark", "基准", "参考值", "reference", "试验", "实验", "experiment",
    "ITTC", "Wigley", "Holtrop", "Michel", "Lewis", "Oosterveld", "SOLAS",
    "IMO", "MEPC", "DNV", "ABS", "ISO", "ground truth", "真值",
)

#: 自洽类关键词（"自己对自己"，不足以构成外部复核）
SELF_KEYWORDS = ("自洽", "互校", "双路线", "交叉校验", "内部一致", "self-consistent", "cross-check")

#: 判定词（出现才认为这份文档在做 PASS/FAIL 裁决）
VERDICT_KEYWORDS = ("PASS", "FAIL", "通过", "不通过", "达标", "未达标", "判决", "裁决", "判据")

#: 证据文档判定：命中任一即进入证据语料（否则是普通叙述文档，不计入分母）
EVIDENCE_HINT_RE = re.compile(
    r"(\bPASS\b|\bFAIL\b|误差|偏差|容差|tolerance|判据|基准|verification|验证|复核|审计|audit)",
    re.IGNORECASE,
)

GLOB_CHARS = ("*", "?", "[")
SHORTHAND_CHARS = ("{", "}", "..")

#: 自引用排除：本工具生成的审计页/工件由 registry 反推，纳入语料会构成循环（数字自己改自己）
EXCLUDE_DEFAULT = ("docs/audits/verification_evidence_pointer_rot_m2055.md",)

GRADE_ROTTED = "ROTTED"
GRADE_OFFBRANCH = "OFFBRANCH_ONLY"
GRADE_UNANCHORED = "UNANCHORED"
GRADE_SELF_ONLY = "SELF_CONSISTENT_ONLY"
GRADE_REAL = "REAL_ANCHORED"
GRADE_ORDER = [GRADE_REAL, GRADE_SELF_ONLY, GRADE_UNANCHORED, GRADE_OFFBRANCH, GRADE_ROTTED]

STATUS_OK = ("EXACT", "MOVED")

#: 上下文词：命中说明该行/标题在讲「提案/计划/待建」，指针不是已交付证据 → PROPOSED（不计腐烂）
PROPOSED_KEYWORDS = ("提案", "计划", "待建", "拟", "将新增", "新增", "TODO", "planned",
                     "proposed", "roadmap", "提案池", "design draft")

#: 合法 nodeid 名（标识符 + 允许尾随通配）；带空格/加号/多指针散文 → NOT_A_NODEID
NODEID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\*)?$")


# ---------------------------------------------------------------------------
# 仓库文件索引与指针解析
# ---------------------------------------------------------------------------
def build_index(root: str, skip_dirs=(".git", "node_modules", "__pycache__")) -> dict:
    """建基名 → 相对路径列表 的索引（用于 MOVED 解析）。"""
    idx: dict[str, list[str]] = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            idx.setdefault(fn, []).append(rel)
    for k in idx:
        idx[k].sort()
    return idx


def load_git_history(root: str, timeout: int = 600) -> set | None:
    """``git log --all --name-only`` 全历史路径集合；非 git 仓/失败返回 None。

    只读子命令，用于把"当前树没有"细分出 ``OFFBRANCH``（历史里有）与 ``LOST``（从没有过）。
    """
    try:
        out = subprocess.run(
            ["git", "log", "--all", "--name-only", "--pretty=format:", "--no-renames"],
            cwd=root, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    paths = {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
    return paths or None


def classify_path_token(tok: str) -> str | None:
    """反引号 token → 指针原文；不是仓内路径则返回 None。"""
    tok = tok.strip().strip("()[]{}<>,;:'\"")
    if "::" in tok:
        tok = tok.split("::", 1)[0]
    while tok.startswith("./"):
        tok = tok[2:]
    if "/" not in tok or " " in tok:
        return None
    if not tok.endswith(PATH_EXTS):
        return None
    head = tok.split("/", 1)[0]
    if head not in PATH_ROOTS:
        return None
    return tok


def is_glob(rel: str) -> bool:
    return any(c in rel for c in GLOB_CHARS)


def is_shorthand(rel: str) -> bool:
    return any(c in rel for c in SHORTHAND_CHARS)


def resolve_pointer(root: str, rel: str, index: dict, history: set | None = None) -> tuple[str, str | None]:
    """返回 (status, resolved_rel_path|None)。status ∈ EXACT/MOVED/AMBIGUOUS/OFFBRANCH/LOST。"""
    if is_shorthand(rel):
        return "SHORTHAND", None
    if is_glob(rel):
        hits = sorted(
            os.path.relpath(p, root).replace(os.sep, "/")
            for p in glob.glob(os.path.join(root, rel), recursive=True)
            if os.path.isfile(p)
        )
        return ("EXACT", hits[0]) if hits else ("LOST", None)
    if os.path.exists(os.path.join(root, rel)):
        return "EXACT", rel
    base = os.path.basename(rel)
    hits = index.get(base, [])
    if len(hits) == 1:
        return "MOVED", hits[0]
    if len(hits) > 1:
        return "AMBIGUOUS", None
    if history and rel in history:
        return "OFFBRANCH", None
    return "LOST", None


def candidate_paths(root: str, rel: str, index: dict, resolved: str | None) -> list[str]:
    """指针可能对应的全部树内路径（自身 + 所有同名拷贝），用于 nodeid 二次定位。"""
    cands = []
    if resolved:
        cands.append(resolved)
    if os.path.exists(os.path.join(root, rel)) and rel not in cands:
        cands.append(rel)
    for h in index.get(os.path.basename(rel), []):
        if h not in cands:
            cands.append(h)
    return cands


def check_nodeid(root: str, resolved: str | None, name: str) -> str:
    """校验 ``file.py::Name`` 里的 Name 在同文件里以 def/class 存在。"""
    if not resolved:
        return "UNRESOLVED"
    try:
        src = open(os.path.join(root, resolved), encoding="utf-8", errors="replace").read()
    except OSError:
        return "UNRESOLVED"
    if name.endswith("*"):
        # 通配 nodeid（`test_missing_*`）：命中前缀族即算存在
        prefix = name.rstrip("*")
        for pat in (r"^\s*def\s+%s", r"^\s*async\s+def\s+%s", r"^\s*class\s+%s"):
            if re.search(pat % re.escape(prefix), src, re.MULTILINE):
                return "OK"
        return "MISSING"
    for pat in (r"^\s*def\s+%s\b", r"^\s*async\s+def\s+%s\b", r"^\s*class\s+%s\b",
                r"^\s*%s\s*(?::[^=\n]*)?="):
        if re.search(pat % re.escape(name), src, re.MULTILINE):
            return "OK"
    return "MISSING"


# ---------------------------------------------------------------------------
# 单文档抽取与判级
# ---------------------------------------------------------------------------
def parse_tolerance(groups: tuple[str, str | None]) -> float:
    """把 (数字, 单位) 归一成百分数。"""
    num = float(groups[0])
    unit = groups[1]
    if unit == "‰":
        return num / 10.0
    if unit == "%":
        return num
    return num  # 无单位按百分数口径沿用


def _is_proposed_context(line: str, heading: str) -> bool:
    ctx = (line + " " + heading).lower()
    return any(k.lower() in ctx for k in PROPOSED_KEYWORDS)


def extract_pointers(text: str) -> list[dict]:
    """抽所有指针（去重、保序），并带上上下文判定（提案/计划里的路径不算已交付证据）。"""
    seen, out = set(), []
    heading = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
        for raw in INLINE_RE.findall(line):
            name = None
            tok = raw.strip()
            if "::" in tok:
                tok, name = tok.split("::", 1)
                name = name.split("::")[-1].strip()
                name = name.split("[")[0].split("(")[0].split(".")[0].strip()
            rel = classify_path_token(tok)
            if rel is None:
                continue
            key = (rel, name or "")
            if key in seen:
                continue
            seen.add(key)
            out.append({"raw": raw.strip(), "path": rel, "node": name or None,
                        "proposed_context": _is_proposed_context(line, heading)})
    return out


def analyze_doc(root: str, rel_doc: str, index: dict, history: set | None = None) -> dict:
    text = open(os.path.join(root, rel_doc), encoding="utf-8", errors="replace").read()

    tols = []
    for m in TOL_RE.finditer(text):
        val = parse_tolerance((m.group(1), m.group(2)))
        if 0 <= val < 1e6:
            tols.append(val)

    low = text.lower()
    ref_hits = [k for k in REF_KEYWORDS if k.lower() in low]
    self_hits = [k for k in SELF_KEYWORDS if k.lower() in low]
    verdict_hits = [k for k in VERDICT_KEYWORDS if k.lower() in low]

    ptrs = []
    for p in extract_pointers(text):
        if p["proposed_context"]:
            # 提案/计划里的路径：不是已交付证据，既不计腐烂也不计证据
            ptrs.append({"raw": p["raw"], "path": p["path"], "node": p["node"],
                         "status": "PROPOSED", "resolved": None, "node_status": None})
            continue
        status, resolved = resolve_pointer(root, p["path"], index, history)
        node_status = None
        if p["node"] and not NODEID_NAME_RE.match(p["node"]):
            node_status = "NOT_A_NODEID"          # 散文里拼进来的多指针/通配，不当缺陷
        elif p["node"]:
            node_status = check_nodeid(root, resolved, p["node"])
            if node_status != "OK":
                # 二次定位：同名测试多拷贝 / 已迁移 / 证据在别的分支时，按候选路径逐个找名字
                for alt in candidate_paths(root, p["path"], index, resolved):
                    if alt == resolved:
                        continue
                    if check_nodeid(root, alt, p["node"]) == "OK":
                        node_status = "OK_ELSEWHERE"
                        resolved = alt
                        if status == "EXACT":
                            status = "MOVED"
                        break
        ptrs.append({
            "raw": p["raw"], "path": p["path"], "node": p["node"],
            "status": status, "resolved": resolved, "node_status": node_status,
        })

    counted = [p for p in ptrs if p["status"] not in ("SHORTHAND", "PROPOSED")]
    lost = [p for p in counted if p["status"] == "LOST"]
    offbranch = [p for p in counted if p["status"] == "OFFBRANCH"]
    node_missing = [p for p in ptrs if p["node_status"] == "MISSING"]
    existing_tests = sorted({
        p["resolved"] or p["path"] for p in counted
        if p["status"] in STATUS_OK and (
            p["path"].startswith("tests/") or "/tests/" in "/" + p["path"]
        )
    })

    if lost or node_missing:
        grade = GRADE_ROTTED
    elif offbranch:
        grade = GRADE_OFFBRANCH
    elif not tols and not existing_tests:
        grade = GRADE_UNANCHORED
    elif not ref_hits or not existing_tests:
        grade = GRADE_UNANCHORED if not existing_tests else GRADE_SELF_ONLY
    elif not ref_hits:
        grade = GRADE_SELF_ONLY
    else:
        grade = GRADE_REAL

    return {
        "doc": rel_doc,
        "grade": grade,
        "tol_claims": len(tols),
        "tol_min_pct": min(tols) if tols else None,
        "ref_keywords": ref_hits[:8],
        "self_keywords": self_hits[:8],
        "verdict_keywords": verdict_hits[:6],
        "pointers": ptrs,
        "n_pointers": len(counted),
        "n_shorthand": sum(1 for p in ptrs if p["status"] == "SHORTHAND"),
        "n_proposed": sum(1 for p in ptrs if p["status"] == "PROPOSED"),
        "n_lost": len(lost),
        "n_offbranch": len(offbranch),
        "n_moved": sum(1 for p in counted if p["status"] == "MOVED"),
        "n_ambiguous": sum(1 for p in counted if p["status"] == "AMBIGUOUS"),
        "n_node_missing": len(node_missing),
        "existing_test_pointers": existing_tests,
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def build_registry(root: str, corpus_glob: str, history: set | None = None,
                   exclude: tuple[str, ...] = EXCLUDE_DEFAULT) -> dict:
    index = build_index(root)
    docs = sorted(
        os.path.relpath(p, root).replace(os.sep, "/")
        for p in glob.glob(os.path.join(root, corpus_glob), recursive=True)
        if os.path.isfile(p)
    )
    docs = [d for d in docs if d not in set(exclude)]
    records, skipped = [], 0
    for rel in docs:
        text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        if not EVIDENCE_HINT_RE.search(text):
            skipped += 1
            continue
        records.append(analyze_doc(root, rel, index, history))

    grade_counts = {g: 0 for g in GRADE_ORDER}
    for r in records:
        grade_counts[r["grade"]] += 1

    def rows(status):
        return [{"doc": r["doc"], "raw": p["raw"], "path": p["path"], "node": p["node"]}
                for r in records for p in r["pointers"] if p["status"] == status]

    n_ptr = sum(r["n_pointers"] for r in records)
    lost, offbranch = rows("LOST"), rows("OFFBRANCH")
    moved = [{"doc": r["doc"], "path": p["path"], "resolved": p["resolved"]}
             for r in records for p in r["pointers"] if p["status"] == "MOVED"]
    node_missing = [{"doc": r["doc"], "raw": p["raw"], "resolved": p["resolved"]}
                    for r in records for p in r["pointers"] if p["node_status"] == "MISSING"]

    evidence_docs = len(records)
    numerator = grade_counts[GRADE_REAL]
    return {
        "schema_version": "1.0",
        "tool": "tools/verification_evidence_registry.py",
        "corpus": {
            "glob": corpus_glob, "md_scanned": len(docs), "non_evidence_skipped": skipped,
            "evidence_docs": evidence_docs, "git_history_used": history is not None,
            "excluded_self_reference": list(exclude),
        },
        "grade_counts": grade_counts,
        "kpi": {
            "name": "已合并功能复核通过率（机器口径：本树可复核）",
            "numerator_real_anchored": numerator,
            "denominator_evidence_docs": evidence_docs,
            "rate": round(numerator / evidence_docs, 4) if evidence_docs else None,
            "note": "分子=REAL_ANCHORED（外部基准+容差+现存测试指针+无 LOST/OFFBRANCH）；"
                    "分母=含验证/判据线索的证据文档（叙事文档不算）",
        },
        "pointers": {
            "total": n_ptr,
            "lost": len(lost),
            "offbranch": len(offbranch),
            "moved": len(moved),
            "ambiguous": sum(r["n_ambiguous"] for r in records),
            "node_missing": len(node_missing),
            "shorthand": sum(r["n_shorthand"] for r in records),
            "proposed": sum(r["n_proposed"] for r in records),
            "rot_rate_over_docs": round(grade_counts[GRADE_ROTTED] / evidence_docs, 4) if evidence_docs else None,
            "rot_rate_over_pointers": round(len(lost) / n_ptr, 4) if n_ptr else None,
        },
        "rot": {"lost": lost, "offbranch": offbranch, "moved": moved, "node_missing": node_missing},
        "records": records,
    }


def render_md(reg: dict) -> str:
    g, p, k = reg["grade_counts"], reg["pointers"], reg["kpi"]
    lines = [
        "# 验证证据登记册（指针腐烂审计）",
        "",
        f"语料：`{reg['corpus']['glob']}` · md 扫描 {reg['corpus']['md_scanned']} 篇 · "
        f"证据文档 {reg['corpus']['evidence_docs']} 篇（叙事文档跳过 {reg['corpus']['non_evidence_skipped']}）· "
        f"git 全历史判定 {'开' if reg['corpus']['git_history_used'] else '关'}",
        "",
        "## KPI（机器口径）",
        "",
        f"- 已合并功能复核通过率 = {k['numerator_real_anchored']}/{k['denominator_evidence_docs']}"
        f" = **{k['rate']}**",
        "",
        "| 等级 | 文档数 | 含义 |",
        "|---|---:|---|",
        f"| REAL_ANCHORED | {g[GRADE_REAL]} | 外部基准 + 容差 + 现存测试指针 |",
        f"| SELF_CONSISTENT_ONLY | {g[GRADE_SELF_ONLY]} | 只有自洽/互校，无外部基准词 |",
        f"| UNANCHORED | {g[GRADE_UNANCHORED]} | 无数值容差也无现存测试指针 |",
        f"| OFFBRANCH_ONLY | {g[GRADE_OFFBRANCH]} | 指针只在别的分支历史里，本树不可复核 |",
        f"| ROTTED | {g[GRADE_ROTTED]} | 指针全历史不存在 / nodeid 名缺失 |",
        "",
        "## 指针健康",
        "",
        f"- 指针总数 {p['total']}（另有花括号/区间缩写 {p['shorthand']} 条、提案上下文 {p['proposed']} 条不计）",
        f"- LOST {p['lost']} · OFFBRANCH {p['offbranch']} · MOVED {p['moved']} · "
        f"AMBIGUOUS {p['ambiguous']} · nodeid 名缺失 {p['node_missing']}",
        f"- 腐烂率：文档口径 {p['rot_rate_over_docs']} · 指针口径 {p['rot_rate_over_pointers']}",
        "",
    ]
    if reg["rot"]["lost"]:
        lines += ["## LOST 指针（全历史不存在：证据不可复核）", "", "| 文档 | 指针 |", "|---|---|"]
        for it in reg["rot"]["lost"]:
            lines.append(f"| `{it['doc']}` | `{it['raw']}` |")
        lines.append("")
    if reg["rot"]["offbranch"]:
        lines += ["## OFFBRANCH 指针（历史里有、本树没有：合并/清理欠账）", "",
                  "| 文档 | 指针 |", "|---|---|"]
        for it in reg["rot"]["offbranch"][:60]:
            lines.append(f"| `{it['doc']}` | `{it['raw']}` |")
        if len(reg["rot"]["offbranch"]) > 60:
            lines.append(f"| … | 其余 {len(reg['rot']['offbranch']) - 60} 条见 registry.json |")
        lines.append("")
    if reg["rot"]["node_missing"]:
        lines += ["## nodeid 名缺失（文件在、函数/类不在）", "", "| 文档 | 指针 | 落到 |", "|---|---|---|"]
        for it in reg["rot"]["node_missing"]:
            lines.append(f"| `{it['doc']}` | `{it['raw']}` | `{it['resolved']}` |")
        lines.append("")
    lines += ["## 逐文档等级（按等级优先级排序，前 40 条）", "",
              "| 文档 | 等级 | 指针 | 最小容差% |", "|---|---|---:|---:|"]
    order = {g_: i for i, g_ in enumerate(GRADE_ORDER)}
    for r in sorted(reg["records"], key=lambda r: (order[r["grade"]], r["doc"]))[:40]:
        lines.append(f"| `{r['doc']}` | {r['grade']} | {r['n_pointers']} | {r['tol_min_pct']} |")
    lines.append("")
    return "\n".join(lines)


def make_figures(reg: dict, outdir: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths = []
    g = reg["grade_counts"]
    labels = GRADE_ORDER
    vals = [g[k] for k in labels]

    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    bars = ax.bar(labels, vals, color=["#2a7f62", "#3b6ea5", "#c8922a", "#7d5ba6", "#b03a2e"])
    ax.set_ylabel("documents")
    ax.set_title("Verification evidence grading (%d evidence docs)" % reg["corpus"]["evidence_docs"])
    for b, v in zip(bars, vals):
        ax.annotate(str(v), (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=9)
    ax.tick_params(axis="x", rotation=12)
    fig.tight_layout()
    f1 = os.path.join(outdir, "fig_grade_distribution.png")
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    paths.append(f1)

    p = reg["pointers"]
    names = ["EXACT", "MOVED", "AMBIGUOUS", "OFFBRANCH", "LOST"]
    counts = [p["total"] - p["moved"] - p["ambiguous"] - p["offbranch"] - p["lost"],
              p["moved"], p["ambiguous"], p["offbranch"], p["lost"]]
    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    bars = ax.bar(names, counts, color=["#2a7f62", "#3b6ea5", "#c8922a", "#7d5ba6", "#b03a2e"])
    ax.set_ylabel("pointers")
    ax.set_title("Evidence pointer resolution (%d pointers)" % p["total"])
    for b, v in zip(bars, counts):
        ax.annotate(str(v), (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    f2 = os.path.join(outdir, "fig_pointer_status.png")
    fig.savefig(f2, dpi=140)
    plt.close(fig)
    paths.append(f2)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="验证证据登记册 + 指针腐烂门禁")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--corpus", default="docs/**/*.md", help="语料 glob（相对 --root）")
    ap.add_argument("--out", default="artifacts/verification_evidence", help="产物目录")
    ap.add_argument("--figures", action="store_true", help="出 png 图")
    ap.add_argument("--no-git", action="store_true", help="不做 git 历史判定（OFFBRANCH 与 LOST 合并计入 LOST）")
    ap.add_argument("--check", action="store_true", help="门禁模式：腐烂超标以非零退出码表达")
    ap.add_argument("--max-rot", type=int, default=0, help="允许的 ROTTED 文档数（--check 用）")
    ap.add_argument("--exclude", action="append", default=None,
                    help="语料排除（可重复；默认排除本工具生成的审计页以免自引用）")
    ap.add_argument("--stamp", default=None, help="可选时间戳字符串，写入 JSON（默认不写，保确定性）")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    history = None if args.no_git else load_git_history(root)
    exclude = tuple(args.exclude) if args.exclude else EXCLUDE_DEFAULT
    reg = build_registry(root, args.corpus, history, exclude)
    if args.stamp:
        reg["stamp"] = args.stamp

    outdir = os.path.join(root, args.out)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "registry.json"), "w", encoding="utf-8") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=1, sort_keys=True)
    with open(os.path.join(outdir, "registry.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(reg))
    if args.figures:
        for f in make_figures(reg, outdir):
            print("figure:", os.path.relpath(f, root))

    g, p = reg["grade_counts"], reg["pointers"]
    print(
        "evidence_docs=%d REAL=%d SELF_ONLY=%d UNANCHORED=%d OFFBRANCH=%d ROTTED=%d | "
        "pointers=%d LOST=%d OFFBRANCH=%d MOVED=%d node_missing=%d shorthand=%d proposed=%d"
        % (reg["corpus"]["evidence_docs"], g[GRADE_REAL], g[GRADE_SELF_ONLY], g[GRADE_UNANCHORED],
           g[GRADE_OFFBRANCH], g[GRADE_ROTTED], p["total"], p["lost"], p["offbranch"],
           p["moved"], p["node_missing"], p["shorthand"], p["proposed"])
    )

    if args.check and g[GRADE_ROTTED] > args.max_rot:
        print("FAIL: ROTTED documents %d > allowed %d" % (g[GRADE_ROTTED], args.max_rot), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())