import json
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from platform_workspace import Workspace,git,digest,review

def repository(tmp_path):
 root=tmp_path/'platform';root.mkdir()
 subprocess.run(['git','init',str(root)],check=True,capture_output=True)
 (root/'backend').mkdir();(root/'backend/service.py').write_text('value = 1\n')
 git(root,'add','.');git(root,'-c','user.name=test','-c','user.email=test@local','commit','-m','baseline')
 (root/'.runtime').mkdir()
 return root

def test_real_branch_edit_preserves_host_and_checks_stale_writes(tmp_path):
 root=repository(tmp_path);w=Workspace(root,uuid.uuid4());w.ensure()
 before=w.call('workspace.read',{'path':'backend/service.py'})
 w.call('workspace.write',{'path':'backend/service.py','content':'value = 2\n','expected_sha256':before['sha256']})
 assert (root/'backend/service.py').read_text()=='value = 1\n'
 assert (w.path/'backend/service.py').read_text()=='value = 2\n'
 with pytest.raises(ValueError,match='changed'):w.call('workspace.write',{'path':'backend/service.py','content':'value=3','expected_sha256':before['sha256']})
 for path in ['../escape.py','.runtime/auth.json','backend/../../escape.py','backend/passwords.py']:
  with pytest.raises(ValueError):w.file(path)
 with pytest.raises(FileNotFoundError):w.call('workspace.commit',{'message':'untested'})

def test_review_requires_peer_exact_commit_and_clean_candidate(tmp_path):
 root=repository(tmp_path);sid=str(uuid.uuid4());w=Workspace(root,sid);w.ensure();head=git(w.path,'rev-parse','HEAD').strip()
 with pytest.raises(ValueError,match='independent'):review(root,sid,sid,{'expected_commit':head})
 with pytest.raises(ValueError,match='changed'):review(root,str(uuid.uuid4()),sid,{'expected_commit':'0'*40})
 (w.path/'backend/service.py').write_text('value=2')
 with pytest.raises(ValueError,match='clean'):review(root,str(uuid.uuid4()),sid,{'expected_commit':head})

def test_platform_check_rejects_paths_and_oversized_sources():
 import importlib.util
 p=Path(__file__).resolve().parents[2]/'deploy/platform-check.py'
 spec=importlib.util.spec_from_file_location('platform_check',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 for name in ['/etc/passwd','../escape.py','.runtime/auth.json']:
  with pytest.raises(ValueError):module.validate({name:'x'})
 with pytest.raises(ValueError):module.validate({'backend/service.py':'x'*1000001})
