"""Real process locks: capacity, fairness, cancellation and crashed waiter recovery."""
import fcntl
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import threading
import time
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from inference_scheduler import inference_slot


def waiter(root,events,label,release):
    with inference_slot(root,time.monotonic()+10):
        events.put(label)
        release.wait(5)


def wait_tickets(root,count):
    until=time.monotonic()+5
    while time.monotonic()<until:
        if len(list((root/'.runtime/inference-queue').glob('*.json')))>=count:return
        time.sleep(.02)
    raise AssertionError('waiter did not register')


def test_capacity_and_fifo_across_processes(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_INFERENCE_CONCURRENCY','1')
    ctx=mp.get_context('fork');events=ctx.Queue();release_a=ctx.Event();release_b=ctx.Event()
    a=ctx.Process(target=waiter,args=(tmp_path,events,'a',release_a))
    b=ctx.Process(target=waiter,args=(tmp_path,events,'b',release_b))
    with inference_slot(tmp_path,time.monotonic()+5):
        a.start();wait_tickets(tmp_path,1)
        b.start();wait_tickets(tmp_path,2)
        assert events.empty()
    assert events.get(timeout=5)=='a'
    assert events.empty()
    release_a.set()
    assert events.get(timeout=5)=='b'
    release_b.set();a.join(5);b.join(5)
    assert a.exitcode==b.exitcode==0
    assert not list((tmp_path/'.runtime/inference-queue').glob('*.json'))


def test_two_slots_allow_parallel_processes(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_INFERENCE_CONCURRENCY','2')
    ctx=mp.get_context('fork');events=ctx.Queue();release=ctx.Event()
    child=ctx.Process(target=waiter,args=(tmp_path,events,'parallel',release))
    with inference_slot(tmp_path,time.monotonic()+5):
        child.start()
        assert events.get(timeout=5)=='parallel'
        release.set();child.join(5)
    assert child.exitcode==0


def test_cancelled_waiter_removes_ticket(tmp_path):
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(RuntimeError,match='cancelled'):
        with inference_slot(tmp_path,time.monotonic()+5,cancelled):pass
    assert not list((tmp_path/'.runtime/inference-queue').glob('*.json'))


def test_dead_waiter_is_reaped(tmp_path):
    directory=tmp_path/'.runtime/inference-queue';directory.mkdir(parents=True)
    dead=directory/'00000000000000000000-dead.json';dead.write_text(json.dumps({'pid':999999999}))
    with inference_slot(tmp_path,time.monotonic()+3):assert not dead.exists()


def test_deadline_waiter_does_not_hold_slot(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_INFERENCE_CONCURRENCY','1')
    runtime=tmp_path/'.runtime';runtime.mkdir()
    with (runtime/'inference-slot-0.lock').open('a') as busy:
        fcntl.flock(busy,fcntl.LOCK_EX)
        with pytest.raises(RuntimeError,match='deadline'):
            with inference_slot(tmp_path,time.monotonic()+.1):pass
    with inference_slot(tmp_path,time.monotonic()+3):pass


def test_default_capacity_allows_eight_residents(tmp_path,monkeypatch):
    from contextlib import ExitStack
    monkeypatch.delenv('FACTORY_INFERENCE_CONCURRENCY',raising=False)
    with ExitStack() as stack:
        for _ in range(8):
            stack.enter_context(inference_slot(tmp_path,time.monotonic()+2))
        with pytest.raises(RuntimeError,match='deadline'):
            with inference_slot(tmp_path,time.monotonic()+.1):pass
