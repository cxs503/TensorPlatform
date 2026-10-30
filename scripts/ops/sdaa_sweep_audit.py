"""SDAA 扫荡线只读审计 + 报告素材生成（值班长班次 m242）。

零手抄：全部 mission / report 数据来自平台 API（GET /tensor/missions, GET /tensor/reports, GET /tensor/reports/{id}）。

用法（环境变量 $TENSOR_API_BASE / $TENSOR_AGENT_USER / $TENSOR_AGENT_PASS 就绪）：
  python scripts/ops/sdaa_sweep_audit.py --out docs/_sdaa_sweep_m242.json --table docs/_sdaa_sweep_m242.md

判据抽取规则（强标记优先，冲突显式暴露，不猜）：
  S1  判定[：:] (PASS|FAIL|HONEST-FAIL|WITHHELD)
  S2  verdict.pass = True/False ; all_pass = true/false
  S3  overall (:|=) PASS/FAIL
  S4  (N)/(N) gates|判据 ... PASS   （全门通过）
  S5  结论/诊断结论 ... (PASS|FAIL|honest-FAIL)
  S6  升级为 PASS / WITHHELD
无强标记 -> WITHHELD(no-token)；多条强标记互相矛盾 -> CONFLICT(待人工/下轮复核)。
"""
import argparse
import collections
import json
import os
import re
import urllib.request

BASE = os.environ.get('TENSOR_API_BASE', 'http://127.0.0.1:8010/api/v1')
USER = os.environ.get('TENSOR_AGENT_USER', 'eco_admin')
PWD = os.environ.get('TENSOR_AGENT_PASS', '')


def api(path, body=None, method=None, token=None, timeout=90):
    url = f"{BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, headers=headers, data=data, method=method or ('POST' if data else 'GET'))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def login():
    d = api('auth/login', {'username': USER, 'password': PWD})
    return d.get('access_token') or d.get('token')


def paged(path, tok, key='items', limit=200):
    out, off = [], 0
    while True:
        d = api(f'{path}{"&" if "?" in path else "?"}limit={limit}&offset={off}', token=tok)
        items = d.get(key) or []
        out += items
        off += len(items)
        if not items or off >= (d.get('total') or 0):
            break
    return out


IS_SWEEP = re.compile(r'SDAA 验证案例扫荡')


def is_sweep(m):
    g = m.get('goal') or ''
    return bool(IS_SWEEP.search(g)) and g.startswith(('SDAA', '【诊断'))


def case_of(g):
    mm = re.search(r'案例\s*([A-Za-z0-9_]+)', g or '')
    return mm.group(1) if mm else '?'


def rnd_of(g):
    mm = re.search(r'第\s*(\d+)\s*轮', g or '')
    return int(mm.group(1)) if mm else None


def mission_text(m):
    r = m.get('result') or {}
    parts = [r.get('headline') or '', r.get('declared_summary') or '', r.get('checks') or '',
             r.get('summary') or '', r.get('error') or '']
    ev = r.get('evidence') or {}
    if isinstance(ev, dict):
        s = ev.get('summary') or {}
        if isinstance(s, dict):
            parts += [str(s.get('headline') or ''), str(s.get('checks') or ''),
                      str(s.get('declared_summary') or '')]
    parts.append(str((m.get('approval') or {}).get('reason') or ''))
    return '\n'.join(p for p in parts if p)


import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdaa_verdict import resolve_text  # noqa: E402

_RANK = {'A': 0, 'B': 1, 'C-weak': 2}


def mission_verdict(text, report_mds):
    """按 A>B>C 优先级合并 mission 文本与报告 markdown 的判据；同级冲突 -> CONFLICT。"""
    cands = []
    v, rule, ev = resolve_text(text)
    if rule != 'no-token':
        cands.append((_RANK.get(rule, 1), v, rule, ev, 'mission.result'))
    for rid, md in report_mds:
        v2, r2, e2 = resolve_text(md)
        if r2 != 'no-token':
            cands.append((_RANK.get(r2, 1), v2, f'{r2}@report{rid}', e2, f'report:{rid}'))
    if not cands:
        return 'WITHHELD', 'no-token', '', 'none'
    cands.sort(key=lambda c: c[0])
    best = cands[0]
    same = [c for c in cands if c[0] == best[0]]
    if len({c[1] for c in same}) > 1:
        return ('CONFLICT', best[2] + '-conflict',
                ' | '.join(f'{c[1]}({c[3]})' for c in same[:3]), best[4])
    return best[1], best[2], best[3], best[4]


PROC_RE = re.compile(r'(未调用 finish|工作树无任何改动|未完成：|超时|timeout|工具调用)', re.I)
ENV_RE = re.compile(r'(CUDA|cuInit|驱动|GPU ?不可用|no GPU|OOM|out of memory|内存分配|allocator|环境阻塞|设备不可用|ImportError|ModuleNotFoundError)', re.I)
TRITON_RE = re.compile(r'(triton|绕行|bypass|CPU ?fallback|CPU ?回退)', re.I)
RED_RE = re.compile(r'(真红|判据.{0,4}(?:未过|红|FAIL)|>\s*3\s*%|超过\s*3\s*%|不收敛|未收敛|NOT_RUN|NOT_EVALUATED|窗漂移)', re.I)


def classify_fail(m):
    t = mission_text(m)
    tags = []
    if PROC_RE.search(t):
        tags.append('流程断点')
    if ENV_RE.search(t):
        tags.append('环境')
    if TRITON_RE.search(t):
        tags.append('triton绕行')
    if RED_RE.search(t):
        tags.append('判据真红')
    return tags or ['其他']


DEV_RE = re.compile(r'/dev/tcaicard(\d+)')
PCT_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='docs/_sdaa_sweep_m242.json')
    ap.add_argument('--table', default='docs/_sdaa_sweep_m242.md')
    args = ap.parse_args()

    tok = login()
    ms = {m['id']: m for m in paged('tensor/missions', tok)}
    # 单条 detail 补齐 approval.reason（列表接口未带 approval）
    sw = sorted([m for m in ms.values() if is_sweep(m)], key=lambda m: m['id'])
    for m in sw:
        try:
            d = api(f"tensor/missions/{m['id']}", token=tok)
            m['approval'] = d.get('approval')
            if d.get('result'):
                m['result'] = d['result']
        except Exception as e:  # noqa
            m['_detail_error'] = str(e)

    reports = {r['id']: r for r in paged('tensor/reports', tok, limit=100)}
    rep_by_mission = collections.defaultdict(list)
    for r in reports.values():
        if r.get('mission_id'):
            rep_by_mission[int(r['mission_id'])].append(int(r['id']))

    recs = []
    for m in sw:
        t = mission_text(m)
        v, rule, ev_snip, src = mission_verdict(t, [])
        recs.append({
            'id': m['id'], 'status': m['status'], 'case': case_of(m['goal']), 'round': rnd_of(m['goal']),
            'finished_at': m.get('finished_at'),
            'headline': (m.get('result') or {}).get('headline') or '',
            'text': t[:6000], 'verdict': v, 'verdict_rule': rule, 'verdict_evidence': ev_snip, 'verdict_source': src,
            'fail_tags': classify_fail(m) if m['status'] == 'failed' else [],
            'devices': sorted({f'/dev/tcaicard{d}' for d in DEV_RE.findall(t)}),
            'pct_values': PCT_RE.findall(t)[:8],
            'report_ids': sorted(rep_by_mission.get(m['id'], [])),
        })

    # 报告 markdown（全文，未截断）参与判据合并
    md_cache = {}
    need = {i for rec in recs for i in rec['report_ids']}
    for rid in sorted(need):
        try:
            md_cache[rid] = api(f'tensor/reports/{rid}', token=tok).get('markdown') or ''
        except Exception:
            md_cache[rid] = ''
    for rec in recs:
        mds = [(rid, md_cache.get(rid, '')) for rid in rec['report_ids']]
        v, rule, ev_snip, src = mission_verdict(rec['text'], mds)
        rec['verdict'], rec['verdict_rule'], rec['verdict_evidence'], rec['verdict_source'] = v, rule, ev_snip, src

    by_case = collections.defaultdict(list)
    for rec in recs:
        by_case[rec['case']].append(rec)
    cases = {}
    for case, lst in by_case.items():
        done = sorted([r for r in lst if r['status'] in ('succeeded', 'failed')], key=lambda r: r['id'])
        last = done[-1] if done else None
        cases[case] = {
            'missions': [(r['id'], r['status'], r['verdict']) for r in lst],
            'n_done': len(done), 'n_failed': sum(1 for r in done if r['status'] == 'failed'),
            'latest_done': last['id'] if last else None,
            'latest_verdict': last['verdict'] if last else 'WITHHELD',
            'latest_verdict_rule': last['verdict_rule'] if last else '',
            'latest_evidence': last['verdict_evidence'] if last else '',
            'latest_headline': (last['headline'] if last else '')[:120],
            'fail_tag_counts': dict(collections.Counter(t for r in done for t in r['fail_tags'])),
            'devices': sorted({d for r in lst for d in r['devices']}),
            'report_ids': sorted({i for r in lst for i in r['report_ids']}),
        }

    sweep_inflight = [m['id'] for m in sw if m['status'] in ('running', 'queued', 'pending', 'claimed')]
    in_flight = [mid for mid, m in ms.items()
                 if m['status'] in ('running', 'queued', 'pending', 'claimed')]
    summary = {
        'missions_total_on_platform': len(ms),
        'sweep_missions_total': len(sw),
        'sweep_done': sum(1 for r in recs if r['status'] in ('succeeded', 'failed')),
        'sweep_succeeded': sum(1 for r in recs if r['status'] == 'succeeded'),
        'sweep_failed': sum(1 for r in recs if r['status'] == 'failed'),
        'sweep_in_flight': sweep_inflight,
        'cases_total': len(cases),
        'case_verdict_counts': dict(collections.Counter(c['latest_verdict'] for c in cases.values())),
        'mission_fail_tag_counts': dict(collections.Counter(t for r in recs for t in r['fail_tags'])),
        'device_histogram': dict(sorted(collections.Counter(
            d for r in recs for d in r['devices']).items())),
        'in_flight_all': sorted(in_flight),
    }
    out = {'summary': summary, 'cases': cases, 'missions': recs}
    with open(args.out, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # markdown 表（程序化生成，零手抄）
    lines = ['| 案例 | 累计单数 | 失败单数 | 最新单 | 最新判据 | 判据来源 | 证据片段 | 设备 |',
             '|---|---|---|---|---|---|---|---|']
    for case in sorted(cases):
        c = cases[case]
        ev = (c['latest_evidence'] or c['latest_headline']).replace('|', '/').replace('\n', ' ')[:70]
        lines.append(f"| {case} | {c['n_done']} | {c['n_failed']} | {c['latest_done']} | "
                     f"{c['latest_verdict']} | {c['latest_verdict_rule']} | {ev} | "
                     f"{','.join(c['devices']) or '-'} |")
    with open(args.table, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()