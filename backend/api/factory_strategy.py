"""Bounded business campaigns: research -> six workstreams -> synthesis -> weekly review."""
import json
import os
import hashlib
from functools import lru_cache
from pathlib import Path
from uuid import UUID
from typing import Literal, Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org, append_event
from api.factory_residents import load
from api.tensor_missions import _authorize_agent
from services.factory_profile import get_factory_profile
from database.config import get_db

router=APIRouter(prefix='/tensor/factory/campaigns',tags=['factory-strategy'])
ROLES=['platform','data','models','applications','infrastructure','verifier']
BUDGET={'max_wall_seconds':1800,'max_tool_calls':20,'max_tokens':200000}

def campaign_project():
    profile=get_factory_profile()
    if profile is None:raise HTTPException(503,'resident campaigns require an explicit factory profile')
    project=os.environ.get('FACTORY_CAMPAIGN_REPO') or profile.project_names[0]
    if project not in profile.projects:raise HTTPException(422,'campaign project is outside the factory profile')
    return project

def task_limit():
    value=int(os.environ.get('FACTORY_CAMPAIGN_TASKS_PER_DAY','0'))
    return None if value<=0 else max(3,value)

def round_limit(campaign):
    value=int(os.environ.get('FACTORY_CAMPAIGN_ROUNDS_PER_DAY','0'))
    return None if value<=0 else value

@lru_cache(maxsize=1)
def runtime_fingerprint():
    root=Path(__file__).resolve().parents[2]
    paths=[root/'backend/api/factory_strategy.py',root/'backend/api/factory_residents.py',root/'backend/services/factory_codex.py',root/'scripts/run_resident_agent.py',*sorted((root/'tools/factory').glob('*.py'))]
    return hashlib.sha256(b''.join(str(p.relative_to(root)).encode()+p.read_bytes() for p in paths)).hexdigest()

def repair_checkpoint(db,campaign):
    rows=db.execute(text("SELECT result FROM tensor_dev_missions WHERE campaign_id=:cid AND campaign_phase='workstream' ORDER BY id"),{'cid':campaign['id']}).scalars().all()
    hashes=sorted({a['sha256'] for r in rows for a in (r or {}).get('artifacts',[]) if a.get('name')=='package.json'})
    return {'runtime':runtime_fingerprint(),'implementation_hashes':hashes}

class Campaign(BaseModel):
    name: str=Field(min_length=2,max_length=120)
    objective: str=Field(min_length=20,max_length=6000)
    agent_session_id: UUID
    max_rounds: int=Field(default=4,ge=1,le=4)

class Task(BaseModel):
    execution_mode: Literal['design','python-package']='design'
    depends_on_roles: list[Literal['platform','data','models','applications','infrastructure','verifier']]=Field(default_factory=list,max_length=5)
    role: Literal['platform','data','models','applications','infrastructure','verifier']
    goal: str=Field(min_length=20,max_length=2500)
    acceptance_criteria: list[Annotated[str, Field(min_length=1,max_length=600)]]=Field(min_length=1,max_length=8)

class Dispatch(BaseModel):
    parent_id: int
    claim_token: str
    tasks: list[Task]=Field(min_length=0,max_length=6)
    plan: dict

class Control(BaseModel):
    enabled: bool

@router.get('')
def listing(db:Session=Depends(get_db),user=Depends(require_auth)):
    rows=db.execute(text('SELECT * FROM factory_strategy_campaigns WHERE organization_id=:org ORDER BY id'),{'org':resolved_org(None,user,db)}).mappings().all()
    used=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE organization_id=:org AND campaign_id IS NOT NULL AND created_at>now()-interval '24 hours'"),{'org':resolved_org(None,user,db)}).scalar()
    campaigns=[]
    for row in rows:
        item=dict(row);item['effective_round_limit']=round_limit(item);item['scheduler_state']='active';item['automatic_resume_at']=None
        if item['phase'] in ['monitoring','budget_complete']:
            item['scheduler_state']='scheduled-review'
            item['automatic_resume_at']=item['next_review_at']
            last=db.execute(text('SELECT result FROM tensor_dev_missions WHERE id=:mid'),{'mid':item['last_mission_id']}).scalar() or {}
            if not isinstance(last.get('continuation'),dict) or last['continuation'].get('action')=='continue':item['scheduler_state']='ready-to-continue'
            if task_limit() is not None and used>task_limit()-3 and item['scheduler_state']=='ready-to-continue':
                item['scheduler_state']='waiting-daily-budget'
                item['automatic_resume_at']=db.execute(text("SELECT created_at+interval '24 hours' FROM tensor_dev_missions WHERE organization_id=:org AND campaign_id IS NOT NULL AND created_at>now()-interval '24 hours' ORDER BY created_at OFFSET :offset LIMIT 1"),{'org':item['organization_id'],'offset':used-task_limit()+2}).scalar()
        if item['scheduler_state']=='ready-to-continue':
            recent=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE campaign_id=:cid AND campaign_phase='research' AND task_key NOT LIKE :retry_pattern AND created_at>now()-interval '24 hours'"),{'cid':item['id'],'retry_pattern':'%:retry%'}).scalar()
            if round_limit(item) is not None and recent>=round_limit(item):
                item['scheduler_state']='waiting-round-budget'
                item['automatic_resume_at']=db.execute(text("SELECT created_at+interval '24 hours' FROM tensor_dev_missions WHERE campaign_id=:cid AND campaign_phase='research' AND task_key NOT LIKE :retry_pattern AND created_at>now()-interval '24 hours' ORDER BY created_at OFFSET :offset LIMIT 1"),{'cid':item['id'],'retry_pattern':'%:retry%','offset':recent-round_limit(item)}).scalar()
        if item['phase']=='needs_harness_improvement':
            state=db.execute(text('SELECT status FROM tensor_dev_missions WHERE id=:mid'),{'mid':item['last_mission_id']}).scalar()
            if state not in ['queued','claimed','running']:item['scheduler_state']='blocked-unchanged-failure'
        if not item['enabled']:item['scheduler_state']='paused';item['automatic_resume_at']=None
        campaigns.append(item)
    return {'campaigns':campaigns,'daily_task_slots_remaining':None if task_limit() is None else max(0,task_limit()-used),'daily_task_limit':task_limit()}

@router.post('')
def create(body:Campaign,db:Session=Depends(get_db),user=Depends(require_role('ADMIN'))):
    resident=load(db,body.agent_session_id,user,lock=True)
    if resident['role']!='strategy' or not resident['enabled']:
        raise HTTPException(422,'campaign requires an enabled strategy session')
    existing=db.execute(text('SELECT id,objective,agent_session_id FROM factory_strategy_campaigns WHERE organization_id=:org AND name=:name'),{'org':resident['organization_id'],'name':body.name}).mappings().first()
    if existing:
        if existing['objective']!=body.objective or existing['agent_session_id']!=body.agent_session_id:
            raise HTTPException(409,'campaign name identifies a different scope')
        return {'id':existing['id'],'idempotent':True}
    active=db.execute(text('SELECT 1 FROM factory_strategy_campaigns WHERE agent_session_id=:sid AND enabled'),{'sid':str(body.agent_session_id)}).scalar()
    if active:raise HTTPException(409,'pause the current campaign before creating another for this session')
    ident=db.execute(text('INSERT INTO factory_strategy_campaigns(organization_id,agent_session_id,name,objective,max_rounds) VALUES(:org,:sid,:name,:goal,:rounds) RETURNING id'),{'org':resident['organization_id'],'sid':str(body.agent_session_id),'name':body.name,'goal':body.objective,'rounds':body.max_rounds}).scalar()
    db.commit();return {'id':ident}

@router.patch('/{cid}')
def control(cid:int,body:Control,db:Session=Depends(get_db),user=Depends(require_role('ADMIN'))):
    r=db.execute(text('UPDATE factory_strategy_campaigns SET enabled=:enabled WHERE id=:cid AND organization_id=:org RETURNING id'),{'enabled':body.enabled,'cid':cid,'org':resolved_org(None,user,db)}).scalar()
    if not r:raise HTTPException(404,'campaign not found')
    db.commit();return {'id':r,'enabled':body.enabled}


def queue(db,campaign,resident,phase,uid,attempt=0):
    key=f"campaign:{campaign['id']}:{campaign['round']}:{phase}:{resident['role']}"+(f':retry{attempt}' if attempt else '')
    existing=db.execute(text('SELECT id FROM tensor_dev_missions WHERE organization_id=:org AND task_key=:key'),{'org':campaign['organization_id'],'key':key}).scalar()
    if existing:return existing
    goal=('依据用户目标调研需求和现有平台，形成发展方向、建设方案、明确假设及可验收工作线。' if phase=='research' else '收集本轮各条工作线的真实任务产物，逐项评审缺口与依赖，汇总目标驱动的平台建设方案和下一轮优先级；禁止把文档交付说成开发、训练或客户验证完成。')+'\n业务目标：'+campaign['objective']+'\n业务补充：'+json.dumps(campaign.get('feedback',[]),ensure_ascii=False)[-6000:]
    if phase=='harness_improvement':goal='诊断本轮规划执行失败的 harness 能力缺口：读取失败分类与事件证据，定位工具、上下文、记忆、输出契约或调度问题，提出最小修复方案与可复现验收用例。不得把设计建议称为已经修复代码。业务目标：'+campaign['objective']
    if attempt:goal+=f'\n本阶段自动恢复第{attempt}次：查看本期失败记录，纠正证据、上下文或产物格式问题；不得绕过工具、权限及预算约束。'
    sid=db.execute(text("INSERT INTO tensor_agent_sessions(organization_id,agent_name,status,budget,started_by) VALUES(:org,:name,'active',CAST(:budget AS jsonb),:uid) RETURNING id"),{'org':campaign['organization_id'],'name':resident['worker_id'],'budget':json.dumps(BUDGET),'uid':uid}).scalar()
    mid=db.execute(text("""INSERT INTO tensor_dev_missions(organization_id,goal,repo,base_ref,budget,runner,origin,model,created_by,worker_role,task_key,agent_session_id,session_id,campaign_id,campaign_phase)
        VALUES(:org,:goal,:repo,'main',CAST(:budget AS jsonb),'native','agent',:model,:uid,:role,:key,:agent,:sid,:cid,:phase) RETURNING id"""),{'org':campaign['organization_id'],'goal':goal,'repo':campaign_project(),'budget':json.dumps(BUDGET),'model':resident['model'],'uid':uid,'key':key,'agent':resident['agent_session_id'],'sid':sid,'cid':campaign['id'],'phase':phase,'role':resident['role']}).scalar()
    append_event(db,sid,'plan',{'mission_id':mid,'campaign_id':campaign['id'],'campaign_round':campaign['round'],'goal':goal,'agent_session_id':str(resident['agent_session_id'])})
    return mid

@router.post('/tick/{sid}')
def tick(sid:UUID,db:Session=Depends(get_db),user=Depends(require_auth)):
    resident=load(db,sid,user,owner=True)
    if resident['role']!='strategy':raise HTTPException(403,'only strategy sessions can schedule campaigns')
    if not resident['enabled']:return {'scheduled':False,'reason':'session-disabled'}
    db.execute(text('SELECT pg_advisory_xact_lock(92801,:org)'),{'org':resident['organization_id']})
    c=db.execute(text('SELECT * FROM factory_strategy_campaigns WHERE agent_session_id=:sid AND enabled ORDER BY id LIMIT 1 FOR UPDATE'),{'sid':str(sid)}).mappings().first()
    if not c:return {'scheduled':False,'reason':'no-enabled-campaign'}
    # Consistent lock order: campaign then session, also used by dispatch.
    resident=load(db,sid,user,owner=True,lock=True)
    if not resident['enabled']:return {'scheduled':False,'reason':'session-disabled'}
    active=db.execute(text("SELECT 1 FROM tensor_dev_missions WHERE agent_session_id=:sid AND status IN ('queued','claimed','running') LIMIT 1"),{'sid':str(sid)}).scalar()
    if active:return {'scheduled':False,'reason':'strategy-busy'}
    phase=c['phase'];attempt=0
    if phase=='workstreams':
        children=db.execute(text("SELECT status FROM tensor_dev_missions WHERE parent_mission_id=:parent AND campaign_id=:cid"),{'parent':c['last_mission_id'],'cid':c['id']}).scalars().all()
        expected=len(c['plan'].get('_dispatch',{}).get('children',[]))
        if len(children)!=expected:return {'scheduled':False,'reason':'incomplete-workstream-dispatch'}
        if any(s in ['queued','claimed','running'] for s in children):return {'scheduled':False,'reason':'workstreams-running'}
        phase='synthesis'
    elif phase in ['research','synthesis']:
        if c['last_mission_id']:
            last=db.execute(text('SELECT status,campaign_phase,task_key,result FROM tensor_dev_missions WHERE id=:mid'),{'mid':c['last_mission_id']}).mappings().first()
            if last['campaign_phase']==phase and last['status']=='failed':
                attempt=(int(last['task_key'].rsplit(':retry',1)[1]) if ':retry' in last['task_key'] else 0)+1
                reason=(last['result'] or {}).get('safe_reason') or (last['result'] or {}).get('error_class') or '执行失败；详见任务事件链'
                db.execute(text("INSERT INTO factory_harness_incidents(organization_id,campaign_id,mission_id,kind,detail) VALUES(:org,:cid,:mid,'execution-failure',:reason) ON CONFLICT DO NOTHING"),{'org':c['organization_id'],'cid':c['id'],'mid':c['last_mission_id'],'reason':str(reason)[:1000]})
                if (last['result'] or {}).get('error_class')=='HarnessBlocked':attempt=3
                if attempt>2:
                    target=db.execute(text("SELECT * FROM factory_resident_workers WHERE organization_id=:org AND role='platform' AND enabled ORDER BY created_at LIMIT 1 FOR UPDATE"),{'org':c['organization_id']}).mappings().first()
                    checkpoint=repair_checkpoint(db,c)
                    plan={**c['plan'],'_recovery_checkpoint':checkpoint,'_recovery_failure':str(reason)[:1000]}
                    db.execute(text('UPDATE factory_strategy_campaigns SET plan=CAST(:plan AS jsonb) WHERE id=:cid'),{'cid':c['id'],'plan':json.dumps(plan)})
                    hid=queue(db,c,target,'harness_improvement',user['id']) if target else None
                    db.execute(text("UPDATE factory_strategy_campaigns SET phase='needs_harness_improvement',last_mission_id=COALESCE(:hid,last_mission_id) WHERE id=:cid"),{'cid':c['id'],'hid':hid});db.commit()
                    return {'scheduled':bool(hid),'mission_id':hid,'reason':'harness-improvement-required'}
            elif last['campaign_phase']==phase and last['status']=='stopped':
                return {'scheduled':False,'reason':'owner-stopped-strategy-task'}
            if phase=='synthesis' and last['status'] in ['awaiting_acceptance','succeeded']:
                decision=(last['result'] or {}).get('continuation')
                # Legacy summaries receive one autonomous reassessment; no human wake-up needed.
                immediate=not isinstance(decision,dict) or decision.get('action')=='continue'
                db.execute(text("UPDATE factory_strategy_campaigns SET phase='monitoring',next_review_at=CASE WHEN :immediate THEN now() ELSE now()+make_interval(days => :days) END WHERE id=:cid"),{'cid':c['id'],'immediate':immediate,'days':max(1,min(30,int(c['plan'].get('review_after_days',7))))});db.commit()
                return {'scheduled':False,'reason':'autonomous-continuation-pending' if immediate else 'weekly-monitoring'}
    elif phase=='needs_harness_improvement':
        last=db.execute(text('SELECT status FROM tensor_dev_missions WHERE id=:mid'),{'mid':c['last_mission_id']}).scalar()
        if last in ['queued','claimed','running']:return {'scheduled':False,'reason':'harness-diagnosis-running'}
        if last=='stopped':return {'scheduled':False,'reason':'owner-stopped-harness-task'}
        # A diagnostic report is not a repair. Only changed runtime or implementation
        # permits one new canary cycle; unchanged failures cannot mint more tasks.
        checkpoint=repair_checkpoint(db,c)
        previous=c['plan'].get('_recovery_checkpoint')
        if previous is None:
            db.execute(text('UPDATE factory_strategy_campaigns SET plan=CAST(:plan AS jsonb) WHERE id=:cid'),{'cid':c['id'],'plan':json.dumps({**c['plan'],'_recovery_checkpoint':checkpoint})});db.commit()
            return {'scheduled':False,'reason':'awaiting-repair-change','checkpoint_initialized':True}
        if previous==checkpoint:return {'scheduled':False,'reason':'awaiting-repair-change'}
        # A diagnosis is evidence for replanning, not proof of a repaired harness.
        # Resume through the same rolling quotas rather than leaving a permanent dead end.
        db.execute(text("UPDATE factory_strategy_campaigns SET phase='monitoring',next_review_at=now() WHERE id=:cid"),{'cid':c['id']});db.commit()
        return {'scheduled':False,'reason':'harness-diagnosis-replanning'}
    elif phase in ['monitoring','budget_complete']:
        due=db.execute(text('SELECT next_review_at<=now() FROM factory_strategy_campaigns WHERE id=:cid'),{'cid':c['id']}).scalar()
        last=db.execute(text('SELECT result FROM tensor_dev_missions WHERE id=:mid'),{'mid':c['last_mission_id']}).scalar()
        if not due and isinstance(last,dict) and 'continuation' in last:return {'scheduled':False,'reason':'weekly-monitoring'}
        recent=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE campaign_id=:cid AND campaign_phase='research' AND task_key NOT LIKE :retry_pattern AND created_at>now()-interval '24 hours'"),{'cid':c['id'],'retry_pattern':'%:retry%'}).scalar()
        if round_limit(c) is not None and recent>=round_limit(c):return {'scheduled':False,'reason':'rolling-round-budget'}
        used=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE organization_id=:org AND campaign_id IS NOT NULL AND created_at>now()-interval '24 hours'"),{'org':c['organization_id']}).scalar()
        if task_limit() is not None and used>task_limit()-3:return {'scheduled':False,'reason':'daily-campaign-cap-reserve-work-and-synthesis'}
        history=list(c['plan'].get('_history',[]))
        if c['last_mission_id'] not in history:history.append(c['last_mission_id'])
        updated_plan={**c['plan'],'_history':history[-2:]}
        db.execute(text("UPDATE factory_strategy_campaigns SET round=round+1,phase='research',plan=CAST(:plan AS jsonb) WHERE id=:cid"),{'cid':c['id'],'plan':json.dumps(updated_plan)})
        c=dict(c);c['round']+=1;phase='research'
    else:return {'scheduled':False,'reason':'campaign-not-schedulable'}
    day_count=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE organization_id=:org AND campaign_id IS NOT NULL AND created_at>now()-interval '24 hours'"),{'org':c['organization_id']}).scalar()
    if task_limit() is not None and day_count>=task_limit():return {'scheduled':False,'reason':'daily-campaign-cap'}
    mid=queue(db,c,resident,phase,user['id'],attempt)
    db.execute(text('UPDATE factory_strategy_campaigns SET phase=:phase,last_mission_id=:mid WHERE id=:cid'),{'cid':c['id'],'phase':phase,'mid':mid})
    db.commit();return {'scheduled':True,'mission_id':mid,'campaign_id':c['id'],'phase':phase}

@router.post('/{cid}/dispatch')
def dispatch(cid:int,body:Dispatch,db:Session=Depends(get_db),user=Depends(require_auth)):
    db.execute(text('SELECT pg_advisory_xact_lock(92801,:org)'),{'org':resolved_org(None,user,db)})
    c=db.execute(text('SELECT * FROM factory_strategy_campaigns WHERE id=:cid AND organization_id=:org FOR UPDATE'),{'cid':cid,'org':resolved_org(None,user,db)}).mappings().first()
    if not c:raise HTTPException(404,'campaign not found')
    if not c['enabled']:raise HTTPException(409,'campaign is paused')
    parent=_authorize_agent(db,body.parent_id,body.claim_token,user)
    p=db.execute(text('SELECT agent_session_id,campaign_id,campaign_phase FROM tensor_dev_missions WHERE id=:mid'),{'mid':body.parent_id}).mappings().first()
    if parent['status']!='running' or p['agent_session_id']!=c['agent_session_id'] or p['campaign_id']!=cid or p['campaign_phase']!='research' or c['last_mission_id']!=body.parent_id:
        raise HTTPException(403,'only the active campaign research session may dispatch')
    roles={t.role for t in body.tasks}
    dependencies={t.role:set(t.depends_on_roles or ([r for r in roles if r!='verifier'] if t.role=='verifier' else [])) for t in body.tasks}
    def visit(role,path):
        if role in path:raise HTTPException(422,'workstream dependency cycle')
        for dep in dependencies[role]:
            if dep not in roles:raise HTTPException(422,'dependency must be selected in this planning round')
            visit(dep,path|{role})
    for role in roles:visit(role,set())
    if len({t.role for t in body.tasks})!=len(body.tasks):raise HTTPException(422,'a workstream may only receive one task per planning round')
    if type(body.plan.get('review_after_days',7)) is not int or not 1<=body.plan.get('review_after_days',7)<=30:raise HTTPException(422,'review interval must be between 1 and 30 days')
    gaps=body.plan.get('harness_gaps',[])
    if not isinstance(gaps,list) or len(gaps)>8 or any(not isinstance(g,str) or len(g)>1500 for g in gaps):raise HTTPException(422,'invalid harness gap list')
    if len(json.dumps(body.plan,ensure_ascii=False))>50000:raise HTTPException(422,'plan exceeds limit')
    existing=db.execute(text('SELECT id,worker_role FROM tensor_dev_missions WHERE parent_mission_id=:parent ORDER BY id'),{'parent':body.parent_id}).mappings().all()
    if c['phase']=='workstreams' or existing:
        if len(existing)!=len(body.tasks):raise HTTPException(409,'partial campaign dispatch requires inspection')
        recorded=dict(c['plan']);meta=recorded.pop('_dispatch',{})
        recorded.pop('_history',None);recorded.pop('_prior_children',None)
        if recorded!=body.plan or [{**t,'execution_mode':t.get('execution_mode','design'),'depends_on_roles':t.get('depends_on_roles',[])} for t in meta.get('tasks',[])]!=[t.model_dump() for t in body.tasks]:
            raise HTTPException(409,'this research task already dispatched a different plan')
        return {'children':[dict(r) for r in existing],'idempotent':True}
    total=db.execute(text("SELECT count(*) FROM tensor_dev_missions WHERE organization_id=:org AND campaign_id IS NOT NULL AND created_at>now()-interval '24 hours'"),{'org':c['organization_id']}).scalar()
    if task_limit() is not None and total+len(body.tasks)+1>task_limit():raise HTTPException(429,'daily campaign task cap would be exceeded')
    targets={}
    for role in sorted(t.role for t in body.tasks):
        row=db.execute(text('SELECT * FROM factory_resident_workers WHERE organization_id=:org AND role=:role AND enabled ORDER BY created_at LIMIT 1 FOR UPDATE'),{'org':c['organization_id'],'role':role}).mappings().first()
        if not row:raise HTTPException(409,f'enabled workstream session missing: {role}')
        targets[role]=row
    children=[]
    for task in body.tasks:
        target=targets[task.role]
        key=f"campaign:{cid}:{c['round']}:workstream:{task.role}"
        goal='执行模式：'+task.execution_mode+'\n'+task.goal+'\n验收标准：\n'+'\n'.join('- '+x for x in task.acceptance_criteria)
        budget=json.dumps(BUDGET)
        sid=db.execute(text("INSERT INTO tensor_agent_sessions(organization_id,agent_name,status,budget,started_by) VALUES(:org,:name,'active',CAST(:budget AS jsonb),:uid) RETURNING id"),{'org':c['organization_id'],'name':target['worker_id'],'budget':budget,'uid':user['id']}).scalar()
        mid=db.execute(text("""INSERT INTO tensor_dev_missions(organization_id,goal,repo,base_ref,budget,runner,origin,model,parent_mission_id,created_by,worker_role,task_key,agent_session_id,session_id,campaign_id,campaign_phase)
            VALUES(:org,:goal,:repo,'main',CAST(:budget AS jsonb),'native','agent',:model,:parent,:uid,:role,:key,:agent,:sid,:cid,'workstream') RETURNING id"""),{'org':c['organization_id'],'goal':goal,'repo':campaign_project(),'budget':budget,'model':target['model'],'parent':body.parent_id,'uid':user['id'],'role':task.role,'key':key,'agent':target['agent_session_id'],'sid':sid,'cid':cid}).scalar()
        append_event(db,sid,'plan',{'mission_id':mid,'parent_mission_id':body.parent_id,'campaign_id':cid,'goal':goal,'worker_role':task.role,'agent_session_id':str(target['agent_session_id'])})
        append_event(db,parent['session_id'],'note',{'subtype':'campaign-workstream-dispatched','child_id':mid,'role':task.role,'agent_session_id':str(target['agent_session_id'])})
        children.append({'id':mid,'worker_role':task.role})
    ids={child['worker_role']:child['id'] for child in children}
    for child in children:
        deps=[ids[r] for r in sorted(dependencies[child['worker_role']])]
        db.execute(text("UPDATE tensor_dev_missions SET result=result || CAST(:deps AS jsonb) WHERE id=:mid"),{'mid':child['id'],'deps':json.dumps({'dependency_ids':deps})})
    db.execute(text("UPDATE factory_strategy_campaigns SET phase=:phase,plan=CAST(:plan AS jsonb) WHERE id=:cid"),{'cid':cid,'phase':'workstreams' if children else 'synthesis','plan':json.dumps({**body.plan,'_history':c['plan'].get('_history',[]),'_prior_children':c['plan'].get('_dispatch',{}).get('children',[]),'_dispatch':{'parent_id':body.parent_id,'children':children,'tasks':[t.model_dump() for t in body.tasks]}},ensure_ascii=False)})
    for gap in body.plan.get('harness_gaps',[])[:8]:
        db.execute(text("INSERT INTO factory_harness_incidents(organization_id,campaign_id,mission_id,kind,detail) VALUES(:org,:cid,:mid,:kind,:detail) ON CONFLICT DO NOTHING"),{'org':c['organization_id'],'cid':cid,'mid':body.parent_id,'kind':'planner-gap-'+str(body.plan.get('harness_gaps',[]).index(gap)),'detail':str(gap)[:1500]})
    db.commit();return {'children':children}


class Feedback(BaseModel):
    content: str=Field(min_length=10,max_length=3000)
    kind: Literal['business-fact','planning-assistance','takeover','harness-repair']='business-fact'

@router.post('/{cid}/feedback')
def feedback(cid:int,body:Feedback,db:Session=Depends(get_db),user=Depends(require_role('ADMIN','MANAGER','ENGINEER'))):
    from datetime import datetime,timezone
    c=db.execute(text('SELECT feedback FROM factory_strategy_campaigns WHERE id=:cid AND organization_id=:org FOR UPDATE'),{'cid':cid,'org':resolved_org(None,user,db)}).mappings().first()
    if not c:raise HTTPException(404,'campaign not found')
    notes=list(c['feedback'])+[{'content':body.content,'kind':body.kind,'by':user['username'],'at':datetime.now(timezone.utc).isoformat()}]
    db.execute(text('UPDATE factory_strategy_campaigns SET feedback=CAST(:notes AS jsonb),next_review_at=now() WHERE id=:cid'),{'cid':cid,'notes':json.dumps(notes[-20:],ensure_ascii=False)})
    if body.kind!='business-fact':
        import uuid
        db.execute(text("INSERT INTO factory_harness_incidents(organization_id,campaign_id,kind,detail) VALUES(:org,:cid,:kind,:detail)"),{'org':resolved_org(None,user,db),'cid':cid,'kind':'manual-'+body.kind+'-'+uuid.uuid4().hex[:8],'detail':body.content[:1500]})
    db.commit();return {'id':cid,'feedback_recorded':True}

@router.get('/harness-incidents')
def incidents(db:Session=Depends(get_db),user=Depends(require_auth)):
    rows=db.execute(text('SELECT * FROM factory_harness_incidents WHERE organization_id=:org ORDER BY id DESC LIMIT 100'),{'org':resolved_org(None,user,db)}).mappings().all()
    return {'incidents':[dict(r) for r in rows]}
