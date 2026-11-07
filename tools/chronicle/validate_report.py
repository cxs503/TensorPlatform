#!/usr/bin/env python3
"""
T3 Anti-Fabrication Validator for research reports.

Checks that all numeric claims in the report are backed by extracted metrics.

Two validation routes, and one hard failure:

  (a) METRIC  - claim value resolves to an entry of the provenance index that is
                *derived* from the metrics artifact (see ``build_provenance_index``).
                When the claim's source line names a subject that the index knows
                (repository, model, plan, node status), only entries carrying that
                subject are eligible -- this is what stops "TensorLBM | 72" from
                silently borrowing another row's 72.
  (b) DECLARED - claim value is an explicitly declared, non-measured constant
                (a stated bound or a gate threshold), listed with a reason in a
                declared-constants JSON. Declared claims are reported separately
                and never counted as metric-validated.
  (c) anything else -> UNVALIDATED -> gate FAIL.

Usage:
    python3 validate_report.py --report report.md --metrics metrics.json \
        [--declared config/report_declared_constants.json] [--strict] \
        [--json-out artifacts/xxx.json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Tolerance used when comparing a printed value with a derived value.
# Reports print at most one decimal, so half an ulp is 0.05; a hair of slack
# absorbs binary float noise (26.343 -> printed 26.3) without accepting a
# genuinely different number.
TOL = 0.0501


def _num(token):
    token = token.replace(',', '')
    return float(token) if '.' in token else int(token)


def extract_numbers_from_report(report_text):
    r"""Extract all numeric claims from the report.

    ``(?<![\d.])`` keeps a heading number ("### 4.2 Repository Distribution")
    and the fractional tail of a rate ("6.9 missions/hour" -> 9 missions) from
    being read as claims: phantom claims are as harmful as missing ones.
    """
    claims = []

    # Pattern: "N missions" or "N tasks" etc.
    for match in re.finditer(r'(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s+(missions|tasks|workers|nodes|campaigns|repositories|repos)\b', report_text, re.IGNORECASE):
        claims.append({
            'value': _num(match.group(1)),
            'unit': match.group(2).lower(),
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })

    # Pattern: "N%" percentages
    for match in re.finditer(r'(?<![\d.])(\d+\.?\d*)\s*%', report_text):
        claims.append({
            'value': float(match.group(1)),
            'unit': 'percent',
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })

    # Pattern: time durations like "26.1 minutes" or "1568 seconds"
    for match in re.finditer(r'(?<![\d.])(\d+\.?\d*)\s*(seconds?|minutes?|hours?)\b', report_text, re.IGNORECASE):
        claims.append({
            'value': float(match.group(1)),
            'unit': match.group(2).lower().rstrip('s'),
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })

    return claims


# ---------------------------------------------------------------------------
# provenance index
# ---------------------------------------------------------------------------

def _entry(value, kind, source, subject=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return {'value': value, 'kind': kind, 'source': source, 'subject': subject}


def build_provenance_index(metrics):
    """Build the set of numbers that the metrics artifact actually supports.

    Every entry carries ``kind`` (count/percent/second/minute/hour) and an
    optional ``subject`` (repo, model, plan, node status) used for
    context-aware resolution.
    """
    idx = []

    def add(entry):
        if entry is not None:
            idx.append(entry)

    missions = metrics.get('missions', {}) or {}
    total = missions.get('total_missions')
    for key in ('total_missions', 'succeeded', 'failed', 'awaiting_pr_merge'):
        if key in missions:
            add(_entry(missions[key], 'count', f'missions.{key}'))

    if total:
        for key, label in (('succeeded', 'succeeded'), ('failed', 'failed'),
                           ('awaiting_pr_merge', 'awaiting_pr_merge')):
            if key in missions:
                add(_entry(100.0 * missions[key] / total, 'percent',
                           f'missions.{label}/total'))

    duration = (metrics.get('window', {}) or {}).get('duration_hours')
    if duration:
        add(_entry(duration, 'hour', 'window.duration_hours'))
        if total:
            add(_entry(float(total) / float(duration), 'count',
                       'missions.total_missions/window.duration_hours'))

    lifecycle = missions.get('avg_lifecycle_seconds')
    if lifecycle is not None:
        add(_entry(lifecycle, 'second', 'missions.avg_lifecycle_seconds'))
        add(_entry(float(lifecycle) / 60.0, 'minute', 'missions.avg_lifecycle_seconds/60'))

    workers = metrics.get('workers', {}) or {}
    if 'total_workers' in workers:
        add(_entry(workers['total_workers'], 'count', 'workers.total_workers'))
        if total:
            add(_entry(float(total) / workers['total_workers'], 'count',
                       'missions.total_missions/workers.total_workers'))

    # --- repositories -------------------------------------------------------
    repos = metrics.get('by_repository', []) or []
    if repos:
        add(_entry(len(repos), 'count', 'len(by_repository)'))
    repo_total = sum(r.get('missions', 0) for r in repos) or total
    cumulative = 0.0
    for rank, repo in enumerate(sorted(repos, key=lambda r: r.get('missions', 0), reverse=True), start=1):
        name = repo.get('repo')
        n = repo.get('missions', 0)
        ok = repo.get('succeeded', 0)
        add(_entry(n, 'count', f'by_repository[{name}].missions', name))
        add(_entry(ok, 'count', f'by_repository[{name}].succeeded', name))
        if n:
            add(_entry(100.0 * ok / n, 'percent', f'by_repository[{name}].succeeded/missions', name))
        if repo_total:
            share = 100.0 * n / repo_total
            add(_entry(share, 'percent', f'by_repository[{name}].missions/total', name))
            cumulative += share
            add(_entry(cumulative, 'percent',
                       f'cumulative top-{rank} by_repository share'))
        else:
            cumulative += 0.0

    # --- models ------------------------------------------------------------
    models = metrics.get('by_model', []) or []
    if models:
        add(_entry(len(models), 'count', 'len(by_model)'))
    attributed = sum(m.get('count', 0) for m in models)
    for row in models:
        name = row.get('model')
        c = row.get('count', 0)
        add(_entry(c, 'count', f'by_model[{name}].count', name))
        if attributed:
            add(_entry(100.0 * c / attributed, 'percent', f'by_model[{name}].count/attributed', name))
    if total and attributed:
        add(_entry(total - attributed, 'count', 'missions.total_missions-attributed',
                   'model:unattributed'))
        add(_entry(100.0 * (total - attributed) / total, 'percent',
                   '(missions.total_missions-attributed)/total'))

    # --- campaigns and nodes ------------------------------------------------
    campaigns = metrics.get('campaigns', []) or []
    if campaigns:
        add(_entry(len(campaigns), 'count', 'len(campaigns)'))

    nodes = metrics.get('campaign_nodes', []) or []
    if nodes:
        grand_total = sum(n.get('node_count', 0) for n in nodes) or 0
        add(_entry(grand_total, 'count', 'sum(campaign_nodes.node_count)'))
        per_plan, per_status, per_plan_status = {}, {}, {}
        for row in nodes:
            plan = row.get('plan_id')
            status = row.get('status')
            c = row.get('node_count', 0)
            per_plan[plan] = per_plan.get(plan, 0) + c
            per_status[status] = per_status.get(status, 0) + c
            per_plan_status[(plan, status)] = per_plan_status.get((plan, status), 0) + c
        for plan, c in sorted(per_plan.items(), key=lambda kv: str(kv[0])):
            subject = f'Plan {plan}'
            add(_entry(c, 'count', f'campaign_nodes[plan_id={plan}]', subject))
            for (p, status), sc in per_plan_status.items():
                if p == plan:
                    add(_entry(sc, 'count', f'campaign_nodes[plan_id={plan},status={status}]', subject))
        for status, c in per_status.items():
            add(_entry(c, 'count', f'campaign_nodes[status={status}]', f'campaign_status:{status}'))
            if grand_total:
                add(_entry(100.0 * c / grand_total, 'percent',
                           f'campaign_nodes[status={status}]/total', f'campaign_status:{status}'))

    # --- throughput --------------------------------------------------------
    ht = metrics.get('hourly_throughput', []) or []
    values = [h.get('missions_completed', 0) for h in ht]
    if values:
        add(_entry(max(values), 'count', 'hourly_throughput.max'))
        add(_entry(min(values), 'count', 'hourly_throughput.min'))

    return idx


_PLURAL = {
    'missions': 'mission', 'tasks': 'task', 'workers': 'worker', 'nodes': 'node',
    'campaigns': 'campaign', 'repositories': 'repository', 'repos': 'repo',
    'seconds': 'second', 'minutes': 'minute', 'hours': 'hour',
}


def _kind_of(unit):
    unit = (unit or '').lower()
    unit = _PLURAL.get(unit, unit.rstrip('s'))
    if unit in ('mission', 'task', 'worker', 'node', 'campaign', 'repository', 'repo'):
        return 'count'
    if unit == 'percent':
        return 'percent'
    if unit in ('second', 'minute', 'hour'):
        return unit
    return unit


# A count claim may only be resolved by a metric family of the same *kind of thing*:
# "4 repositories" must not be satisfied by a repository that happens to have four
# missions. Without this, unit-typed matching degrades into "the number exists
# somewhere", which is not a gate.
_COUNT_FAMILY = {
    'repositories': ('len(',),
    'repos': ('len(',),
    'campaigns': ('len(campaigns)',),
    'workers': ('workers.',),
    'nodes': ('campaign_nodes', 'sum(campaign_nodes'),
    'missions': ('missions.', 'by_repository[', 'by_model[', 'hourly_throughput'),
    'tasks': ('missions.', 'by_repository[', 'by_model[', 'hourly_throughput'),
}


def _family_ok(unit, entry):
    allowed = _COUNT_FAMILY.get((unit or '').lower())
    if not allowed:
        return True
    return any(entry['source'].startswith(a) for a in allowed)


def _line_of(report_lines, claim):
    if not report_lines:
        return ''
    n = claim.get('line') or 0
    if 0 < n <= len(report_lines):
        return report_lines[n - 1]
    return ''


def _value_match(claim_value, entry_value, kind):
    if kind == 'count' and float(claim_value).is_integer():
        return int(claim_value) == int(round(entry_value))
    return abs(float(claim_value) - float(entry_value)) <= TOL


def validate_claims(claims, metrics, report_lines=None, declared=None):
    """Validate numeric claims against metrics data.

    ``report_lines`` enables context-aware resolution (a claim sitting on a line
    that names a known subject may only be resolved by that subject's entries).
    ``declared`` is a list of declared, non-measured constants.
    """
    index = build_provenance_index(metrics)
    known_subjects = sorted({e['subject'] for e in index if e['subject']}, key=len, reverse=True)
    declared = declared or []
    results = []

    for claim in claims:
        kind = _kind_of(claim['unit'])
        line = _line_of(report_lines, claim)

        # Subject-scoped resolution applies to *record lines* (markdown table rows,
        # campaign registry entries): there the row's subject defines the intended
        # referent, so a value must not be borrowed from another row. Prose that
        # merely mentions a subject keeps the whole index (an explanatory sentence
        # naming a plan does not make the plan the referent of every number).
        is_record_line = bool(line) and (line.lstrip().startswith('|')
                                         or re.match(r'^\s*\d+\.\s+\*\*Plan\b', line))
        line_subjects = [s for s in known_subjects if s in line] if is_record_line else []
        # Repo names may appear as substrings of the line's prose (e.g.
        # "ship-performance-platform"); keep them all, longest first.
        candidates = [e for e in index if e['kind'] == kind]
        if kind == 'count':
            candidates = [e for e in candidates if _family_ok(claim['unit'], e)]
        if line_subjects:
            filtered = [e for e in candidates if e['subject'] in line_subjects]
            candidates = filtered or candidates

        validated = False
        source = None
        declared_hit = None
        for entry in candidates:
            if _value_match(claim['value'], entry['value'], kind):
                validated = True
                source = entry['source']
                break

        if not validated and declared:
            for d in declared:
                if _kind_of(d.get('unit')) != kind:
                    continue
                needle = d.get('context_contains') or ''
                hay = f"{claim.get('context', '')} {line}"
                if needle and needle not in hay:
                    continue
                if _value_match(claim['value'], d.get('value'), kind):
                    validated = True
                    declared_hit = d.get('reason') or 'declared constant'
                    source = f'DECLARED: {declared_hit}'
                    break

        results.append({
            'claim': claim,
            'validated': validated,
            'source': source,
            'declared': declared_hit is not None,
        })

    return results


def load_declared(path):
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        return data.get('constants', [])
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description='T3 Anti-Fabrication Validator')
    parser.add_argument('--report', required=True, help='Report markdown file')
    parser.add_argument('--metrics', required=True, help='Metrics JSON file')
    parser.add_argument('--declared', default=None,
                        help='Declared (non-measured) constants JSON')
    parser.add_argument('--strict', action='store_true',
                        help='Fail if any claim is unvalidated')
    parser.add_argument('--json-out', default=None,
                        help='Write the machine-readable verdict here')
    parser.add_argument('--quiet', action='store_true', help='Only print the summary')
    args = parser.parse_args(argv)

    report_text = Path(args.report).read_text(encoding='utf-8')
    metrics = json.loads(Path(args.metrics).read_text(encoding='utf-8'))
    declared = load_declared(args.declared)

    claims = extract_numbers_from_report(report_text)
    results = validate_claims(claims, metrics, report_text.splitlines(), declared)

    metric_ok = sum(1 for r in results if r['validated'] and not r['declared'])
    declared_ok = sum(1 for r in results if r['declared'])
    unvalidated = [r for r in results if not r['validated']]
    total = len(results)

    print(f"T3 Validation: {total} claims | {metric_ok} metric-validated | "
          f"{declared_ok} declared | {len(unvalidated)} unvalidated")

    if not args.quiet:
        for r in results:
            mark = '!' if r['declared'] else ('✓' if r['validated'] else '✗')
            source = r['source'] or 'UNVALIDATED'
            print(f"  {mark} Line {r['claim']['line']}: {r['claim']['context']} → {source}")

    verdict = {
        'report': str(args.report),
        'metrics': str(args.metrics),
        'claims_total': total,
        'metric_validated': metric_ok,
        'declared': declared_ok,
        'unvalidated': len(unvalidated),
        'unvalidated_claims': [
            {'line': r['claim']['line'], 'context': r['claim']['context'],
             'unit': r['claim']['unit'], 'value': r['claim']['value']}
            for r in unvalidated
        ],
        'verdict': 'PASS' if not unvalidated else 'FAIL',
    }
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(verdict, indent=2) + '\n', encoding='utf-8')
        print(f"verdict written: {out}")

    if unvalidated:
        print(f"FAILED: {len(unvalidated)} claims not validated")
        return 1 if args.strict or True else 0
    print(f"PASSED: all {total} claims resolved ({metric_ok} metric, {declared_ok} declared)")
    return 0


if __name__ == '__main__':
    sys.exit(main())