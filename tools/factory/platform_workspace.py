"""Real platform branches with offline checks and independent peer review."""
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

PREFIXES=('backend/','tools/factory/','scripts/','tests/factory/','docs/reports/','frontend-console/src/','frontend-factory/src/')
SUFFIXES={'.py','.md','.ts','.vue','.json','.sql','.txt','.css'}
def digest(raw):return hashlib.sha256(raw).hexdigest()
def git(root,*args):
 return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',*args],cwd=root,stderr=subprocess.PIPE,timeout=30).decode()

class Workspace:
 def __init__(self,root,sid):
  self.root=Path(root);self.sid=str(uuid.UUID(str(sid)));self.base=self.root/'.runtime/workspaces'/self.sid;self.path=self.base/'repo'
 def ensure(self):
  self.base.mkdir(parents=True,exist_ok=True)
  if not self.path.exists():
   with (self.root/'.runtime/workspace-git.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    branch='factory/agent/'+self.sid
    if git(self.root,'branch','--list',branch).strip():git(self.root,'worktree','add',str(self.path),branch)
    else:git(self.root,'worktree','add','-b',branch,str(self.path),'HEAD')
  return self.path
 def file(self,name):
  if not isinstance(name,str) or not name.startswith(PREFIXES) or '..' in Path(name).parts or any(p.startswith('.') for p in Path(name).parts) or Path(name).suffix not in SUFFIXES:raise ValueError('outside platform source scope')
  if re.search(r'secret|credential|password|accounts|auth\.json',name,re.I):raise ValueError('sensitive path')
  p=self.ensure()/name
  if not p.resolve().is_relative_to(self.path.resolve()) or any(x.is_symlink() for x in [p,*p.parents] if x!=self.path.parent):raise ValueError('symlink or escaped path')
  return p
 def snapshot(self):
  path=self.ensure();names=set(git(path,'ls-files').splitlines())|set(git(path,'ls-files','--others','--exclude-standard').splitlines())
  files={}
  for name in sorted(names):
   # Export source and test configuration only, never repository metadata or local state.
   if not (name.startswith(PREFIXES+('config/','deploy/','examples/factory-pilot/')) or name in ['requirements-factory.txt','pytest.ini','pyproject.toml']):continue
   p=path/name
   if p.is_symlink() or not p.is_file() or any(x.startswith('.') for x in Path(name).parts):continue
   if re.search(r'secret|credential|password|accounts|\.local\.',name,re.I):continue
   if p.stat().st_size>1000000:continue
   try:files[name]=p.read_text()
   except UnicodeError:continue
  if sum(len(x.encode()) for x in files.values())>8000000:raise ValueError('source snapshot exceeds 8MB')
  raw=json.dumps({'files':files},ensure_ascii=False,sort_keys=True).encode()
  return raw,digest(raw)
 def check(self):
  raw,sha=self.snapshot()
  result=subprocess.run(['sudo','-n','/usr/local/libexec/tensorplatform-platform-check'],input=raw,capture_output=True,timeout=210)
  if result.returncode:raise RuntimeError('platform check executor rejected candidate')
  report=json.loads(result.stdout)
  if report['snapshot_sha256']!=sha:raise ValueError('check snapshot mismatch')
  (self.base/'check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
  return report
 def call(self,name,args):
  path=self.ensure()
  if name=='workspace.status':return {'owner_session_id':self.sid,'branch':git(path,'branch','--show-current').strip(),'head':git(path,'rev-parse','HEAD').strip(),'changes':git(path,'status','--short'),'diff_stat':git(path,'diff','--stat'),'scope':'real platform source branch, not production deployment'}
  if name=='workspace.read':
   p=self.file(args['path']);raw=p.read_bytes();offset=max(0,int(args.get('offset',0)));size=max(1,min(12000,int(args.get('limit',12000))))
   text=raw.decode();return {'path':args['path'],'sha256':digest(raw),'content':text[offset:offset+size],'total_characters':len(text),'offset':offset}
  if name=='workspace.write':
   p=self.file(args['path']);expected=args.get('expected_sha256');actual=digest(p.read_bytes()) if p.exists() else None
   if expected!=actual:raise ValueError('file changed; read current SHA before writing')
   content=args['content']
   if not isinstance(content,str) or len(content.encode())>100000:raise ValueError('source file exceeds 100KB')
   p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
   return {'path':args['path'],'sha256':digest(p.read_bytes()),'production_modified':False}
  if name=='workspace.check':return self.check()
  if name=='workspace.sync':
   if git(path,'status','--porcelain').strip():raise ValueError('commit or resolve candidate changes before sync')
   git(path,'rebase',git(self.root,'rev-parse','HEAD').strip())
   return self.call('workspace.status',{})
  if name=='workspace.integrate':
   if git(self.root,'status','--porcelain').strip() or git(path,'status','--porcelain').strip():raise ValueError('integration requires clean source and candidate')
   head=git(path,'rev-parse','HEAD').strip();_,sha=self.snapshot()
   report=json.loads((self.base/'check.json').read_text())
   if not report['passed'] or report['snapshot_sha256']!=sha:raise ValueError('candidate regression stale or failed')
   reviews=[json.loads(p.read_text()) for p in (self.base/'reviews').glob('*.json')]
   valid=[r for r in reviews if r['reviewer_session_id']!=self.sid and r['commit']==head and r['snapshot_sha256']==sha and r['tests_passed'] and r['decision']=='approve']
   if not valid:raise ValueError('independent peer approval for this exact tested commit required')
   with (self.root/'.runtime/workspace-git.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    if git(self.root,'status','--porcelain').strip():raise ValueError('source changed during integration')
    git(self.root,'merge','--ff-only',head)
   return {'integrated_commit':head,'reviewers':[r['reviewer_session_id'] for r in valid],'production_deployed':False,'note':'source integration complete; service activation is a separate operation'}
  if name=='workspace.commit':
   _,sha=self.snapshot();report=json.loads((self.base/'check.json').read_text())
   if report['snapshot_sha256']!=sha or not report['passed']:raise ValueError('current candidate must pass actual frozen regression suite')
   changed=git(path,'diff','--name-only').splitlines()+git(path,'ls-files','--others','--exclude-standard').splitlines()
   if not changed:raise ValueError('no candidate changes')
   for filename in changed:self.file(filename)
   git(path,'add','--',*changed)
   message=args['message']
   if not isinstance(message,str) or not 1<=len(message)<=200:raise ValueError('invalid commit message')
   git(path,'-c','user.name=resident-'+self.sid,'-c','user.email=resident@tensorplatform.local','commit','-m',message)
   return {'commit':git(path,'rev-parse','HEAD').strip(),'branch':git(path,'branch','--show-current').strip(),'production_deployed':False,'next':'invite a peer to workspace.review'}
  if name=='workspace.inspect':
   return {'head':git(path,'rev-parse','HEAD').strip(),'diff':git(path,'diff','HEAD~1','HEAD')[:24000],'status':self.call('workspace.status',{})}
  raise ValueError('unknown workspace tool')

def review(root,reviewer,target,args):
 if str(reviewer)==str(target):raise ValueError('independent peer required')
 workspace=Workspace(root,target);path=workspace.ensure()
 head=git(path,'rev-parse','HEAD').strip()
 if args.get('expected_commit')!=head:raise ValueError('peer candidate changed; inspect immutable commit')
 if git(path,'status','--porcelain').strip():raise ValueError('review requires clean committed candidate')
 result=workspace.check()
 decision=args.get('decision')
 if decision not in ['approve','request_changes']:raise ValueError('explicit review decision required')
 reason=args.get('reason','')
 if not isinstance(reason,str) or not 10<=len(reason)<=4000:raise ValueError('review rationale required')
 record={'reviewer_session_id':str(reviewer),'owner_session_id':str(target),'commit':head,'snapshot_sha256':result['snapshot_sha256'],'tests_passed':result['passed'],'decision':decision,'reason':reason,'production_deployed':False}
 folder=workspace.base/'reviews';folder.mkdir(exist_ok=True)
 (folder/(str(uuid.UUID(str(reviewer)))+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
 return record
