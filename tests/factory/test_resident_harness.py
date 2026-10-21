import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from resident_harness import deliberate,Tools,HarnessBlocked

TARGET={'type':'object','properties':{'summary':{'type':'string'}},'required':['summary'],'additionalProperties':False}
def action(tool,args):return {'data':{'tool':tool,'arguments_json':json.dumps(args),'decision':'inspect observed failure'},'usage':{'input_tokens':5},'latency_seconds':1}

def test_observations_drive_next_decision_and_persist_only_own_session(tmp_path):
    answers=iter([action('mission.inspect',{'mission_id':600}),action('finish',{'result':{'summary':'observed failure'}})])
    prompts=[]
    class T:
        current_context=''
        def call(self,name,args):return {'status':'failed','safe_reason':'artifact size limit'}
    def infer(prompt,schema):prompts.append(prompt);return next(answers)
    result=deliberate('diagnose',TARGET,infer=infer,tools=T(),private_folder=tmp_path/'session-a',event=lambda *a:None)
    assert 'artifact size limit' in prompts[1] and result['harness']['steps']==2
    assert result['usage']['input_tokens']==10 and (tmp_path/'session-a/deliberation.jsonl').exists()
    assert not (tmp_path/'session-b').exists()

def test_repeated_unchanged_tool_request_stops_without_side_effect(tmp_path):
    calls=[]
    class T:
        current_context=''
        def call(self,name,args):calls.append(name);return {'status':'unchanged'}
    with pytest.raises(HarnessBlocked,match='unchanged'):
        deliberate('diagnose',TARGET,infer=lambda *a:action('mission.inspect',{'mission_id':1}),tools=T(),private_folder=tmp_path,event=lambda *a:None)
    assert calls==['mission.inspect']

def test_invalid_final_schema_is_returned_for_correction(tmp_path):
    answers=iter([action('finish',{'result':{'wrong':1}}),action('finish',{'result':{'summary':'corrected'}})])
    prompts=[]
    class T:current_context=''
    def infer(prompt,schema):prompts.append(prompt);return next(answers)
    assert deliberate('task',TARGET,infer=infer,tools=T(),private_folder=tmp_path,event=lambda *a:None)['data']=={'summary':'corrected'}
    assert 'ValidationError' in prompts[1]

def test_large_context_uses_on_demand_reads(tmp_path):
    class T:current_context=''
    tool=T();seen=[]
    def infer(prompt,schema):seen.append(prompt);return action('finish',{'result':{'summary':'bounded'}})
    deliberate('x'*200000,TARGET,infer=infer,tools=tool,private_folder=tmp_path,event=lambda *a:None)
    assert len(seen[0])<30000 and len(tool.current_context)==200000 and list(tmp_path.glob('input-*.txt'))

def test_tool_paths_cannot_reach_credentials_or_other_sessions(tmp_path):
    t=Tools(None,tmp_path,tmp_path,tmp_path)
    for name in ['.runtime/local-accounts.json','../other.py','backend/../../other.py','backend/auth.json']:
        with pytest.raises(ValueError):t.path(tmp_path,name)
    t.current_context='own session context'
    assert t.call('context.read',{'query':'own'})['content']=='own session context'

def test_persona_and_full_memory_survive_new_instances_and_model_changes(tmp_path):
    from persona import Persona
    a=Persona(tmp_path/'session-a','session-a')
    a.update({'name':'观察者','values':['证据优先'],'working_style':'先复现实验，再提出不同意见'})
    a.update({'goals':['改善能源数据质量']})
    for i in range(30):a.append(f'经验{i}',kind='lesson')
    b=Persona(tmp_path/'session-a','session-a')
    assert b.read()['name']=='观察者' and b.read()['working_style'].startswith('先复现')
    assert b.search('经验0')['matches'][0]['text']=='经验0'
    assert len((tmp_path/'session-a/memory.jsonl').read_text().splitlines())==30
    assert Persona(tmp_path/'session-b','session-b').search()['matches']==[]
    with pytest.raises(ValueError):b.update({'agent_session_id':'session-b'})
    assert len((tmp_path/'session-a/persona-history.jsonl').read_text().splitlines())==2

def test_own_experiment_is_readable_before_registration_and_context_not_line_redacted(tmp_path):
    t=Tools(None,tmp_path,tmp_path,tmp_path)
    raw=json.dumps({'files':{'test.py':'def test_token_rejection(): pass'}})
    (tmp_path/'harness-experiment-1-package.json').write_text(raw)
    assert t.call('experiment.read',{'name':'harness-experiment-1-package.json'})['content']==raw
    with pytest.raises(ValueError):t.call('experiment.read',{'name':'../persona.json'})
    t.current_context=raw
    assert t.call('context.read',{})['content']==raw

def test_historical_experiment_read_requires_same_session(tmp_path):
    from persona import Persona
    class API:
        def call(self,*args):return {'agent_session_id':'own' if args[1].endswith('/608') else 'another'}
    t=Tools(API(),tmp_path,tmp_path,tmp_path)
    t.persona=Persona(tmp_path/'own','own')
    folder=tmp_path/'.runtime/agent/m608/artifacts';folder.mkdir(parents=True)
    (folder/'harness-experiment-1-package.json').write_text('{"files":{}}')
    assert t.call('experiment.read',{'mission_id':608,'name':'harness-experiment-1-package.json'})['content']=='{"files":{}}'
    with pytest.raises(ValueError,match='another session'):t.call('experiment.read',{'mission_id':607,'name':'harness-experiment-1-package.json'})
