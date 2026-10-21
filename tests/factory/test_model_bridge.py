import json
from pathlib import Path
import subprocess

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from services import factory_codex


def test_model_auth_roles_and_draft_has_no_mission_side_effect(factory, monkeypatch):
    client, headers = factory["client"], factory["headers"]
    root = "/api/v1/tensor/factory/model"
    calls = []

    def generate(prompt, schema):
        calls.append(prompt)
        data = {"marker": "TENSORPLATFORM_GPT_OK"} if schema == factory_codex.PROBE_SCHEMA else {
            "goal": "检查指定能耗数据并生成质量报告", "steps": ["核对缺失与重复记录"],
            "acceptance_criteria": ["报告包含检查方法和实际统计结果"]}
        return {"model": "test-model", "data": data, "usage": {"output_tokens": 25}}

    monkeypatch.setattr(factory_codex, "generate", generate)
    assert client.get(root).status_code == 401
    assert client.post(root + "/test", headers=headers(5)).status_code == 403
    assert client.post(root + "/draft", headers=headers(5), json={"goal": "Generate a quality report"}).status_code == 403
    assert not calls
    assert client.post(root + "/test", headers=headers(1)).json()["ok"]
    with factory["engine"].connect() as db:
        before = db.execute(text("SELECT count(*) FROM tensor_dev_missions")).scalar()
    draft = client.post(root + "/draft", headers=headers(2), json={"goal": "检查示例能耗数据的质量并给出验收标准"})
    assert draft.status_code == 200, draft.text
    assert draft.json()["requires_review"] is True
    with factory["engine"].connect() as db:
        assert db.execute(text("SELECT count(*) FROM tensor_dev_missions")).scalar() == before
    assert client.post(root + "/draft", headers=headers(2), json={"goal": "x" * 6001}).status_code == 422
    monkeypatch.setattr(factory_codex, "generate", lambda *a: {"model": "test", "data": {"goal": "missing fields"}, "usage": {}})
    assert client.post(root + "/draft", headers=headers(2), json={"goal": "Generate a quality report"}).status_code == 502


def test_codex_bridge_redaction_and_tool_free_command(monkeypatch):
    config = {"configured": True, "model": "test-model", "_binary": "/test/codex",
              "_provider_id": "local", "_provider": {"wire_api": "responses"}}
    monkeypatch.setattr(factory_codex, "configuration", lambda: config)
    assert "_provider" not in factory_codex.status()
    assert "_binary" not in factory_codex.status()
    seen = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            seen.update(command=command, kwargs=kwargs)
            self.output = Path(command[command.index("--output-last-message") + 1])

        def communicate(self, input, timeout):
            self.output.write_text('{"marker":"TENSORPLATFORM_GPT_OK"}')
            seen["kwargs"]["stdout"].write(json.dumps({"type": "turn.completed", "usage": {
                "output_tokens": 10, "private": "do-not-expose"}}) + "\n")

    monkeypatch.setattr(factory_codex.subprocess, "Popen", Process)
    result = factory_codex.generate("fixed prompt", factory_codex.PROBE_SCHEMA)
    assert result["data"] == {"marker": "TENSORPLATFORM_GPT_OK"}
    assert result["usage"] == {"output_tokens": 10}
    assert "--ignore-user-config" in seen["command"]
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in seen["command"]
    assert "features.multi_agent=false" in seen["command"]
    assert 'model_reasoning_effort="high"' in seen["command"]
    assert result["reasoning_effort"]=="high"
    assert "--dangerously-bypass-approvals-and-sandbox" not in seen["command"]


def test_codex_failure_and_busy_are_bounded_and_do_not_echo_secrets(monkeypatch):
    monkeypatch.setattr(factory_codex, "configuration", lambda: {
        "configured": True, "model": "test", "_binary": "/test/codex", "_provider_id": "test", "_provider": {}})

    def fail(*args, **kwargs):
        raise OSError("sensitive-provider-credential")

    monkeypatch.setattr(factory_codex.subprocess, "Popen", fail)
    with pytest.raises(HTTPException) as error:
        factory_codex.generate("prompt", factory_codex.PROBE_SCHEMA)
    assert error.value.status_code == 502
    assert "sensitive" not in error.value.detail
    assert factory_codex._CALL_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            factory_codex.generate("prompt", factory_codex.PROBE_SCHEMA)
        assert error.value.status_code == 429
    finally:
        factory_codex._CALL_LOCK.release()


def test_timeout_terminates_the_entire_model_process_group(monkeypatch):
    monkeypatch.setattr(factory_codex, "configuration", lambda: {
        "configured": True, "model": "test", "_binary": "/test/codex", "_provider_id": "test", "_provider": {}})
    killed = []

    class Process:
        pid = 987654

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, input, timeout):
            raise subprocess.TimeoutExpired("codex", timeout)

        def wait(self):
            killed.append("reaped")

    monkeypatch.setattr(factory_codex.subprocess, "Popen", Process)
    monkeypatch.setattr(factory_codex.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(HTTPException) as error:
        factory_codex.generate("prompt", factory_codex.PROBE_SCHEMA)
    assert error.value.status_code == 504
    assert killed == [(987654, factory_codex.signal.SIGKILL), "reaped"]
    assert not factory_codex._CALL_LOCK.locked()
