"""Regression coverage for importing the named committed check dependency."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from platform_workspace import Workspace,git,digest

SCRIPT='scripts/record_organization_research.py'

def setup_workspace(tmp_path):
 root=tmp_path/'platform';root.mkdir()
 subprocess.run(['git','init',str(root)],check=True,capture_output=True)
 (root/'backend').mkdir();(root/'backend/service.py').write_bytes(b'baseline=1\n')
 git(root,'add','.');git(root,'-c','user.name=test','-c','user.email=test@local','commit','-m','baseline')
 (root/'.runtime').mkdir()
 workspace=Workspace(root,uuid.uuid4());workspace.ensure()
 return root,workspace

def commit(root):
 git(root,'add','.');git(root,'-c','user.name=test','-c','user.email=test@local','commit','-m','dependency')
 return git(root,'rev-parse','HEAD').strip()

def test_imports_exact_committed_bytes_and_only_named_script(tmp_path):
 root,w=setup_workspace(tmp_path)
 (root/'scripts').mkdir()
 raw=b'# committed dependency\r\nvalue = \x22\xc3\xa9\x22\r\n'
 (root/SCRIPT).write_bytes(raw)
 (root/'scripts/other.py').write_bytes(b'other=1\n')
 mainline=commit(root)
 (root/SCRIPT).write_bytes(b'uncommitted_source=1\n')
 (w.path/'backend/service.py').write_bytes(b'local_edit=4\n')
 result=w.call('workspace.refresh_support',{})
 assert result['trusted_mainline_commit']==mainline
 assert result['imported']=={SCRIPT:digest(raw)}
 assert (w.path/SCRIPT).read_bytes()==raw
 assert not (w.path/'scripts/other.py').exists()
 assert (w.path/'backend/service.py').read_bytes()==b'local_edit=4\n'
 (w.path/SCRIPT).write_bytes(b'local_script_edit=2\n')
 assert w.call('workspace.refresh_support',{})['imported']=={}
 assert (w.path/SCRIPT).read_bytes()==b'local_script_edit=2\n'

def test_uncommitted_script_is_not_imported(tmp_path):
 root,w=setup_workspace(tmp_path)
 (root/'scripts').mkdir();(root/SCRIPT).write_bytes(b'untracked=1\n')
 assert w.call('workspace.refresh_support',{})['imported']=={}
 assert not (w.path/SCRIPT).exists()

def test_committed_symlink_is_not_imported(tmp_path):
 root,w=setup_workspace(tmp_path)
 (root/'scripts').mkdir();(root/SCRIPT).symlink_to('../backend/service.py');commit(root)
 assert w.call('workspace.refresh_support',{})['imported']=={}
 assert not (w.path/SCRIPT).exists()

@pytest.mark.parametrize('parent_link',[False,True])
def test_target_symlinks_cannot_redirect_import(tmp_path,parent_link):
 root,w=setup_workspace(tmp_path)
 (root/'scripts').mkdir();(root/SCRIPT).write_bytes(b'trusted=1\n');commit(root)
 outside=tmp_path/'outside';outside.mkdir()
 sentinel=outside/'record_organization_research.py';sentinel.write_bytes(b'preserve=1\n')
 if parent_link:(w.path/'scripts').symlink_to(outside,target_is_directory=True)
 else:
  (w.path/'scripts').mkdir();(w.path/SCRIPT).symlink_to(sentinel)
 with pytest.raises(ValueError,match='symlink or escaped path'):w.call('workspace.refresh_support',{})
 assert sentinel.read_bytes()==b'preserve=1\n'

def test_exclusive_creation_preserves_concurrent_file(tmp_path,monkeypatch):
 root,w=setup_workspace(tmp_path)
 (root/'scripts').mkdir();(root/SCRIPT).write_bytes(b'trusted=1\n');commit(root)
 original=Path.open
 def racing_open(path,mode='r',*args,**kwargs):
  if path==w.path/SCRIPT and mode=='xb':
   with original(path,'wb') as output:output.write(b'concurrent_edit=1\n')
  return original(path,mode,*args,**kwargs)
 monkeypatch.setattr(Path,'open',racing_open)
 assert w.call('workspace.refresh_support',{})['imported']=={}
 assert (w.path/SCRIPT).read_bytes()==b'concurrent_edit=1\n'

def test_frozen_executor_accepts_named_dependency_path():
 path=Path(__file__).resolve().parents[2]/'deploy/platform-check.py'
 spec=importlib.util.spec_from_file_location('dependency_platform_check',path)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 module.validate({SCRIPT:'value=1\n'})
