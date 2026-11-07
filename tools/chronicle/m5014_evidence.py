#!/usr/bin/env python3
"""Evidence for the v1.1 boundary contraction (judge case #616).

Measures, without hand transcription:

  * how many numeric claims the **new** T3 validator can resolve in the v1.0 text
    (i.e. how many numbers v1.0 printed that its own metrics artifact does not
    support) versus in the v1.1 text (expected: zero unvalidated);
  * what the **old** validator said about v1.0 (it reported 24/107 validated yet
    the report claimed 47/47 -- the gate could not say NO on a borrowed row);
  * the boundary-gate check matrix for v1.0 (expected FAILs) versus v1.1.

Outputs ``artifacts/m5014_gate_evidence.json`` and a two-panel figure.

Usage: python3 tools/chronicle/m5014_evidence.py [--fig artifacts/m5014_gate_evidence.png]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'chronicle'))

import report_boundary as gate  # noqa: E402
import validate_report as t3  # noqa: E402

REPORT_REL = 'docs/reports/system_research_report_v1.md'
METRICS = ROOT / 'artifacts/m4977_metrics.json'
DECLARED = ROOT / 'config/report_declared_constants.json'
CONFIG = ROOT / 'config/report_boundary_gate.json'
T3_JSON = ROOT / 'artifacts/m5014_t3_validation.json'


def git_show(rev_path, commit='HEAD'):
    out = subprocess.run(['git', 'show', f'{commit}:{rev_path}'], cwd=ROOT,
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def resolve_counts(text, metrics, declared):
    claims = t3.extract_numbers_from_report(text)
    results = t3.validate_claims(claims, metrics, text.splitlines(), declared)
    return {
        'claims_total': len(results),
        'metric_validated': sum(1 for r in results if r['validated'] and not r['declared']),
        'declared': sum(1 for r in results if r['declared']),
        'unvalidated': sum(1 for r in results if not r['validated']),
        'unvalidated_claims': [
            {'line': r['claim']['line'], 'context': r['claim']['context']}
            for r in results if not r['validated']
        ][:20],
    }


def legacy_validator_counts(text, metrics):
    """Run the pre-change validator (HEAD) against the same text and metrics."""
    src = git_show('tools/chronicle/validate_report.py')
    if not src:
        return None
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'legacy_validate.py'
        path.write_text(src, encoding='utf-8')
        spec = importlib.util.spec_from_file_location('legacy_validate', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        claims = mod.extract_numbers_from_report(text)
        results = mod.validate_claims(claims, metrics)
        return {
            'claims_total': len(results),
            'validated': sum(1 for r in results if r['validated']),
        }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--fig', default=str(ROOT / 'artifacts/m5014_gate_evidence.png'))
    ap.add_argument('--json-out', default=str(ROOT / 'artifacts/m5014_gate_evidence.json'))
    args = ap.parse_args(argv)

    metrics = json.loads(METRICS.read_text(encoding='utf-8'))
    declared = json.loads(DECLARED.read_text(encoding='utf-8'))['constants']
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))

    v10 = git_show(REPORT_REL)
    v11 = (ROOT / REPORT_REL).read_text(encoding='utf-8')
    if v10 is None:
        raise SystemExit('cannot read v1.0 from git HEAD')

    legacy = legacy_validator_counts(v10, metrics)
    after_v10 = resolve_counts(v10, metrics, declared)
    after_v11 = resolve_counts(v11, metrics, declared)

    with tempfile.TemporaryDirectory() as td:
        p10 = Path(td) / 'v1_0.md'
        p10.write_text(v10, encoding='utf-8')
        gate_v10 = gate.run(cfg, p10, None)
        gate_v11 = gate.run(cfg, ROOT / REPORT_REL, T3_JSON)

    evidence = {
        'report': REPORT_REL,
        'metrics': str(METRICS.relative_to(ROOT)),
        'legacy_validator_on_v1_0': legacy,
        'new_validator_on_v1_0': after_v10,
        'new_validator_on_v1_1': after_v11,
        'boundary_gate_v1_0': {'verdict': gate_v10['verdict'], 'checks': gate_v10['checks']},
        'boundary_gate_v1_1': {'verdict': gate_v11['verdict'], 'checks': gate_v11['checks']},
        'finding_counts_v1_0': {
            code: sum(1 for f in gate_v10['findings'] if f['code'] == code)
            for code in ('VERSION_NOTE', 'SECTION_STRUCTURE', 'DOMAIN_DETAIL',
                         'DOMAIN_TERM', 'REFERENCE_LINE', 'METRICS_ARTIFACT')
        },
    }
    out = Path(args.json_out)
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: evidence[k] for k in ('legacy_validator_on_v1_0',
                                               'new_validator_on_v1_0',
                                               'new_validator_on_v1_1')}, indent=2))

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib unavailable; figure skipped')
        return 0

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    ax = axes[0]
    labels = ['metric-resolved', 'declared', 'unvalidated']
    v10_vals = [after_v10['metric_validated'], after_v10['declared'], after_v10['unvalidated']]
    v11_vals = [after_v11['metric_validated'], after_v11['declared'], after_v11['unvalidated']]
    xs = range(len(labels))
    ax.bar([x - 0.2 for x in xs], v10_vals, width=0.4, label='v1.0 text', color='#c0504d')
    ax.bar([x + 0.2 for x in xs], v11_vals, width=0.4, label='v1.1 text', color='#4f81bd')
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylabel('numeric claims')
    ax.set_title('T3 resolution of the same metrics artifact\n'
                 f"(claims: v1.0 {after_v10['claims_total']}, v1.1 {after_v11['claims_total']})")
    for x, (a, b) in enumerate(zip(v10_vals, v11_vals)):
        ax.text(x - 0.2, a + 1, str(a), ha='center', fontsize=8)
        ax.text(x + 0.2, b + 1, str(b), ha='center', fontsize=8)
    ax.legend(fontsize=8)
    ax.grid(axis='y', alpha=0.3)

    ax = axes[1]
    checks = ['VERSION_NOTE', 'SECTION_STRUCTURE', 'DOMAIN_DETAIL', 'DOMAIN_TERM',
              'REFERENCE_LINE']
    status = {'PASS': 1.0, 'FAIL': 0.0}
    rows = [[status[gate_v10['checks'][c]] for c in checks],
            [status[gate_v11['checks'][c]] for c in checks]]
    ax.imshow(rows, cmap='RdYlGn', vmin=0, vmax=1, aspect='auto')
    ax.set_xticks(range(len(checks)))
    ax.set_xticklabels([c.replace('_', '\n') for c in checks], fontsize=7)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(['v1.0', 'v1.1'])
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            ax.text(j, i, 'PASS' if val else 'FAIL', ha='center', va='center', fontsize=7)
    ax.set_title('Boundary gate (case #616): %s -> %s'
                 % (gate_v10['verdict'], gate_v11['verdict']))

    fig.suptitle('Platform report v1.1: numeric provenance and domain-neutrality boundary',
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(args.fig, dpi=150)
    print(f'figure: {args.fig}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())