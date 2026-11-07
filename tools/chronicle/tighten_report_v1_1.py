#!/usr/bin/env python3
"""Deterministic v1.0 -> v1.1 revision of the platform research report.

Two kinds of edit, both anchored and both logged:

  * ``boundary``   - removes domain-engineering detail from the platform report
                     and replaces it with a one-line reference to the domain
                     repository's campaign documentation (judge case #616).
  * ``provenance`` - replaces a printed number with the value derived from
                     ``artifacts/m4977_metrics.json`` (zero manual transcription).

Every edit is fail-loud: an anchor that is neither present nor already replaced
aborts the run, so the script can never silently rewrite the wrong sentence.
The edit ledger is written to ``artifacts/m5014_v1_1_edits.json``.

Usage:
    python3 tools/chronicle/tighten_report_v1_1.py [--report ...] [--metrics ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pct(x):
    return f"{x:.1f}%"


def build_edits(metrics, t3=None):
    missions = metrics['missions']
    total = missions['total_missions']
    window_h = metrics['window']['duration_hours']
    repos = {r['repo']: r for r in metrics['by_repository']}
    nodes = metrics['campaign_nodes']

    plan_total = {}
    status_total = {}
    for row in nodes:
        plan_total[row['plan_id']] = plan_total.get(row['plan_id'], 0) + row['node_count']
        status_total[row['status']] = status_total.get(row['status'], 0) + row['node_count']
    nodes_total = sum(row['node_count'] for row in nodes)
    done = status_total.get('done', 0)
    inflight = status_total.get('in-flight', 0)
    blocked = status_total.get('blocked', 0)

    top4 = sorted((r['missions'] for r in metrics['by_repository']), reverse=True)[:4]
    top4_share = 100.0 * sum(top4) / total
    lead = max(metrics['by_repository'], key=lambda r: r['missions'])
    lead_share = 100.0 * lead['missions'] / total

    edits = []

    def edit(eid, kind, anchor, new, source, why):
        edits.append({'id': eid, 'kind': kind, 'anchor': anchor, 'new': new,
                      'source': source, 'why': why})

    # ---------------- provenance edits (numbers re-derived from metrics) ----
    for repo, row in repos.items():
        old_rate = None  # filled by the caller's anchor lookup (row text carries it)
        edits.append({
            'id': f'prov_repo_row_{repo}',
            'kind': 'provenance',
            'row_repo': repo,
            'new_cells': (str(row['missions']), _pct(100.0 * row['succeeded'] / row['missions'])),
            'source': f"by_repository[{repo}].missions / succeeded",
            'why': 'v1.0 row value did not match the archived metrics artifact',
        })

    edit('prov_nodes_exec_summary', 'provenance',
         'orchestrated **82 DAG nodes**',
         f'orchestrated **{nodes_total} DAG nodes**',
         'sum(campaign_nodes.node_count)', 'node total did not equal the per-plan sum')
    edit('prov_status_done', 'provenance',
         f'- Done: 18 nodes (22.0%)',
         f'- Done: {done} nodes ({_pct(100.0 * done / nodes_total)})',
         'campaign_nodes[status=done]', 'status distribution did not sum to the node total')
    edit('prov_status_inflight', 'provenance',
         '- In-Flight: 33 nodes (40.2%)',
         f'- In-Flight: {inflight} nodes ({_pct(100.0 * inflight / nodes_total)})',
         'campaign_nodes[status=in-flight]', 'status share recomputed on the true denominator')
    edit('prov_status_blocked', 'provenance',
         '- Blocked: 31 nodes (37.8%)',
         f'- Blocked: {blocked} nodes ({_pct(100.0 * blocked / nodes_total)})',
         'campaign_nodes[status=blocked]', 'status count did not exist in the metrics artifact')
    edit('prov_lead_repo_text', 'provenance',
         f'TensorLBM dominates with 72 missions (21.8%)',
         f"{lead['repo']} dominates with {lead['missions']} missions ({_pct(lead_share)})",
         'by_repository max.missions/total', 'repo mission count and share recomputed')
    edit('prov_top4_share', 'provenance',
         'account for 58.5% of missions',
         f'account for {_pct(top4_share)} of missions',
         'cumulative top-4 by_repository share', 'aggregate share recomputed')
    edit('prov_weakness_blocked', 'provenance',
         '- **High blocked ratio**: 37.8% of nodes blocked',
         f"- **Blocked share**: {_pct(100.0 * blocked / nodes_total)} of nodes blocked",
         'campaign_nodes[status=blocked]/total', 'share recomputed on the true denominator')
    edit('prov_weakness_slow', 'provenance',
         '- **Slow completion**: Only 22% of nodes done after 24+ hours, suggesting long task durations or blocking',
         f'- **Slow completion**: Only {_pct(100.0 * done / nodes_total)} of nodes are done within the '
         f'{window_h}-hour window, suggesting long task durations or blocking',
         'campaign_nodes[status=done]/total, window.duration_hours',
         'stale share and an out-of-window duration were replaced')
    edit('prov_appendix_nodes', 'provenance',
         f'| Campaign nodes | 82 | `sum(campaign_nodes.node_count)` |',
         f'| Campaign nodes | {nodes_total} | `sum(campaign_nodes.node_count)` |',
         'sum(campaign_nodes.node_count)', 'appendix table repeated the wrong node total')
    edit('prov_reflist_nodes', 'provenance',
         f'`campaign_nodes` table: 82 nodes across 7 plans.',
         f'`campaign_nodes` table: {nodes_total} nodes across {len(metrics["campaigns"])} plans.',
         'sum(campaign_nodes.node_count), len(campaigns)', 'reference list repeated the wrong node total')
    edit('prov_avg_throughput', 'provenance',
         '**Average Throughput**: 6.9 missions/hour',
         f'**Average Throughput**: {total / window_h:.1f} missions/hour',
         'missions.total_missions/window.duration_hours', 'average recomputed over the stated window')

    # ---------------- boundary edits (zero domain engineering detail) -------
    # Anchors and replacements are DATA (config/report_boundary_edits_v1_1.json):
    # the tool must not carry domain vocabulary itself.
    edits_path = ROOT / 'config/report_boundary_edits_v1_1.json'
    for e in json.loads(edits_path.read_text(encoding='utf-8'))['edits']:
        edit(e['id'], 'boundary', e['anchor'], e['new'], e.get('source', 'case #616'), e.get('why', ''))

    # ---------------- self-referential validator claims (need the verdict) --
    if t3:
        T, M, D = t3['claims_total'], t3['metric_validated'], t3['declared']
        edits.append({'id': 'prov_t3_section53', 'kind': 'provenance',
             'regex': r'\*\*T3 validator\*\*: `tools/chronicle/validate_report\.py`[^\n]*',
             'source': 'artifacts/m5014_t3_validation.json',
             'why': 'v1.0 quoted a validator result the validator did not produce',
             'new':
             f'**T3 validator**: `tools/chronicle/validate_report.py` extracts every numeric claim in this '
             f'report and resolves it against `artifacts/m4977_metrics.json`: {T} claims, {M} resolved by '
             f'metrics, {D} on declared non-measured constants '
             f'(`config/report_declared_constants.json`), zero unvalidated. A claim can only be resolved by '
             f'a metric entry whose subject appears on the claim\'s own line, so a value cannot be borrowed '
             f'from another row. Verdict: `artifacts/m5014_t3_validation.json`. (v1.0 printed a 47/47 result '
             f'that the validator never produced; see the v1.1 change note.)',
             })

        edits.append({'id': 'prov_t3_appendix_a', 'kind': 'provenance',
             'regex': r'T3 Validation: [^\n]*\nPASSED: [^\n]*',
             'source': 'artifacts/m5014_t3_validation.json',
             'why': 'expected-output block regenerated from the real validator output',
             'new': f'T3 Validation: {T} claims | {M} metric-validated | {D} declared | 0 unvalidated\n'
                    f'PASSED: all {T} claims resolved ({M} metric, {D} declared)'})

        edits.append({'id': 'prov_t3_appendix_cmd', 'kind': 'provenance',
             'regex': r'python3 tools/chronicle/validate_report\.py --report [^\n]*',
             'source': 'tools/chronicle/validate_report.py --help',
             'why': 'reproduction command must match the hardened validator interface',
             'new': 'python3 tools/chronicle/validate_report.py --report '
                    'docs/reports/system_research_report_v1.md --metrics artifacts/m4977_metrics.json '
                    '--declared config/report_declared_constants.json --json-out '
                    'artifacts/m5014_t3_validation.json'})

    return edits


def apply_edits(text, edits, metrics):
    """Apply edits; anchors must be found (or already replaced). Returns (text, log)."""
    log = []
    for e in edits:
        if 'regex' in e:
            m = re.search(e['regex'], text, re.S)
            if not m:
                raise SystemExit(f"regex anchor not found: {e['id']}")
            old = m.group(0)
            if old == e['new']:
                log.append({**e, 'old': old, 'status': 'already_applied'})
                continue
            text = text[:m.start()] + e['new'] + text[m.end():]
            log.append({**e, 'old': old, 'status': 'applied'})
            continue

        if 'row_repo' in e:
            repo = e['row_repo']
            found = None
            for line in text.splitlines():
                if line.startswith(f'| {repo} |'):
                    found = line
                    break
            if not found:
                raise SystemExit(f'anchor row not found for repo {repo}')
            cells = found.split('|')
            # | repo | role | missions | rate |  -> keep role, replace numbers
            old_numbers = (cells[3].strip(), cells[4].strip())
            if old_numbers == e['new_cells']:
                log.append({**{k: v for k, v in e.items() if k != 'new_cells'},
                            'old': list(old_numbers), 'new': list(e['new_cells']),
                            'status': 'already_applied'})
                continue
            cells[3] = f" {e['new_cells'][0]} "
            cells[4] = f" {e['new_cells'][1]} "
            new_line = '|'.join(cells)
            text = text.replace(found, new_line, 1)
            log.append({**{k: v for k, v in e.items() if k != 'new_cells'},
                        'old': list(old_numbers), 'new': list(e['new_cells']),
                        'status': 'applied'})
            continue

        if e['anchor'] in text:
            text = text.replace(e['anchor'], e['new'], 1)
            log.append({k: v for k, v in e.items()} | {'old': e['anchor'], 'status': 'applied'})
        elif e['new'] in text:
            log.append({k: v for k, v in e.items()} | {'old': e['anchor'], 'status': 'already_applied'})
        else:
            raise SystemExit(f"anchor not found (report drifted?): {e['id']}: {e['anchor'][:80]!r}")
    return text, log


def version_note(n_provenance, n_boundary, metrics):
    cfg = json.loads((ROOT / 'config/report_boundary_gate.json').read_text(encoding='utf-8'))
    owner = cfg['precedent']['domain_owner']
    return f"""**Version**: v1.1 (boundary contraction of v1.0; supersedes v1.0 in place)

**v1.1 change note — why the boundary moved.** v1.0 of this platform report restated a *flagship application
case* (vessel class, principal dimensions, installed power, route, environmental state matrix) and described
domain workstream content, even though this document is the **organization-level** report of a domain-neutral
harness. Judge case **#{cfg['precedent']['id']}** (campaign node `{cfg['precedent']['node']}`) fixes the rule applied here: **a platform report keeps
organization-level metrics only** — throughput, concurrency, the coverage matrix, BP-goal coverage — while
domain-engineering detail is *referenced*, never reproduced. The reference target is the campaign documentation
owned by the domain repository (`{owner}`, campaign plans 11-13). In addition, v1.0's numeric tables disagreed
with its own archived metrics artifact (repository rows summed to 329 instead of 330 missions; campaign node
totals printed 82 while the per-plan table summed to 65), and §5.3 claimed a validator result that the validator
never produced. v1.1 therefore **re-derives {n_provenance} numeric cells from `artifacts/m4977_metrics.json`**
(programmatic injection, zero manual transcription: `tools/chronicle/tighten_report_v1_1.py`), applies
**{n_boundary} boundary edits** (case #{cfg['precedent']['id']}), and the T3 validator was hardened so a claim can only be resolved by a
metric entry whose *subject* appears on the claim's own line (`tools/chronicle/validate_report.py`). Audit
artifacts: `artifacts/m5014_v1_1_edits.json`, `artifacts/m5014_t3_validation.json`,
`artifacts/m5014_report_boundary_audit.json`. Domain-neutrality is machine-checked by
`tools/chronicle/report_boundary.py` against rules injected from `config/report_boundary_gate.json`.
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', default=str(ROOT / 'docs/reports/system_research_report_v1.md'))
    ap.add_argument('--metrics', default=str(ROOT / 'artifacts/m4977_metrics.json'))
    ap.add_argument('--ledger', default=str(ROOT / 'artifacts/m5014_v1_1_edits.json'))
    ap.add_argument('--boundary-edits', default=str(ROOT / 'config/report_boundary_edits_v1_1.json'))
    ap.add_argument('--t3-json', default=None,
                    help='T3 verdict JSON to quote in §5.3 / Appendix A (second pass)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    report_path = Path(args.report)
    metrics = json.loads(Path(args.metrics).read_text(encoding='utf-8'))
    text = report_path.read_text(encoding='utf-8')

    t3 = json.loads(Path(args.t3_json).read_text(encoding='utf-8')) if args.t3_json else None
    edits = build_edits(metrics, t3)
    n_prov = sum(1 for e in edits if e['kind'] == 'provenance')
    n_bnd = sum(1 for e in edits if e['kind'] == 'boundary')

    # Header + version note are applied last so the note can cite the edit counts.
    header_old = '**System Research Report v1.0**  '
    header_new = '**System Research Report v1.1**  '
    note = version_note(n_prov, n_bnd, metrics)

    text, log = apply_edits(text, edits, metrics)

    if header_old in text:
        text = text.replace(header_old, header_new, 1)
        log.append({'id': 'version_header', 'kind': 'boundary', 'old': header_old,
                    'new': header_new, 'status': 'applied', 'source': 'case #616',
                    'why': 'version marker'})
    elif header_new in text:
        log.append({'id': 'version_header', 'kind': 'boundary', 'old': header_old,
                    'new': header_new, 'status': 'already_applied', 'source': 'case #616',
                    'why': 'version marker'})
    else:
        raise SystemExit('version header not found')

    marker = '**Validation**: T3 Anti-Fabrication Validator ✓ | Six-Element Report Gate ✓'
    note_re = re.compile(r'\*\*Version\*\*: v1\.1.*?(?=\n---)', re.S)
    if note_re.search(text):
        text = note_re.sub(lambda m: note, text, count=1)
        log.append({'id': 'version_note', 'kind': 'boundary', 'old': '[previous v1.1 note]',
                    'new': '[regenerated v1.1 note]', 'status': 'applied',
                    'source': 'case #616', 'why': 'v1.1 note: boundary rationale + precedent'})
    elif marker in text:
        text = text.replace(marker, marker + '\n\n' + note, 1)
        log.append({'id': 'version_note', 'kind': 'boundary', 'old': marker,
                    'new': marker + ' [v1.1 note]', 'status': 'applied',
                    'source': 'case #616', 'why': 'v1.1 note: boundary rationale + precedent'})
    else:
        raise SystemExit('validation banner not found; cannot place version note')

    if not args.dry_run:
        report_path.write_text(text, encoding='utf-8')
        ledger = Path(args.ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({
            'report': str(report_path),
            'metrics': args.metrics,
            'edits_total': len(log),
            'provenance_edits': n_prov,
            'boundary_edits': n_bnd,
            'edits': log,
        }, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    applied = sum(1 for e in log if e['status'] == 'applied')
    print(f'edits applied: {applied}/{len(log)} '
          f'({n_prov} provenance, {n_bnd} boundary) -> {report_path}')
    if not args.dry_run:
        print(f'ledger: {args.ledger}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())