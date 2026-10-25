"""Exercise the shared inference entry point with failing generation and retry."""
import json
from pathlib import Path
import sys
import time
import threading
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/factory'))
from inference_boundary import InferenceBoundary


class API:
    def call(self, method, path):
        return {'messages': [{'id': 9, 'body': 'new review evidence', 'recipient_session_id': 'a'}]}


def test_generation_failure_replays_then_success_persists_cursor(tmp_path):
    boundary = InferenceBoundary(API(), tmp_path, 'a', time.monotonic()+5)
    with pytest.raises(RuntimeError, match='model unavailable'):
        with boundary.admit('task') as prompt:
            assert 'new review evidence' in prompt
            raise RuntimeError('model unavailable')
    assert not boundary.inbox.path.exists()
    with boundary.admit('task') as prompt:
        assert 'new review evidence' in prompt
    assert json.loads(boundary.inbox.path.read_text())['after'] == 9


def test_failed_admission_does_not_consume_input(tmp_path):
    cancelled = threading.Event(); cancelled.set()
    boundary = InferenceBoundary(API(), tmp_path, 'a', time.monotonic()+5, cancelled)
    with pytest.raises(RuntimeError, match='cancelled'):
        with boundary.admit('task'):
            pytest.fail('cancelled model must not run')
    assert not boundary.inbox.path.exists()


def test_prompt_limit_includes_incoming_message_and_keeps_input(tmp_path):
    boundary = InferenceBoundary(API(), tmp_path, 'a', time.monotonic()+5)
    with pytest.raises(RuntimeError, match='preflight'):
        with boundary.admit('task', max_prompt_bytes=4):
            pytest.fail('oversized model input must not run')
    assert not boundary.inbox.path.exists()
