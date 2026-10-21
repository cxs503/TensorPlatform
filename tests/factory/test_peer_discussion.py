import uuid
from test_resident_sessions import create

BASE='/api/v1/tensor/factory/agents/discussion/messages'
def message(**kw):return {'kind':'question','body':'这份证据是否支持当前判断？欢迎提出不同意见。','client_key':str(uuid.uuid4()),**kw}

def test_peer_identity_reply_isolation_and_no_task_creation(factory):
 f=factory;c=f['client'];h=f['headers'];a=create(f);b=create(f,'peer-b',uid=4)
 payload=message(author_session_id=a,recipient_session_id=b)
 assert c.post(BASE,headers=h(4),json=payload).status_code==403
 first=c.post(BASE,headers=h(3),json=payload);assert first.status_code==200,first.text
 mid=first.json()['message']['id']
 assert first.json()['creates_mission'] is False
 assert c.post(BASE,headers=h(3),json=payload).json()['message']['id']==mid
 assert c.post(BASE,headers=h(3),json={**payload,'body':'changed'}).status_code==409
 reply=c.post(BASE,headers=h(4),json=message(author_session_id=b,reply_to=mid,kind='critique',body='我认为证据不足，建议增加负对照。'))
 assert reply.status_code==200 and reply.json()['message']['reply_to']==mid
 assert len(c.get(BASE,headers=h(3)).json()['messages'])==2
 assert c.get(BASE,headers=h(7)).json()['messages']==[]
 assert c.post(BASE,headers=h(7),json=message(reply_to=mid)).status_code==404
 from sqlalchemy import text
 with f['engine'].connect() as db:assert db.execute(text('SELECT count(*) FROM tensor_dev_missions')).scalar()==0

def test_external_peer_is_identified_and_cannot_impersonate(factory):
 f=factory;c=f['client'];h=f['headers'];a=create(f)
 assert c.post(BASE,headers=h(1),json=message(author_session_id=a)).status_code==403
 r=c.post(BASE,headers=h(1),json=message(participant_label='Codex',recipient_session_id=a))
 assert r.status_code==200,r.text
 m=r.json()['message'];assert m['author_user_id']==1 and m['author_session_id'] is None and m['participant_label']=='Codex'
 assert c.post(BASE,headers=h(3),json=message()).status_code==403
 assert c.post(BASE,headers=h(5),json=message()).status_code==403

def test_idle_discussion_can_decline_and_does_not_repeat(tmp_path):
 import sys,json
 from pathlib import Path
 sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
 from peer_discussion import converse
 calls=[]
 class API:
  def call(self,method,path):
   calls.append(path)
   return {'messages':[] if 'after=8' in path else [{'id':8,'author_session_id':None,'recipient_session_id':'a','body':'请谈谈你的不同看法'}]}
 def generate(prompt,schema,**kw):
  assert '不是任务命令' in prompt
  return {'data':{'tool':'finish','arguments_json':json.dumps({'result':{'summary':'暂无新增证据，选择不回复','responded':False}}),'decision':'no substantive contribution'},'usage':{}}
 resident={'agent_session_id':'a','model':'gpt-6.1-sol'}
 assert converse(API(),resident,tmp_path,tmp_path,generate)['responded'] is False
 assert converse(API(),resident,tmp_path,tmp_path,generate) is None
 assert json.loads((tmp_path/'.runtime/sessions/a/discussion-cursor.json').read_text())['after']==8

def test_community_topics_preserve_dissent_and_do_not_create_consensus(factory):
 f=factory;c=f['client'];h=f['headers'];a=create(f);b=create(f,'community-b',uid=4)
 path='/api/v1/tensor/factory/agents/community/topics'
 body={'author_session_id':a,'title':'先修复证据通道还是继续业务原型？','category':'proposal','opening':'我建议先冻结可复现失败，请其他成员提出反例。','client_key':str(uuid.uuid4())}
 r=c.post(path,headers=h(3),json=body);assert r.status_code==200,r.text
 topic=r.json()['topic']['id']
 assert c.post(path,headers=h(3),json=body).json()['topic']['id']==topic
 for sid,uid,stance in [(a,3,'support'),(b,4,'oppose')]:
  r=c.post(f'{path}/{topic}/position',headers=h(uid),json={'agent_session_id':sid,'stance':stance,'reason':'保留独立判断及对应证据'})
  assert r.status_code==200 and r.json()['represents_consensus'] is False
 rows=c.get(path,headers=h(3)).json()['topics'];assert len(rows)==1
 assert {p['stance'] for p in rows[0]['positions']}=={'support','oppose'}
 assert c.get(path,headers=h(7)).json()['topics']==[]
 msgs=c.get(BASE,headers=h(3)).json()['messages'];parent=msgs[0]
 reply=c.post(BASE,headers=h(4),json=message(author_session_id=b,reply_to=parent['id']))
 assert reply.status_code==200 and reply.json()['message']['topic_id']==topic
 assert c.post(BASE,headers=h(7),json=message(topic_id=topic)).status_code==404
 assert c.post(f'{path}/{topic}/position',headers=h(3),json={'agent_session_id':b,'stance':'support','reason':'冒充他人'}).status_code==403

def test_api_connection_refusal_retries_before_request_can_commit(monkeypatch):
 import sys
 from pathlib import Path
 sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
 import run_resident_agent as resident
 from urllib.error import URLError
 from urllib.request import Request
 from io import BytesIO
 calls=[]
 def open_url(*a,**kw):
  calls.append(1)
  if len(calls)<3:raise URLError(ConnectionRefusedError(111,'connection refused'))
  return BytesIO(b'{"ok":true}')
 monkeypatch.setattr(resident,'urlopen',open_url)
 monkeypatch.setattr(resident.time,'sleep',lambda _:None)
 assert resident.read_response(Request('http://127.0.0.1/test'))=={'ok':True}
 assert len(calls)==3

def test_community_can_reflect_without_external_message_and_wait_afterward(tmp_path):
 from peer_discussion import converse
 import json
 calls=[]
 class API:
  def call(self,*a):return {'messages':[]}
 def generate(prompt,schema,**kw):
  calls.append(prompt)
  assert '没有新消息也可以' in prompt
  return {'data':{'tool':'finish','arguments_json':json.dumps({'result':{'summary':'缺少新证据，暂不制造改动','responded':False}}),'decision':'wait for substantive evidence'},'usage':{}}
 resident={'agent_session_id':'autonomous-community','model':'gpt-6.1-sol','memory':['过去一次真实任务的记录']}
 assert converse(API(),resident,tmp_path,tmp_path,generate)['responded'] is False
 assert converse(API(),resident,tmp_path,tmp_path,generate) is None
 assert len(calls)==1
 assert (tmp_path/'.runtime/sessions/autonomous-community/memory.jsonl').exists()
