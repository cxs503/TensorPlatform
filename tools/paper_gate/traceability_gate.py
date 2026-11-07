#!/usr/bin/env python3
"""Manuscript traceability gate (PAPER-TRACE) — machine check that every
checkable claim in a paper draft resolves to a real artifact.

Why this exists
---------------
The paper production line lost its first two drafts to blind review for the
*same* root cause: numbers that could not be traced back to any artifact.

* paper-draft-ice-ridge-keel-grounding (m1122) — cited report #825 that the
  reviewer could not resolve, and Table 2 / Table 4 were arithmetically
  inconsistent (error column did not equal (value-ref)/ref).
* paper-draft-ice-wave-mechanics-r2 (m1244) — H2 WITHHELD (numbers not
  traceable), H3 figs 2-5 synthesised, H4 zero-citation figures, H8 two
  arithmetic errata accepted against the evidence base #823.

The existing report QA gate (``backend/api/tensor_reports.py::_check_report_quality``)
only matches *keywords* ("是否出现『误差』二字"), so a manuscript full of invented
numbers passes it. This gate is the missing quantitative counterpart: it does
not judge prose, it judges **resolvability**.

Checks
------
PG1 phantom_path      backticked repo/artifact path that does not resolve on disk
PG2 phantom_report    ``报告 #N`` / ``Report #N`` / ``report_id: N`` not in the report ledger
PG3 figure_ref        markdown image link with no file in the bundle (phantom figure),
                      plus zero-citation figure files (H4 failure mode)
PG4 table_arithmetic  error column recomputed from (value, reference) columns
PG5 inline_error      prose claim "A vs B, 误差 Z%" recomputed
PG6 anchor_mismatch   declared claim anchor ``path#json.pointer`` that does not
                      match the value printed in the manuscript

Out of scope (declared honestly, not silently): fabricated *figures* whose
data is synthesised inside the figure script. Detecting that requires
executing the generator and diffing against the artifact tree; the gate can
only flag figures whose data source is not cited (PG3 zero-citation).

Usage
-----
    python3 tools/paper_gate/traceability_gate.py \
        --manuscript papers/x/manuscript.md --bundle papers/x \
        --artifact-root /root/tensor-ecosystem \
        --report-ids 823,825,857 --json-out /tmp/trace.json

Exit code 0 = no FAIL findings, 1 = at least one FAIL, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SUPER = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
         "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "⁺": "+"}

PATH_SUFFIX = {".json", ".py", ".md", ".csv", ".npz", ".npy", ".txt", ".yaml",
               ".yml", ".log", ".png", ".tex", ".tif", ".pdf", ".h5"}

_CODE_SPAN = re.compile(r"`([^`\n]{3,240})`")
_REPORT_REF = re.compile(r"(?:报告|Report|report|REPORT)\s*#\s*(\d{1,6})")
_REPORT_ID_FIELD = re.compile(r"report_id\s*[:：]\s*(\d{1,6})")
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)\s]{3,240})\)")
_ANCHOR = re.compile(r"`([^`\n]+?)#([^`\n]+?)`")
_ANCHOR_COLON = re.compile(r"`([^`\n]+?\.(?:json|yaml|yml)):([A-Za-z0-9_.\[\]-]+)`")


# ───────────────────────────── number parsing ─────────────────────────────


def parse_number(text: str):
    """Parse the first number in a cell, tolerating units, **bold**, $math$,
    thousands separators and unicode powers-of-ten (1.16 × 10⁻¹³)."""
    if text is None:
        return None
    s = str(text)
    for k, v in SUPER.items():
        s = s.replace(k, v)
    s = (s.replace("×", "x").replace("**", "").replace("$", "")
         .replace("，", ",").replace("（", "(").replace("）", ")")
         .replace("\u00a0", " ").replace("−", "-").replace("–", "-"))
    # 1.16x10-13  ->  1.16e-13   (superscript digits already folded to ASCII)
    s = re.sub(r"([0-9.]+)\s*x\s*10\s*\^?\s*([-+]?\d+)", r"\1e\2", s)
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    m = re.search(NUM, s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:  # pragma: no cover - regex already constrains input
        return None


def numbers_in(text: str) -> list[float]:
    """All numbers in a text after folding unicode powers of ten / x10^n."""
    s = str(text)
    for k, v in SUPER.items():
        s = s.replace(k, v)
    s = (s.replace("×", "x").replace("**", "").replace("$", "")
         .replace("−", "-").replace("–", "-"))
    s = re.sub(r"([0-9.]+)\s*x\s*10\s*\^?\s*([-+]?\d+)", r"\1e\2", s)
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    out = []
    for m in re.finditer(NUM, s):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


def value_present(claim: float, corpus: list[float], rtol: float = 1e-6) -> bool:
    return any(abs(c - claim) <= 1e-12 + rtol * abs(claim) for c in corpus)


# ── 打印精度传播（PG4/PG5 容差） ────────────────────────────────────────────
# 旧口径固定容差（0.05pp + 0.5%）是「人眼扫一眼」的量级；对 6 位小数表它比
# 真实可判性松两个数量级，于是误差列被扰动 1%~3% 照样放行（=判据不可证伪，
# 变异存活）。可判性上限由**印刷精度**决定，所以再叠一条逐行传播带宽：
#     δpct = 100·(δv/|ref| + |val|·δref/ref²) + δstated      （δ = 该字面量 0.5 ulp）
# 取严（min）而不是取代：门对旧口径**只紧不松**——表印得比旧口径能分辨的更细时
# 按印刷精度判（1% 扰动即被抓住），印得粗时退回旧口径（粗精度下本来就判不出
# 小扰动，不制造新的假阳）。
_LIT_CLEAN = str.maketrans({",": "", " ": "", "%": "",
                            "\u2030": "", "\u2212": "-", "\u2013": "-"})
_LIT_NUM = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def half_ulp(literal) -> float | None:
    """字面量的半个末位单位（0.5×10^exponent）；无法判读返回 None。"""
    if literal is None:
        return None
    m = _LIT_NUM.search(str(literal).translate(_LIT_CLEAN))
    if not m:
        return None
    try:
        d = Decimal(m.group(0))
    except InvalidOperation:  # pragma: no cover - 防御
        return None
    return float(Decimal(5).scaleb(d.as_tuple().exponent - 1))


def literal_number(literal) -> float | None:
    m = _LIT_NUM.search(str(literal).translate(_LIT_CLEAN))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:  # pragma: no cover - 防御
        return None


def error_band(stated_lit, val_lit, ref_lit, computed, tol_pp, tol_rel) -> float:
    """一行/一句误差声明的合理带宽（百分点）：旧口径与印刷精度传播取严。"""
    legacy = tol_pp + tol_rel * abs(computed)
    d_st, d_v, d_r = half_ulp(stated_lit), half_ulp(val_lit), half_ulp(ref_lit)
    if d_st is None or d_v is None or d_r is None:
        return legacy                                     # 精度不可判读 → 旧口径兜底
    ref, val = literal_number(ref_lit), literal_number(val_lit)
    if not ref:
        return legacy
    propagated = d_st + 100.0 * (d_v / abs(ref) + abs(val or 0.0) * d_r / (ref * ref))
    return min(legacy, propagated)


def _close(stated: float, computed: float, tol_pp: float, tol_rel: float,
           band: float | None = None) -> bool:
    """Tolerance band. Errors are quoted as magnitudes as often as with sign, so
    all three conventions (±pct, |pct|) are accepted — the gate must not fail a
    manuscript over a sign convention."""
    band = tol_pp + tol_rel * abs(computed) if band is None else band
    return min(abs(stated - computed), abs(stated - abs(computed)),
               abs(abs(stated) - abs(computed))) <= band


# ───────────────────────────── markdown tables ─────────────────────────────


def iter_tables(md: str):
    """Yield (header_cells, [row_cells,...], start_line) for pipe tables."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = _cells(lines[i])
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(_cells(lines[j]))
                j += 1
            yield header, rows, i + 1
            i = j
        else:
            i += 1


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


_ERR_HDR = re.compile(r"(?i)(误差|偏差|err(or)?\b|deviation|相对差|δ)")
_ERR_THRESHOLD = re.compile(r"(?i)(阈值|threshold|容限|tol|判据|目标|criteria|require|必须)")
_REF_HDR = re.compile(r"(?i)(参考|ref\b|参考值|基准|baseline|analytic|解析|文献|theory|expected|实验|experiment|target)")
_VAL_HDR = re.compile(r"(?i)(数值|value|计算|present|ours|num|sim|measured|本文|仿真|结果)")


def check_table_arithmetic(header, rows, line_no, tol_pp, tol_rel):
    """PG4: recompute an error column from value/reference columns."""
    findings, checked = [], 0
    err_idx = [i for i, h in enumerate(header)
               if _ERR_HDR.search(h) and not _ERR_THRESHOLD.search(h)]
    if not err_idx:
        return findings, checked
    ref_idx = [i for i, h in enumerate(header) if _REF_HDR.search(h)]
    val_idx = [i for i, h in enumerate(header) if _VAL_HDR.search(h)]
    ref_idx = [i for i in ref_idx if i not in err_idx]
    val_idx = [i for i in val_idx if i not in err_idx and i not in ref_idx]
    if not ref_idx or not val_idx:
        return findings, checked
    err_i, ref_i, val_i = err_idx[0], ref_idx[0], val_idx[0]
    as_pct = ("%" in header[err_i] or "％" in header[err_i]
              or bool(re.search(r"(?i)(相对|relative|百分比)", header[err_i])))
    for r_off, row in enumerate(rows):
        if max(err_i, ref_i, val_i) >= len(row):
            continue
        stated, val, ref = (parse_number(row[err_i]), parse_number(row[val_i]),
                            parse_number(row[ref_i]))
        if stated is None or val is None or ref is None or ref == 0:
            continue
        checked += 1
        pct = (val - ref) / ref * 100.0
        band = error_band(row[err_i], row[val_i], row[ref_i], pct, tol_pp, tol_rel)
        ok = _close(stated, pct, tol_pp, tol_rel, band=band)
        if not ok and not as_pct:  # header had no unit: accept fraction form
            ok = _close(stated, pct / 100.0, tol_pp, tol_rel, band=band / 100.0)
        if not ok:
            findings.append({
                "code": "PG4_table_arithmetic", "severity": "FAIL",
                "line": line_no + r_off + 1,
                "detail": (f"误差列声明 {stated:g}{'%' if as_pct else ''} 与 "
                           f"(值 {val:g} − 参考 {ref:g})/参考 = {pct:.6g}% 不符"
                           f"（容差 {tol_pp:g}pp + {tol_rel*100:g}% 与印刷精度"
                           f"取严 = {band:.3g}pp）"),
                "band_pp": band,
            })
    return findings, checked


# ───────────────────────────── inline claims ─────────────────────────────

_INLINE = re.compile(
    rf"({NUM})\s*(?:vs\.?|VS\.?|对比|相对|与|/|—|~)\s*({NUM})\s*[)）]?\s*[,，;；:：]?\s*"
    rf"(?:相对)?(?:误差|偏差|error|difference)\s*(?:为|是|=|:|约|≈)?\s*({NUM})\s*(%|％)?",
)


def check_inline_errors(md: str, tol_pp, tol_rel):
    findings, checked = [], 0
    for m in _INLINE.finditer(md):
        a, b, stated = (parse_number(m.group(1)), parse_number(m.group(2)),
                        parse_number(m.group(3)))
        if None in (a, b, stated) or b == 0:
            continue
        checked += 1
        pct = (a - b) / b * 100.0
        is_pct = bool(m.group(4))
        band = error_band(m.group(3), m.group(1), m.group(2), pct, tol_pp, tol_rel)
        ok = _close(stated, pct, tol_pp, tol_rel, band=band)
        if not ok and not is_pct:
            ok = _close(stated, pct / 100.0, tol_pp, tol_rel, band=band / 100.0)
        if not ok:
            line = md[:m.start()].count("\n") + 1
            findings.append({
                "code": "PG5_inline_error", "severity": "FAIL", "line": line,
                "detail": (f"«{m.group(0)[:80]}» 声明误差 {stated:g}"
                           f"{'%' if is_pct else ''}，复算 (A−B)/B={pct:.6g}%"
                           f"（容差 {tol_pp:g}pp + {tol_rel*100:g}% 与印刷精度"
                           f"取严 = {band:.3g}pp）"),
                "band_pp": band,
            })
    return findings, checked


# ───────────────────────────── resolvability ─────────────────────────────


def _candidate_roots(artifact_roots, manuscript_path: Path) -> list[Path]:
    roots = [Path(p) for p in artifact_roots]
    if manuscript_path:
        roots += [manuscript_path.parent, manuscript_path.parent.parent]
    return roots


def _resolve_path(token: str, roots) -> Path | None:
    cand = [r / token for r in roots] + [r.parent / token for r in roots]
    for c in cand:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


_CMD_PREFIX = re.compile(
    r"^(?:\$\s*)?(?:python3?|pytest|uv|bash|sh|node|npm|git|curl|make|docker|"
    r"poetry|pip|conda)\s+(?:-\S+\s+)*")


def check_paths(md: str, roots):
    findings, total, resolved = [], 0, 0
    for m in _CODE_SPAN.finditer(md):
        raw = m.group(1).strip()
        is_cmd = bool(_CMD_PREFIX.match(raw))
        tok = _CMD_PREFIX.sub("", raw).strip()
        if is_cmd and "/" in tok and not _resolve_path(tok, roots):
            findings.append({"code": "PG1_command_script", "severity": "WARN",
                             "line": md[:m.start()].count("\n") + 1,
                             "detail": f"复现命令引用的脚本未找到：`{raw}`"})
            continue
        if "/" not in tok or tok.startswith(("http://", "https://")):
            continue
        if Path(tok).suffix.lower() not in PATH_SUFFIX and "*" not in tok:
            continue
        total += 1
        if _resolve_path(tok, roots):
            resolved += 1
        else:
            findings.append({
                "code": "PG1_phantom_path", "severity": "FAIL",
                "line": md[:m.start()].count("\n") + 1,
                "detail": f"引用的工件路径不可解析：`{tok}`（尝试根：{[str(r) for r in roots]}）",
            })
    return findings, total, resolved


def check_report_refs(md: str, report_ids):
    findings, total, resolved = [], 0, 0
    known = set(int(x) for x in report_ids)
    hits = [(m, int(m.group(1))) for m in _REPORT_REF.finditer(md)]
    hits += [(m, int(m.group(1))) for m in _REPORT_ID_FIELD.finditer(md)]
    for m, rid in hits:
        total += 1
        if rid in known:
            resolved += 1
        else:
            findings.append({
                "code": "PG2_phantom_report", "severity": "FAIL",
                "line": md[:m.start()].count("\n") + 1,
                "detail": f"引用的报告 #{rid} 不在报告台账中（已载入 {len(known)} 条 id）",
            })
    return findings, total, resolved


def check_figures(md: str, bundle: Path | None, extra_roots=()):
    findings, total, resolved = [], 0, 0
    for m in _IMAGE_LINK.finditer(md):
        ref = m.group(1)
        if ref.startswith(("http://", "https://", "data:")):
            continue
        total += 1
        cands = [((bundle / ref) if bundle else Path(ref))]
        cands += [Path(str(r)) / ref for r in extra_roots]
        cands += [Path(str(r)) / ref.split("/", 1)[-1] for r in extra_roots]
        ok = any(c.is_file() for c in cands)
        if ok:
            resolved += 1
        else:
            findings.append({
                "code": "PG3_phantom_figure", "severity": "FAIL",
                "line": md[:m.start()].count("\n") + 1,
                "detail": f"正文插图不存在：{ref}（bundle={bundle}，额外根={list(map(str, extra_roots))}）",
            })
    if bundle:
        fig_dir = bundle / "figures"
        if fig_dir.is_dir():
            for f in sorted(fig_dir.glob("*.png")):
                stem_key = re.match(r"(?:fig|figure|图)[_-]?(\d+)", f.stem, re.I)
                cited = f.stem in md or (stem_key and
                                         f"fig{stem_key.group(1)}" in md.lower())
                if not cited:
                    findings.append({
                        "code": "PG3_zero_citation_figure", "severity": "WARN",
                        "line": None,
                        "detail": f"图件零引用（H4 失效模式）：{f.name} 未被正文提及",
                    })
    return findings, total, resolved


def check_anchors(md: str, roots, tol_pp, tol_rel):
    """PG6: ``path#json.pointer`` / ``path.json:pointer`` style claims."""
    findings, total, resolved = [], 0, 0
    for rx in (_ANCHOR, _ANCHOR_COLON):
        for m in rx.finditer(md):
            tok, ptr = m.group(1).strip(), m.group(2).strip()
            if any(tok.startswith(p) for p in ("http", "mailto")):
                continue
            path = _resolve_path(tok, roots)
            if path is None:
                continue  # PG1 already reports the dangling path
            if path.suffix.lower() not in (".json",):
                continue
            total += 1
            stated = None
            tail = md[m.end():m.end() + 160]
            num = re.search(rf"({NUM})", tail)
            if num:
                stated = parse_number(num.group(1))
            value = _json_pointer(json.loads(path.read_text(encoding="utf-8")), ptr)
            if value is None:
                findings.append({
                    "code": "PG6_anchor_mismatch", "severity": "FAIL",
                    "line": md[:m.start()].count("\n") + 1,
                    "detail": f"锚点 {tok}#{ptr} 在工件中不存在",
                })
                continue
            if isinstance(value, (int, float)) and stated is not None:
                if _close(stated, float(value), tol_pp, tol_rel):
                    resolved += 1
                else:
                    findings.append({
                        "code": "PG6_anchor_mismatch", "severity": "FAIL",
                        "line": md[:m.start()].count("\n") + 1,
                        "detail": f"声明 {stated:g} ≠ 工件值 {value:g}（{tok}#{ptr}）",
                    })
            else:
                resolved += 1
    return findings, total, resolved


def _json_pointer(obj, ptr: str):
    node = obj
    for part in [p for p in re.split(r"[.\[\]/]+", ptr) if p]:
        if isinstance(node, list):
            try:
                node = node[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


# ──────────────────── report-sourced values (H2/H3 failure mode) ────────────────────

_SRC_HDR = re.compile(r"(?i)(source|来源|出处|证据|evidence|report)")


def check_report_values(md: str, report_texts: dict[int, str], tol_pp, tol_rel):
    """PG8: a row that cites ``报告 #N`` must have its numbers present in #N.

    This is the check that catches invented numbers (H2 WITHHELD / H3 synthetic
    data): the draft says "traceable to report #823", so the number has to be
    *in* report #823, not merely plausible.
    """
    findings, total, resolved = [], 0, 0
    for header, rows, line_no in iter_tables(md):
        src_cols = [i for i, h in enumerate(header) if _SRC_HDR.search(h)]
        if not src_cols:
            continue
        for r_off, row in enumerate(rows):
            cited = set()
            for i in src_cols:
                if i >= len(row):
                    continue
                for m in _REPORT_REF.finditer(row[i]):
                    cited.add(int(m.group(1)))
            if not cited:
                continue
            corpus: list[float] = []
            missing_src = False
            for rid in cited:
                if rid not in report_texts:
                    missing_src = True
                    continue
                corpus += numbers_in(report_texts[rid])
            if missing_src or not corpus:
                findings.append({
                    "code": "PG8_source_unavailable", "severity": "WARN",
                    "line": line_no + r_off + 1,
                    "detail": f"引用报告 {sorted(cited)} 的正文未载入，无法核验行内数值",
                })
                continue
            for i, cell in enumerate(row):
                if i in src_cols:
                    continue
                # structural references (§3.1, 图2, Table 4) are not claims
                cell = re.sub(r"[§#]\s*[\d.]+", " ", cell)
                cell = re.sub(r"(?i)(figure|fig\.?|table|图|表)\s*\d+", " ", cell)
                for claim in numbers_in(cell):
                    total += 1
                    if value_present(claim, corpus, rtol=max(tol_rel, 1e-6)):
                        resolved += 1
                    else:
                        findings.append({
                            "code": "PG8_value_not_in_source", "severity": "FAIL",
                            "line": line_no + r_off + 1,
                            "detail": (f"行内数值 {claim:g} 在被引报告 {sorted(cited)} "
                                       f"正文中不存在（H2/H3 编造风险）"),
                        })
    return findings, total, resolved


def artifact_provenance(md: str, stats: dict) -> list[dict]:
    """PG7: a results-bearing manuscript must cite artifact-level provenance.

    ``报告 #N`` alone is a *secondary* citation; blind review rejected the two
    prior drafts because no reviewer could walk from a number to a file.
    """
    table_numbers = 0
    for header, rows, _ln in iter_tables(md):
        for row in rows:
            if any(parse_number(c) is not None for c in row):
                table_numbers += 1
                break
    artifact_anchors = stats["paths"]["total"] + stats["anchors"]["total"]
    if table_numbers and artifact_anchors == 0:
        return [{
            "code": "PG7_no_artifact_provenance", "severity": "FAIL", "line": None,
            "detail": (f"正文有 {table_numbers} 张数值表，但全文 0 条工件级溯源"
                       "（既无 `path/to/artifact.json` 也无 `path#json.pointer`）；"
                       "仅有报告 id 不足以复核（历史上 H2 WITHHELD 即由此判废）"),
        }]
    return []


# ───────────────────────────── driver ─────────────────────────────


def check_manuscript(md: str, *, bundle=None, artifact_roots=(), report_ids=(),
                     tol_pp: float = 0.05, tol_rel: float = 0.005,
                     manuscript_path: Path | None = None,
                     report_texts: dict | None = None) -> dict:
    bundle = Path(bundle) if bundle else None
    roots = _candidate_roots(artifact_roots, manuscript_path)
    findings, stats = [], {}

    f, tot, res = check_paths(md, roots)
    findings += f
    stats["paths"] = {"total": tot, "resolved": res}

    f, tot, res = check_report_refs(md, report_ids)
    findings += f
    stats["report_refs"] = {"total": tot, "resolved": res}

    f, tot, res = check_figures(md, bundle, extra_roots=roots)
    findings += f
    stats["figures"] = {"total": tot, "resolved": res}

    err_rows = 0
    for header, rows, line_no in iter_tables(md):
        f, checked = check_table_arithmetic(header, rows, line_no, tol_pp, tol_rel)
        findings += f
        err_rows += checked
    stats["table_err_rows"] = err_rows

    f, n = check_inline_errors(md, tol_pp, tol_rel)
    findings += f
    stats["inline_claims"] = n

    f, tot, res = check_anchors(md, roots, tol_pp, tol_rel)
    findings += f
    stats["anchors"] = {"total": tot, "resolved": res}

    if report_texts is not None:
        f, tot, res = check_report_values(md, report_texts, tol_pp, tol_rel)
        findings += f
        stats["report_values"] = {"total": tot, "resolved": res}

    findings += artifact_provenance(md, stats)

    n_total = sum(v["total"] for k, v in stats.items()
                  if isinstance(v, dict) and "total" in v)
    n_res = sum(v["resolved"] for k, v in stats.items()
                if isinstance(v, dict) and "resolved" in v)
    fails = [x for x in findings if x["severity"] == "FAIL"]
    warns = [x for x in findings if x["severity"] == "WARN"]
    by_code: dict[str, int] = {}
    for x in findings:
        by_code[x["code"]] = by_code.get(x["code"], 0) + 1

    return {
        "verdict": "FAIL" if fails else ("WARN" if warns else "PASS"),
        "counts": {"fail": len(fails), "warn": len(warns), "by_code": by_code},
        "coverage": {
            "resolvable_claims": n_total,
            "resolved": n_res,
            "rate": round(n_res / n_total, 4) if n_total else 1.0,
        },
        "stats": stats,
        "findings": findings,
        "policy": {"tol_pp": tol_pp, "tol_rel": tol_rel,
                   "artifact_roots": [str(r) for r in roots]},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manuscript traceability gate")
    ap.add_argument("--manuscript", required=True)
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--artifact-root", action="append", default=[])
    ap.add_argument("--report-ids", default="",
                    help="comma separated ids, or @file with one id per line")
    ap.add_argument("--tol-pp", type=float, default=0.05)
    ap.add_argument("--tol-rel", type=float, default=0.005)
    ap.add_argument("--report-text-dir", default=None,
                    help="dir holding <id>.md dumps of cited reports (PG8)")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    mp = Path(a.manuscript)
    if not mp.is_file():
        print(f"manuscript not found: {mp}", file=sys.stderr)
        return 2
    ids: list[int] = []
    if a.report_ids:
        if a.report_ids.startswith("@"):
            ids = [int(x) for x in Path(a.report_ids[1:]).read_text().split() if x.strip().isdigit()]
        else:
            ids = [int(x) for x in re.split(r"[,\s]+", a.report_ids) if x.strip().isdigit()]

    texts = {}
    if a.report_text_dir:
        for f in Path(a.report_text_dir).glob("*.md"):
            if f.stem.isdigit():
                texts[int(f.stem)] = f.read_text(encoding="utf-8", errors="replace")

    out = check_manuscript(mp.read_text(encoding="utf-8"), bundle=a.bundle,
                           artifact_roots=a.artifact_root, report_ids=ids,
                           tol_pp=a.tol_pp, tol_rel=a.tol_rel, manuscript_path=mp,
                           report_texts=(texts if a.report_text_dir else None))
    out["manuscript"] = str(mp)
    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("verdict", "counts", "coverage")},
                     ensure_ascii=False, indent=2))
    for f in out["findings"][:40]:
        print(f"  [{f['severity']}] {f['code']} L{f['line']}: {f['detail']}")
    return 0 if out["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())