"""Soul 迁移的版本/校验和/幂等语义（契约 MVP 第 2 条）。

真实 PostgreSQL（tests/factory/conftest.py 的临时库），不 mock：
- 重复应用同一迁移 = 幂等空操作，不产生副作用；
- 已登记版本的迁移文件内容变化 → 抛 MigrationChecksumMismatch，而不是静默覆盖。
"""
from pathlib import Path

import pytest

from services import soul_migrations


def _count(engine, table):
    from sqlalchemy import text
    with engine.begin() as db:
        return db.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def test_reapplying_soul_migrations_is_idempotent(factory):
    engine = factory['engine']
    versions_before = _count(engine, 'factory_soul_schema_versions')
    tables_before = _count(engine, 'pg_tables')
    # bootstrap 已应用过一次；再应用两次必须都是 unchanged
    for _ in range(2):
        result = soul_migrations.apply_soul_migrations(engine)
        assert result == {'094': 'unchanged'}, result
    assert _count(engine, 'factory_soul_schema_versions') == versions_before
    assert _count(engine, 'pg_tables') == tables_before

    from sqlalchemy import text
    with engine.begin() as db:
        row = db.execute(text("""SELECT version, length(checksum) AS size
            FROM factory_soul_schema_versions WHERE version='094'""")).mappings().one()
    assert row['version'] == '094' and row['size'] == 64


def test_changed_migration_file_is_rejected_not_silently_overwritten(factory, tmp_path, monkeypatch):
    engine = factory['engine']
    fake_dir = tmp_path / 'migrations'
    fake_dir.mkdir()
    original = (soul_migrations.MIGRATIONS_DIR / '094_factory_soul.sql').read_text()
    target = fake_dir / '094_factory_soul.sql'
    target.write_text(original + "\n-- tampered copy\n")
    monkeypatch.setattr(soul_migrations, 'MIGRATIONS_DIR', fake_dir)
    with pytest.raises(soul_migrations.MigrationChecksumMismatch, match='094_factory_soul.sql'):
        soul_migrations.apply_soul_migrations(engine)


def test_soul_migration_declares_the_contract_tables():
    """迁移文件必须真的建出契约要求的实体；不只是装饰性 SQL。"""
    raw = (Path(soul_migrations.MIGRATIONS_DIR) / '094_factory_soul.sql').read_text()
    for table in ('factory_agent_identities', 'factory_soul_sessions',
                  'factory_soul_model_bindings', 'factory_memory_events',
                  'factory_soul_checkpoints', 'factory_learning_candidates',
                  'factory_evidence_records', 'factory_soul_schema_versions',
                  'factory_soul_binding_history'):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in raw, table
    # 未验证内容不得以共享级落库：数据库层兜底
    assert "sensitivity <> 'shared' OR verification_status = 'validated'" in raw

def test_resident_session_links_to_one_stable_identity(factory):
    """091 把会话当身份；稳定身份按 worker_id 复用，同一会话重复挂接幂等。"""
    from services import soul_runtime as soul
    client, headers = factory['client'], factory['headers']
    created = client.post('/api/v1/tensor/factory/agents', headers=headers(1),
                          json={'worker_id': 'legacy-resident', 'role': 'data',
                                'model': 'gpt-6.1-sol', 'user_id': 3})
    assert created.status_code == 200, created.text
    resident_sid = created.json()['agent_session_id']

    first = client.post(f"/api/v1/tensor/soul/agents/link-resident/{resident_sid}",
                        headers=headers(1)).json()['agent']
    second = client.post(f"/api/v1/tensor/soul/agents/link-resident/{resident_sid}",
                         headers=headers(1)).json()['agent']
    assert first['agent_id'] == second['agent_id']
    assert first['worker_id'] == 'legacy-resident'

    from sqlalchemy import text
    with factory['engine'].begin() as db:
        links = db.execute(text("""SELECT count(*) FROM factory_soul_legacy_sessions
            WHERE agent_session_id=:sid"""), {'sid': resident_sid}).scalar()
        agents = db.execute(text("""SELECT count(*) FROM factory_agent_identities
            WHERE organization_id=1 AND worker_id='legacy-resident'""")).scalar()
    assert links == 1 and agents == 1

    # 跨组织挂接必失败（不泄露他组织会话）
    assert client.post(f"/api/v1/tensor/soul/agents/link-resident/{resident_sid}",
                       headers=headers(7)).status_code == 404
