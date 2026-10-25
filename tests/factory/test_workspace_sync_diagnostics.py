"""Exercise sync diagnostics and recovery against real temporary Git histories."""
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/factory'))
from platform_workspace import Workspace, git


def commit(root, message, *names):
    git(root, 'add', '--', *names)
    git(root, 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD').strip()


def repository(tmp_path, conflict_name='backend/service.py'):
    root = tmp_path / 'platform'
    root.mkdir()
    git(root, 'init')
    git(root, 'config', 'user.name', 'sync-test')
    git(root, 'config', 'user.email', 'sync-test@local')
    (root / 'backend').mkdir()
    (root / conflict_name).write_text('value = 1\n')
    (root / 'backend/wip.py').write_text('work = 1\n')
    base = commit(root, 'baseline', 'backend')
    (root / '.runtime').mkdir()
    workspace = Workspace(root, uuid.uuid4())
    workspace.ensure()
    return root, workspace, base


@pytest.mark.parametrize('conflict_name', [
    'backend/service.py', 'backend/space name.py', 'backend/line\nname.py',
])
def test_rebase_conflicts_survive_abort_and_wip_is_restored(tmp_path, conflict_name):
    root, workspace, base = repository(tmp_path, conflict_name)
    (workspace.path / conflict_name).write_text('value = 2\n')
    commit(workspace.path, 'candidate conflict', conflict_name)
    (workspace.path / 'backend/candidate.py').write_text('candidate = True\n')
    candidate = commit(workspace.path, 'candidate addition', 'backend/candidate.py')
    (root / conflict_name).write_text('value = 3\n')
    mainline = commit(root, 'mainline conflict', conflict_name)
    (workspace.path / 'backend/wip.py').write_text('work = 7\n')
    (workspace.path / 'backend/untracked.py').write_text('untracked = 8\n')

    result = workspace.call('workspace.sync', {})

    assert result['candidate_commit'] == candidate
    assert result['mainline_commit'] == mainline
    assert result['merge_base'] == base
    assert result['merge_bases'] == [base]
    assert (result['ahead_count'], result['behind_count']) == (2, 1)
    assert result['rebased'] is False and result['rebase_error']
    assert result['rebase_conflicts'] == [conflict_name]
    assert result['conflicts'] == [conflict_name]
    assert result['restore_conflicts'] == []
    assert result['restore_error'] is None
    assert result['diagnostic_error'] is None
    assert result['head'] == candidate
    assert git(workspace.path, 'diff', '--name-only', '--diff-filter=U') == ''
    assert (workspace.path / conflict_name).read_text() == 'value = 2\n'
    assert (workspace.path / 'backend/wip.py').read_text() == 'work = 7\n'
    assert (workspace.path / 'backend/untracked.py').read_text() == 'untracked = 8\n'
    backup = result['backup_commit']
    assert git(workspace.path, 'rev-parse', 'refs/stash').strip() == backup
    assert git(workspace.path, 'show', backup + ':backend/wip.py') == 'work = 7\n'
    assert git(workspace.path, 'show', backup + '^3:backend/untracked.py') == 'untracked = 8\n'
    assert git(root, 'rev-parse', 'HEAD').strip() == mainline
    assert (root / conflict_name).read_text() == 'value = 3\n'


def test_restore_conflict_is_distinguished_from_rebase_conflict(tmp_path):
    root, workspace, base = repository(tmp_path)
    (workspace.path / 'backend/service.py').write_text('value = 2\n')
    (root / 'backend/service.py').write_text('value = 3\n')
    mainline = commit(root, 'mainline conflict with WIP', 'backend/service.py')

    result = workspace.call('workspace.sync', {})

    assert result['candidate_commit'] == base
    assert result['mainline_commit'] == mainline
    assert result['merge_bases'] == [base]
    assert (result['ahead_count'], result['behind_count']) == (0, 1)
    assert result['rebased'] is True
    assert result['rebase_error'] is None
    assert result['rebase_conflicts'] == []
    assert result['restore_conflicts'] == ['backend/service.py']
    assert result['conflicts'] == ['backend/service.py']
    assert result['restore_error']
    assert result['head'] == mainline
    assert git(workspace.path, 'diff', '--name-only', '--diff-filter=U').splitlines() == ['backend/service.py']
    backup = result['backup_commit']
    assert git(workspace.path, 'rev-parse', 'refs/stash').strip() == backup
    assert git(workspace.path, 'show', backup + ':backend/service.py') == 'value = 2\n'


def test_success_reports_pre_sync_divergence_and_restores_work(tmp_path):
    root, workspace, base = repository(tmp_path)
    (workspace.path / 'backend/candidate.py').write_text('candidate = True\n')
    candidate = commit(workspace.path, 'candidate addition', 'backend/candidate.py')
    (root / 'backend/mainline.py').write_text('mainline = True\n')
    mainline = commit(root, 'mainline addition', 'backend/mainline.py')
    (workspace.path / 'backend/wip.py').write_text('work = 7\n')
    (workspace.path / 'backend/untracked.py').write_text('untracked = 8\n')

    result = workspace.call('workspace.sync', {})

    assert result['candidate_commit'] == candidate
    assert result['mainline_commit'] == mainline
    assert result['merge_bases'] == [base]
    assert (result['ahead_count'], result['behind_count']) == (1, 1)
    assert result['rebased'] is True
    assert result['head'] != candidate
    assert git(workspace.path, 'rev-parse', 'HEAD^').strip() == mainline
    assert result['conflicts'] == result['rebase_conflicts'] == result['restore_conflicts'] == []
    assert result['rebase_error'] is result['restore_error'] is result['diagnostic_error'] is None
    assert (workspace.path / 'backend/wip.py').read_text() == 'work = 7\n'
    assert (workspace.path / 'backend/untracked.py').read_text() == 'untracked = 8\n'
    assert (workspace.path / 'backend/mainline.py').is_file()
    assert (workspace.path / 'backend/candidate.py').is_file()
    assert git(workspace.path, 'rev-parse', 'refs/stash').strip() == result['backup_commit']
