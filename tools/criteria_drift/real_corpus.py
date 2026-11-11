"""Real-text corpus: acceptance criteria harvested from live campaign node exports.

Source: ``data/campaigns/*/nodes.json`` - machine exports of ``campaign_nodes``
(plan_id, node, mission_id, repo, status, acceptance_criteria, goal_excerpt).
The criteria text is human-written Chinese/English by the agents that ran those
campaigns; nobody wrote it for this auditor.  It is therefore the external
validity check for the parser and comparator.

Mutations are applied *by construction* to the real text, so the label of every
case is known without the detector:

=====================  =============================================  =========
family                 construction                                   label
=====================  =============================================  =========
``identity``           report = proposal verbatim                     no drift
``reorder``            report criteria list shuffled                  no drift
``unit_reformat``      %↔ratio, ≤↔<=, extra spaces                    no drift
``append_stricter``    one extra criterion appended to report         no drift
``relax_12``           threshold widened by +12%                      drift
``relax_05``           threshold widened by +5%  (below 10% rule)     no drift
``drop_undeclared``    criterion removed, no waiver                   drift
``drop_declared``      criterion removed + waiver(reason, approver)   no drift
``op_downgrade``       strict ``<``/``>`` -> ``<=``/``>=``            drift
``kind_swap``          criterion replaced by another kind             drift
=====================  =============================================  =========
"""
from __future__ import annotations

import glob
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import model
from .corpus_synth import _fmt_num, parsed_relaxation
from . import parser
from .parser import comparator_directions, parse_with_cn_order

DEFAULT_ROOTS = ("data/campaigns/*/nodes.json",)
DOC_ROOTS = ("docs/*.md",)
_HEADING = re.compile(r"^#{1,6}\s*(?P<title>.*)$")
_CRIT_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)、]|\|)\s*(?P<body>.+)$")
_COMPARATOR = re.compile(r"≤|≥|<|>|<=|>=|不超过|不低于|至少|至多|低于|高于")


@dataclass
class RealCriterion:
    campaign: str
    node: str
    index: int
    text: str
    criterion: model.Criterion


@dataclass
class RealCase:
    case_id: str
    family: str
    proposal: List[str]
    report: List[str]
    waivers: List[Dict[str, str]]
    label_drift: bool
    source: str
    detail: str = ""


def harvest(root: Path, patterns: Sequence[str] = DEFAULT_ROOTS,
            include_docs: bool = True) -> List[RealCriterion]:
    out: List[RealCriterion] = []
    seen = set()
    for pat in patterns:
        for path in sorted(glob.glob(str(root / pat))):
            try:
                doc = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for node in doc.get("nodes", []):
                crits = node.get("acceptance_criteria") or []
                for i, text in enumerate(crits):
                    if not isinstance(text, str) or not text.strip():
                        continue
                    key = text.strip()
                    if key in seen:          # de-duplicate identical criteria
                        continue
                    seen.add(key)
                    out.append(RealCriterion(
                        campaign=doc.get("campaign") or Path(path).stem,
                        node=node.get("node") or "",
                        index=i,
                        text=text.strip(),
                        criterion=parse_with_cn_order(text.strip()),
                    ))
    if include_docs:
        seen = {r.text for r in out}
        for rc in harvest_markdown(root):
            if rc.text not in seen:
                seen.add(rc.text)
                out.append(rc)
    return out


def harvest_markdown(root: Path, patterns: Sequence[str] = DOC_ROOTS,
                     max_len: int = 140) -> List[RealCriterion]:
    """Harvest criteria written in human/agent-authored markdown sections.

    Only lines inside a section whose heading mentions 判据/criteria/验收 are
    considered; the first table cell (or the bullet body) is taken as the
    criterion text.  Lines without a comparator + number are dropped: this is a
    *criteria* corpus, not a prose corpus, and a wrong extraction would pollute
    the mutation benchmark.
    """
    out: List[RealCriterion] = []
    seen = set()
    for pat in patterns:
        for path in sorted(glob.glob(str(root / pat))):
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            in_section = False
            for line in text.splitlines():
                m = _HEADING.match(line)
                if m:
                    in_section = bool(re.search(r"判据|criteria|验收", m.group("title"), re.I))
                    continue
                if not in_section:
                    continue
                lm = _CRIT_LINE.match(line)
                if not lm:
                    continue
                body = lm.group("body").strip()
                if body.startswith("---"):
                    continue
                if "|" in body:
                    body = body.split("|")[0].strip()
                body = body.strip("*` ")
                if not body or len(body) > max_len:
                    continue
                if not _COMPARATOR.search(body):
                    continue
                parsed = parse_with_cn_order(body)
                if parsed.value is None or parsed.op == model.PRESENCE_OP:
                    continue
                if body in seen:
                    continue
                seen.add(body)
                out.append(RealCriterion(campaign=Path(path).stem, node="docs", index=len(out),
                                         text=body, criterion=parsed))
    return out


def _split_compound(text: str) -> Optional[str]:
    """Split a compound criterion at its conjunction, keeping the first half."""
    m = re.split(r"且|并且|而且|同时|以及|\band\b|&", text, maxsplit=1)
    if len(m) < 2:
        return None
    head = m[0].strip().rstrip("，,、")
    if head.count("（") > head.count("）"):
        head = head  # unbalanced paren is kept: the Parser reads what is written
    return head if head else None


def _render_like(c: model.Criterion, value: float, *, op: Optional[str] = None,
                 percent: bool = False, tolerance: Optional[float] = None) -> str:
    op = op or c.op
    subject = c.subject or c.raw
    if c.unit == model.UNIT_FRACTION:
        if percent:
            num, suf = f"{value * 100:.4g}", "%"
        else:
            num, suf = f"{value:.4g}", ""
    elif c.unit == model.UNIT_COUNT:
        num, suf = f"{int(round(value))}", "例"
    elif c.unit == model.UNIT_ORDER:
        num, suf = f"{value:g}", "阶"
    else:
        num, suf = f"{value:g}", ""
    text = f"{subject}{op}{num}{suf}"
    if tolerance is not None:
        tol_num, tol_suf = _fmt_num(tolerance, model.UNIT_FRACTION)
        text += f"±{tol_num}{tol_suf}"
    return text


def _relaxed(c: model.Criterion, factor: float) -> float:
    assert c.value is not None
    if model.operator_direction(c.op) == "upper":
        return c.value * factor
    return c.value / factor


def build_cases(criteria: Sequence[RealCriterion], seed: int = 20261112,
                index: Optional[Sequence[RealCriterion]] = None) -> List[RealCase]:
    """One case per (criterion, family) so every family is measured separately."""
    rng = random.Random(seed)
    pool = list(index if index is not None else criteria)
    cases: List[RealCase] = []
    for i, rc in enumerate(pool):
        c = rc.criterion
        src = f"{rc.campaign}/{rc.node}#{rc.index}"
        base = [rc.text]
        if c.value is None or c.op == model.PRESENCE_OP:
            continue
        # identity
        cases.append(RealCase(f"real-{i:03d}-identity", "identity", base, list(base), [], False, src,
                              "verbatim copy"))
        # compound criteria: a single-bound re-render *drops* one constraint, so
        # it is a narrowing, not a benign re-format
        if parser.is_compound(rc.text):
            head = _split_compound(rc.text)
            if head:
                parsed_head = parse_with_cn_order(head)
                if parsed_head.value is not None:
                    cases.append(RealCase(f"real-{i:03d}-compound", "compound_split", base, [head],
                                          [], True, src, f"compound -> single bound: {head}"))
                    continue
        # unit reformat / op spelling
        if c.unit == model.UNIT_FRACTION:
            alt = _render_like(c, c.value, percent=False)
        else:
            alt = _render_like(c, c.value).replace("≤", "<=").replace("≥", ">=")
        cases.append(RealCase(f"real-{i:03d}-unit", "unit_reformat", base, [alt], [], False, src,
                              f"{rc.text} -> {alt}"))
        # relax +12% / +5%: the *label* is the relaxation actually written in the
        # rendered text (a zero-scale threshold such as ``>= 0`` cannot be
        # relaxed multiplicatively, so it must not be labelled as drift)
        for factor, fam, default_label in ((1.12, "relax_12", True), (1.05, "relax_05", False)):
            new_val = _relaxed(c, factor)
            text = _render_like(c, new_val, percent=(c.unit == model.UNIT_FRACTION))
            delta = parsed_relaxation(rc.text, text)
            label = bool(delta is not None and delta > 0.10)
            cases.append(RealCase(f"real-{i:03d}-{fam}", fam, base, [text], [], label, src,
                                  f"{c.value:g} -> {new_val:g} (rendered relax={delta})"))
        # undeclared drop
        cases.append(RealCase(f"real-{i:03d}-drop", "drop_undeclared", base, [], [], True, src,
                              "criterion removed"))
        # declared drop
        cases.append(RealCase(f"real-{i:03d}-dropw", "drop_declared", base, [], [{
            "criterion": rc.text,
            "reason": "参考值来源更新，需换锚后重测",
            "approved_by": "gate-owner",
        }], False, src, "criterion removed with waiver"))
        # operator downgrade (only meaningful for a strict operator)
        if c.op in ("<", ">"):
            new_op = "<=" if c.op == "<" else ">="
            text = _render_like(c, c.value or 0.0, op=new_op,
                                percent=(c.unit == model.UNIT_FRACTION))
            cases.append(RealCase(f"real-{i:03d}-opdown", "op_downgrade", base, [text], [], True, src,
                                  f"{c.op} -> {new_op}"))
        # kind swap: take a criterion of a different kind family from the pool
        other = [o for o in criteria
                 if o.criterion.op != model.PRESENCE_OP and o.criterion.value is not None
                 and _family(o.criterion) != _family(c)]
        if other:
            o = rng.choice(other)
            cases.append(RealCase(f"real-{i:03d}-swap", "kind_swap", base, [o.text], [], True, src,
                                  f"{rc.text} -> {o.text}"))

    # multi-criterion document cases (reorder / append), 40 sampled docs
    by_node: Dict[str, List[RealCriterion]] = {}
    for rc in criteria:
        by_node.setdefault(f"{rc.campaign}/{rc.node}", []).append(rc)
    keys = sorted(by_node)[:40]
    for n, key in enumerate(keys):
        docs = [rc for rc in by_node[key] if rc.criterion.value is not None]
        if len(docs) < 2:
            continue
        base = [rc.text for rc in docs]
        shuffled = list(base)
        rng.shuffle(shuffled)
        cases.append(RealCase(f"real-doc-{n:02d}-reorder", "reorder", base, shuffled, [], False, key,
                              "criteria reordered"))
        extra = _append_candidate(criteria, docs)
        if extra:
            cases.append(RealCase(f"real-doc-{n:02d}-append", "append_stricter", base, base + [extra], [],
                                  False, key, f"appended {extra}"))
    return cases


def _family(c: model.Criterion) -> str:
    from .matching import kind_family
    return kind_family(c)


def _append_candidate(criteria: Sequence[RealCriterion], docs: Sequence[RealCriterion]) -> Optional[str]:
    used = {rc.text for rc in docs}
    for rc in criteria:
        if rc.text in used or rc.criterion.value is None:
            continue
        if rc.criterion.kind == model.KIND_COUNT:
            return rc.text
    return None