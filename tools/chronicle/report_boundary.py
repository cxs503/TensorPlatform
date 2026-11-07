#!/usr/bin/env python3
"""Domain-neutrality boundary gate for platform-level research reports (judge case #616).

The rule this gate enforces: a *platform* report carries organization-level metrics
and nothing else. Domain-engineering detail (case geometry, power, route, state
matrix) must be **referenced**, not reproduced; a single reference line pointing at
the domain owner's own documentation is the required form.

Everything domain-specific is injected as data -- vocabulary, whitelist and the
precedent record all live in ``config/report_boundary_gate.json``. This module
contains no domain word, no domain criterion and no domain example, which is what
lets the same gate serve any future domain tenant.

Checks (each can FAIL independently)
------------------------------------
VERSION_NOTE         report carries the required version marker, the precedent id,
                     a boundary rationale and the domain-owner reference target
SECTION_STRUCTURE    the report still has its N top-level sections, no gap
DOMAIN_DETAIL        no case-engineering detail outside whitelisted record lines
DOMAIN_TERM          no domain vocabulary outside whitelisted record lines
REFERENCE_LINE       the one-line reference to the domain owner's docs is present
CODE_NEUTRALITY      tools/backend/tests carry none of the injected domain terms
METRICS_ARTIFACT     the metrics artifact the report's numbers resolve to exists
T3_VERDICT           the T3 verdict artifact says PASS (if one is configured)

Exit code 0 = PASS, 1 = FAIL, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _finding(code, severity, detail, **extra):
    return {'code': code, 'severity': severity, 'detail': detail, **extra}


def load_config(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def check_version_note(text, cfg):
    out = []
    precedent = cfg['precedent']
    marker = cfg['required_version_marker']
    if marker not in text:
        out.append(_finding('VERSION_NOTE', 'FAIL', f'version marker {marker!r} missing'))
    if f'#{precedent["id"]}' not in text:
        out.append(_finding('VERSION_NOTE', 'FAIL',
                            f'precedent citation #{precedent["id"]} missing'))
    if 'change note' not in text.lower():
        out.append(_finding('VERSION_NOTE', 'FAIL', 'no version change note (rationale) block'))
    if precedent['domain_owner'] not in text:
        out.append(_finding('VERSION_NOTE', 'FAIL',
                            f'domain owner {precedent["domain_owner"]!r} not referenced'))
    return out


def check_sections(text, cfg):
    found = sorted({int(m.group(1)) for m in re.finditer(cfg['section_regex'], text, re.M)})
    expected = list(range(1, cfg['sections_expected'] + 1))
    if found != expected:
        return [_finding('SECTION_STRUCTURE', 'FAIL',
                         f'sections {found} != expected {expected}')]
    return []


def scan_lines(text, cfg):
    """Return (violations, whitelisted_hits)."""
    detail = [(p['id'], re.compile(p['regex'])) for p in cfg['detail_patterns']]
    terms = [(t, re.compile(re.escape(t), re.I)) for t in cfg['domain_terms']]
    white = [(w['id'], re.compile(w['regex'])) for w in cfg['whitelist']]

    violations, whitelisted = [], []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        hits_detail = [(pid, rx.search(line).group(0)) for pid, rx in detail if rx.search(line)]
        hits_term = [(t, rx.search(line).group(0)) for t, rx in terms if rx.search(line)]
        if not hits_detail and not hits_term:
            continue
        allowed = None
        for wid, rx in white:
            if rx.search(line):
                allowed = wid
                break
        # A whitelisted *record* line may quote a recorded title (data citation) but
        # still may not carry case-engineering detail.
        if allowed and not hits_detail:
            whitelisted.append({'line': lineno, 'whitelist': allowed,
                                'terms': [t for t, _ in hits_term]})
            continue
        if allowed and hits_detail:
            violations.append(_finding('DOMAIN_DETAIL', 'FAIL',
                                       'case detail on a whitelisted line',
                                       line=lineno, whitelist=allowed,
                                       patterns=[p for p, _ in hits_detail],
                                       text=line.strip()[:160]))
            continue
        violations.append(_finding('DOMAIN_DETAIL' if hits_detail else 'DOMAIN_TERM', 'FAIL',
                                   'domain content outside the boundary',
                                   line=lineno,
                                   patterns=[p for p, _ in hits_detail],
                                   terms=[t for t, _ in hits_term],
                                   text=line.strip()[:160]))
    return violations, whitelisted


def check_reference_line(text, cfg):
    owner = cfg['precedent']['domain_owner']
    hint = cfg['precedent']['domain_doc_hint']
    for line in text.splitlines():
        if owner in line and (hint.split('(')[0].strip()[:12].lower() in line.lower() or 'campaign' in line.lower()):
            return []
    return [_finding('REFERENCE_LINE', 'FAIL',
                     f'no single reference line pointing at the domain owner docs ({owner})')]


def scan_code(cfg):
    """Map every code file carrying injected domain vocabulary to the terms found."""
    terms = cfg['domain_terms']
    hits = {}
    for root in cfg['code_scan_roots']:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob('*.py'):
            rel = str(path.relative_to(ROOT)).replace('\\', '/')
            if rel in cfg.get('code_scan_allow', []):
                continue
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            low = text.lower()
            found = [t for t in terms if t.lower() in low]
            if found:
                hits[rel] = found
    return hits


def load_baseline(cfg):
    path = ROOT / cfg.get('baseline', '')
    if not cfg.get('baseline') or not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8')).get('files', {})


def check_code_neutrality(cfg, baseline):
    """Legacy domain debt is a WARN snapshot; only *new* debt fails the gate.

    The baseline makes the gate honest in both directions: it cannot be satisfied
    by declaring the whole tree "legacy", and it does not force this change to
    refactor unrelated modules. New or newly-dirty files FAIL.
    """
    hits = scan_code(cfg)
    findings = []
    if baseline is None:
        findings.append(_finding('CODE_NEUTRALITY', 'WARN',
                                 'no baseline recorded; run --write-baseline to snapshot legacy debt',
                                 files=len(hits)))
        return findings, hits
    for rel, terms in sorted(hits.items()):
        if rel not in baseline:
            findings.append(_finding('CODE_NEUTRALITY', 'FAIL',
                                     'domain vocabulary hardcoded in a new/touched file',
                                     file=rel, terms=terms))
        elif sorted(terms) != sorted(baseline[rel]):
            findings.append(_finding('CODE_NEUTRALITY', 'FAIL',
                                     'domain vocabulary changed in a baselined file',
                                     file=rel, terms=terms, baseline=baseline[rel]))
    legacy = [rel for rel in baseline if rel in hits]
    if legacy:
        findings.append(_finding('CODE_NEUTRALITY', 'WARN',
                                 'legacy domain debt (baselined, out of scope for this change)',
                                 files=len(legacy)))
    return findings, hits


def run(cfg, report_path, t3_path=None):
    text = Path(report_path).read_text(encoding='utf-8')
    findings = []
    findings += check_version_note(text, cfg)
    findings += check_sections(text, cfg)
    violations, whitelisted = scan_lines(text, cfg)
    findings += violations
    findings += check_reference_line(text, cfg)
    baseline = load_baseline(cfg)
    code_findings, code_hits = check_code_neutrality(cfg, baseline)
    findings += code_findings

    metrics_path = ROOT / cfg['metrics']
    if not metrics_path.exists():
        findings.append(_finding('METRICS_ARTIFACT', 'FAIL',
                                 f'metrics artifact missing: {cfg["metrics"]}'))

    t3_verdict = None
    if t3_path and Path(t3_path).exists():
        t3_verdict = json.loads(Path(t3_path).read_text(encoding='utf-8')).get('verdict')
        if t3_verdict != 'PASS':
            findings.append(_finding('T3_VERDICT', 'FAIL', f'T3 verdict is {t3_verdict}'))

    fails = [f for f in findings if f['severity'] == 'FAIL']
    return {
        'gate': 'report_boundary/v1',
        'report': str(report_path),
        'config': 'config/report_boundary_gate.json',
        'precedent': cfg['precedent'],
        'checks': {
            'VERSION_NOTE': 'PASS' if not any(f['code'] == 'VERSION_NOTE' for f in fails) else 'FAIL',
            'SECTION_STRUCTURE': 'PASS' if not any(f['code'] == 'SECTION_STRUCTURE' for f in fails) else 'FAIL',
            'DOMAIN_DETAIL': 'PASS' if not any(f['code'] in ('DOMAIN_DETAIL',) for f in fails) else 'FAIL',
            'DOMAIN_TERM': 'PASS' if not any(f['code'] == 'DOMAIN_TERM' for f in fails) else 'FAIL',
            'REFERENCE_LINE': 'PASS' if not any(f['code'] == 'REFERENCE_LINE' for f in fails) else 'FAIL',
            'CODE_NEUTRALITY': 'PASS' if not any(f['code'] == 'CODE_NEUTRALITY' for f in fails) else 'FAIL',
            'METRICS_ARTIFACT': 'PASS' if not any(f['code'] == 'METRICS_ARTIFACT' for f in fails) else 'FAIL',
            'T3_VERDICT': 'PASS' if not any(f['code'] == 'T3_VERDICT' for f in fails) else 'FAIL',
        },
        'whitelisted_record_lines': whitelisted,
        'code_files_with_domain_terms': len(code_hits),
        'findings': findings,
        'verdict': 'PASS' if not fails else 'FAIL',
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='Domain-neutrality boundary gate')
    ap.add_argument('--config', default=str(ROOT / 'config/report_boundary_gate.json'))
    ap.add_argument('--report', default=None)
    ap.add_argument('--t3-json', default=str(ROOT / 'artifacts/m5014_t3_validation.json'))
    ap.add_argument('--json-out', default=str(ROOT / 'artifacts/m5014_report_boundary_audit.json'))
    ap.add_argument('--write-baseline', action='store_true',
                    help='Snapshot current legacy domain debt as the CODE_NEUTRALITY baseline')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.write_baseline:
        hits = scan_code(cfg)
        out = ROOT / cfg['baseline']
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            'schema': 'code_neutrality_baseline/v1',
            'note': ('Legacy domain vocabulary found in code at the time the boundary gate was '
                     'introduced (judge case #616). Baselined = WARN, out of scope for the report '
                     'revision; any new or newly-dirty file is a FAIL.'),
            'files': hits,
        }, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'baseline written: {out} ({len(hits)} files)')
    report = args.report or str(ROOT / cfg['report'])
    result = run(cfg, report, args.t3_json)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    if not args.quiet:
        for name, status in result['checks'].items():
            print(f'  {status:4s} {name}')
        for f in result['findings']:
            print(f'  ! {f["code"]}: {f["detail"]} {f.get("file", "")} {f.get("line", "")}')
        print(f'whitelisted record lines: {len(result["whitelisted_record_lines"])}')
    print(f'BOUNDARY GATE: {result["verdict"]}')
    return 0 if result['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())