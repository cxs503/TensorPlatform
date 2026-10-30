"""m371 shift analyzer: programmatic sweep-line ledger from platform API dump.

Inputs : /tmp/missions.json (GET /tensor/missions?limit=50), /tmp/reports.json
Output : agent_work/m371/ledger.json  (per-mission + per-case aggregates)
No numbers are typed by hand; every figure is computed from API payloads.
"""
import json, re, collections

KEY = "SDAA 验证案例扫荡"
KNOWN = set("""acoustics aircraft_icing_naca0012_rime aircraft_icing_rg15_ipw2 cavity
laplace_cac_phasefield maneuvering_steady_turn permeability permeability3d
poole_3d poiseuille_3d_duct poiseuille_3d_pipe sphere_re100 sphere_re100_mem
startup_poiseuille stokes_second_problem suboff_re1000 taylor_green_3d
thermal_cavity thermal_cavity_3d womersley cylinder_3d dam_break_cavity""".split())

CASE_PATS = (r'案例\s*[:：]?\s*([A-Za-z][A-Za-z0-9_]{2,})',
             r'benchmarks/(?:verified|pending)/([A-Za-z0-9_]+)')


def load_missions():
    d = json.load(open('/tmp/missions.json'))
    return d.get('items', d if isinstance(d, list) else [])


def is_sweep(m):
    return KEY in (m.get('goal') or '')


def kind(m):
    g = m.get('goal') or ''
    if '值班长班次' in g:
        return 'shift'
    if '诊断+修复绕行' in g:
        return 'diag'
    if '下探' in g:
        return 'probe'
    return 'other'


def _texts(m):
    r = m.get('result') or {}
    ev = r.get('evidence') or {}
    summ = ev.get('summary') or {}
    checks = r.get('checks') or ''
    if isinstance(checks, dict):
        checks = json.dumps(checks, ensure_ascii=False)
    return [str(r.get('headline') or ''),
            str(summ.get('declared_summary') or ''),
            checks,
            m.get('goal') or '']


def case_of(m):
    for txt in _texts(m):
        for pat in CASE_PATS:
            for c in re.findall(pat, txt):
                if c in KNOWN:
                    return c
        for c in sorted(KNOWN, key=len, reverse=True):
            if re.search(r'(?<![A-Za-z0-9_])' + re.escape(c) + r'(?![A-Za-z0-9_])', txt):
                return c
    return 'UNRESOLVED'


# interpretive layer: (verdict, category) justified by quoted API text, see shift doc S4
OVERRIDES = {
    # m319 Cd_tot=0.061495 (+46.43% vs baseline) quoted inside m330's goal; m337 reproduced
    # 0.061424 (=-0.12% vs m319) at L=24 and only hardened the gate -> red line stands.
    'suboff_re1000': ('HONEST_FAIL', 'triton 绕行+判据真红'),
}


def mission_view(m):
    r = m.get('result') or {}
    ev = r.get('evidence') or {}
    summ = ev.get('summary') or {}
    checks = r.get('checks') or ''
    if isinstance(checks, dict):
        checks = json.dumps(checks, ensure_ascii=False)
    gate = ev.get('checks') if isinstance(ev.get('checks'), dict) else {}
    # flags come from the mission's own OUTPUT only (goal text carries history of
    # earlier missions and would contaminate verdicts, e.g. m345 mentioning m293 HONEST_FAIL)
    raw = ' '.join([checks, str(r.get('headline') or ''),
                    str(summ.get('declared_summary') or ''),
                    json.dumps(gate, ensure_ascii=False)])
    # drop sentences that quote OTHER missions (history contamination):
    # m345's headline quotes m293 HONEST_FAIL although m345 itself is PASS.
    keep, self_id = [], m['id']
    for sent in re.split(r'[。;\n]', raw):
        ids = set(re.findall(r'\bm(\d{3})\b', sent))
        if ids - {str(self_id)}:
            continue
        if any(k in sent for k in ('前序', '此前', '前次', '历史', '前两次')):
            continue
        keep.append(sent)
    blob = ' '.join(keep)
    status = m.get('status')
    noev = (gate.get('gate') == 'no-evidence')
    triton = any(k in blob for k in ('eager_fallback', 'teco_inductor',
                                     'InductorError', 'TRITON_INTERPRET'))
    honest = ('HONEST_FAIL' in blob or 'HONEST-FAIL' in blob)
    red = bool(re.search(r'4\.890|46\.43|3\.03e-2|verdict=FAIL|FAIL\s*\(|> 3%|>3%', blob))
    # verdict + failure category (precedence: infra gate > triton bypass > true red > pass)
    if status == 'running':
        verdict, cat = 'RUNNING', '-'
    elif status == 'failed':
        verdict = 'GATE_FAIL'
        cat = '其他(agent 基础设施)'
    elif honest and triton:
        verdict, cat = 'HONEST_FAIL', 'triton 绕行+判据真红'
    elif honest or red:
        verdict, cat = 'HONEST_FAIL', '判据真红'
    else:
        verdict, cat = 'PASS', '通过'
    ov = OVERRIDES.get(case_of(m))
    # interpretive override only for COMPLETED missions: a gate-FAIL mission (no worktree
    # change) must stay classified as infrastructure, never inherit a physics verdict.
    if ov and status == 'succeeded':
        verdict, cat = ov[0], ov[1]
    snip = re.sub(r'\s+', ' ', checks or str(summ.get('declared_summary') or '') or str(r.get('headline') or ''))
    return dict(id=m['id'], kind=kind(m), case=case_of(m), status=status,
                verdict=verdict, category=cat, gate=gate.get('gate'),
                reason=gate.get('reason'), triton=triton, honest=honest,
                created=m.get('created_at'), snippet=snip[:260])


def build():
    rows = [mission_view(m) for m in sorted(load_missions(), key=lambda x: x['id']) if is_sweep(m)]
    sweep_cases = [r for r in rows if r['kind'] != 'shift']
    per_case = collections.OrderedDict()
    for r in sweep_cases:
        c = per_case.setdefault(r['case'], dict(case=r['case'], missions=[], verdicts=[], cats=[]))
        c['missions'].append(r['id'])
        if r['verdict'] != 'RUNNING':
            c['verdicts'].append(r['verdict'])
            c['cats'].append(r['category'])
    table = []
    for c in per_case.values():
        last = [r for r in sweep_cases if r['case'] == c['case'] and r['verdict'] != 'RUNNING']
        last.sort(key=lambda x: x['id'])
        latest = last[-1] if last else None
        table.append(dict(case=c['case'], attempts=len(c['missions']), missions=c['missions'],
                          latest_mission=latest['id'] if latest else None,
                          latest_verdict=latest['verdict'] if latest else 'RUNNING',
                          category=latest['category'] if latest else '-',
                          snippet=latest['snippet'] if latest else ''))
    st = collections.Counter(r['status'] for r in rows)
    vd = collections.Counter(r['verdict'] for r in sweep_cases)
    cats = collections.Counter(r['category'] for r in sweep_cases if r['verdict'] != 'RUNNING')
    lcat = collections.Counter(t['category'] for t in table)
    lver = collections.Counter(t['latest_verdict'] for t in table)
    out = dict(generated_from='/tmp/missions.json',
               sweep_total=len(rows), shift_missions=st.get(None, 0),
               status_counts=dict(st), case_missions=len(sweep_cases),
               verdict_counts=dict(vd), category_counts=dict(cats),
               per_case=table, missions=rows,
               case_latest_verdict=dict(lver), case_latest_category=dict(lcat))
    json.dump(out, open('agent_work/m371/ledger.json', 'w'), ensure_ascii=False, indent=1)
    return out


if __name__ == '__main__':
    o = build()
    print("sweep missions:", o['sweep_total'], "status:", o['status_counts'])
    print("non-shift sweep missions:", o['case_missions'], "verdicts:", o['verdict_counts'])
    print("mission-level categories:", o['category_counts'])
    print("unique cases:", len(o['per_case']), "latest verdicts:", o['case_latest_verdict'])
    print("latest categories:", o['case_latest_category'])
    print()
    for t in sorted(o['per_case'], key=lambda x: x['case']):
        print("%-26s att=%-2d last=%-4s %-11s %-16s | %s" % (
            t['case'], t['attempts'], t['latest_mission'], t['latest_verdict'],
            t['category'], t['snippet'][:90]))