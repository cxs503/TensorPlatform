"""Compare real baseline/candidate outcomes under one immutable test suite."""
import hashlib
import json

def frozen_suite(package):
    tests={n:s for n,s in package['files'].items() if n.startswith('test_')}
    return hashlib.sha256(json.dumps(tests,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def baseline_with_tests(baseline,candidate):
    implementation={n:s for n,s in baseline['files'].items() if not n.startswith('test_')}
    names={n for n in candidate['files'] if not n.startswith('test_')}
    if set(implementation)!=names:raise ValueError('baseline implementation module names differ')
    return {'files':{**implementation,**{n:s for n,s in candidate['files'].items() if n.startswith('test_')}}}

def attribute(baseline,candidate,*,baseline_suite_sha256,candidate_suite_sha256):
    base=baseline.get('tests',{});head=candidate.get('tests',{})
    def unknown(reason):return {'status':'unknown','reason':reason,'new_failures':None,'baseline_failures':None,'resolved_failures':None}
    if baseline_suite_sha256!=candidate_suite_sha256:return unknown('different frozen test suites')
    for key in ['image','test_runner_sha256']:
        if not baseline.get(key) or baseline.get(key)!=candidate.get(key):return unknown('different or missing execution environment')
    if baseline.get('timed_out') or candidate.get('timed_out'):return unknown('execution timeout')
    if base.get('status')!='completed' or head.get('status')!='completed' or base.get('truncated') or head.get('truncated'):return unknown('incomplete test collection')
    b={x['id']:x['status'] for x in base.get('methods',[])};h={x['id']:x['status'] for x in head.get('methods',[])}
    if not b or b.keys()!=h.keys():return unknown('test identity mismatch or empty suite')
    if any('_FailedTest.' in key for key in b):return unknown('test discovery/import error')
    if any((b[k]=='skipped')!=(h[k]=='skipped') for k in b):return unknown('skip coverage differs')
    red={'failure','error','unexpected_success'}
    br={k for k,v in b.items() if v in red};hr={k for k,v in h.items() if v in red}
    return {'status':'compared','frozen_suite_sha256':candidate_suite_sha256,'new_failures':sorted(hr-br),'baseline_failures':sorted(hr&br),'resolved_failures':sorted(br-hr),'candidate_failures':sorted(hr),'semantic_acceptance':False,'note':'existing failures remain failures; attribution never approves a delivery'}
