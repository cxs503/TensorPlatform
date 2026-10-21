import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from evidence_gates import attachment,reference,check_claim,check_figure,evaluate,make_test_figure
from baseline_attribution import attribute,baseline_with_tests,frozen_suite

def test_claim_recomputed_and_tampering_rejected(tmp_path):
    p=tmp_path/'result.json';p.write_text('{"n":3,"flag":true,"array":[5]}')
    c={'artifact':reference(tmp_path,p.name),'pointer':'/n','expected':3,'unit':'methods'}
    assert check_claim(tmp_path,c)==3
    assert not evaluate(tmp_path,[{**c,'expected':4}],[])['passed']
    assert not evaluate(tmp_path,[{**c,'pointer':'/missing'}],[])['passed']
    assert not evaluate(tmp_path,[{**c,'pointer':'/flag','expected':1}],[])['passed']
    p.write_text('{"n":4}')
    assert not evaluate(tmp_path,[c],[])['passed']

@pytest.mark.parametrize('name',['../escape','/etc/passwd','.','..'])
def test_no_path_escape(tmp_path,name):
    with pytest.raises(ValueError):attachment(tmp_path,{'name':name})

def test_symlink_rejected(tmp_path):
    (tmp_path/'x').write_text('hello');(tmp_path/'y').symlink_to(tmp_path/'x')
    with pytest.raises(ValueError):attachment(tmp_path,reference(tmp_path,'y'))

def test_fake_png_rejected_even_with_matching_digest(tmp_path):
    for name,raw in [('x.png',b'\x89PNG\r\n\x1a\n'+bytes(range(256))*32),('data.json',b'{}'),('plot.py',b'pass')]:
        (tmp_path/name).write_bytes(raw)
    f={'image':reference(tmp_path,'x.png'),'data':reference(tmp_path,'data.json'),'script':reference(tmp_path,'plot.py'),'caption':'test'}
    assert not evaluate(tmp_path,[],[f])['passed']

def test_actual_chart_and_provenance(tmp_path):
    counts={k:0 for k in ['passed','failure','error','skipped','expected_failure','unexpected_success']};counts['passed']=3
    data={'candidate':{'tests':{'status':'completed','counts':counts}}}
    (tmp_path/'result.json').write_text(json.dumps(data))
    f=make_test_figure(tmp_path,data)[0]
    assert check_figure(tmp_path,f)['width']>500
    (tmp_path/'test-measurements.json').write_text('{}')
    assert not evaluate(tmp_path,[],[f])['passed']

def test_same_suite_failure_attribution():
    def result(statuses):return {'image':'immutable','test_runner_sha256':'runner','tests':{'status':'completed','methods':[{'id':k,'status':v} for k,v in statuses.items()]}}
    b=result({'a':'error','b':'passed','c':'failure'});h=result({'a':'error','b':'failure','c':'passed'})
    r=attribute(b,h,baseline_suite_sha256='s',candidate_suite_sha256='s')
    assert r['new_failures']==['b'] and r['baseline_failures']==['a'] and r['resolved_failures']==['c']
    assert r['semantic_acceptance'] is False
    assert attribute(b,h,baseline_suite_sha256='x',candidate_suite_sha256='s')['status']=='unknown'
    h['tests']['methods'].pop()
    assert attribute(b,h,baseline_suite_sha256='s',candidate_suite_sha256='s')['status']=='unknown'

def test_baseline_uses_candidate_tests():
    base={'files':{'code.py':'old','test_code.py':'old tests'}};head={'files':{'code.py':'new','test_code.py':'new tests'}}
    frozen=baseline_with_tests(base,head)
    assert frozen['files']['code.py']=='old' and frozen_suite(frozen)==frozen_suite(head)

def test_report_rejects_wrong_model_numeric_claim(tmp_path):
    from progress_report import write_report
    (tmp_path/'input.json').write_text('{"tests":37}')
    claim={'artifact':reference(tmp_path,'input.json'),'pointer':'/tests','expected':29,'unit':'methods'}
    data={'scope':'test','evidence_claims':[claim]}
    (tmp_path/'result.json').write_text(json.dumps(data))
    m=write_report(Path(__file__).resolve().parents[2],tmp_path,{'id':1,'goal':'验证错误声明'},{'agent_session_id':'test'},data,'错误数字不得通过')
    assert m['execution_status']=='failed' and not m['quality_gate']['passed']
    assert (tmp_path/'report.md').exists() and (tmp_path/'evidence-gates.json').exists()

def test_source_counter_change_with_updated_digest_still_rejected(tmp_path):
    counts={k:0 for k in ['passed','failure','error','skipped','expected_failure','unexpected_success']};counts['passed']=3
    data={'candidate':{'tests':{'status':'completed','counts':counts}}}
    (tmp_path/'result.json').write_text(json.dumps(data))
    f=make_test_figure(tmp_path,data)[0]
    measurements=json.loads((tmp_path/'test-measurements.json').read_text());measurements['counts']['passed']=4
    (tmp_path/'test-measurements.json').write_text(json.dumps(measurements))
    f['data']=reference(tmp_path,'test-measurements.json')
    assert not evaluate(tmp_path,[],[f])['passed']

def test_collector_distinguishes_method_from_subtest_errors():
    import importlib.util,unittest
    spec=importlib.util.spec_from_file_location('collector',Path(__file__).resolve().parents[2]/'tools/factory/resident/run_unittest.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Cases(unittest.TestCase):
        def test_boundary(self):
            for value in [1,2]:
                with self.subTest(value=value):raise TypeError('boundary')
    result=module.Results();unittest.defaultTestLoader.loadTestsFromTestCase(Cases).run(result)
    assert result.testsRun==1 and len(result.methods)==1 and result.methods[0]['status']=='error'
    assert len(result.subtests)==2 and all(r['status']=='error' for r in result.subtests)
