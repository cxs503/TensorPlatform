#!/usr/bin/env python3
"""误差宣称一致性审计（error-claim consistency audit）—— 把平台精度标准做成机器闸门。

动机（m1856 自找活，tp-ver-4 席）
--------------------------------
平台精度标准写死为「对标实验/参考值误差 **>15% 不可接受**」「误差判定须显式回答**是否<3%**」。
但这两条此前**只存在于散文与 owner 指令中**：

* ``tools/report_corpus_audit.py`` 只检查报告里**是否出现过**「是否<3%」句式（结构存在性），
  不检查紧随其后的数字是否与宣称**矛盾**——一篇报告完全可以写
  「相对误差 19.6%，是否<3%：是」并通过结构闸门；
* ``tools/validation_debt_ledger.py`` 抽取「局限/注意点」节，抽的是**欠账陈述**，
  不碰正文里的**数值宣称**；
* 质检席（LLM 复审）是主观判定，跨批不可复算。

本模块补的正是这个空档：把「报告的误差数字 + 同一句里的判定词」抽成声明条目，
用平台阈值做**确定性**裁决，产出可复算的违规台账。

判定规则（全部为显式常量，测试钉死）
------------------------------------
============= =====================================================
规则            触发条件
============= =====================================================
HARD_GT15     数值 v > 15%（平台硬标准「不可接受」）却带**肯定判定**
              （达标/合格/通过/PASS/满足…）。这是平台标准的直接违反。
SOFT_GT3      v > 宣称阈值（缺省 3%）却仍以「<3% 达标」类肯定语收尾
              —— 自相矛盾，阈值被自己引用的数字推翻。
UNSUPPORTED   报告给出「是否<3%：是/达标」的显式答复，但全文**没有任何**
              数值误差量可支撑（无数字的达标 = 不可复算）。
============= =====================================================

以下情形**不计违规**（负控制，避免把诚实声明打成缺陷）：

* 同一句出现否定判定（不达标/不可接受/超标/FAIL/未通过）—— 诚实 FAIL 是纪律要求，不是缺陷；
* 声明了域内容差且 v ≤ 该容差且 v ≤ 15%（如提案验收「<10%」、ITTC 建议）；
* 数值出现在纯参考/要求列（如「判据 <3%」）而判定词属于相邻条款 —— 归 ``DEFERRED`` 桶，
  只计数不判级（启发式边界，如实暴露）。

纪律
----
* 只读：``--db`` 走 psql 只读 SELECT，``--from-json`` 读离线快照；不改任何报告与数据库。
* 零第三方依赖（matplotlib 仅在 ``--figures`` 时按需导入）。
* 只裁「宣称与其引用数字是否自洽」，**不裁物理精度本身**——物理精度仍需 benchmark 复核。

用法
----
    python3 tools/error_claim_audit.py --db --limit 800 --out artifacts/error_claim_audit
    python3 tools/error_claim_audit.py --from-json /tmp/reports_snap.json --out /tmp/eca
    python3 tools/error_claim_audit.py --self artifacts/error_claim_audit/claims.json
    python3 tools/error_claim_audit.py --db --out artifacts/error_claim_audit --figures

产物
----
* ``claims.json``：逐声明条目（value / sentence / declared_threshold / verdict / rule / evidence）
* ``audit.md``：人读摘要（统计 + 违规样例 + 规则口径）
* ``--figures``：``fig_error_claim_magnitude.png``、``fig_error_claim_rules.png``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "error-claim-audit/1"

# --------------------------------------------------------------------------- #
# 平台精度标准（显式常量：改这里等于改判据，必须同步改测试）
# --------------------------------------------------------------------------- #
TARGET_TOL_PCT = 3.0     # ⑥ 显式回答「是否<3%」的目标阈值
HARD_TOL_PCT = 15.0      # owner 精度标准：对标实验/参考值误差 >15% 不可接受

RULES = ("HARD_GT15", "SOFT_THRESH", "UNSUPPORTED")
NON_RULE_BUCKETS = ("DEFERRED", "OK", "HONEST_FAIL", "UNJUDGED")

# --------------------------------------------------------------------------- #
# 正则（显式常量，测试逐条钉死）
# --------------------------------------------------------------------------- #
_NUM = r"[-+]?\d+(?:\.\d+)?"
# 正向：误差 19.6% / 相对误差为 3.2 % / deviation of 4.1% / 相差约 7%
_CLAIM_FWD_RE = re.compile(
    r"(?:相对误差|误差|偏差|相对偏差|相差|差值|error|deviation|difference)"
    r"[^。；;\n]{0,12}?" + _NUM + r"\s*[%％]", re.IGNORECASE)
# 反向：19.6% 的相对误差 / 3.2 % error
_CLAIM_REV_RE = re.compile(
    _NUM + r"\s*[%％]\s*(?:的)?\s*(?:相对)?(?:误差|偏差|error|deviation)", re.IGNORECASE)
_CLAIM_VALUE_RE = re.compile(_NUM + r"\s*[%％]")

# 「验收判定词」白名单（只收**验收语义**的词；比较语义词如「优于/符合预期/✅」不算，
# 否则审计对照表里的 ✅ 会被误读成对误差的背书——v1 实测 FP 主因）。
_PASS_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"达标"), re.compile(r"合格"),
    re.compile(r"(?<![A-Za-z])PASS(?![A-Za-z])"), re.compile(r"在容差内"),
    re.compile(r"满足\s*(?:[<＜≤]?\s*[\d.]+\s*[%％]?\s*)?(?:判据|要求|阈值|容差|标准|验收|门禁|指标)"),
    re.compile(r"通过\s*(?:判据|验收|门禁|门槛)"),
    re.compile(r"符合\s*(?:判据|验收标准|容差|门槛)"),
    re.compile(r"判定\s*(?:为)?\s*(?:达标|合格|PASS)"),
)
_FAIL_TOKENS = ("不达标", "未达标", "不可接受", "超标", "超阈", "超出容差", "不合格",
                "未通过", "不满足", "失败", "fail", "❌", "✗", "已超出")
# 否定型「达标」：无一项满足 / 尚未达标 / 不能通过
_NEG_PASS_RE = re.compile(
    r"(?:无(?:一|任何|一项)?|未|不|没有|均未|并不|不存在|全部未|仍不|尚未)"
    r"[^，。；\n]{0,10}?(?:达标|满足|符合|通过|合格)")
# 披露/审计语境：句中判定词多属「审计结论/修复对照」，不是对误差的背书
_DISCLOSURE_RE = re.compile(
    r"(最差|缺陷|旧实现|修复前|修复之前|未披露|检出|命中|反证|越域|老实现|但仍|然而"
    r"|不一致|违背|误判|伪造|占位|假阳性|假通过|待修|无关|before\s+fix|defect|bug)",
    re.IGNORECASE)
# 数值紧跟这些词 → 该数字是**判据**而非实测（「满足 3 % 判据」）
_CRITERION_AFTER_RE = re.compile(r"\s*(?:判据|阈值|要求|容差|门槛|标准|验收|门限)")
_CRITERION_CONTEXT = ("判据", "阈值", "要求", "验收", "门槛", "标准要求", "容差", "criterion", "criteri", "tolera")

# 显式「是否<3%」肯定答复：必须带肯定词，且**不是**在转述问题（含「是否」的问句不算答复）
_EXPLICIT3_POS_RE = re.compile(
    r"(?:[<＜≤]\s*3\s*[%％]\s*[：:]?\s*(?:是|成立|达标|✅|yes)"
    r"|(?<!是否)(?:全部|均|所有)[^。\n]{0,10}?[<＜≤]\s*3\s*[%％]"
    r"|so\s+yes[^.\n]{0,12}[<＜≤]\s*3\s*[%％])", re.IGNORECASE)
_EXPLICIT3_DISCLAIM_RE = re.compile(r"(不诚实|不真实|不得|禁止|说法|表述|宣称|反问|疑问)")

_THRESH_RE = re.compile(
    r"(?:<|＜|≤|<=|不超过|小于|within|under)\s*(" + _NUM + r")\s*[%％]")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；;\n])")
_SCINUM_RE = re.compile(r"\d+(?:\.\d+)?\s*[eE][-+]?\d+")
_CLAUSE_RE = re.compile(r"[^，,、（）()；;|]+")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _clauses(sentence: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).strip()) for m in _CLAUSE_RE.finditer(sentence)]


def has_pass_token(text: str) -> bool:
    return any(rx.search(text) for rx in _PASS_PATTERNS)


def verdict_of(sentence: str) -> str:
    low = sentence.lower()
    if _NEG_PASS_RE.search(sentence) or any(t in low for t in _FAIL_TOKENS):
        return "fail"
    if has_pass_token(sentence):
        return "pass"
    return "none"


def judge(value: float, sentence: str, pos: int, is_table: bool = False) -> Tuple[str, Optional[float], str]:
    """单条声明裁决 → (rule, declared_threshold, evidence)。

    判定词必须落在**数值所在子句或紧邻子句**内，否则 ``DEFERRED``（作用域不明，不当罪证）；
    表格行 ``DEFERRED_TABLE``（静态解析无法区分「实测值列」与「判据/文献列」）。
    HARD_GT15 只需「数值 >15% + 验收判定」；SOFT_THRESH 需**句内已声明容差**且数值超容差
    —— 因此「4.92% → PASS」（无声明容差）不判违规，只有自相矛盾才判。
    """
    thresh = declared_threshold(sentence)
    ev = sentence.strip().replace("\n", " ")[:180]

    if is_table:
        return "DEFERRED_TABLE", thresh, ev
    # 收敛链（73.9% → 27.2% → … → 0.05%，PASS）：判定只背书最细档，不能摊到整条链
    if ("→" in sentence or "->" in sentence) and len(
            re.findall(r"\d+(?:\.\d+)?\s*[%％]", sentence)) >= 2:
        return "DEFERRED_CHAIN", thresh, ev
    if _DISCLOSURE_RE.search(sentence):
        return "DISCLOSURE", thresh, ev
    if _NEG_PASS_RE.search(sentence) or any(t in sentence.lower() for t in _FAIL_TOKENS):
        return "HONEST_FAIL", thresh, ev

    spans = _clauses(sentence)
    idx = None
    for i, (a, b, _t) in enumerate(spans):
        if a <= pos < b:
            idx = i
            break
    if idx is None:
        return "UNJUDGED", thresh, ev
    near = "，".join(t for _a, _b, t in spans[idx: idx + 3])
    if not has_pass_token(near):
        return ("DEFERRED" if has_pass_token(sentence) else "UNJUDGED"), thresh, ev

    # 数值恰等于句内声明的容差且处于判据语境（「满足 3 % 判据」）：该数字是要求，不是实测
    if thresh is not None and abs(value - thresh) < 1e-9 and any(
            c in sentence for c in _CRITERION_CONTEXT):
        return "DEFERRED", thresh, ev

    if value > HARD_TOL_PCT:
        return "HARD_GT15", thresh, ev
    if thresh is not None and value > thresh:
        return "SOFT_THRESH", thresh, ev
    return "OK", thresh, ev




@dataclass
class Claim:
    report_id: int
    mission_id: Optional[int]
    title: str
    value_pct: float
    sentence: str
    line: int
    declared_threshold: Optional[float]
    verdict: str
    rule: str
    evidence: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def split_sentences(markdown: str) -> List[Tuple[int, str]]:
    """逐行切句（句末标点 。！？；; 为界），行号 1-based、取句所在行。"""
    out: List[Tuple[int, str]] = []
    for i, line in enumerate(markdown.splitlines(), 1):
        for part in re.split(r"(?<=[。！？；;])", line):
            part = part.strip()
            if part:
                out.append((i, part))
    return out


def extract_claims(markdown: str) -> List[Tuple[int, float, str, int]]:
    """返回 [(line, value_pct, sentence, pos_in_sentence)]，同一句同值去重。

    只在「误差/偏差/error/deviation」标签的 ±12 字窗口内取数，且**排除判据数字**
    （``满足 3 % 判据`` 里的 3 % 是要求不是实测）。
    """
    seen = set()
    out: List[Tuple[int, float, str, int]] = []
    for line, sent in split_sentences(markdown):
        if "%" not in sent and "％" not in sent:
            continue
        hits: List[Tuple[float, int]] = []
        for rx in (_CLAIM_FWD_RE, _CLAIM_REV_RE):
            for m in rx.finditer(sent):
                vm = _CLAIM_VALUE_RE.search(m.group(0))
                if not vm:
                    continue
                try:
                    v = abs(float(vm.group(0).replace("％", "%").rstrip("% ").strip()))
                except ValueError:
                    continue
                pos = m.start() + vm.start()
                if pos > 0 and sent[pos - 1] in "<＜≤>＞":
                    continue  # 阈/门限式数字（"<3%"），非实测
                tail = sent[pos + len(vm.group(0)): pos + len(vm.group(0)) + 8]
                if _CRITERION_AFTER_RE.match(tail):
                    continue
                hits.append((v, pos))
        for v, pos in hits:
            key = (_hash(sent), v)
            if key in seen:
                continue
            seen.add(key)
            out.append((line, v, sent, pos))
    return out


def declared_threshold(sentence: str) -> Optional[float]:
    m = _THRESH_RE.search(sentence)
    return abs(float(m.group(1))) if m else None



# --------------------------------------------------------------------------- #
# 变异测试：向真实报告注入**已知真值**的宣称，测量检出率（正控制）+ 误报（负控制）
# --------------------------------------------------------------------------- #
# (注入句模板, 期望判定)。期望 "" 表示负控制（不得判违规）。
MUTATIONS: Tuple[Tuple[str, str], ...] = (
    ("\n\n附：本算例最终相对误差 22.4%，判定达标。\n", "HARD_GT15"),
    ("\n\n附：静水阻力相对误差 18.0%，满足验收标准。\n", "HARD_GT15"),
    ("\n\n附：升力系数偏差 9.6%，满足<3%判据，判定达标。\n", "SOFT_THRESH"),
    ("\n\n附：桨距比偏差 4.5%，满足<5%判据。\n", ""),
    ("\n\n附：垂荡 RAO 相对误差 1.8%，判定达标。\n", ""),
    ("\n\n附：该量相对误差 22.4%，判定不达标，已记入欠账。\n", ""),
    ("\n\n附：相对误差 22.4%，远超平台 15% 标准，属已知缺陷。\n", ""),
)
MUTATION_SEED = 20261102


def run_mutation(reports: Sequence[Dict[str, Any]], per_template: int = 40) -> Dict[str, Any]:
    """把模板句追加进真实报告，测量检出率。返回逐模板统计。"""
    import random
    rnd = random.Random(MUTATION_SEED)
    pool = [r for r in reports if len(r.get("markdown") or "") > 200]
    results: List[Dict[str, Any]] = []
    pos_tot = pos_hit = neg_tot = neg_fp = 0
    for tmpl, expect in MUTATIONS:
        hits = 0
        n = 0
        for rep in (rnd.sample(pool, min(per_template, len(pool))) if pool else []):
            mutated = dict(rep)
            mutated["markdown"] = (rep.get("markdown") or "") + tmpl
            res = analyze_report(mutated)
            rules = {v["rule"] for v in res["violations"]}
            n += 1
            if expect:
                if expect in rules:
                    hits += 1
            else:
                if rules:
                    hits += 1  # 负控制里 hits 表示「误报」
        results.append({"template": tmpl.strip(), "expect": expect or "(no-flag)",
                        "n": n, "hit": hits,
                        "rate": round(hits / n, 4) if n else None})
        if expect:
            pos_tot += n
            pos_hit += hits
        else:
            neg_tot += n
            neg_fp += hits
    return {
        "per_template": results,
        "positive_recall": round(pos_hit / pos_tot, 4) if pos_tot else None,
        "positive_n": pos_tot,
        "negative_fp_rate": round(neg_fp / neg_tot, 4) if neg_tot else None,
        "negative_n": neg_tot,
        "seed": MUTATION_SEED,
    }


def _has_measured_pct(md: str) -> bool:
    """全篇是否存在**实测式**百分比（排除 ``<3%`` 一类阈值写法）。"""
    for m in re.finditer(r"\d+(?:\.\d+)?\s*[%％]", md):
        if m.start() > 0 and md[m.start() - 1] in "<＜≤>＞":
            continue
        return True
    return False


def analyze_report(row: Dict[str, Any]) -> Dict[str, Any]:
    md = row.get("markdown") or ""
    claims: List[Claim] = []
    table_lines = {i for i, ln in enumerate(md.splitlines(), 1) if ln.lstrip().startswith("|")}
    for line, v, sent, pos in extract_claims(md):
        rule, thresh, ev = judge(v, sent, pos, is_table=(line in table_lines))
        claims.append(Claim(
            report_id=int(row.get("id") or 0),
            mission_id=row.get("mission_id"),
            title=(row.get("title") or "")[:120],
            value_pct=round(v, 6),
            sentence=sent.strip().replace("\n", " ")[:400],
            line=line,
            declared_threshold=thresh,
            verdict=verdict_of(sent),
            rule=rule,
            evidence=ev,
        ))

    # UNSUPPORTED：显式答复「<3% 是/达标」但全篇无量值声明
    unsupported: List[Dict[str, Any]] = []
    m = None
    for cand in _EXPLICIT3_POS_RE.finditer(md):
        ctx = md[max(0, cand.start() - 40): cand.end() + 40]
        if _EXPLICIT3_DISCLAIM_RE.search(ctx):
            continue
        m = cand
        break
    numeric_evidence = bool(claims) or bool(_SCINUM_RE.search(md)) or _has_measured_pct(md)
    if m and not numeric_evidence:
        line = md[: m.start()].count("\n") + 1
        unsupported.append({
            "report_id": int(row.get("id") or 0),
            "mission_id": row.get("mission_id"),
            "title": (row.get("title") or "")[:120],
            "line": line,
            "evidence": md[max(0, m.start() - 60): m.end() + 60].strip().replace("\n", " ")[:180],
        })

    violations = [c for c in claims if c.rule in RULES]
    return {
        "report_id": int(row.get("id") or 0),
        "mission_id": row.get("mission_id"),
        "title": (row.get("title") or "")[:120],
        "claims": [c.as_dict() for c in claims],
        "violations": [c.as_dict() for c in violations],
        "unsupported": unsupported,
        "has_explicit_lt3": bool(m),
    }


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rule_counts: Dict[str, int] = {}
    total_claims = 0
    reports_with_violation = 0
    by_rule_reports: Dict[str, int] = {}
    magnitudes: List[float] = []
    for r in rows:
        total_claims += len(r["claims"])
        magnitudes.extend(c["value_pct"] for c in r["claims"])
        rules_here = set(c["rule"] for c in r["claims"])
        for k in rules_here:
            rule_counts[k] = rule_counts.get(k, 0) + 1
        if r["violations"] or r["unsupported"]:
            reports_with_violation += 1
        for v in r["violations"]:
            by_rule_reports[v["rule"]] = by_rule_reports.get(v["rule"], 0) + 1
    n = max(1, len(rows))
    return {
        "reports_scanned": len(rows),
        "reports_with_explicit_lt3": sum(1 for r in rows if r["has_explicit_lt3"]),
        "reports_with_violation": reports_with_violation,
        "violation_rate": round(reports_with_violation / n, 4),
        "claims_total": total_claims,
        "claim_rules_by_report": rule_counts,
        "violation_items_by_rule": by_rule_reports,
        "violation_items_total": sum(by_rule_reports.values()),
        "unsupported_reports": sum(1 for r in rows if r["unsupported"]),
        "magnitude_bins": {
            "<=3%": sum(1 for v in magnitudes if v <= 3),
            "3-5%": sum(1 for v in magnitudes if 3 < v <= 5),
            "5-10%": sum(1 for v in magnitudes if 5 < v <= 10),
            "10-15%": sum(1 for v in magnitudes if 10 < v <= 15),
            ">15%": sum(1 for v in magnitudes if v > 15),
        },
    }


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def load_reports_json(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("reports") or []
    return list(data)


def load_reports_psql(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """只读拉取报告（口令沿用平台 psql 客户端环境）。"""
    sql = "select row_to_json(t) from (select id, title, markdown, mission_id from tensor_reports order by id desc limit %s) t"
    q = sql % (limit if limit else 1000000)
    p = subprocess.run(["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform",
                        "-tAc", q], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise SystemExit(f"psql 失败: {p.stderr.strip()[:300]}")
    return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]


def render_markdown(summary: Dict[str, Any], rows: Sequence[Dict[str, Any]], top: int) -> str:
    s = summary
    out: List[str] = []
    out.append("# 误差宣称一致性审计台账\n")
    out.append(f"生成时间（UTC）：`{datetime.now(timezone.utc).isoformat(timespec='seconds')}`\n")
    out.append("## 口径\n")
    out.append(f"* 目标阈值 `TARGET_TOL_PCT = {TARGET_TOL_PCT}%`（⑥ 显式回答「是否<3%」）")
    out.append(f"* 硬标准 `HARD_TOL_PCT = {HARD_TOL_PCT}%`（误差 >15% 不可接受，owner 常设精度标准）")
    out.append("* `HARD_GT15` = 数值 >15% 却带验收判定；`SOFT_THRESH` = 数值 > 句内声明容差却称达标（自相矛盾）；"
               "`UNSUPPORTED` = 有「<3% 是」答复但全篇无数值误差\n")
    out.append("## 统计\n")
    out.append("| 指标 | 值 |")
    out.append("|---|---|")
    out.append(f"| 扫描报告 | {s['reports_scanned']} |")
    out.append(f"| 含显式「是否<3%」答复 | {s['reports_with_explicit_lt3']} |")
    out.append(f"| 含违规/无支撑宣称的报告 | {s['reports_with_violation']}（{s['violation_rate']*100:.1f}%） |")
    out.append(f"| 数值误差声明条目 | {s['claims_total']} |")
    out.append(f"| 违规条目 | {s['violation_items_total']} |")
    out.append(f"| 无支撑（UNSUPPORTED）报告 | {s['unsupported_reports']} |")
    out.append("")
    out.append("### 声明量级分布\n")
    out.append("| 区间 | 条目数 |")
    out.append("|---|---|")
    for k, v in s["magnitude_bins"].items():
        out.append(f"| {k} | {v} |")
    out.append("")
    out.append("### 判定分布（按报告去重）\n")
    out.append("| 判定 | 报告数 |")
    out.append("|---|---|")
    for k, v in sorted(s["claim_rules_by_report"].items(), key=lambda kv: -kv[1]):
        out.append(f"| {k} | {v} |")
    out.append("")
    out.append(f"## 违规样例（≤{top} 条，逐字原文 + 报告号）\n")
    shown = 0
    for r in rows:
        for v in r["violations"]:
            if shown >= top:
                break
            out.append(f"- **#{r['report_id']}** `{v['rule']}` v={v['value_pct']}% "
                       f"thresh={v['declared_threshold']} · {r['title'][:50]}")
            out.append(f"  - L{v['line']}：{v['evidence']}")
            shown += 1
        if shown >= top:
            break
    out.append("")
    return "\n".join(out)


def make_figures(summary: Dict[str, Any], rows: Sequence[Dict[str, Any]], outdir: Path) -> List[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Path] = []
    mags = [c["value_pct"] for r in rows for c in r["claims"]]
    fig, ax = plt.subplots(figsize=(7, 4))
    if mags:
        ax.hist(mags, bins=[0, 1, 2, 3, 5, 10, 15, 30, 100], color="#3b6ea5", edgecolor="white")
    ax.axvline(TARGET_TOL_PCT, color="#2e7d32", ls="--", lw=1.4, label=f"target {TARGET_TOL_PCT}%")
    ax.axvline(HARD_TOL_PCT, color="#c62828", ls="-", lw=1.6, label=f"hard bound {HARD_TOL_PCT}%")
    ax.set_xscale("symlog")
    ax.set_xlabel("claimed relative error [%] (symlog)")
    ax.set_ylabel("# claims")
    ax.set_title(f"Claimed error magnitudes (n={len(mags)} claims, {summary['reports_scanned']} reports)")
    ax.legend()
    fig.tight_layout()
    p1 = outdir / "fig_error_claim_magnitude.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append(p1)

    labels = ["HARD_GT15", "SOFT_THRESH", "UNSUPPORTED"]
    vals = [summary["violation_items_by_rule"].get("HARD_GT15", 0),
            summary["violation_items_by_rule"].get("SOFT_THRESH", 0),
            summary["unsupported_reports"]]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    bars = ax.bar(labels, vals, color=["#c62828", "#ef6c00", "#5c6bc0"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("# findings")
    ax.set_title("Findings by rule (deterministic gate)")
    fig.tight_layout()
    p2 = outdir / "fig_error_claim_rules.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append(p2)
    return figs


def run_self(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("reports") if isinstance(data, dict) else data
    n = sum(1 for r in rows if r.get("violations") or r.get("unsupported"))
    print(json.dumps({"reports": len(rows), "reports_with_violation": n}, ensure_ascii=False))
    return 1 if n else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="误差宣称一致性审计：报告误差数字 vs 平台精度标准")
    ap.add_argument("--from-json", help="离线报告快照（list 或 {reports: [...]})")
    ap.add_argument("--db", action="store_true", help="从 tensor_reports 只读拉取")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="artifacts/error_claim_audit")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--mutate", action="store_true", help="变异测试：测量检出率/误报率")
    ap.add_argument("--self", dest="self_path", help="复用既有 claims.json 做闸门（有违规 → 返回码 1）")
    args = ap.parse_args(argv)

    if args.self_path:
        return run_self(Path(args.self_path))

    if args.from_json:
        reports = load_reports_json(args.from_json)
    elif args.db:
        reports = load_reports_psql(args.limit)
    else:
        ap.error("需要 --from-json 或 --db")

    rows = [analyze_report(r) for r in reports]
    summary = summarize(rows)
    mutation = run_mutation(reports) if args.mutate else None
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules": {"TARGET_TOL_PCT": TARGET_TOL_PCT, "HARD_TOL_PCT": HARD_TOL_PCT},
        "summary": summary,
        "mutation": mutation,
        "reports": rows,
    }
    (outdir / "claims.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "audit.md").write_text(render_markdown(summary, rows, args.top), encoding="utf-8")
    print(json.dumps({"summary": summary, "mutation": mutation}, ensure_ascii=False))
    if args.figures:
        for p in make_figures(summary, rows, outdir):
            print(f"figure: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())