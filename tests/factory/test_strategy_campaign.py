import uuid
from sqlalchemy import text

ROLES=['platform','data','models','applications','infrastructure','verifier']

def setup(f):
    c,h=f['client'],f['headers']
    sessions={}
    for role in ['strategy']+ROLES:
        r=c.post('/api/v1/tensor/factory/agents',headers=h(1),json={'worker_id':'test-'+role,'role':role,'model':'gpt-6.1-sol','user_id':3})
        assert r.status_code==200,r.text
        sessions[role]=r.json()['agent_session_id']
    r=c.post('/api/v1/tensor/factory/campaigns',headers=h(1),json={'name':'钢铁能源服务','objective':'围绕钢铁行业能源服务开展业务调研、方案建设与任务派发，生成可验收设计。','agent_session_id':sessions['strategy']})
    assert r.status_code==200,r.text
    return sessions,r.json()['id']

def test_planner_dispatch_and_synthesis_are_session_scoped_and_idempotent(factory):
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(4),json={}).status_code==403
    assert c.post(path+'/tick/'+sessions['data'],headers=h(3),json={}).status_code==403
    assert not c.get(path,headers=h(7)).json()['campaigns']
    scheduled=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={})
    assert scheduled.status_code==200,scheduled.text
    mid=scheduled.json()['mission_id']
    assert not c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['scheduled']
    claim=c.post('/api/v1/tensor/missions/claim',headers=h(3),json={'agent_id':'test-strategy','agent_session_id':sessions['strategy'],'worker_role':'strategy','model':'gpt-6.1-sol','runner':'native'}).json()
    assert claim['mission']['campaign_phase']=='research'
    token=claim['mission']['claim_token']
    assert c.post(f'/api/v1/tensor/missions/{mid}/start',headers=h(3),json={'claim_token':token}).status_code==200
    body={'parent_id':mid,'claim_token':token,'plan':{'summary':'test'},'tasks':[{'role':r,'goal':f'{r}工作线：形成字段字典和业务建设方案，说明数据依赖与验收范围。','acceptance_criteria':['给出字段、单位、输入输出与正反例']} for r in ROLES]}
    invalid={**body,'tasks':[body['tasks'][0]]*6}
    assert c.post(f'{path}/{cid}/dispatch',headers=h(3),json=invalid).status_code==422
    result=c.post(f'{path}/{cid}/dispatch',headers=h(3),json=body)
    assert result.status_code==200,result.text
    children=result.json()['children'];assert len(children)==6
    assert c.post(f'{path}/{cid}/dispatch',headers=h(3),json=body).json()['children']==children
    with f['engine'].connect() as db:
        rows=db.execute(text('SELECT worker_role,agent_session_id,parent_mission_id FROM tensor_dev_missions WHERE parent_mission_id=:mid'),{'mid':mid}).mappings().all()
    assert all(str(r['agent_session_id'])==sessions[r['worker_role']] for r in rows)
    c.post(f'/api/v1/tensor/missions/{mid}/complete',headers=h(3),json={'claim_token':token,'status':'succeeded','acceptance':True,'summary':{'headline':'调研方案交付'}})
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='workstreams-running'
    with f['engine'].begin() as db:db.execute(text("UPDATE tensor_dev_missions SET status='awaiting_acceptance' WHERE parent_mission_id=:mid"),{'mid':mid})
    synth=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={})
    assert synth.status_code==200,synth.text
    assert synth.json()['phase']=='synthesis'
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET status='awaiting_acceptance' WHERE id=:mid"),{'mid':synth.json()['mission_id']})
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='autonomous-continuation-pending'
    continued=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()
    assert continued['scheduled'] and continued['phase']=='research'
    assert c.get(path,headers=h(1)).json()['campaigns'][0]['round']==2
    assert c.patch(f'{path}/{cid}',headers=h(2),json={'enabled':False}).status_code==403
    assert c.patch(f'{path}/{cid}',headers=h(1),json={'enabled':False}).status_code==200
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='no-enabled-campaign'

def test_research_rejects_fabricated_citations():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import validate_plan
    import pytest
    plan={'review_after_days':7,'harness_gaps':[],'tasks':[{'role':r,'goal':'这是可以验收的能源服务建设任务，必须明确业务需求和交付物。','acceptance_criteria':['交付方案']} for r in ROLES], 'business_needs':[{'need':'钢铁能效','confidence':'document-evidence','evidence_ids':['FAKE']} ]}
    with pytest.raises(ValueError):validate_plan(plan,[{'id':'DOE','verification':'public-page-fetched'}])


def test_planning_failure_becomes_harness_improvement_task(factory):
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    for attempt in range(3):
        r=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={})
        assert r.status_code==200,r.text
        mid=r.json()['mission_id']
        with f['engine'].begin() as db:
            db.execute(text("UPDATE tensor_dev_missions SET status='failed',result=CAST(:result AS jsonb) WHERE id=:mid"),{'mid':mid,'result':'{"error_class":"ValueError","safe_reason":"unsupported evidence citation"}'})
    outcome=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={})
    assert outcome.status_code==200,outcome.text
    assert outcome.json()['reason']=='harness-improvement-required'
    task=c.get('/api/v1/tensor/missions/'+str(outcome.json()['mission_id']),headers=h(2)).json()
    assert task['campaign_phase']=='harness_improvement' and task['worker_role']=='platform'
    assert task['agent_session_id']==sessions['platform']
    incidents=c.get(path+'/harness-incidents',headers=h(2)).json()['incidents']
    assert len(incidents)==3
    assert not c.get(path+'/harness-incidents',headers=h(7)).json()['incidents']


def test_runtime_capabilities_do_not_share_other_session_memories():
    import sys,json
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import runtime_evidence
    class Client:
        def call(self,method,path):
            if path.endswith('/campaigns'):return {'daily_task_slots_remaining':4}
            if path.endswith('/agents'):
                return {'sessions':[{'agent_session_id':'independent','worker_id':'worker','role':'data','online':True,'memory':['private context must stay in its session']}]}
            return {'id':11,'status':'running'}
    result=runtime_evidence(Client(),{'id':11})
    assert result['observed']['sessions'][0]['memory_entry_count']==1
    assert 'private context must stay in its session' not in json.dumps(result)


def test_manual_planning_assistance_is_recorded(factory):
    f=factory;c,h=f['client'],f['headers'];_,cid=setup(f)
    path=f'/api/v1/tensor/factory/campaigns/{cid}/feedback'
    r=c.post(path,headers=h(2),json={'content':'早期人工协助：这里只提供业务事实和规划约束，不代写具体任务。','kind':'planning-assistance'})
    assert r.status_code==200,r.text
    rows=c.get('/api/v1/tensor/factory/campaigns/harness-incidents',headers=h(2)).json()['incidents']
    assert len(rows)==1 and rows[0]['kind'].startswith('manual-planning-assistance')
    assert c.post(path,headers=h(7),json={'content':'跨组织的反馈不能影响本组织的业务建设计划。'}).status_code==404


def test_structured_wait_and_budget_recovery(factory):
    import json
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    first=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['mission_id']
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET campaign_phase='synthesis',status='succeeded',result=CAST(:r AS jsonb) WHERE id=:mid"),{'mid':first,'r':json.dumps({'continuation':{'action':'wait','reason':'真实数据授权未到位'}})})
        db.execute(text("UPDATE factory_strategy_campaigns SET phase='synthesis' WHERE id=:cid"),{'cid':cid})
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='weekly-monitoring'
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='weekly-monitoring'
    with f['engine'].begin() as db:
        db.execute(text("UPDATE factory_strategy_campaigns SET next_review_at=now()-interval '1 second',round=4 WHERE id=:cid"),{'cid':cid})
    resumed=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()
    assert resumed['scheduled'] and resumed['phase']=='research'
    # Lifetime round number does not permanently exhaust a rolling budget.
    assert c.get(path,headers=h(1)).json()['campaigns'][0]['round']==5


def test_full_verified_deliverable_and_tamper_rejection(tmp_path):
    import sys,json,hashlib,pytest
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import verified_json
    folder=tmp_path/'.runtime/agent/m1/artifacts';folder.mkdir(parents=True)
    content={'scope':'design','deliverable':{'content_markdown':'正文'*3000}}
    p=folder/'result.json';p.write_text(json.dumps(content))
    ref={'name':'result.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size}
    class Client:
        def call(self,*args):return {'status':'succeeded','result':{'artifacts':[ref]}}
    got=verified_json(Client(),tmp_path,1)
    assert got['deliverable']==content['deliverable']
    p.write_text('{}')
    with pytest.raises(ValueError):verified_json(Client(),tmp_path,1)


def test_daily_quota_pauses_and_automatically_resumes(factory,monkeypatch):
    monkeypatch.setenv('FACTORY_CAMPAIGN_TASKS_PER_DAY','12')
    import json
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    mid=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['mission_id']
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET status='succeeded',campaign_phase='synthesis',result=CAST(:r AS jsonb) WHERE id=:mid"),{'mid':mid,'r':json.dumps({'continuation':{'action':'continue','reason':'存在可执行增量任务'}})})
        db.execute(text("UPDATE factory_strategy_campaigns SET phase='monitoring',next_review_at=now() WHERE id=:cid"),{'cid':cid})
        for i in range(9):
            db.execute(text("INSERT INTO tensor_dev_missions(organization_id,goal,repo,base_ref,budget,runner,origin,model,created_by,campaign_id,status) VALUES(1,'quota fixture','factory-project','main','{}','native','agent','gpt-6.1-sol',3,:cid,'succeeded')"),{'cid':cid})
    blocked=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()
    assert blocked['reason']=='daily-campaign-cap-reserve-work-and-synthesis'
    view=c.get(path,headers=h(1)).json()['campaigns'][0]
    assert view['scheduler_state']=='waiting-daily-budget' and view['automatic_resume_at']
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET created_at=now()-interval '25 hours' WHERE campaign_id=:cid"),{'cid':cid})
    resumed=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()
    assert resumed['scheduled'] and resumed['phase']=='research'


def test_verifier_waits_for_producer_and_cycles_are_rejected(factory):
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    mid=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['mission_id']
    claim=c.post('/api/v1/tensor/missions/claim',headers=h(3),json={'agent_id':'test-strategy','agent_session_id':sessions['strategy'],'worker_role':'strategy','model':'gpt-6.1-sol','runner':'native'}).json()
    token=claim['mission']['claim_token']
    c.post(f'/api/v1/tensor/missions/{mid}/start',headers=h(3),json={'claim_token':token})
    tasks=[{'role':role,'goal':'实现离线计量准入原型及独立验证，保留真实运行与错误证据。','acceptance_criteria':['真实产物可追溯'],'execution_mode':'python-package'} for role in ['data','verifier']]
    cyclic=[{**tasks[0],'depends_on_roles':['verifier']},tasks[1]]
    assert c.post(f'{path}/{cid}/dispatch',headers=h(3),json={'parent_id':mid,'claim_token':token,'plan':{},'tasks':cyclic}).status_code==422
    dispatched=c.post(f'{path}/{cid}/dispatch',headers=h(3),json={'parent_id':mid,'claim_token':token,'plan':{},'tasks':tasks})
    assert dispatched.status_code==200,dispatched.text
    body={'agent_id':'test-verifier','agent_session_id':sessions['verifier'],'worker_role':'verifier','model':'gpt-6.1-sol','runner':'native'}
    assert not c.post('/api/v1/tensor/missions/claim',headers=h(3),json=body).json()['claimed']
    producer=next(x['id'] for x in dispatched.json()['children'] if x['worker_role']=='data')
    with f['engine'].begin() as db:db.execute(text("UPDATE tensor_dev_missions SET status='awaiting_acceptance' WHERE id=:mid"),{'mid':producer})
    assert c.post('/api/v1/tensor/missions/claim',headers=h(3),json=body).json()['claimed']


def test_configured_budget_resumes_without_resetting_history(factory,monkeypatch):
    monkeypatch.setenv('FACTORY_CAMPAIGN_TASKS_PER_DAY','12')
    import json
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    mid=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['mission_id']
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET status='succeeded',result=CAST(:r AS jsonb) WHERE id=:mid"),{'mid':mid,'r':json.dumps({'continuation':{'action':'continue','reason':'repair'}})})
        db.execute(text("UPDATE factory_strategy_campaigns SET phase='monitoring',next_review_at=now() WHERE id=:cid"),{'cid':cid})
        for i in range(11):
            db.execute(text("INSERT INTO tensor_dev_missions(organization_id,goal,repo,base_ref,budget,runner,origin,model,created_by,campaign_id,status) VALUES(1,'quota fixture','factory-project','main','{}','native','agent','gpt-6.1-sol',3,:cid,'succeeded')"),{'cid':cid})
    assert not c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['scheduled']
    monkeypatch.setenv('FACTORY_CAMPAIGN_TASKS_PER_DAY','48');monkeypatch.setenv('FACTORY_CAMPAIGN_ROUNDS_PER_DAY','16')
    view=c.get(path,headers=h(1)).json()
    assert view['daily_task_limit']==48 and view['daily_task_slots_remaining']==36
    assert view['campaigns'][0]['effective_round_limit']==16
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['scheduled']
    with f['engine'].begin() as db:
        assert db.execute(text('SELECT count(*) FROM tensor_dev_missions WHERE campaign_id=:cid'),{'cid':cid}).scalar()==13


def test_unlimited_campaign_budget(factory,monkeypatch):
    monkeypatch.setenv('FACTORY_CAMPAIGN_TASKS_PER_DAY','0');monkeypatch.setenv('FACTORY_CAMPAIGN_ROUNDS_PER_DAY','0')
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    view=c.get(path,headers=h(1)).json()
    assert view['daily_task_limit'] is None and view['daily_task_slots_remaining'] is None
    assert view['campaigns'][0]['effective_round_limit'] is None
    from api.factory_strategy import task_limit,round_limit
    assert task_limit() is None and round_limit({'max_rounds':4}) is None
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['scheduled']


def test_unlimited_runtime_allows_six_workstreams():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import runtime_evidence
    class Client:
        def call(self,method,path):
            if path.endswith('/campaigns'):return {'daily_task_slots_remaining':None}
            if path.endswith('/agents'):return {'sessions':[]}
            return {'id':1,'status':'running'}
    result=runtime_evidence(Client(),{'id':1})
    assert result['observed']['max_workstreams_this_round']==6


def test_planner_preserves_rejection_and_corrects_citation(tmp_path):
    import sys,json
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import propose_plan
    invalid={'review_after_days':1,'harness_gaps':[],'tasks':[],'business_needs':[{'need':'diagnose','confidence':'document-evidence','evidence_ids':['TASK-FAKE']}]}
    valid={**invalid,'business_needs':[{'need':'diagnose','confidence':'document-evidence','evidence_ids':['TASK-20']}]}
    calls=[]
    def infer(prompt,schema):
        calls.append(prompt);return {'data':invalid if len(calls)==1 else valid}
    result,plan=propose_plan(tmp_path,'plan',[{'id':'TASK-20','verification':'registered-task-artifact'}],infer)
    assert plan==valid and 'TASK-FAKE' in calls[1] and 'TASK-20' in calls[1]
    assert json.loads((tmp_path/'planning-attempt-1.json').read_text())==invalid
    assert not json.loads((tmp_path/'planning-validation-1.json').read_text())['passed']


def test_diagnosis_does_not_replan_without_changed_repair_evidence(factory,monkeypatch):
    import json
    import api.factory_strategy as strategy
    f=factory;c,h=f['client'],f['headers'];sessions,cid=setup(f)
    path='/api/v1/tensor/factory/campaigns'
    mid=c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['mission_id']
    with f['engine'].begin() as db:
        db.execute(text("UPDATE tensor_dev_missions SET status='awaiting_acceptance',campaign_phase='harness_improvement' WHERE id=:mid"),{'mid':mid})
        db.execute(text("UPDATE factory_strategy_campaigns SET phase='needs_harness_improvement',plan=CAST(:p AS jsonb) WHERE id=:cid"),{'cid':cid,'p':json.dumps({'_recovery_checkpoint':{'runtime':'old','implementation_hashes':[]}})})
    monkeypatch.setattr(strategy,'runtime_fingerprint',lambda:'old')
    for _ in range(20):assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='awaiting-repair-change'
    with f['engine'].begin() as db:assert db.execute(text('SELECT count(*) FROM tensor_dev_missions WHERE campaign_id=:cid'),{'cid':cid}).scalar()==1
    assert c.get(path,headers=h(1)).json()['campaigns'][0]['scheduler_state']=='blocked-unchanged-failure'
    monkeypatch.setattr(strategy,'runtime_fingerprint',lambda:'patched')
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['reason']=='harness-diagnosis-replanning'
    assert c.post(path+'/tick/'+sessions['strategy'],headers=h(3),json={}).json()['scheduled']


def test_prompt_excerpt_keeps_counts_and_source_refs_without_recursive_code():
    import sys,json
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import evidence_excerpt
    raw={'prototype_files':{'x.py':'x'*200000},'deliverable':{'content_markdown':'m'*100000},'candidate':{'tests':{'counts':{'error':2},'methods':[{'id':'x'}]*1000}},'artifact_refs':[{'name':'package.json','sha256':'abc'}]}
    view=evidence_excerpt(raw)
    assert len(json.dumps(view))<5000 and view['candidate']['tests']['counts']['error']==2
    assert view['artifact_refs'][0]['sha256']=='abc' and 'prototype_files' not in view


def test_large_registered_report_is_not_mislabeled_hash_failure(tmp_path):
    import sys,json,hashlib
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
    from campaign import verified_json
    folder=tmp_path/'.runtime/agent/m1/artifacts';folder.mkdir(parents=True)
    raw=json.dumps({'scope':'design','deliverable':{'content_markdown':'x'*160000}}).encode();(folder/'result.json').write_bytes(raw)
    class Client:
        def call(self,*args):return {'status':'succeeded','result':{'artifacts':[{'name':'result.json','size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}}
    assert verified_json(Client(),tmp_path,1)['verified_artifact']

def test_generic_campaign_uses_profile_project_and_rejects_unconfigured_target(factory,monkeypatch):
    import pytest
    from api.factory_strategy import campaign_project
    assert campaign_project()=='factory-project'
    monkeypatch.setenv('FACTORY_CAMPAIGN_REPO','outside-project')
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:campaign_project()
    assert error.value.status_code==422
