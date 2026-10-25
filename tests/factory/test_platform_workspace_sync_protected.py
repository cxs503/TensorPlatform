"""Exercise candidate migration against real, isolated Git repositories."""
from pathlib import Path
import json
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/factory'))
from platform_workspace import Workspace, git


def repository(tmp_path):
    root = tmp_path / 'platform'
    root.mkdir()
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    git(root, 'config', 'user.name', 'test')
    git(root, 'config', 'user.email', 'test@local')
    (root / 'backend').mkdir()
    (root / 'backend/service.py').write_text('value = 1\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'baseline')
    (root / '.runtime').mkdir()
    workspace = Workspace(root, uuid.uuid4())
    workspace.ensure()
    return root, workspace


def upstream(root, content='upstream = True\n', name='backend/upstream.py'):
    (root / name).write_text(content)
    git(root, 'add', '--', name)
    git(root, 'commit', '-m', 'advance platform')
    return git(root, 'rev-parse', 'HEAD').strip()


def state(workspace):
    return {
        'head': git(workspace.path, 'rev-parse', 'HEAD'),
        'status': git(workspace.path, 'status', '--porcelain'),
        'index': git(workspace.path, 'diff', '--cached', '--binary'),
        'worktree': git(workspace.path, 'diff', '--binary'),
        'untracked': {
            name: (workspace.path / name).read_bytes()
            for name in git(workspace.path, 'ls-files', '--others', '--exclude-standard').splitlines()
        },
    }


def assert_no_operation(workspace):
    for marker in ['rebase-merge', 'rebase-apply', 'MERGE_HEAD']:
        location = Path(git(workspace.path, 'rev-parse', '--git-path', marker).strip())
        if not location.is_absolute():
            location = workspace.path / location
        assert not location.exists()
    assert not list(workspace.base.glob('sync-probe-*'))


def test_sync_preserves_index_worktree_and_untracked_with_recovery_ref(tmp_path):
    root, workspace = repository(tmp_path)
    original = git(workspace.path, 'rev-parse', 'HEAD').strip()
    candidate = workspace.path / 'backend/service.py'
    candidate.write_text('value = 2\n')
    git(workspace.path, 'add', 'backend/service.py')
    candidate.write_text('value = 3\n')
    tests = workspace.path / 'tests/factory'
    tests.mkdir(parents=True)
    (tests / 'test_candidate.py').write_text('def test_candidate():\n    assert True\n')
    before = state(workspace)
    check = workspace.base / 'check.json'
    check.write_text('{"passed": true}')
    target = upstream(root)

    result = workspace.call('workspace.sync', {'protect_candidate': True})

    assert result['head'] == target
    assert result['previous_head'] == original
    assert result['checks_require_renewal'] is True
    assert candidate.read_text() == 'value = 3\n'
    assert git(workspace.path, 'show', ':backend/service.py') == 'value = 2\n'
    after = state(workspace)
    for key in ['status', 'index', 'worktree', 'untracked']:
        assert after[key] == before[key]
    assert (workspace.path / 'backend/upstream.py').read_text() == 'upstream = True\n'
    assert (root / 'backend/service.py').read_text() == 'value = 1\n'
    assert not check.exists()
    saved = git(workspace.path, 'rev-parse', result['backup_ref']).strip()
    assert git(workspace.path, 'rev-parse', saved + '^1').strip() == original
    assert git(workspace.path, 'show', saved + '^3:tests/factory/test_candidate.py') == before['untracked']['tests/factory/test_candidate.py'].decode()
    receipt = json.loads(next(workspace.base.glob('sync-backup-*.json')).read_text())
    assert receipt['backup_ref'] == result['backup_ref']
    assert receipt['stash_commit'] == saved
    assert_no_operation(workspace)


def test_sync_preserves_untracked_only_candidate(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/new.py').write_text('new = True\n')
    target = upstream(root)
    result = workspace.call('workspace.sync', {'protect_candidate': True})
    assert result['head'] == target
    assert (workspace.path / 'backend/new.py').read_text() == 'new = True\n'
    assert git(workspace.path, 'ls-files', '--others', '--exclude-standard').splitlines() == ['backend/new.py']
    assert_no_operation(workspace)


def test_sync_preserves_tracked_deletion(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/service.py').unlink()
    upstream(root)
    workspace.call('workspace.sync', {'protect_candidate': True})
    assert not (workspace.path / 'backend/service.py').exists()
    assert git(workspace.path, 'status', '--porcelain') == ' D backend/service.py\n'
    assert_no_operation(workspace)


def test_dirty_conflict_restores_original_candidate_and_keeps_backup(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/service.py').write_text('value = 2\n')
    git(workspace.path, 'add', 'backend/service.py')
    (workspace.path / 'backend/service.py').write_text('value = 3\n')
    (workspace.path / 'backend/new.py').write_text('preserved = True\n')
    check = workspace.base / 'check.json'
    check.write_text('original receipt')
    before = state(workspace)
    target = upstream(root, 'value = 99\n', 'backend/service.py')

    with pytest.raises(ValueError, match='original candidate restored; recovery backup'):
        workspace.call('workspace.sync', {'protect_candidate': True})

    assert state(workspace) == before
    assert git(root, 'rev-parse', 'HEAD').strip() == target
    assert check.read_text() == 'original receipt'
    receipt = json.loads(next(workspace.base.glob('sync-backup-*.json')).read_text())
    assert git(workspace.path, 'rev-parse', receipt['backup_ref']).strip() == receipt['stash_commit']
    assert_no_operation(workspace)


def test_untracked_collision_is_detected_without_replacing_candidate(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/new.py').write_text('candidate = True\n')
    before = state(workspace)
    upstream(root, 'platform = True\n', 'backend/new.py')
    with pytest.raises(ValueError, match='original candidate restored'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert state(workspace) == before
    assert_no_operation(workspace)


def test_committed_conflict_restores_dirty_candidate(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/service.py').write_text('value = 2\n')
    git(workspace.path, 'add', 'backend/service.py')
    git(workspace.path, 'commit', '-m', 'candidate history')
    (workspace.path / 'backend/new.py').write_text('keep = True\n')
    before = state(workspace)
    upstream(root, 'value = 99\n', 'backend/service.py')
    with pytest.raises(ValueError, match='original candidate restored'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert state(workspace) == before
    assert_no_operation(workspace)


def test_clean_sync_invalidates_previous_check(tmp_path):
    root, workspace = repository(tmp_path)
    check = workspace.base / 'check.json'
    check.write_text('old receipt')
    target = upstream(root)
    result = workspace.call('workspace.sync', {'protect_candidate': True})
    assert result['head'] == target
    assert result['changes'] == ''
    assert not check.exists()
    assert_no_operation(workspace)


def test_sensitive_dirty_path_is_rejected_before_stashing(tmp_path):
    root, workspace = repository(tmp_path)
    (workspace.path / 'backend/passwords.py').write_text('placeholder = True\n')
    before = state(workspace)
    upstream(root)
    with pytest.raises(ValueError, match='sensitive path'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert state(workspace) == before
    assert not list(workspace.base.glob('sync-backup-*.json'))
    assert git(workspace.path, 'for-each-ref', '--format=%(refname)', 'refs/stash') == ''


def test_existing_git_operation_is_rejected_without_mutation(tmp_path):
    root, workspace = repository(tmp_path)
    upstream(root)
    marker = Path(git(workspace.path, 'rev-parse', '--git-path', 'CHERRY_PICK_HEAD').strip())
    if not marker.is_absolute():
        marker = workspace.path / marker
    marker.write_text(git(workspace.path, 'rev-parse', 'HEAD'))
    before = state(workspace)
    with pytest.raises(ValueError, match='existing Git operation'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert state(workspace) == before
    assert marker.exists()


def index_bytes(workspace):
    location = Path(git(workspace.path, 'rev-parse', '--git-path', 'index').strip())
    if not location.is_absolute():
        location = workspace.path / location
    return location.read_bytes()


def assert_candidate_unchanged(workspace, before, branch, index, receipt):
    assert index_bytes(workspace) == index
    assert git(workspace.path, 'branch', '--show-current') == branch
    assert state(workspace) == before
    assert (workspace.base / 'check.json').read_bytes() == receipt
    assert_no_operation(workspace)


@pytest.mark.parametrize('backend', ['apply', 'merge'])
@pytest.mark.parametrize('dirty', [False, True])
def test_protected_conflict_recovers_both_rebase_backends(tmp_path, backend, dirty):
    root, workspace = repository(tmp_path)
    git(workspace.path, 'config', 'rebase.backend', backend)
    (workspace.path / 'backend/service.py').write_text('value = 2\n')
    git(workspace.path, 'add', 'backend/service.py')
    git(workspace.path, 'commit', '-m', 'candidate conflict')
    if dirty:
        (workspace.path / 'backend/dirty.py').write_text('staged = True\n')
        git(workspace.path, 'add', 'backend/dirty.py')
        (workspace.path / 'backend/dirty.py').write_text('worktree = True\n')
        (workspace.path / 'backend/untracked.py').write_text('keep = True\n')
    upstream(root, 'value = 99\n', 'backend/service.py')
    receipt = b'{"passed": true, "marker": "original"}'
    (workspace.base / 'check.json').write_bytes(receipt)
    before = state(workspace)
    branch = git(workspace.path, 'branch', '--show-current')
    index = index_bytes(workspace)
    with pytest.raises(ValueError, match='original candidate restored'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert_candidate_unchanged(workspace, before, branch, index, receipt)


@pytest.mark.parametrize('dirty, phase, conflict', [
    (False, 'candidate', False),
    (False, 'candidate', True),
    (True, 'probe', False),
    (True, 'probe', True),
    (True, 'candidate', False),
])
def test_protected_timeout_restores_real_git_state(tmp_path, monkeypatch, dirty, phase, conflict):
    import platform_workspace as module
    root, workspace = repository(tmp_path)
    git(workspace.path, 'config', 'rebase.backend', 'apply')
    (workspace.path / 'backend/service.py').write_text('value = 2\n')
    git(workspace.path, 'add', 'backend/service.py')
    git(workspace.path, 'commit', '-m', 'candidate history')
    if dirty:
        (workspace.path / 'backend/dirty.py').write_text('staged = True\n')
        git(workspace.path, 'add', 'backend/dirty.py')
        (workspace.path / 'backend/dirty.py').write_text('worktree = True\n')
        (workspace.path / 'backend/untracked.py').write_text('keep = True\n')
    if conflict:
        upstream(root, 'value = 99\n', 'backend/service.py')
    else:
        upstream(root)
    receipt = b'{"passed": true, "marker": "original"}'
    (workspace.base / 'check.json').write_bytes(receipt)
    before = state(workspace)
    branch = git(workspace.path, 'branch', '--show-current')
    index = index_bytes(workspace)
    actual_git = module.git
    injected = []

    def timeout_after_real_rebase(path, *args):
        is_probe = Path(path) != workspace.path
        matches = is_probe if phase == 'probe' else not is_probe
        if matches and 'rebase' in args and '--abort' not in args and not injected:
            try:
                actual_git(path, *args)
            except subprocess.CalledProcessError:
                pass
            injected.append({'path': str(path), 'head': actual_git(path, 'rev-parse', 'HEAD').strip()})
            raise subprocess.TimeoutExpired(['git', *args], 30)
        return actual_git(path, *args)

    monkeypatch.setattr(module, 'git', timeout_after_real_rebase)
    with pytest.raises(ValueError, match='original candidate restored'):
        workspace.call('workspace.sync', {'protect_candidate': True})
    assert len(injected) == 1
    assert_candidate_unchanged(workspace, before, branch, index, receipt)
    if dirty:
        recovery = json.loads(next(workspace.base.glob('sync-backup-*.json')).read_text())
        assert actual_git(workspace.path, 'rev-parse', recovery['backup_ref']).strip() == recovery['stash_commit']
