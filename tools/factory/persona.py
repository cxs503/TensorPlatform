"""Durable, agent-authored identity and memory, scoped to a single session directory."""
import json
import os
from pathlib import Path
import time
import uuid

FIELDS={'name','self_description','working_style','values','goals','preferences','relationships','notes'}
class Persona:
 def __init__(self,folder,session_id):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True,mode=0o700);self.session_id=str(session_id)
  self.path=self.folder/'persona.json';self.memory=self.folder/'memory.jsonl'
 def read(self):
  if self.path.exists():
   data=json.loads(self.path.read_text())
   if data['agent_session_id']!=self.session_id:raise ValueError('persona identity mismatch')
   return data
  data={'agent_session_id':self.session_id,'version':0,'name':'','self_description':'','working_style':'','values':[],'goals':[],'preferences':{},'relationships':[],'notes':[]}
  return data
 def update(self,changes):
  if not isinstance(changes,dict) or not set(changes)<=FIELDS:raise ValueError('identity immutable; unknown persona fields')
  data=self.read();before=data.copy();data.update(changes)
  encoded=json.dumps(data,ensure_ascii=False,allow_nan=False)
  if len(encoded.encode())>24000:raise ValueError('persona exceeds 24KB; archive detail in durable memory')
  for key in ['name','self_description','working_style']:
   if not isinstance(data[key],str):raise ValueError('persona text fields must be strings')
  for key in ['values','goals','relationships','notes']:
   if not isinstance(data[key],list):raise ValueError('persona list fields must be arrays')
  if not isinstance(data['preferences'],dict):raise ValueError('preferences must be an object')
  data['version']+=1;data['updated_at']=time.time()
  with (self.folder/'persona-history.jsonl').open('a') as out:out.write(json.dumps({'before':before,'after':data},ensure_ascii=False)+'\n')
  temp=self.folder/('persona-'+uuid.uuid4().hex+'.tmp')
  with temp.open('x') as out:out.write(json.dumps(data,ensure_ascii=False,indent=2))
  os.replace(temp,self.path)
  return data
 def append(self,text,*,kind='experience',evidence_refs=None):
  if not isinstance(text,str) or not 1<=len(text)<=4000:raise ValueError('memory text must have 1..4000 characters')
  if kind not in ['experience','fact','goal','preference','lesson','uncertainty','relationship']:raise ValueError('invalid memory kind')
  refs=evidence_refs or []
  if not isinstance(refs,list) or len(refs)>16 or any(not isinstance(r,str) or len(r)>300 for r in refs):raise ValueError('invalid evidence references')
  item={'id':str(uuid.uuid4()),'agent_session_id':self.session_id,'kind':kind,'text':text,'evidence_refs':refs,'created_at':time.time()}
  with self.memory.open('a') as out:out.write(json.dumps(item,ensure_ascii=False)+'\n')
  return item
 def search(self,query='',limit=6):
  if not isinstance(query,str) or len(query)>200:raise ValueError('invalid memory query')
  matches=[]
  if self.memory.exists():
   for line in self.memory.open():
    item=json.loads(line)
    if not query or query.casefold() in item['text'].casefold():matches.append(item)
    if len(matches)>max(1,min(12,int(limit))):matches.pop(0)
  return {'matches':matches,'query':query,'archived_records_preserved':True}
