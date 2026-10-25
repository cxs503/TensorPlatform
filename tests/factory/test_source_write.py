"""Regression coverage for SHA-guarded literal replacement and full writes."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/factory'))
import source_write
from source_write import write_source
from platform_workspace import Workspace, git


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def apply(path, args, **kwargs):
    return write_source(path, args, lock_path=path.parent / 'writer.lock',
                        max_bytes=100000, **kwargs)


def test_literal_unicode_replacement_preserves_bytes_and_mode(tmp_path):
    path = tmp_path / 'sample.py'
    before = '头部\r\nvalue = 1\r\n尾部\n'.encode('utf-8')
    path.write_bytes(before)
    path.chmod(0o640)
    result = apply(path, {'expected_sha256': sha(before),
                         'replacement': {'old': 'value = 1', 'new': 'value = 2'}})
    expected = before.replace(b'value = 1', b'value = 2')
    assert path.read_bytes() == expected
    assert result == sha(expected)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(tmp_path.glob('.source-write-*'))


@pytest.mark.parametrize('expected', [None, '', '0' * 64, 'A' * 64, 123])
def test_replacement_rejects_missing_invalid_or_stale_sha(tmp_path, expected):
    path = tmp_path / 'sample.py'
    before = b'value = 1\n'
    path.write_bytes(before)
    with pytest.raises(ValueError):
        apply(path, {'expected_sha256': expected,
                     'replacement': {'old': '1', 'new': '2'}})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.source-write-*'))


@pytest.mark.parametrize('before,old', [('abc', 'missing'), ('x x', 'x'), ('aaa', 'aa')])
def test_replacement_requires_exactly_one_match_including_overlap(tmp_path, before, old):
    path = tmp_path / 'sample.py'
    path.write_text(before)
    with pytest.raises(ValueError, match='match'):
        apply(path, {'expected_sha256': sha(before.encode()),
                     'replacement': {'old': old, 'new': 'replacement'}})
    assert path.read_text() == before


@pytest.mark.parametrize('replacement', [None, [], {}, {'old': 'a'},
    {'old': 'a', 'new': 'b', 'extra': True}, {'old': '', 'new': 'b'},
    {'old': 1, 'new': 'b'}, {'old': 'a', 'new': None}])
def test_invalid_replacement_shape_preserves_original(tmp_path, replacement):
    path = tmp_path / 'sample.py'
    path.write_bytes(b'abc')
    with pytest.raises(ValueError):
        apply(path, {'expected_sha256': sha(b'abc'), 'replacement': replacement})
    assert path.read_bytes() == b'abc'


def test_modes_are_exclusive_and_replacement_cannot_create(tmp_path):
    path = tmp_path / 'sample.py'
    path.write_bytes(b'abc')
    with pytest.raises(ValueError, match='not both'):
        apply(path, {'expected_sha256': sha(b'abc'), 'content': 'new',
                     'replacement': {'old': 'a', 'new': 'b'}})
    assert path.read_bytes() == b'abc'
    missing = tmp_path / 'missing.py'
    with pytest.raises(ValueError):
        apply(missing, {'expected_sha256': sha(b''),
                        'replacement': {'old': 'a', 'new': 'b'}})
    assert not missing.exists()


def test_existing_full_write_contract_and_size_limits(tmp_path):
    path = tmp_path / 'sample.py'
    assert apply(path, {'expected_sha256': None, 'content': 'abc'}) == sha(b'abc')
    apply(path, {'expected_sha256': sha(b'abc'), 'content': 'def'})
    with pytest.raises(ValueError, match='changed'):
        apply(path, {'expected_sha256': sha(b'abc'), 'content': 'stale'})
    with pytest.raises(ValueError, match='size limit'):
        apply(path, {'expected_sha256': sha(b'def'), 'content': 'x' * 100001})
    with pytest.raises(ValueError, match='size limit'):
        apply(path, {'expected_sha256': sha(b'def'),
                     'replacement': {'old': 'd', 'new': '长' * 4}}, replacement_max_bytes=10)
    assert path.read_bytes() == b'def'
    apply(path, {'expected_sha256': sha(b'def'),
                 'replacement': {'old': 'd', 'new': ''}})
    assert path.read_bytes() == b'ef'


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
def test_atomic_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch, operation):
    path = tmp_path / 'sample.py'
    path.write_bytes(b'original')
    def fail(*args, **kwargs):
        raise OSError('injected failure')
    monkeypatch.setattr(source_write.os, operation, fail)
    with pytest.raises(OSError, match='injected failure'):
        apply(path, {'expected_sha256': sha(b'original'),
                     'replacement': {'old': 'original', 'new': 'changed'}})
    assert path.read_bytes() == b'original'
    assert not list(tmp_path.glob('.source-write-*'))


def test_detected_external_change_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'sample.py'
    path.write_bytes(b'original')
    real_fsync = os.fsync
    def change_after_flush(fd):
        real_fsync(fd)
        path.write_bytes(b'external')
    monkeypatch.setattr(source_write.os, 'fsync', change_after_flush)
    with pytest.raises(ValueError, match='changed during write'):
        apply(path, {'expected_sha256': sha(b'original'), 'content': 'changed'})
    assert path.read_bytes() == b'external'
    assert not list(tmp_path.glob('.source-write-*'))


def test_cooperative_writers_serialize_and_reject_stale_sha(tmp_path):
    path = tmp_path / 'sample.py'
    path.write_bytes(b'original')
    barrier = threading.Barrier(2)
    results = []
    def writer(new):
        barrier.wait(timeout=5)
        try:
            apply(path, {'expected_sha256': sha(b'original'), 'content': new})
            results.append('written')
        except ValueError:
            results.append('stale')
        except Exception as error:
            results.append(type(error).__name__)
    threads = [threading.Thread(target=writer, args=(value,)) for value in ['first', 'second']]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ['stale', 'written']
    assert path.read_text() in ['first', 'second']


def test_symlink_source_is_rejected(tmp_path):
    target = tmp_path / 'target.py'
    target.write_bytes(b'original')
    link = tmp_path / 'link.py'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        apply(link, {'expected_sha256': sha(b'original'), 'content': 'changed'})
    assert target.read_bytes() == b'original'


@pytest.mark.parametrize('kind', ['platform'])
def test_real_workspace_entry_supports_replacement_and_full_write(tmp_path, kind):
    root = tmp_path / 'source'
    root.mkdir()
    filename = 'backend/services/sample.py'
    source = root / filename
    source.parent.mkdir(parents=True)
    source.write_bytes(b'value = 1\n')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=test', '-c', 'user.email=test@local', 'commit', '-qm', 'baseline')
    workspace = Workspace(root, uuid.uuid4())
    prefix = 'workspace'
    before = workspace.call(prefix + '.read', {'path': filename})
    written = workspace.call(prefix + '.write', {'path': filename,
        'expected_sha256': before['sha256'],
        'replacement': {'old': 'value = 1', 'new': 'value = 2'}})
    assert written['sha256'] == sha(b'value = 2\n')
    assert (workspace.path / filename).read_bytes() == b'value = 2\n'
    assert source.read_bytes() == b'value = 1\n'
    with pytest.raises(ValueError, match='changed'):
        workspace.call(prefix + '.write', {'path': filename,
            'expected_sha256': before['sha256'],
            'replacement': {'old': '2', 'new': '3'}})
    workspace.call(prefix + '.write', {'path': filename,
        'expected_sha256': written['sha256'], 'content': 'value = 4\n'})
    assert (workspace.path / filename).read_bytes() == b'value = 4\n'
    assert source.read_bytes() == b'value = 1\n'
