"""Boundary + anti-fabrication gate tests for the platform research report.

Three things are proven here, and the third is the point:

1. the v1.1 report satisfies the boundary gate (organization-level metrics only,
   domain case detail referenced, judge case #616 cited);
2. the gate can **say NO** -- injected case detail, a missing version note, or a
   domain term outside the whitelist each turn the verdict into FAIL;
3. the T3 validator can **say NO** -- a number that no metric entry backs, or a
   number borrowed from another subject's row, is left unvalidated.

Every fixture uses synthetic domain names and synthetic numbers; the domain
vocabulary itself is read from the injected config, never written into this test
module (zero-domain-semantics iron rule: domain data arrives by injection).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'chronicle'))

import report_boundary as gate  # noqa: E402
import validate_report as t3  # noqa: E402
import tighten_report_v1_1 as tighten  # noqa: E402

REPORT = ROOT / 'docs' / 'reports' / 'system_research_report_v1.md'
CONFIG = ROOT / 'config' / 'report_boundary_gate.json'
METRICS = ROOT / 'artifacts' / 'm4977_metrics.json'
T3_JSON = ROOT / 'artifacts' / 'm5014_t3_validation.json'
DECLARED = ROOT / 'config' / 'report_declared_constants.json'


@pytest.fixture(scope='module')
def cfg():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def metrics():
    return json.loads(METRICS.read_text(encoding='utf-8'))


# --------------------------------------------------------------------------
# 1. the shipped report passes
# --------------------------------------------------------------------------

def test_report_passes_boundary_gate(cfg):
    result = gate.run(cfg, REPORT, T3_JSON)
    assert result['verdict'] == 'PASS', result['findings']
    assert all(v == 'PASS' for v in result['checks'].values()), result['checks']


def test_report_keeps_ten_sections(cfg):
    text = REPORT.read_text(encoding='utf-8')
    assert gate.check_sections(text, cfg) == []


def test_report_version_note_cites_precedent(cfg):
    text = REPORT.read_text(encoding='utf-8')
    assert cfg['required_version_marker'] in text
    assert f"#{cfg['precedent']['id']}" in text
    assert cfg['precedent']['domain_owner'] in text
    assert 'change note' in text.lower()


def test_report_has_one_line_reference_instead_of_case(cfg):
    text = REPORT.read_text(encoding='utf-8')
    assert gate.check_reference_line(text, cfg) == []
    # the reference must be a single line, not a restatement section
    refs = [l for l in text.splitlines() if cfg['precedent']['domain_owner'] in l]
    assert 0 < len(refs) <= 6


def test_t3_verdict_artifact_is_pass():
    verdict = json.loads(T3_JSON.read_text(encoding='utf-8'))
    assert verdict['verdict'] == 'PASS'
    assert verdict['unvalidated'] == 0
    assert verdict['metric_validated'] > 0


def test_report_numbers_match_metrics(metrics, cfg):
    """Every printed campaign node total must equal the metrics sum (v1.0 failed here)."""
    text = REPORT.read_text(encoding='utf-8')
    node_total = sum(row['node_count'] for row in metrics['campaign_nodes'])
    assert f'**{node_total} DAG nodes**' in text
    assert f'| Campaign nodes | {node_total} |' in text
    assert f'`campaign_nodes` table: {node_total} nodes' in text


# --------------------------------------------------------------------------
# 2. the gate can say NO
# --------------------------------------------------------------------------

def _clean_report(cfg):
    """A synthetic, fully organization-level report that satisfies every check."""
    owner = cfg['precedent']['domain_owner']
    hint = cfg['precedent']['domain_doc_hint']
    sections = '\n\n'.join(f'## {i}. Section {i}\n\nOrganizational metric text.' for i in range(1, 11))
    return (
        f'# Synthetic report {cfg["required_version_marker"]}\n\n'
        f'**Validation**: synthetic\n\n'
        f'**v1.1 change note**: boundary contraction per judge case #{cfg["precedent"]["id"]}; '
        f'case detail is owned by `{owner}` ({hint}).\n\n'
        f'{sections}\n\n'
        f'1. **Plan 11: SYNTHETIC CAMPAIGN** (3 nodes)\n'
        f'   Domain case detail is not restated here: it is owned by `{owner}`.\n'
    )


def test_clean_synthetic_report_passes(cfg, tmp_path):
    path = tmp_path / 'synthetic.md'
    path.write_text(_clean_report(cfg), encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'PASS', result['findings']


@pytest.mark.parametrize('detail', [
    'L777/B77/T7',            # principal-dimension triplet form
    '999 MW',                  # installed power form
    'PORTX\u2192PORTY',        # waypoint route form
    '6 \u79cd\u5de5\u51b5',    # environmental state matrix form
])
def test_gate_rejects_injected_case_detail(cfg, tmp_path, detail):
    text = _clean_report(cfg).replace('Organizational metric text.',
                                      f'Case parameters: {detail}.')
    path = tmp_path / 'synthetic_bad.md'
    path.write_text(text, encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'FAIL'
    assert result['checks']['DOMAIN_DETAIL'] == 'FAIL'


def test_gate_rejects_missing_version_note(cfg, tmp_path):
    text = _clean_report(cfg)
    text = text.replace('**v1.1 change note**', '**note**')
    text = text.replace(cfg['required_version_marker'], 'v0.9')
    path = tmp_path / 'synthetic_v09.md'
    path.write_text(text, encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'FAIL'
    assert result['checks']['VERSION_NOTE'] == 'FAIL'


def test_gate_rejects_domain_term_in_prose(cfg, tmp_path):
    term = cfg['domain_terms'][0]  # injected vocabulary, not hardcoded here
    text = _clean_report(cfg).replace('Organizational metric text.',
                                      f'Prose mentioning {term} in passing.')
    path = tmp_path / 'synthetic_term.md'
    path.write_text(text, encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'FAIL'
    assert result['checks']['DOMAIN_TERM'] == 'FAIL'


def test_gate_allows_recorded_title_on_whitelisted_line(cfg, tmp_path):
    term = cfg['domain_terms'][0]
    text = _clean_report(cfg).replace('SYNTHETIC CAMPAIGN', f'{term} campaign')
    path = tmp_path / 'synthetic_title.md'
    path.write_text(text, encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'PASS', result['findings']
    assert result['whitelisted_record_lines']


def test_gate_rejects_missing_section(cfg, tmp_path):
    text = _clean_report(cfg).replace('## 7. Section 7', '## 77. Section 77')
    path = tmp_path / 'synthetic_gap.md'
    path.write_text(text, encoding='utf-8')
    result = gate.run(cfg, path, None)
    assert result['verdict'] == 'FAIL'
    assert result['checks']['SECTION_STRUCTURE'] == 'FAIL'


def test_code_neutrality_flags_new_file(cfg, monkeypatch, tmp_path):
    """A new file carrying injected domain vocabulary must fail the gate."""
    term = cfg['domain_terms'][0]
    fake_root = tmp_path / 'repo'
    (fake_root / 'tools').mkdir(parents=True)
    (fake_root / 'tools' / 'new_module.py').write_text(f'NAME = "{term}"\n', encoding='utf-8')
    monkeypatch.setattr(gate, 'ROOT', fake_root)
    hits = gate.scan_code(cfg)
    assert hits and 'tools/new_module.py' in hits
    findings, _ = gate.check_code_neutrality(cfg, baseline={})
    assert any(f['code'] == 'CODE_NEUTRALITY' and f['severity'] == 'FAIL' for f in findings)


def test_code_neutrality_baselined_file_is_warn(cfg, monkeypatch, tmp_path):
    term = cfg['domain_terms'][0]
    fake_root = tmp_path / 'repo'
    (fake_root / 'tools').mkdir(parents=True)
    (fake_root / 'tools' / 'legacy_module.py').write_text(f'NAME = "{term}"\n', encoding='utf-8')
    monkeypatch.setattr(gate, 'ROOT', fake_root)
    findings, _ = gate.check_code_neutrality(cfg, baseline={'tools/legacy_module.py': [term]})
    assert not [f for f in findings if f['severity'] == 'FAIL']
    assert [f for f in findings if f['severity'] == 'WARN']


# --------------------------------------------------------------------------
# 3. the validator can say NO
# --------------------------------------------------------------------------

def _mini_metrics():
    return {
        'missions': {'total_missions': 100, 'succeeded': 90, 'failed': 10,
                     'awaiting_pr_merge': 0, 'avg_lifecycle_seconds': 600.0},
        'window': {'duration_hours': 10},
        'workers': {'total_workers': 5},
        'by_repository': [
            {'repo': 'SYNTH-A', 'missions': 60, 'succeeded': 54},
            {'repo': 'SYNTH-B', 'missions': 40, 'succeeded': 36},
        ],
        'by_model': [{'model': 'synthetic-model', 'count': 100}],
        'campaigns': [{'id': 1, 'name': 'synthetic'}],
        'campaign_nodes': [{'plan_id': 1, 'status': 'done', 'node_count': 7},
                           {'plan_id': 1, 'status': 'blocked', 'node_count': 7}],
        'hourly_throughput': [{'hour': 'h1', 'missions_completed': 33},
                              {'hour': 'h2', 'missions_completed': 67}],
    }


def test_validator_accepts_true_claim():
    res = t3.validate_claims([{'value': 100, 'unit': 'missions',
                              'context': '100 missions', 'line': 1}], _mini_metrics())
    assert res[0]['validated'] is True
    assert res[0]['source'] == 'missions.total_missions'


def test_validator_rejects_fabricated_claim():
    res = t3.validate_claims([{'value': 999, 'unit': 'missions',
                               'context': '999 missions', 'line': 1}], _mini_metrics())
    assert res[0]['validated'] is False


def test_validator_rejects_borrowed_row_value():
    """A table row naming SYNTH-A cannot be validated by SYNTH-B's numbers."""
    metrics = _mini_metrics()
    claims = [{'value': 40, 'unit': 'missions', 'context': '40 missions', 'line': 1}]
    lines = ['| SYNTH-A | synthetic role | 40 | 90.0% |']
    res = t3.validate_claims(claims, metrics, report_lines=lines)
    assert res[0]['validated'] is False


def test_validator_accepts_own_row_value():
    metrics = _mini_metrics()
    claims = [{'value': 60, 'unit': 'missions', 'context': '60 missions', 'line': 1}]
    lines = ['| SYNTH-A | synthetic role | 60 | 90.0% |']
    res = t3.validate_claims(claims, metrics, report_lines=lines)
    assert res[0]['validated'] is True


def test_validator_rejects_count_of_wrong_unit_family():
    """'4 repositories' may not be satisfied by a repository that has four missions."""
    metrics = _mini_metrics()
    metrics['by_repository'][1]['missions'] = 4
    claims = [{'value': 4, 'unit': 'repositories', 'context': '4 repositories', 'line': 1}]
    res = t3.validate_claims(claims, metrics)
    assert res[0]['validated'] is False


def test_validator_declared_constant_requires_its_context():
    metrics = _mini_metrics()
    declared = [{'value': 25, 'unit': 'percent', 'context_contains': 'exceeds 25%',
                 'reason': 'stated bound'}]
    hit = t3.validate_claims([{'value': 25, 'unit': 'percent', 'context': '25%',
                               'line': 1}], metrics,
                             report_lines=['no single repository exceeds 25% of total'],
                             declared=declared)
    assert hit[0]['validated'] and hit[0]['declared']
    miss = t3.validate_claims([{'value': 25, 'unit': 'percent', 'context': '25%',
                                'line': 1}], metrics,
                              report_lines=['unrelated sentence about 25% growth'],
                              declared=declared)
    assert not miss[0]['validated']


def test_extractor_ignores_heading_and_rate_fragments():
    text = '### 4.2 Repository Distribution\n\n**Average**: 6.9 missions/hour over 48 hours'
    claims = t3.extract_numbers_from_report(text)
    assert not [c for c in claims if c['unit'] == 'repos']
    assert [c for c in claims if c['unit'] == 'missions' and c['value'] == 6.9]
    assert [c for c in claims if c['unit'] == 'hour' and c['value'] == 48]


def test_validator_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / 'good.md'
    good.write_text('The system processed 100 missions.\n', encoding='utf-8')
    metrics_path = tmp_path / 'm.json'
    metrics_path.write_text(json.dumps(_mini_metrics()), encoding='utf-8')
    assert t3.main(['--report', str(good), '--metrics', str(metrics_path), '--strict',
                    '--quiet']) == 0
    bad = tmp_path / 'bad.md'
    bad.write_text('The system processed 4242 missions.\n', encoding='utf-8')
    assert t3.main(['--report', str(bad), '--metrics', str(metrics_path), '--strict',
                    '--quiet']) == 1


# --------------------------------------------------------------------------
# 4. the patch tool is deterministic and idempotent
# --------------------------------------------------------------------------

def test_patch_tool_is_idempotent(tmp_path, metrics):
    copy = tmp_path / 'report.md'
    copy.write_text(REPORT.read_text(encoding='utf-8'), encoding='utf-8')
    ledger = tmp_path / 'ledger.json'
    t3_json = tmp_path / 't3.json'
    t3_json.write_text(json.dumps({'claims_total': 1, 'metric_validated': 1, 'declared': 0}),
                       encoding='utf-8')
    tighten.main(['--report', str(copy), '--metrics', str(METRICS),
                  '--ledger', str(ledger), '--t3-json', str(T3_JSON)])
    first = copy.read_text(encoding='utf-8')
    tighten.main(['--report', str(copy), '--metrics', str(METRICS),
                  '--ledger', str(ledger), '--t3-json', str(T3_JSON)])
    assert copy.read_text(encoding='utf-8') == first


def test_patch_ledger_records_every_edit():
    ledger = json.loads((ROOT / 'artifacts' / 'm5014_v1_1_edits.json').read_text(encoding='utf-8'))
    assert ledger['edits_total'] > 0
    assert ledger['provenance_edits'] > 0
    assert ledger['boundary_edits'] > 0
    for e in ledger['edits']:
        assert e['status'] in ('applied', 'already_applied')
        assert e.get('source')


def test_declared_constants_are_justified():
    data = json.loads(DECLARED.read_text(encoding='utf-8'))
    for c in data['constants']:
        assert c['reason'], c
        assert c['context_contains'], c