import importlib.util
from pathlib import Path

def create(f, name='resident-a', model='gpt-6.1-sol', uid=3):
    c=f['client']; h=f['headers']
    r=c.post('/api/v1/tensor/factory/agents',headers=h(1),json={'worker_id':name,'role':'data','model':model,'user_id':uid})
    assert r.status_code==200,r.text
    return r.json()['agent_session_id']

def test_session_identity_and_model_change_preserve_memory(factory):
    f=factory;c=f['client'];h=f['headers'];sid=create(f); second=create(f,'resident-b','gpt-6-sol')
    assert sid!=second
    p=f'/api/v1/tensor/factory/agents/{sid}'
    assert c.post(p+'/heartbeat',headers=h(4),json={'state':'idle'}).status_code==403
    assert c.post(p+'/heartbeat',headers=h(3),json={'state':'idle','memory_entry':'independent memory'}).status_code==200
    assert c.patch(p,headers=h(2),json={'model':'gpt-6-sol'}).status_code==403
    assert c.patch(p,headers=h(1),json={'model':'gpt-6-sol'}).json()['agent_session_id']==sid
    sessions=c.get('/api/v1/tensor/factory/agents',headers=h(2)).json()['sessions']
    a=next(s for s in sessions if s['agent_session_id']==sid);b=next(s for s in sessions if s['agent_session_id']==second)
    assert a['memory']==['independent memory'] and b['memory']==[]
    assert len(a['model_history'])==2 and a['online']
    assert c.patch(p,headers=h(7),json={'enabled':False}).status_code==404

def test_session_claim_routing_and_idempotency(factory):
    f=factory;c=f['client'];h=f['headers'];sid=create(f); second=create(f,'resident-b','gpt-6-sol',4)
    body={'goal':'修复空数据质量判定并运行回归测试','repo':'factory-project','runner':'native','agent_session_id':sid,'task_key':'test:quality'}
    r=c.post('/api/v1/tensor/missions',headers=h(2),json=body);assert r.status_code==200,r.text
    assert c.post('/api/v1/tensor/missions',headers=h(2),json=body).json()['id']==r.json()['id']
    assert c.post('/api/v1/tensor/missions',headers=h(2),json={**body,'agent_session_id':second}).status_code==409
    assert c.patch(f'/api/v1/tensor/factory/agents/{sid}',headers=h(1),json={'model':'gpt-6-sol'}).status_code==409
    claim={'agent_id':'test','agent_session_id':sid,'worker_role':'data','model':'gpt-6.1-sol','runner':'native'}
    assert c.post('/api/v1/tensor/missions/claim',headers=h(4),json=claim).status_code==403
    assert not c.post('/api/v1/tensor/missions/claim',headers=h(3),json={'agent_id':'legacy','runner':'native'}).json()['claimed']
    first=c.post('/api/v1/tensor/missions/claim',headers=h(3),json=claim)
    assert first.status_code==200,first.text
    assert first.json()['mission']['agent_session_id']==sid
    assert c.post('/api/v1/tensor/missions/claim',headers=h(3),json=claim).json()['busy']
    assert not c.get('/api/v1/tensor/factory/agents',headers=h(7)).json()['sessions']

def test_patch_cannot_change_unadvertised_methods():
    path=Path(__file__).resolve().parents[2]/'scripts/run_resident_agent.py'
    spec=importlib.util.spec_from_file_location('resident',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    original='import os\nclass DataQualityService:\n    def check_completeness(self):\n        return 1\n    def other(self):\n        return 2\n'
    assert 'return 0' in m.patched(original,[{'old_text':'return 1','new_text':'return 0'}])[0]
    import pytest
    with pytest.raises(ValueError): m.patched(original,[{'old_text':'return 2','new_text':'return 0'}])
    with pytest.raises(ValueError): m.patched(original,[{'old_text':'import os','new_text':'import subprocess'}])
