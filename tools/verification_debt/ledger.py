#!/usr/bin/env python3
"""验证欠账台账（verification-debt ledger）——把「已清偿」从散文变成机器裁判。

背景
----
`docs/verification_debt/` 下每个目录是一笔**验证欠账**（某能力曾判 MOCK/WITHHELD，
后来声称已清偿）。清偿结论目前**只写在散文里**：报告正文说自己 PASS、引用了某个测试
文件、某个 artifact 路径。散文无法被裁判，于是出现三类不设防的腐化：

1. **悬空引用（dangling ref）**：报告引用的 `tests/...py` / `artifacts/...json`
   在 HEAD 里已不存在（改名/未提交/写了没跑）——证据链断了但报告仍写「已清偿」。
2. **超红线假阳（overclaim）**：artifact 里自己的数字误差 >15%（平台精度红线：
   >15% 不可接受），而报告仍写「PASS/已修复」。
3. **无证据清偿（no evidence）**：既无脚本也无 artifact，只有一段结论文字。

本工具做三件只读的事，全部判据可机器裁：

* **S1 证据齐备性**：逐笔采集报告(md)/脚本(py)/数据(artifact json,png)，并解析文本中
  的仓内路径引用，逐个 `os.path.exists` 判定悬空。
* **S2 误差统一裁判**：递归扫 artifact JSON，抽出所有「误差型字段」（rel_err/err_pct/…），
  按平台统一口径重判：|e|≤3% PASS_STRONG，≤15% PASS_WEAK，>15% **RED 不可接受**；
  并检查同一条记录里自报的 `pass/verdict/status` 是否与误差矛盾（自称 pass 但 >15%）。
* **S3 台账定级**：每笔欠账给 5 档之一
  `BACKED / UNBACKED / OVERCLAIM / NO_EVIDENCE / OPEN`（见 RANKS）。

用法
----
    python3 -m tools.verification_debt.ledger            # 只读扫描，出 json/md/png
    python3 -m tools.verification_debt.ledger --strict    # 有 UNBACKED/OVERCLAIM/NO_EVIDENCE 则 exit 1
    python3 -m tools.verification_debt.ledger --run-tests # 额外实跑被引用的测试文件（重，慎用）
    python3 -m tools.verification_debt.ledger --root /tmp/fake_repo   # 干跑/自测用

判据全部来自本文件常量，无网络、无随机、无时间依赖（`--date` 仅供署名）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LEDGER_SCHEMA = "tp.verification_debt.ledger/1"

# ---------------------------------------------------------------- 判据常量
STRONG_PCT = 3.0     # 平台目标精度
RED_PCT = 15.0       # 平台红线：>15% 不可接受
# 误差型字段名：**只收相对量/百分量**。绝对量（abs_err、*_m、*_kn）单位不同，
# 混入百分数口径会产生假阳（v1 曾把 `KN_identity_abs_err` 读成 1e32%），一律不收。
ERROR_KEY_TOKENS = (
    "rel_err", "relerr", "rel_error", "relative_error",
    "err_pct", "error_pct", "pct_err", "err_percent", "error_percent",
    "dev_pct", "delta_pct", "pct_dev", "pct_diff", "diff_pct", "self_check_pct",
)
# 绝对量/绝对单位：命中即排除
ABS_KEY_TOKENS = ("abs", "_m", "_kn", "_n", "_kg", "_deg", "metre", "meter")
# 这些名字是「容差/阈值」而不是「实测误差」，不参与裁判
THRESHOLD_KEY_TOKENS = ("threshold", "tol", "band", "limit", "bound", "gate_frac", "scale")
# 自报结论字段：**不含 `status`**（HTTP 200 不是 PASS，v1 曾据此造出 7 条假矛盾）
CLAIM_KEY_TOKENS = ("pass", "passed", "verdict", "ok", "closed", "result", "green")

CLOSURE_PATTERNS = (
    r"已清偿", r"清偿完毕", r"欠账清偿", r"已修复", r"闭环通过",
    r"verdict\s*[:=]\s*pass", r"已合并\s*\+?\s*已验证",
)
OPEN_PATTERNS = (r"未清偿", r"欠账未", r"待清偿", r"WITHHELD", r"NOT_SUPPORTED")
# 「结论行」锚点：只有出现在声明性语句里的 PASS/WITHHELD 才算结论。
# 反例（v1 假阳来源）：treated 判据表里的「三态判据：PASS/FAIL/WITHHELD」只是判据定义，
# 若按全文 grep 会把 8 笔欠账全读成「未清偿」。
DECLARE_ANCHORS = ("结论", "等级", "裁决", "判定", "评级", "状态", "维持", "清偿", "verdict")
# 强声明：状态词必须紧挨锚点（≤24 字），避免把判据定义/讨论读成结论
STRONG_OPEN_RE = r"(结论|等级|裁决|判定|评级|维持|状态|verdict)[^\n]{0,24}?(WITHHELD|NOT_SUPPORTED|未清偿|待清偿)"
STRONG_CLOSE_RE = r"(结论|等级|裁决|判定|评级|清偿|verdict)[^\n]{0,24}?(已清偿|已修复|通过|PASS)"

RANKS = ("BACKED", "OPEN", "OPEN_WITH_RED", "NO_EVIDENCE", "UNBACKED", "OVERCLAIM")
# 严判顺序：一笔欠账同时命中多档时取最严厉的一档
SEVERITY = {"BACKED": 0, "OPEN": 1, "OPEN_WITH_RED": 2, "NO_EVIDENCE": 3, "UNBACKED": 4, "OVERCLAIM": 5}

PATH_REF_RE = re.compile(
    r"((?:tests|tools|src|artifacts|docs|examples|scripts)/[A-Za-z0-9_./+-]+?\.(?:py|json|png|md|yaml|yml|csv))"
)
TEXT_SUFFIXES = (".md", ".py", ".txt")
ARTIFACT_SUFFIXES = (".json", ".png", ".csv")


# ---------------------------------------------------------------- 采集
def _read_text(path: str, limit: int = 400_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _walk_files(root: str) -> List[str]:
    out: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            out.append(os.path.join(dirpath, name))
    return sorted(out)


def _rel(repo_root: str, path: str) -> str:
    return os.path.relpath(path, repo_root).replace(os.sep, "/")


def _ref_resolves(repo_root: str, item_dir: str, ref: str) -> bool:
    """路径引用是否落地。两种通行写法都认：仓根相对、欠账目录相对。"""
    cands = [os.path.join(repo_root, ref), os.path.join(item_dir, ref), ref]
    return any(os.path.exists(c) for c in cands)


def collect_item(repo_root: str, item_dir: str) -> Dict[str, Any]:
    """采集一笔欠账的全部证据（纯只读）。"""
    name = os.path.basename(item_dir.rstrip("/"))
    files = _walk_files(item_dir)
    docs, scripts, artifacts = [], [], []
    for f in files:
        rel = _rel(repo_root, f)
        if f.endswith(".md"):
            docs.append(rel)
        elif f.endswith(".py"):
            scripts.append(rel)
        elif f.endswith(ARTIFACT_SUFFIXES):
            artifacts.append(rel)

    # 文本里引用的仓内路径（含跨目录的测试/工具/产物）
    text = "\n".join(_read_text(os.path.join(repo_root, d)) for d in docs + scripts)
    refs: List[str] = []
    for m in PATH_REF_RE.finditer(text):
        ref = m.group(1).rstrip(".,)")
        if ref not in refs:
            refs.append(ref)
    dangling = [r for r in refs if not _ref_resolves(repo_root, item_dir, r)]

    claim_hits, open_hits, claim_lines, open_lines = [], [], [], []
    for doc in docs:
        for line in _read_text(os.path.join(repo_root, doc)).splitlines():
            low = line.strip()
            if not low:
                continue
            is_heading = low.startswith("#")
            # 判据/指纹定义行不是结论（v1 假阳来源：「三态判据：PASS/FAIL/WITHHELD」）
            definition = any(t in low for t in ("三态", "判据定义", "→ MOCK", "=> MOCK", "PASS/FAIL"))
            for pat in CLOSURE_PATTERNS:
                hit = re.search(pat, low, re.I) and (is_heading or re.search(STRONG_CLOSE_RE, low))
                if hit and not re.search(r"未清偿|WITHHELD|NOT_SUPPORTED", low):
                    if pat not in claim_hits:
                        claim_hits.append(pat)
                    claim_lines.append({"doc": doc, "pattern": pat, "line": low[:180]})
            for pat in OPEN_PATTERNS:
                strong = re.search(STRONG_OPEN_RE, low, re.I)
                if strong and not is_heading:
                    if pat not in open_hits:
                        open_hits.append(pat)
                    open_lines.append({"doc": doc, "pattern": pat, "line": low[:180]})

    return {
        "name": name,
        "dir": _rel(repo_root, item_dir),
        "docs": docs,
        "scripts": scripts,
        "artifacts": artifacts,
        "refs": refs,
        "dangling_refs": dangling,
        "closure_claims": claim_hits,
        "open_claims": open_hits,
        "closure_evidence": claim_lines,
        "open_evidence": open_lines,
        "test_refs": [r for r in refs if r.startswith("tests/") and r.endswith(".py")],
    }


# ---------------------------------------------------------------- 误差抽取 + 裁判
def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _to_percent(key: str, value: float) -> Tuple[float, str]:
    """把误差字段归一到百分数，并回传单位口径（`unit` 字段进台账，便于复核）。

    口径（**靠字段名，不靠数值量级**；v1 用 |v|<=1 猜单位，把 `GM_rel_err=1.846`
    这种「184.6%」误读成 1.85%，正好把最严重的欠账读成最轻的——这条教训进测试）：
      * 键名含 `pct` / `percent` → 已是百分数；
      * 其余（`rel_err` / `*_err` / `dev` / `delta`）→ 无量纲分数，×100。
    字段名即契约：命名与单位不符属于产出方的缺陷，台账会把它暴露成 >100% 的红条。
    """
    lk = key.lower()
    if "pct" in lk or "percent" in lk:
        return float(value), "percent"
    return float(value) * 100.0, "fraction"


def _self_verdict(node: Dict[str, Any]) -> Optional[str]:
    """读出记录自报的结论；HTTP 状态码 / 数值计时不算结论。"""
    for k, v in node.items():
        if k.lower() not in CLAIM_KEY_TOKENS:
            continue
        if isinstance(v, bool):
            return "pass" if v else "fail"
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float)) and v in (0, 1):
            return "pass" if v == 1 else "fail"
    return None


def _claimed_pass(verdict: Optional[str]) -> Optional[bool]:
    if verdict is None:
        return None
    v = verdict.strip().lower()
    if v in ("true", "pass", "passed", "ok", "1", "closed", "green"):
        return True
    if v in ("false", "fail", "failed", "0", "open", "red"):
        return False
    return None


def extract_checks(payload: Any, artifact: str, path: str = "$") -> List[Dict[str, Any]]:
    """递归抽取 artifact 里的误差型记录。"""
    out: List[Dict[str, Any]] = []

    def walk(node: Any, p: str) -> None:
        if isinstance(node, dict):
            errs: Dict[str, float] = {}
            units: Dict[str, str] = {}
            for k, v in node.items():
                lk = k.lower()
                if not _is_number(v):
                    continue
                if any(t in lk for t in THRESHOLD_KEY_TOKENS):
                    continue
                if any(t in lk for t in ABS_KEY_TOKENS):
                    continue
                if any(t in lk for t in ERROR_KEY_TOKENS):
                    pct, unit = _to_percent(k, float(v))
                    errs[k] = pct
                    units[k] = unit
            if errs:
                worst_key = max(errs, key=lambda k: abs(errs[k]))
                pct = errs[worst_key]
                verdict = _self_verdict(node)
                claimed = _claimed_pass(verdict)
                red = abs(pct) > RED_PCT
                out.append({
                    "artifact": artifact,
                    "path": p,
                    "metric": worst_key,
                    "unit": units[worst_key],
                    "err_pct": round(pct, 6),
                    "all_errs_pct": {k: round(x, 6) for k, x in errs.items()},
                    "band": "PASS_STRONG" if abs(pct) <= STRONG_PCT else ("PASS_WEAK" if not red else "RED"),
                    "self_verdict": verdict,
                    "contradiction": bool(claimed is True and red),
                })
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, f"{p}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    walk(v, f"{p}[{i}]")

    walk(payload, path)
    return out


def collect_checks(repo_root: str, artifacts: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    checks: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    for rel in artifacts:
        if not rel.endswith(".json"):
            continue
        full = os.path.join(repo_root, rel)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            unreadable.append(rel)
            continue
        checks.extend(extract_checks(payload, rel))
    return checks, unreadable


# ---------------------------------------------------------------- 定级
def rank_item(item: Dict[str, Any], checks: Sequence[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """给出该笔欠账的等级与理由（理由逐条可读，便于在报告里原文引用）。

    判序 = 先判「正文自己怎么说」，再判「证据链是否断」，再判「数字是否越红线」：
      1. 正文自述未清偿（含 WITHHELD / NOT_SUPPORTED） → OPEN / OPEN_WITH_RED
      2. 引用路径悬空 → UNBACKED（证据链断了，结论无法复现）
      3. 无任何脚本/数据（含可落地的引用） → NO_EVIDENCE
      4. 自报 PASS 但误差 >15% → OVERCLAIM
      5. 其余且声明清偿 → BACKED
    """
    reasons: List[str] = []
    claims_closure = bool(item["closure_claims"]) and not item["open_claims"]
    evidence_refs = [r for r in item["refs"]
                     if r.endswith((".py", ".json", ".png")) and r not in item["dangling_refs"]]
    has_data = bool(item["artifacts"]) or bool(item["scripts"]) or bool(evidence_refs)
    red = [c for c in checks if c["band"] == "RED"]
    contra = [c for c in checks if c["contradiction"]]

    if item["open_claims"]:
        reasons.append("正文自述未清偿/未上线（命中：%s）" % ", ".join(item["open_claims"]))
        if red:
            reasons.append("且仍有 %d 条误差记录 >%.0f%%（最差 %.2f%%）"
                           % (len(red), RED_PCT, max(abs(c["err_pct"]) for c in red)))
            return "OPEN_WITH_RED", reasons
        return "OPEN", reasons
    if item["dangling_refs"]:
        reasons.append("悬空引用 %d 条：%s" % (len(item["dangling_refs"]), ", ".join(item["dangling_refs"][:4])))
        return "UNBACKED", reasons
    if not has_data:
        reasons.append("既无可落地脚本也无 artifact，只有结论文字")
        return "NO_EVIDENCE", reasons
    if contra:
        reasons.append("自报 PASS 但误差超红线 %d 条（例：%s=%.2f%%）"
                       % (len(contra), contra[0]["metric"], contra[0]["err_pct"]))
        return "OVERCLAIM", reasons
    if not claims_closure:
        reasons.append("证据齐备但正文未声明清偿（过程记录/审计报告）")
        if red:
            reasons.append("仍有 %d 条误差记录 >%.0f%%（最差 %.2f%%）"
                           % (len(red), RED_PCT, max(abs(c["err_pct"]) for c in red)))
            return "OPEN_WITH_RED", reasons
        return "OPEN", reasons
    if red:
        reasons.append("声明清偿但有 %d 条误差记录 >%.0f%%（最差 %.2f%%），未在正文标注为已知偏差"
                       % (len(red), RED_PCT, max(abs(c["err_pct"]) for c in red)))
        return "OVERCLAIM", reasons
    reasons.append("证据齐备、无悬空引用、%d 条误差记录全部 ≤%.0f%%" % (len(checks), RED_PCT))
    return "BACKED", reasons


def _run_tests(repo_root: str, test_refs: Sequence[str], timeout: int = 300) -> Dict[str, Any]:
    """实跑被引用的测试文件（可选；重活，默认关闭）。"""
    existing = [t for t in test_refs if os.path.exists(os.path.join(repo_root, t))]
    if not existing:
        return {"ran": [], "note": "无可实跑的测试引用"}
    url = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *existing]
    try:
        proc = subprocess.run(url, cwd=repo_root, capture_output=True, text=True, timeout=timeout)
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        return {"ran": existing, "returncode": proc.returncode, "tail": tail}
    except subprocess.TimeoutExpired:
        return {"ran": existing, "returncode": None, "tail": [f"timeout>{timeout}s"]}


# ---------------------------------------------------------------- 台账
def build_ledger(repo_root: str = ".", run_tests: bool = False) -> Dict[str, Any]:
    debt_root = os.path.join(repo_root, "docs", "verification_debt")
    if not os.path.isdir(debt_root):
        raise FileNotFoundError(f"找不到验证欠账目录：{debt_root}")
    items = []
    for name in sorted(os.listdir(debt_root)):
        d = os.path.join(debt_root, name)
        if not os.path.isdir(d):
            continue
        item = collect_item(repo_root, d)
        checks, unreadable = collect_checks(repo_root, item["artifacts"])
        rank, reasons = rank_item(item, checks)
        item.update({
            "rank": rank,
            "reasons": reasons,
            "checks": checks,
            "unreadable_artifacts": unreadable,
            "n_checks": len(checks),
            "n_strong": sum(1 for c in checks if c["band"] == "PASS_STRONG"),
            "n_weak": sum(1 for c in checks if c["band"] == "PASS_WEAK"),
            "n_red": sum(1 for c in checks if c["band"] == "RED"),
            "n_contradiction": sum(1 for c in checks if c["contradiction"]),
            "worst_err_pct": round(max((abs(c["err_pct"]) for c in checks), default=0.0), 6),
        })
        if run_tests:
            item["test_run"] = _run_tests(repo_root, item["test_refs"])
        items.append(item)

    summary = {
        "n_items": len(items),
        "per_rank": {r: sum(1 for i in items if i["rank"] == r) for r in RANKS},
        "n_checks": sum(i["n_checks"] for i in items),
        "n_red": sum(i["n_red"] for i in items),
        "n_dangling_refs": sum(len(i["dangling_refs"]) for i in items),
        "worst_err_pct": round(max((i["worst_err_pct"] for i in items), default=0.0), 6),
    }
    return {"schema": LEDGER_SCHEMA, "repo_root": os.path.abspath(repo_root),
            "thresholds": {"strong_pct": STRONG_PCT, "red_pct": RED_PCT},
            "summary": summary, "items": items}


# ---------------------------------------------------------------- 渲染
def render_markdown(ledger: Dict[str, Any]) -> str:
    s = ledger["summary"]
    L: List[str] = []
    L.append("# 验证欠账台账（机器裁判版）\n")
    L.append("> 由 `python3 -m tools.verification_debt.ledger` 生成，数字全部程序化注入，勿手改。\n")
    L.append("## 总览\n")
    L.append("| 欠账数 | 误差记录 | 超红线(>%g%%) | 悬空引用 | 最差误差 |" % RED_PCT)
    L.append("|---|---|---|---|---|")
    L.append("| %d | %d | %d | %d | %.2f%% |" % (
        s["n_items"], s["n_checks"], s["n_red"], s["n_dangling_refs"], s["worst_err_pct"]))
    L.append("\n定级分布：" + "，".join(f"{r}={s['per_rank'][r]}" for r in RANKS) + "\n")
    L.append("## 逐笔台账\n")
    L.append("| 欠账 | 等级 | 报告 | 脚本 | artifact | 测试引用 | 误差记录(强/弱/红) | 最差 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i in ledger["items"]:
        L.append("| %s | **%s** | %d | %d | %d | %d | %d/%d/%d | %.2f%% |" % (
            i["name"], i["rank"], len(i["docs"]), len(i["scripts"]), len(i["artifacts"]),
            len(i["test_refs"]), i["n_strong"], i["n_weak"], i["n_red"], i["worst_err_pct"]))
    L.append("\n## 判据与理由\n")
    for i in ledger["items"]:
        L.append(f"### {i['name']} → {i['rank']}\n")
        for r in i["reasons"]:
            L.append(f"- {r}")
        if i["dangling_refs"]:
            L.append("- 悬空清单：" + "，".join(f"`{d}`" for d in i["dangling_refs"]))
        if i["n_checks"]:
            worst = max(i["checks"], key=lambda c: abs(c["err_pct"]))
            L.append("- 最差记录：`%s` %s = %.3f%%（档位 %s）"
                     % (worst["artifact"], worst["metric"], worst["err_pct"], worst["band"]))
        L.append("")
    return "\n".join(L) + "\n"


def render_figure(ledger: Dict[str, Any], out_png: str) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager  # noqa: F401
    except Exception:
        return None
    items = ledger["items"]
    if not items:
        return None
    names = [i["name"] for i in items]
    cols = ["report", "script", "artifact", "test-ref", "no-dangling", "no-red"]
    mat = []
    for i in items:
        mat.append([len(i["docs"]) > 0, len(i["scripts"]) > 0, len(i["artifacts"]) > 0,
                    len(i["test_refs"]) > 0, len(i["dangling_refs"]) == 0, i["n_red"] == 0])
    fig, axes = plt.subplots(1, 2, figsize=(12.5, max(3.2, 0.55 * len(items))), gridspec_kw={"width_ratios": [1, 1.35]})
    ax = axes[0]
    ax.imshow(mat, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)), cols, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(len(names)), names, fontsize=8)
    for r in range(len(names)):
        for c in range(len(cols)):
            ax.text(c, r, "1" if mat[r][c] else "0", ha="center", va="center", fontsize=8)
    ax.set_title("evidence presence (S1)", fontsize=9)

    ax2 = axes[1]
    for idx, i in enumerate(items):
        for c in i["checks"]:
            x = abs(c["err_pct"]) or 1e-6
            color = {"PASS_STRONG": "#2e7d32", "PASS_WEAK": "#f9a825", "RED": "#c62828"}[c["band"]]
            ax2.scatter(x, idx, s=28, color=color, edgecolor="k", linewidth=0.3)
        if not i["checks"]:
            ax2.text(1e-6, idx, " no numeric evidence", fontsize=7, va="center", color="#666")
    ax2.axvline(STRONG_PCT, ls="--", lw=1, color="#2e7d32", label=f"{STRONG_PCT:g}% target")
    ax2.axvline(RED_PCT, ls="--", lw=1, color="#c62828", label=f"{RED_PCT:g}% red line")
    ax2.set_xscale("log")
    ax2.set_yticks(range(len(names)), [f"{i['name']} [{i['rank']}]" for i in items], fontsize=8)
    ax2.set_xlabel("|relative error| [%] (log)", fontsize=9)
    ax2.set_title("S2 error adjudication", fontsize=9)
    ax2.legend(fontsize=7, loc="lower right")
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="验证欠账台账（只读）")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--out-dir", default="artifacts/verification_debt")
    ap.add_argument("--strict", action="store_true", help="出现 UNBACKED/OVERCLAIM/NO_EVIDENCE 则 exit 1")
    ap.add_argument("--run-tests", action="store_true", help="实跑被引用的测试文件（重）")
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args(argv)

    ledger = build_ledger(args.root, run_tests=args.run_tests)
    os.makedirs(os.path.join(args.root, args.out_dir), exist_ok=True)
    jpath = os.path.join(args.root, args.out_dir, "ledger.json")
    mpath = os.path.join(args.root, args.out_dir, "LEDGER.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)
    md = render_markdown(ledger)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(md)
    fig = None
    if not args.no_figure:
        fig = render_figure(ledger, os.path.join(args.root, args.out_dir, "fig1_debt_ledger.png"))
    if fig:
        ledger["figure"] = _rel(os.path.abspath(args.root), fig)

    s = ledger["summary"]
    print(f"[ledger] items={s['n_items']} checks={s['n_checks']} red={s['n_red']} "
          f"dangling={s['n_dangling_refs']} worst={s['worst_err_pct']:.2f}%")
    for i in ledger["items"]:
        print(f"  {i['rank']:<12} {i['name']}")
    print(f"[ledger] wrote {jpath} / {mpath}" + (f" / {fig}" if fig else ""))
    bad = [i for i in ledger["items"] if i["rank"] in ("UNBACKED", "OVERCLAIM", "NO_EVIDENCE")]
    if args.strict and bad:
        print(f"[ledger] STRICT FAIL: {len(bad)} 笔欠账证据不齐", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())