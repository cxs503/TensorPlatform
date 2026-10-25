"""FIFO inference admission across resident processes; independent heartbeat/sampling."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
import uuid


def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


@contextmanager
def inference_slot(root,deadline,cancelled=None):
    slots=int(os.getenv('FACTORY_INFERENCE_CONCURRENCY','8'))
    if slots<1:raise ValueError('inference concurrency must be positive')
    runtime=Path(root)/'.runtime';runtime.mkdir(parents=True,exist_ok=True)
    queue=runtime/'inference-queue';queue.mkdir(exist_ok=True,mode=0o700)
    ticket=queue/(f'{time.time_ns():020d}-'+uuid.uuid4().hex+'.json')
    ticket.write_text(json.dumps({'pid':os.getpid()}))
    acquired=None
    try:
        while acquired is None:
            if time.monotonic()>=deadline:raise RuntimeError('inference admission deadline exhausted')
            if cancelled is not None and cancelled.is_set():raise RuntimeError('inference admission cancelled')
            with (runtime/'inference-admission.lock').open('a') as admission:
                # Never block a heartbeat thread or wait unboundedly on a scheduler mutex.
                try:fcntl.flock(admission,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    tickets=[]
                    for path in sorted(queue.glob('*.json')):
                        try:
                            if not alive(json.loads(path.read_text())['pid']):path.unlink();continue
                        except (OSError,ValueError,KeyError):continue
                        tickets.append(path)
                    if tickets and tickets[0]==ticket:
                        for i in range(slots):
                            candidate=(runtime/f'inference-slot-{i}.lock').open('a')
                            try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            except BlockingIOError:candidate.close();continue
                            acquired=candidate;ticket.unlink();break
            if acquired is None:
                if cancelled is not None:cancelled.wait(.2)
                else:time.sleep(.2)
        yield
    finally:
        ticket.unlink(missing_ok=True)
        if acquired is not None:
            fcntl.flock(acquired,fcntl.LOCK_UN);acquired.close()
