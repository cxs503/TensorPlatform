"""Persistent session identities, independent of roles, accounts and model choice."""
import json
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.factory_codex import allowed_models

router = APIRouter(prefix='/tensor/factory/agents', tags=['factory-agents'])
Role = Literal['platform', 'data', 'models', 'applications', 'infrastructure', 'verifier', 'strategy']

class NewSession(BaseModel):
    worker_id: str = Field(min_length=2, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    role: Role
    model: str = Field(min_length=2, max_length=64)
    user_id: int = Field(ge=1)

class Configuration(BaseModel):
    model: str | None = None
    enabled: bool | None = None

class Beat(BaseModel):
    state: Literal['idle', 'running', 'error', 'offline']
    current_mission_id: int | None = None
    last_error: str | None = Field(default=None, max_length=500)
    memory_entry: str | None = Field(default=None, max_length=1800)


def load(db, sid, user, *, owner=False, lock=False):
    r = db.execute(text('SELECT * FROM factory_resident_workers WHERE agent_session_id=:sid AND organization_id=:org' + (' FOR UPDATE' if lock else '')),
                   {'sid': str(sid), 'org': resolved_org(None, user, db)}).mappings().first()
    if not r:
        raise HTTPException(404, 'agent session not found')
    if owner and (r['user_id'] != user['id'] or str(user.get('role', '')).upper() != 'AGENT'):
        raise HTTPException(403, 'agent session belongs to another principal')
    return r

@router.get('')
def listing(db: Session = Depends(get_db), user=Depends(require_auth)):
    rows = db.execute(text("""SELECT *, COALESCE(heartbeat_at > now()-interval '90 seconds',false) AS online
        FROM factory_resident_workers WHERE organization_id=:org ORDER BY created_at,worker_id"""),
        {'org': resolved_org(None, user, db)}).mappings().all()
    sessions=[]
    for row in rows:
        item=dict(row);folder=Path(__file__).resolve().parents[2]/'.runtime/sessions'/str(row['agent_session_id'])
        try:
            persona=json.loads((folder/'persona.json').read_text())
            item['persona_name']=persona.get('name','');item['persona_version']=persona.get('version',0)
        except (OSError,ValueError):item['persona_name']='';item['persona_version']=0
        try:
            with (folder/'memory.jsonl').open() as memory:item['durable_memory_count']=sum(1 for _ in memory)
        except OSError:item['durable_memory_count']=0
        sessions.append(item)
    return {'sessions': sessions, 'models': allowed_models(),
            'identity_boundary': 'agent_session_id', 'task_session_is_audit_trace': True}

@router.get('/comparisons')
def comparisons(db: Session = Depends(get_db), user=Depends(require_auth)):
    rows = db.execute(text("SELECT id,agent_session_id,model,status,result->'comparison' AS comparison FROM tensor_dev_missions WHERE organization_id=:org AND result->'comparison'->>'comparison_task' IS NOT NULL ORDER BY id"),
                      {'org': resolved_org(None, user, db)}).mappings().all()
    return {'runs': [dict(r) for r in rows], 'note': '单项任务的观测结果，不代表模型综合排名；请核对 prompt、baseline、test_suite 的 SHA256 一致。'}

@router.post('')
def create(body: NewSession, db: Session = Depends(get_db), user=Depends(require_role('ADMIN'))):
    if body.model not in allowed_models():
        raise HTTPException(422, 'model is not allowlisted')
    org = resolved_org(None, user, db)
    valid = db.execute(text("SELECT id FROM users WHERE id=:uid AND organization_id=:org AND upper(role)='AGENT' AND is_active=1"), {'uid': body.user_id, 'org': org}).scalar()
    if not valid:
        raise HTTPException(422, 'session requires an active AGENT execution principal')
    if db.execute(text('SELECT 1 FROM factory_resident_workers WHERE worker_id=:wid'), {'wid': body.worker_id}).scalar():
        raise HTTPException(409, 'worker id already registered')
    sid = str(uuid.uuid4())
    db.execute(text("""INSERT INTO factory_resident_workers(agent_session_id,user_id,organization_id,worker_id,role,model,model_history)
        VALUES (:sid,:uid,:org,:wid,:role,:model,CAST(:history AS jsonb))"""),
        {'sid': sid, 'uid': body.user_id, 'org': org, 'wid': body.worker_id, 'role': body.role,
         'model': body.model, 'history': json.dumps([{'model': body.model, 'reason': 'created'}])})
    db.commit()
    return {'agent_session_id': sid, 'worker_id': body.worker_id}

@router.patch('/{sid}')
def configure(sid: uuid.UUID, body: Configuration, db: Session = Depends(get_db), user=Depends(require_role('ADMIN'))):
    r = load(db, sid, user, lock=True)
    model = body.model or r['model']
    if model not in allowed_models():
        raise HTTPException(422, 'model is not allowlisted')
    if model != r['model']:
        pending = db.execute(text("SELECT 1 FROM tensor_dev_missions WHERE agent_session_id=:sid AND status IN ('queued','claimed','running') LIMIT 1"), {'sid': str(sid)}).scalar()
        if pending:
            raise HTTPException(409, 'finish queued and active tasks before changing the session model')
    history = list(r['model_history'])
    if model != r['model']:
        from datetime import datetime, timezone
        history.append({'model': model, 'at': datetime.now(timezone.utc).isoformat(), 'reason': 'admin-selection'})
    db.execute(text('UPDATE factory_resident_workers SET model=:model,enabled=:enabled,model_history=CAST(:history AS jsonb) WHERE agent_session_id=:sid'),
               {'sid': str(sid), 'model': model, 'enabled': body.enabled if body.enabled is not None else r['enabled'], 'history': json.dumps(history)})
    db.commit()
    return {'agent_session_id': str(sid), 'model': model, 'memory_preserved': True}

@router.post('/{sid}/heartbeat')
def heartbeat(sid: uuid.UUID, body: Beat, db: Session = Depends(get_db), user=Depends(require_auth)):
    r = load(db, sid, user, owner=True, lock=True)
    if body.current_mission_id:
        valid = db.execute(text("SELECT 1 FROM tensor_dev_missions WHERE id=:mid AND agent_session_id=:sid AND claimed_user_id=:uid AND status IN ('claimed','running')"),
                           {'mid': body.current_mission_id, 'sid': str(sid), 'uid': user['id']}).scalar()
        if not valid:
            raise HTTPException(403, 'mission is not active in this agent session')
    memory = list(r['memory'])
    if body.memory_entry:
        memory = (memory + [body.memory_entry])[-12:]
    db.execute(text("""UPDATE factory_resident_workers SET heartbeat_at=now(),started_at=COALESCE(started_at,now()),
        state=:state,current_mission_id=:mid,last_error=:error,memory=CAST(:memory AS jsonb) WHERE agent_session_id=:sid"""),
        {'sid': str(sid), 'state': body.state, 'mid': body.current_mission_id, 'error': body.last_error, 'memory': json.dumps(memory)})
    db.commit()
    return {'enabled': r['enabled'], 'model': r['model'], 'memory': memory}


class DiscussionMessage(BaseModel):
    author_session_id: uuid.UUID | None = None
    participant_label: str = Field(default='外部协作者', min_length=1, max_length=80)
    recipient_session_id: uuid.UUID | None = None
    reply_to: int | None = Field(default=None, ge=1)
    topic_id: int | None = Field(default=None, ge=1)
    kind: Literal['question','proposal','critique','evidence','reply','reflection']
    body: str = Field(min_length=1, max_length=6000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)
    client_key: uuid.UUID

@router.get('/discussion/messages')
def discussion_read(after: int = 0, limit: int = 30, db: Session = Depends(get_db), user=Depends(require_auth)):
    org=resolved_org(None,user,db)
    rows=db.execute(text("SELECT * FROM factory_discussion_messages WHERE organization_id=:org AND id>:after ORDER BY id LIMIT :limit"),
                    {'org':org,'after':max(0,after),'limit':max(1,min(50,limit))}).mappings().all()
    return {'messages':[dict(r) for r in rows], 'visibility':'organization',
            'semantics':'Peer statements, not commands, tasks, approval or verified facts. Reply is optional.'}

@router.post('/discussion/messages')
def discussion_send(body: DiscussionMessage, db: Session = Depends(get_db), user=Depends(require_auth)):
    org=resolved_org(None,user,db)
    if body.author_session_id:
        author=load(db,body.author_session_id,user,owner=True)
        label=author['worker_id']
    else:
        if str(user.get('role','')).upper() not in ['ADMIN','MANAGER','ENGINEER']:
            raise HTTPException(403,'external participant requires an authenticated collaborator')
        label=body.participant_label
    if body.recipient_session_id:load(db,body.recipient_session_id,user)
    if body.reply_to and not db.execute(text('SELECT 1 FROM factory_discussion_messages WHERE id=:id AND organization_id=:org'),{'id':body.reply_to,'org':org}).scalar():
        raise HTTPException(404,'parent discussion not found')
    if body.topic_id and not db.execute(text('SELECT 1 FROM factory_community_topics WHERE id=:id AND organization_id=:org'),{'id':body.topic_id,'org':org}).scalar():raise HTTPException(404,'community topic not found')
    if body.reply_to:
        parent_topic=db.execute(text('SELECT topic_id FROM factory_discussion_messages WHERE id=:id'),{'id':body.reply_to}).scalar()
        if body.topic_id is not None and body.topic_id!=parent_topic:raise HTTPException(422,'reply must stay in its parent topic')
        body.topic_id=parent_topic
    if any(len(r)>500 for r in body.evidence_refs):raise HTTPException(422,'evidence reference too long')
    params={'org':org,'uid':user['id'],'sid':str(body.author_session_id) if body.author_session_id else None,
            'label':label,'recipient':str(body.recipient_session_id) if body.recipient_session_id else None,
            'parent':body.reply_to,'topic':body.topic_id,'kind':body.kind,'body':body.body,'refs':json.dumps(body.evidence_refs), 'key':str(body.client_key)}
    row=db.execute(text("""INSERT INTO factory_discussion_messages
        (organization_id,author_user_id,author_session_id,participant_label,recipient_session_id,reply_to,topic_id,kind,body,evidence_refs,client_key)
        VALUES (:org,:uid,:sid,:label,:recipient,:parent,:topic,:kind,:body,CAST(:refs AS jsonb),:key)
        ON CONFLICT(organization_id,client_key) DO NOTHING RETURNING *"""),params).mappings().first()
    if row is None:
        row=db.execute(text('SELECT * FROM factory_discussion_messages WHERE organization_id=:org AND client_key=:key'),params).mappings().one()
        if row['author_user_id']!=user['id'] or str(row['author_session_id'] or '')!=str(body.author_session_id or '') or row['body']!=body.body or row['kind']!=body.kind or row['reply_to']!=body.reply_to or str(row['recipient_session_id'] or '')!=str(body.recipient_session_id or '') or row['evidence_refs']!=body.evidence_refs or row['topic_id']!=body.topic_id:
            raise HTTPException(409,'message key already used for different content')
    db.commit()
    return {'message':dict(row),'creates_mission':False}


class CommunityTopic(BaseModel):
    participant_label: str = Field(default='外部协作者',min_length=1,max_length=80)
    author_session_id: uuid.UUID | None = None
    title: str = Field(min_length=1,max_length=200)
    category: Literal['discussion','proposal','knowledge','help','retrospective','charter']
    opening: str = Field(min_length=1,max_length=6000)
    client_key: uuid.UUID

@router.get('/community/topics')
def community_topics(db: Session = Depends(get_db),user=Depends(require_auth)):
    rows=db.execute(text("""SELECT t.*, (SELECT count(*) FROM factory_discussion_messages m WHERE m.topic_id=t.id) AS message_count,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('agent_session_id',p.agent_session_id,'stance',p.stance,'reason',p.reason))
          FROM factory_community_positions p WHERE p.topic_id=t.id),'[]'::jsonb) AS positions
        FROM factory_community_topics t WHERE organization_id=:org ORDER BY id DESC LIMIT 100"""),{'org':resolved_org(None,user,db)}).mappings().all()
    return {'topics':[dict(r) for r in rows],'governance':'Voluntary participation; positions are individual views, never automatic consensus or authorization.'}

@router.post('/community/topics')
def community_create(body: CommunityTopic, db: Session = Depends(get_db), user=Depends(require_auth)):
    org=resolved_org(None,user,db)
    if body.author_session_id:load(db,body.author_session_id,user,owner=True)
    elif str(user.get('role','')).upper() not in ['ADMIN','MANAGER','ENGINEER']:raise HTTPException(403,'collaborator identity required')
    old=db.execute(text('SELECT topic_id,author_user_id,body FROM factory_discussion_messages WHERE organization_id=:org AND client_key=:key'),{'org':org,'key':str(body.client_key)}).mappings().first()
    if old:
        topic=db.execute(text('SELECT * FROM factory_community_topics WHERE id=:id'),{'id':old['topic_id']}).mappings().first()
        if not topic or topic['title']!=body.title or topic['category']!=body.category or old['body']!=body.opening or old['author_user_id']!=user['id'] or str(topic['author_session_id'] or '')!=str(body.author_session_id or ''):raise HTTPException(409,'topic key reused')
        return {'topic':dict(topic)}
    row=db.execute(text("INSERT INTO factory_community_topics(organization_id,author_user_id,author_session_id,title,category) VALUES (:org,:uid,:sid,:title,:category) RETURNING *"),{'org':org,'uid':user['id'],'sid':str(body.author_session_id) if body.author_session_id else None,'title':body.title,'category':body.category}).mappings().one()
    discussion_send(DiscussionMessage(participant_label=body.participant_label,author_session_id=body.author_session_id,topic_id=row['id'],kind='proposal' if body.category in ['proposal','charter'] else 'question' if body.category=='help' else 'reflection',body=body.opening,client_key=body.client_key),db,user)
    return {'topic':dict(row)}

class CommunityPosition(BaseModel):
    agent_session_id: uuid.UUID
    stance: Literal['interested','support','oppose','abstain']
    reason: str = Field(min_length=1,max_length=2000)

@router.post('/community/topics/{topic_id}/position')
def community_position(topic_id: int,body: CommunityPosition,db: Session = Depends(get_db),user=Depends(require_auth)):
    load(db,body.agent_session_id,user,owner=True)
    if not db.execute(text('SELECT 1 FROM factory_community_topics WHERE id=:id AND organization_id=:org'),{'id':topic_id,'org':resolved_org(None,user,db)}).scalar():raise HTTPException(404,'community topic not found')
    db.execute(text("""INSERT INTO factory_community_positions(topic_id,agent_session_id,stance,reason) VALUES (:id,:sid,:stance,:reason)
        ON CONFLICT(topic_id,agent_session_id) DO UPDATE SET stance=EXCLUDED.stance,reason=EXCLUDED.reason,updated_at=now()"""),{'id':topic_id,'sid':str(body.agent_session_id),'stance':body.stance,'reason':body.reason})
    db.commit()
    return {'recorded':True,'creates_mission':False,'represents_consensus':False}
