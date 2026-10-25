import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/factory'))
from community_inbox import Inbox


def test_failed_inference_replays_and_success_advances_without_task_completion(tmp_path):
    class API:
        def call(self, method, path):
            return {'messages': [{'id': 1, 'body': 'review candidate', 'recipient_session_id': 'a'},
                                 {'id': 2, 'body': 'private other direction', 'recipient_session_id': 'b'}]}
    inbox = Inbox(API(), tmp_path, 'a')
    prompt = inbox.prepare()
    assert 'review candidate' in prompt and 'private other direction' not in prompt
    assert not inbox.path.exists()
    assert inbox.prepare() == prompt  # inference failure cannot lose input
    inbox.delivered()
    assert json.loads(inbox.path.read_text())['after'] == 2
    assert 'not acknowledgement' in json.loads(inbox.path.read_text())['meaning']


def test_message_arriving_during_work_and_backlog_draining(tmp_path, monkeypatch):
    now = [100]
    monkeypatch.setattr('community_inbox.time.monotonic', lambda: now[0])
    class API:
        rows = []
        def call(self, method, path):
            after = int(path.split('after=')[1].split('&')[0])
            return {'messages': [r for r in self.rows if r['id'] > after][:50]}
    api = API()
    inbox = Inbox(api, tmp_path, 'a')
    assert inbox.prepare() == ''
    api.rows = [{'id': n, 'body': f'input {n}'} for n in range(1, 102)]
    now[0] += 31
    assert 'input 50' in inbox.prepare()
    inbox.delivered()
    assert 'input 100' in inbox.prepare()
    inbox.delivered()
    assert 'input 101' in inbox.prepare()
    inbox.delivered()
    assert inbox.state['after'] == 101


def test_large_input_is_paged_without_advancing_unseen_messages(tmp_path):
    class API:
        def call(self,method,path):
            if 'coordination?' in path:return {'items':[]}
            return {'messages':[{'id':i,'body':'x'*6000} for i in range(1,51)]}
    inbox=Inbox(API(),tmp_path,'a')
    assert len(inbox.prepare())<40000
    inbox.delivered()
    assert 0<inbox.state['after']<50
