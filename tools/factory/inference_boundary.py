"""Shared live-input/admission boundary for task and idle resident inference."""
from contextlib import contextmanager
from community_inbox import Inbox
from inference_scheduler import inference_slot


class InferenceBoundary:
    def __init__(self, api, root, session_id, deadline, cancelled=None):
        self.inbox = Inbox(api, root, session_id)
        self.root = root
        self.deadline = deadline
        self.cancelled = cancelled

    @contextmanager
    def admit(self, prompt, *, max_prompt_bytes=None):
        prompt += self.inbox.prepare()
        if max_prompt_bytes is not None and len(prompt.encode()) > max_prompt_bytes:
            raise RuntimeError('conservative token budget preflight rejected this prompt')
        with inference_slot(self.root, self.deadline, self.cancelled):
            yield prompt
            # Admission, generation or event-write failures leave input pending.
            # This acknowledges inference delivery only, not task acceptance.
            self.inbox.delivered()
