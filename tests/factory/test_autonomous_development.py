import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
import development


def test_failed_execution_drives_one_actual_repair_attempt(tmp_path,monkeypatch):
    calls=[]
    def infer(prompt,schema):
        calls.append(prompt)
        return {'data':{'summary':'prototype','files':[{'name':'code.py','source':'VALUE=1'},{'name':'test_code.py','source':'import unittest'}],'limitations':['not production']}}
    results=iter([{'process_passed':False,'exit_code':1,'timed_out':False,'package_sha256':'first','log':'assert failed'}, {'process_passed':True,'exit_code':0,'timed_out':False,'package_sha256':'second','log':'OK'}])
    monkeypatch.setattr(development,'sandbox',lambda package:next(results))
    result,text,success=development.develop(tmp_path,infer,lambda *args:None,'task',[])
    assert success and len(calls)==2 and 'assert failed' in calls[1]
    assert len(result['attempts'])==2 and (tmp_path/'package-attempt-1.json').exists()
    assert 'not-production' in result['scope']


def test_independent_tests_cannot_replace_implementation(tmp_path,monkeypatch):
    package={'files':{'code.py':'VALUE=1','test_code.py':'import unittest'}}
    def infer(*args):return {'data':{'summary':'review','files':[{'name':'code.py','source':'VALUE=2'}],'limitations':[]}}
    with pytest.raises(ValueError):development.independent_verify(tmp_path,1,package,'goal',infer)
    assert package['files']['code.py']=='VALUE=1'

def test_repair_compares_prior_implementation_with_frozen_candidate_tests(tmp_path,monkeypatch):
    import json
    calls=[]
    def infer(*args):return {'data':{'summary':'repair','files':[{'name':'code.py','source':'VALUE=2'},{'name':'test_code.py','source':'NEW_TESTS'}],'limitations':[]}}
    def sandbox(package):
        calls.append(package)
        return {'process_passed':True,'exit_code':0,'timed_out':False,'package_sha256':'hash','log':''}
    monkeypatch.setattr(development,'sandbox',sandbox)
    prior={'mission_id':20,'prototype_files':{'code.py':'VALUE=1','test_code.py':'OLD_TESTS'}}
    result,_,_=development.develop(tmp_path,infer,lambda *a:None,'task',[],baseline_candidates=[prior])
    assert calls[1]['files']=={'code.py':'VALUE=1','test_code.py':'NEW_TESTS'}
    assert result['baseline_attribution']['status']=='unknown' # missing structured environment cannot prove attribution
    assert json.loads((tmp_path/'baseline-package.json').read_text())==calls[1]
