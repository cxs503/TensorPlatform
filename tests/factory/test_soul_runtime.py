"""Tensor Soul Runtime 第一阶段验收（RFC #72 / 契约 v0.1 MVP 六条）。

六个场景全部走真实 FastAPI + 真实 PostgreSQL（tests/factory/conftest.py 起临时库），
不使用 mock、不连生产库：

1. 跨 session 连续性：同一 agent_id 两个 episode，目标/未完成任务/记忆连续；
2. 进程重启恢复：新进程（新 TestClient/新连接池）从最新 checkpoint 一致恢复，
   校验和被篡改必须拒绝恢复而不是静默从零开始；
3. 模型切换连续：切两个 ModelBinding，agent_id 与持久状态不变；
4. observe 幂等去重：同一 idempotency_key 重复提交无第二次副作用，异内容冲突；
5. 租户（组织）隔离：另一组织的 agent/session/记忆不可读、不可写；
6. 未验证信息隔离：observed 模型输出不得进共享知识，必须走 LearningCandidate 晋级门。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
SOUL = '/api/v1/tensor/soul'


# ── 辅助 ──────────────────────────────────────────────────────────────────
def _agent(client, headers, worker_id, *, uid=3, goal='维护能耗数据质量门', as_user=1):
    body = {'worker_id': worker_id, 'role': 'data', 'goal': goal,
            'constraints': ['不得把模型输出当事实'], 'capabilities': ['workspace.read'],
            'owner_user_id': uid}
    r = client.post(SOUL + '/agents', headers=headers(as_user), json=body)
    assert r.status_code == 200, r.text
    return r.json()['agent']


def _binding(client, headers, name, provider, model):
    r = client.post(SOUL + '/bindings', headers=headers(1),
                    json={'name': name, 'provider': provider, 'model': model,
                          'adapter': {'wire_api': 'responses'},
                          'capability_limits': {'max_context': 128000}})
    assert r.status_code == 200, r.text
    return r.json()['binding']


def _session(client, headers, agent_id, *, binding=None, task_id=None):
    body = {'agent_id': agent_id, 'active_plan': {'steps': ['核对缺失记录']}}
    if binding:
        body['model_binding_id'] = binding
    if task_id:
        body['task_id'] = task_id
    r = client.post(SOUL + '/sessions', headers=headers(3), json=body)
    assert r.status_code == 200, r.text
    return r.json()['session']


def _observe(client, headers, agent_id, *, key, source='agent-model-output',
             event_type='observation', payload=None, session=None, **extra):
    body = {'agent_id': agent_id, 'event_type': event_type, 'source': source,
            'payload': payload or {'text': '缺失率 3/120'},
            'idempotency_key': key, 'provenance': {'model_binding': 'gpt-6.1-sol'},
            **extra}
    if session:
        body['session_id'] = session
    return client.post(SOUL + '/observe', headers=headers(3), json=body)


def _task(client, headers, task_key):
    body = {'goal': '核对能耗数据缺失率并给出证据', 'repo': 'factory-project',
            'runner': 'native', 'task_key': task_key}
    r = client.post('/api/v1/tensor/missions', headers=headers(2), json=body)
    assert r.status_code == 200, r.text
    return r.json()['id']


# ── 场景 1：跨 session 连续性 ──────────────────────────────────────────────
def test_agent_identity_and_unfinished_task_survive_independent_sessions(factory):
    client, headers = factory['client'], factory['headers']
    agent = _agent(client, headers, 'soul-continuity')
    agent_id = agent['agent_id']

    task_id = _task(client, headers, 'soul:continuity')
    first = _session(client, headers, agent_id, task_id=task_id)
    assert first['current_task_id'] == task_id
    assert first['task_refs'] == [{'task_id': task_id, 'kind': 'primary'}]
    client.put(f"{SOUL}/sessions/{first['session_id']}/plan", headers=headers(3),
               json={'plan': {'steps': ['核对缺失记录', '生成质量报告'], 'cursor': 1}})
    _observe(client, headers, agent_id, key='continuity-1', session=first['session_id'],
             payload={'text': '能耗表缺失 3/120 行，需补齐后再评估'})
    client.post(f"{SOUL}/sessions/{first['session_id']}/checkpoints", headers=headers(3),
                json={'version': 1, 'state': {'cursor': 1, 'checklist': ['缺失', '报告']}})
    client.post(f"{SOUL}/sessions/{first['session_id']}/close", headers=headers(3))

    # 第二个独立 session：同一 agent_id，episode 递增，身份不变
    second = _session(client, headers, agent_id)
    assert second['agent_id'] == agent_id
    assert second['episode'] == first['episode'] + 1
    assert second['session_id'] != first['session_id']

    # 任务引用持久化：episode 2 续做同一任务（去重不重复记）+ 再挂一个新任务（累积）
    second = client.post(f"{SOUL}/sessions/{second['session_id']}/task", headers=headers(3),
                         json={'task_id': task_id, 'note': 'episode 2 续做'}).json()['session']
    assert [ref['task_id'] for ref in second['task_refs']] == [task_id]
    assert second['current_task_id'] == task_id
    extra_task = _task(client, headers, 'soul:continuity:followup')
    second = client.post(f"{SOUL}/sessions/{second['session_id']}/task", headers=headers(3),
                         json={'task_id': extra_task, 'note': '后续任务'}).json()['session']
    assert [ref['task_id'] for ref in second['task_refs']] == [task_id, extra_task]
    assert second['task_refs'][1]['kind'] == 'linked'
    assert second['current_task_id'] == extra_task

    # 未完成任务：第一个 session 的活跃计划与检查点在库里仍可读
    restored = client.get(f"{SOUL}/sessions/{first['session_id']}/checkpoints/restore",
                          headers=headers(3)).json()
    assert restored['checkpoint']['state'] == {'cursor': 1, 'checklist': ['缺失', '报告']}
    old = client.get(f"{SOUL}/sessions/{first['session_id']}", headers=headers(3)).json()['session']
    assert old['active_plan']['cursor'] == 1

    # 记忆连续：第二 session 无需重放 transcript 也能 recall 到第一条观察
    recalled = client.post(SOUL + '/recall', headers=headers(3),
                           json={'agent_id': agent_id, 'task_context': '能耗缺失 补齐',
                                 'limit': 5}).json()
    assert any(item['payload'].get('text', '').startswith('能耗表缺失')
               for item in recalled['matches'])
    assert all('source' in item and 'verification_status' in item
               for item in recalled['matches'])

    # 目标修订只涨 revision，不改身份
    revised = client.patch(f"{SOUL}/agents/{agent_id}/goal", headers=headers(3),
                           json={'goal': '维护能耗数据质量门（第二阶段）'}).json()
    assert revised['identity_preserved'] is True
    assert revised['agent']['goal_revision'] == 2


# ── 场景 2：进程重启恢复 ───────────────────────────────────────────────────
def test_latest_checkpoint_restores_after_process_restart(factory):
    client, headers = factory['client'], factory['headers']
    agent = _agent(client, headers, 'soul-restart')
    session = _session(client, headers, agent['agent_id'])
    state = {'step': 3, 'observations': ['a', 'b', 'c'], 'nested': {'ok': True}}
    first = client.post(f"{SOUL}/sessions/{session['session_id']}/checkpoints",
                        headers=headers(3), json={'version': 1, 'state': state}).json()
    assert first['applied'] is True and first['idempotent'] is False

    # 同版本同内容重复提交 = 幂等空操作（不是第二次写入）
    again = client.post(f"{SOUL}/sessions/{session['session_id']}/checkpoints",
                        headers=headers(3), json={'version': 1, 'state': state}).json()
    assert again['applied'] is False and again['idempotent'] is True
    # 同版本不同内容 = 冲突，不被覆盖
    conflict = client.post(f"{SOUL}/sessions/{session['session_id']}/checkpoints",
                           headers=headers(3),
                           json={'version': 1, 'state': {'step': 99}})
    assert conflict.status_code == 409, conflict.text

    # 独立进程（新解释器/新连接）读同一数据库恢复最新检查点
    script = f"""
import json, os, sys
sys.path.insert(0, {str(ROOT / 'backend')!r})
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
engine = create_engine(os.environ['DATABASE_URL'])
db = sessionmaker(bind=engine)()
from services import soul_runtime as soul
out = soul.restore_checkpoint(db, session_id={session['session_id']!r},
                              organization_id=1)
print(json.dumps({{'state': out['checkpoint']['state'],
                  'version': out['checkpoint']['version'],
                  'agent_id': out['agent_id'],
                  'checksum_verified': out['checksum_verified'],
                  'state_version': out['state_version']}}))
"""
    env = {'DATABASE_URL': _database_url(factory), 'PATH': '/usr/bin:/bin',
           'PYTHONPATH': os_environ_path()}
    proc = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                          env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr
    recovered = json.loads(proc.stdout.strip().splitlines()[-1])
    assert recovered['state'] == state
    assert recovered['version'] == 1
    assert recovered['agent_id'] == agent['agent_id']
    assert recovered['checksum_verified'] is True

    # 校验和被篡改：必须拒绝恢复，而不是静默丢弃状态从零开始
    with factory['engine'].begin() as db:
        db.execute(text("""UPDATE factory_soul_checkpoints
            SET checksum=repeat('0',64) WHERE session_id=:sid AND version=1"""),
            {'sid': session['session_id']})
    corrupt = client.get(f"{SOUL}/sessions/{session['session_id']}/checkpoints/restore",
                         headers=headers(3))
    assert corrupt.status_code == 409, corrupt.text
    assert 'checksum' in corrupt.text.lower()


def os_environ_path():
    import os
    return os.environ.get('PYTHONPATH', '')


def _database_url(factory):
    import os
    return os.environ['DATABASE_URL']


# ── 场景 3：模型切换连续 ───────────────────────────────────────────────────
def test_model_binding_switch_preserves_identity_and_durable_state(factory):
    client, headers = factory['client'], factory['headers']
    agent = _agent(client, headers, 'soul-model-switch')
    binding_a = _binding(client, headers, 'primary', 'openai', 'gpt-6.1-sol')
    binding_b = _binding(client, headers, 'fallback', 'relay', 'qwen3.7-plus')
    session = _session(client, headers, agent['agent_id'], binding=binding_a['binding_id'])

    _observe(client, headers, agent['agent_id'], key='switch-memo',
             session=session['session_id'], payload={'text': '质量门阈值 0.98'})
    client.post(f"{SOUL}/sessions/{session['session_id']}/checkpoints", headers=headers(3),
                json={'version': 2, 'state': {'threshold': 0.98}})
    # 再登记同名绑定的新版本：版本化配置，旧版本保留
    binding_a_v2 = _binding(client, headers, 'primary', 'openai', 'gpt-6.1-sol')

    switched = client.post(f"{SOUL}/sessions/{session['session_id']}/binding",
                           headers=headers(3),
                           json={'binding_id': binding_b['binding_id']}).json()
    assert switched['identity_changed'] is False
    assert switched['agent_id_before'] == switched['agent_id_after'] == agent['agent_id']
    assert switched['checkpoint_version_preserved'] is True
    assert switched['session']['model_binding_id'] == binding_b['binding_id']

    # 切回新版本绑定：agent_id、检查点、记忆全部还在
    back = client.post(f"{SOUL}/sessions/{session['session_id']}/binding",
                       headers=headers(3),
                       json={'binding_id': binding_a_v2['binding_id']}).json()
    assert back['agent_id_after'] == agent['agent_id']
    restored = client.get(f"{SOUL}/sessions/{session['session_id']}/checkpoints/restore",
                          headers=headers(3)).json()
    assert restored['checkpoint']['state'] == {'threshold': 0.98}

    prepared = client.post(SOUL + '/prepare', headers=headers(3),
                           json={'session_id': session['session_id'],
                                 'task_context': '质量门 阈值'}).json()
    assert prepared['model_binding_is_identity_key'] is False
    assert prepared['identity_key'] == 'agent_id'
    assert prepared['agent_id'] == agent['agent_id']
    identity = next(s for s in prepared['sections'] if s['kind'] == 'identity')
    assert identity['model_binding']['model'] == 'gpt-6.1-sol'
    assert identity['model_binding']['version'] == 2

    # 绑定是版本化配置：同名两版并存可查
    assert binding_a['version'] == 1 and binding_a_v2['version'] == 2


# ── 场景 4：observe 幂等去重 ───────────────────────────────────────────────
def test_observe_is_idempotent_and_detects_conflicting_writes(factory):
    client, headers = factory['client'], factory['headers']
    agent = _agent(client, headers, 'soul-idempotent')
    payload = {'text': '模型建议把阈值设为 0.95'}

    first = _observe(client, headers, agent['agent_id'], key='idem-1', payload=payload)
    assert first.status_code == 200, first.text
    assert first.json()['duplicate'] is False
    assert first.json()['side_effect_applied'] is True

    for _ in range(3):
        repeat = _observe(client, headers, agent['agent_id'], key='idem-1', payload=payload)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()['duplicate'] is True
        assert repeat.json()['side_effect_applied'] is False
        assert repeat.json()['event']['event_id'] == first.json()['event']['event_id']

    with factory['engine'].begin() as db:
        count = db.execute(text("""SELECT count(*) FROM factory_memory_events
            WHERE organization_id=1 AND idempotency_key='idem-1'""")).scalar()
    assert count == 1

    # 同键异内容：冲突而非静默覆盖
    conflict = _observe(client, headers, agent['agent_id'], key='idem-1',
                        payload={'text': '另一个结论'})
    assert conflict.status_code == 409, conflict.text

    # 内容级 append-only：数据库触发器拒绝改内容
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match='append-only'):
        with factory['engine'].begin() as db:
            db.execute(text("""UPDATE factory_memory_events SET payload='{"text":"x"}'::jsonb
                WHERE organization_id=1 AND idempotency_key='idem-1'"""))
    with pytest.raises(DBAPIError, match='append-only'):
        with factory['engine'].begin() as db:
            db.execute(text("""DELETE FROM factory_memory_events
                WHERE organization_id=1 AND idempotency_key='idem-1'"""))


# ── 场景 5：租户（组织）隔离 ───────────────────────────────────────────────
def test_tenant_isolation_for_identity_session_and_memory(factory):
    client, headers = factory['client'], factory['headers']
    mine = _agent(client, headers, 'soul-org1')
    session = _session(client, headers, mine['agent_id'])
    _observe(client, headers, mine['agent_id'], key='org1-memo',
             session=session['session_id'], payload={'text': '组织一私有观察'})

    # 组织 2 的管理员（id=7）登录后看不到组织 1 的任何 soul 数据
    assert client.get(f"{SOUL}/agents/{mine['agent_id']}", headers=headers(7)).status_code == 404
    assert client.get(f"{SOUL}/sessions/{session['session_id']}",
                      headers=headers(7)).status_code == 404
    other = _agent(client, headers, 'soul-org2', uid=6, as_user=7)
    assert other['agent_id'] != mine['agent_id']

    cross_observe = client.post(SOUL + '/observe', headers=headers(7),
                                json={'agent_id': mine['agent_id'],
                                      'event_type': 'observation',
                                      'source': 'cross-tenant-forgery',
                                      'payload': {'text': '跨组织伪造'},
                                      'idempotency_key': 'org2-forged'})
    assert cross_observe.status_code in (403, 404), cross_observe.text

    # 组织 2 的 recall 只看得到自己的记录
    theirs = client.post(SOUL + '/recall', headers=headers(7),
                         json={'agent_id': other['agent_id'],
                               'task_context': '组织'}).json()
    assert all(item['agent_id'] == other['agent_id'] for item in theirs['matches'])
    assert theirs['matches'] == []

    # 跨组织 session 绑定必失败
    assert client.post(f"{SOUL}/sessions/{session['session_id']}/binding",
                       headers=headers(7),
                       json={'binding_id': '00000000-0000-0000-0000-000000000000'}
                       ).status_code in (403, 404)

    # 同组织内不同 agent 的私有记忆也不互相可见
    peer = _agent(client, headers, 'soul-peer', uid=4)
    peer_recall = client.post(SOUL + '/recall', headers=headers(4),
                              json={'agent_id': peer['agent_id'],
                                    'task_context': '组织一私有观察'}).json()
    assert all(item['own_agent'] for item in peer_recall['matches'])
    assert not any(item['payload'].get('text') == '组织一私有观察'
                   for item in peer_recall['matches'])


# ── 场景 6：未验证信息隔离 ─────────────────────────────────────────────────
def test_unverified_model_output_cannot_reach_shared_knowledge(factory):
    client, headers = factory['client'], factory['headers']
    agent = _agent(client, headers, 'soul-verify')
    observed = _observe(client, headers, agent['agent_id'], key='unverified-1',
                        payload={'text': '模型声称缺失率为 0'}).json()['event']
    assert observed['verification_status'] == 'observed'

    # observed 内容不能直接以共享级写入
    direct = _observe(client, headers, agent['agent_id'], key='unverified-shared',
                      payload={'text': '直接共享'}, sensitivity='shared')
    assert direct.status_code == 422, direct.text

    # validated 必须带具名校验器
    assert _observe(client, headers, agent['agent_id'], key='unverified-validated',
                    payload={'text': '空验证'}, verification_status='validated').status_code == 422

    # 未验证不得直接提升为共享知识
    assert client.post(f"{SOUL}/events/{observed['event_id']}/promote",
                       headers=headers(2)).status_code == 422

    # LearningCandidate 晋级门：未通过评测不能 approved
    candidate = client.post(SOUL + '/learning/candidates', headers=headers(3),
                            json={'agent_id': agent['agent_id'],
                                  'source_event_ids': [observed['event_id']],
                                  'proposed_generalization': '缺失率恒为 0',
                                  'scope': 'organization',
                                  'evidence': {'model': 'gpt-6.1-sol'}}).json()
    assert candidate['auto_promoted'] is False
    cid = candidate['candidate']['candidate_id']
    assert candidate['candidate']['promotion_status'] == 'proposed'

    assert client.post(f"{SOUL}/learning/candidates/{cid}/decision", headers=headers(2),
                       json={'status': 'approved', 'reason': '无评测'}
                       ).status_code == 422
    # 未批准不得 consolidate
    assert client.post(f"{SOUL}/learning/candidates/{cid}/consolidate",
                       headers=headers(2)).status_code == 422
    assert client.get(SOUL + '/knowledge', headers=headers(2)).json()['items'] == []

    # 独立校验器给出验证：状态迁移到 validated
    validated = client.patch(f"{SOUL}/events/{observed['event_id']}/verification",
                             headers=headers(2),
                             json={'status': 'validated',
                                   'validator': {'name': 'data_quality_gate',
                                                 'version': '1.0.0',
                                                 'evidence': 'artifact:sha256:abc'}})
    assert validated.status_code == 200, validated.text
    assert validated.json()['event']['verification_status'] == 'validated'

    # 未通过评测依然不能 approved（申报与真实评测分离）
    assert client.post(f"{SOUL}/learning/candidates/{cid}/decision", headers=headers(2),
                       json={'status': 'approved', 'reason': '无评测'}
                       ).status_code == 422
    decided = client.post(f"{SOUL}/learning/candidates/{cid}/decision", headers=headers(2),
                          json={'status': 'approved', 'reason': '校验器通过',
                                'evaluation': {'passed': True, 'validator': 'data_quality_gate'}})
    assert decided.status_code == 200, decided.text
    consolidated = client.post(f"{SOUL}/learning/candidates/{cid}/consolidate",
                               headers=headers(2)).json()
    assert consolidated['promoted_count'] == 1
    assert consolidated['shared_knowledge_written'] == 1

    shared = client.get(SOUL + '/knowledge', headers=headers(2)).json()
    assert len(shared['items']) == 1
    assert shared['items'][0]['sensitivity'] == 'shared'
    assert shared['items'][0]['verification_status'] == 'validated'
    assert shared['items'][0]['provenance']['promoted_from_event'] == observed['event_id']

    # 共享知识对同组织其他 agent 的 recall 可见（且带来源/验证状态）
    peer = _agent(client, headers, 'soul-verify-peer', uid=4)
    peer_recall = client.post(SOUL + '/recall', headers=headers(4),
                              json={'agent_id': peer['agent_id'],
                                    'task_context': '缺失率'}).json()
    shared_hits = [item for item in peer_recall['matches'] if item['scope'] == 'shared']
    assert shared_hits and all(item['verification_status'] == 'validated'
                               for item in shared_hits)
    assert all(item['provenance'].get('promoted_from_event') for item in shared_hits)

    # rejected 内容不可作为学习来源，也不可再迁移
    rejected = _observe(client, headers, agent['agent_id'], key='unverified-reject',
                        payload={'text': '错误结论'}).json()['event']
    assert client.patch(f"{SOUL}/events/{rejected['event_id']}/verification",
                        headers=headers(2), json={'status': 'rejected'}
                        ).json()['event']['verification_status'] == 'rejected'
    assert client.post(SOUL + '/learning/candidates', headers=headers(3),
                       json={'agent_id': agent['agent_id'],
                             'source_event_ids': [rejected['event_id']],
                             'proposed_generalization': '错误推广'}).status_code == 422
    assert client.patch(f"{SOUL}/events/{rejected['event_id']}/verification",
                        headers=headers(2), json={'status': 'validated',
                                                  'validator': {'name': 'x'}}
                        ).status_code == 422