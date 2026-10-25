"""Real platform branches with offline checks and independent peer review."""
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid
from source_write import write_source

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
  if result.returncode:raise RuntimeError('platform check executor failed: '+(result.stderr or result.stdout).decode(errors='replace')[-2500:])
  report=json.loads(result.stdout)
  if report['snapshot_sha256']!=sha:raise ValueError('check snapshot mismatch')
  (self.base/'check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
  return report
 def _sync_protected(self):
  path=self.ensure()
  with (self.root/'.runtime/workspace-git.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   for marker in ['rebase-merge','rebase-apply','MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD']:
    location=Path(git(path,'rev-parse','--git-path',marker).strip())
    if not location.is_absolute():location=path/location
    if location.exists():raise ValueError('resolve existing Git operation before sync')
   if git(path,'ls-files','--unmerged').strip():raise ValueError('resolve existing conflicts before sync')
   target=git(self.root,'rev-parse','HEAD').strip()
   original=git(path,'rev-parse','HEAD').strip()
   original_branch=git(path,'branch','--show-current').strip()
   index_path=Path(git(path,'rev-parse','--git-path','index').strip())
   if not index_path.is_absolute():index_path=path/index_path
   original_index=index_path.read_bytes() if index_path.exists() else None
   def rollback_rebase():
    for marker in ['rebase-merge','rebase-apply']:
     location=Path(git(path,'rev-parse','--git-path',marker).strip())
     if not location.is_absolute():location=path/location
     if location.exists():
      git(path,'rebase','--abort');break
    if git(path,'branch','--show-current').strip()!=original_branch:
     raise RuntimeError('sync recovery needs assistance; candidate branch changed')
    if git(path,'rev-parse','HEAD').strip()!=original:
     git(path,'reset','--hard',original)
   def restore_index():
    if original_index is None:index_path.unlink(missing_ok=True)
    else:index_path.write_bytes(original_index)
   dirty=bool(git(path,'status','--porcelain').strip())
   if not dirty:
    try:git(path,'rebase',target)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
     rollback_rebase()
     restore_index()
     raise ValueError('sync failed; original candidate restored; candidate was not integrated') from None
    (self.base/'check.json').unlink(missing_ok=True)
    return self.call('workspace.status',{})
   # Validate every dirty source path before putting any candidate into Git storage.
   names=set(git(path,'diff','HEAD','--name-only','-z').split('\0'))
   names.update(git(path,'ls-files','--others','--exclude-standard','-z').split('\0'))
   for name in names:
    if name:self.file(name)
   token=str(uuid.uuid4());backup='refs/factory-sync/'+self.sid+'/'+token
   probe=self.base/('sync-probe-'+token)
   git(path,'-c','user.name=resident-'+self.sid,'-c','user.email=resident@tensorplatform.local',
       'stash','push','--include-untracked','--message','workspace.sync '+token)
   saved=git(path,'rev-parse','refs/stash').strip()
   # Keep an explicit recovery ref and receipt; never drop another worktree's stash.
   git(path,'update-ref',backup,saved)
   receipt=self.base/('sync-backup-'+token+'.json')
   receipt.write_text(json.dumps({'original_head':original,'target_head':target,'backup_ref':backup,'stash_commit':saved}))
   def restore_original():
    rollback_rebase()
    try:git(path,'stash','apply','--index',saved)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
     raise RuntimeError('candidate restoration needs assistance; recovery backup retained at '+backup) from None
    restore_index()
   try:
    # Test both committed history and dirty changes in a disposable worktree.
    git(path,'worktree','add','--detach',str(probe),original)
    git(probe,'-c','user.name=resident-'+self.sid,'-c','user.email=resident@tensorplatform.local','rebase',target)
    git(probe,'stash','apply','--index',saved)
   except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
    restore_original()
    raise ValueError('sync preflight failed; original candidate restored; recovery backup '+backup) from None
   finally:
    if probe.exists():
     # The probe contains only copies already protected by the recovery ref.
     git(path,'worktree','remove','--force',str(probe))
   try:
    git(path,'-c','user.name=resident-'+self.sid,'-c','user.email=resident@tensorplatform.local','rebase',target)
   except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
    restore_original()
    raise ValueError('sync rebase failed; original candidate restored; recovery backup '+backup) from None
   try:git(path,'stash','apply','--index',saved)
   except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
    raise RuntimeError('sync restoration failed after rebase; do not discard candidate; recovery backup '+backup) from None
   (self.base/'check.json').unlink(missing_ok=True)
   result=self.call('workspace.status',{})
   result.update({'backup_ref':backup,'backup_commit':saved,'previous_head':original,'rebased':True,'conflicts':[],'checks_require_renewal':True})
   return result
 def call(self,name,args):
  path=self.ensure()
  if name=='workspace.check_result':
   receipt=self.base/'check.json'
   if not receipt.is_file():raise ValueError('no saved check receipt; run check first')
   result=json.loads(receipt.read_text());log=result.pop('log','');offset=max(0,int(args.get('offset',0)));limit=max(1,min(12000,int(args.get('limit',12000))))
   return {**result,'log':log[offset:offset+limit],'offset':offset,'total_characters':len(log),'freshness':'saved receipt; compare snapshot with current source before acceptance'}
  if name=='workspace.refresh_support':
   imported={}
   for source in sorted((self.root/'tools/factory').rglob('*.py')):
    relative=source.relative_to(self.root).as_posix();target=self.file(relative)
    if source.is_symlink() or not source.resolve().is_relative_to(self.root.resolve()) or source.stat().st_size>1000000 or target.exists():continue
    raw=source.read_bytes();target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);imported[relative]=digest(raw)
   # Only this named check dependency may be imported from trusted mainline.
   # Read its committed blob as bytes, never reconstruct it from tool output.
   mainline=git(self.root,'rev-parse','HEAD').strip()
   for relative in ('scripts/record_organization_research.py',):
    target=self.file(relative)
    if target.exists():continue
    entry=git(self.root,'ls-tree','-z',mainline,'--',relative)
    if not entry:continue
    metadata,filename=entry.rstrip('\0').split('\t',1)
    mode,kind,blob=metadata.split()
    if filename!=relative or mode not in ('100644','100755') or kind!='blob':continue
    if int(git(self.root,'cat-file','-s',blob).strip())>1000000:continue
    raw=subprocess.check_output(['git','-c','core.hooksPath=/dev/null','cat-file','blob',blob],cwd=self.root,stderr=subprocess.PIPE,timeout=30)
    target.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also preserves a file created since the missing check.
    try:
     with target.open('xb') as output:output.write(raw)
    except FileExistsError:continue
    imported[relative]=digest(raw)
   return {'imported':imported,'existing_files_preserved':True,'trusted_mainline_commit':mainline,'scope':'missing support source and explicitly named committed check dependency only; rerun tests; existing stale code requires reconciliation'}
  if name=='workspace.status':return {'owner_session_id':self.sid,'branch':git(path,'branch','--show-current').strip(),'head':git(path,'rev-parse','HEAD').strip(),'changes':git(path,'status','--short'),'diff_stat':git(path,'diff','--stat'),'scope':'real platform source branch, not production deployment'}
  if name=='workspace.read':
   p=self.file(args['path']);raw=p.read_bytes();offset=max(0,int(args.get('offset',0)));size=max(1,min(12000,int(args.get('limit',12000))))
   text=raw.decode();return {'path':args['path'],'sha256':digest(raw),'content':text[offset:offset+size],'total_characters':len(text),'offset':offset}
  if name=='workspace.write':
   p=self.file(args['path'])
   result_sha=write_source(p,args,lock_path=self.base/'source-write.lock',max_bytes=100000)
   return {'path':args['path'],'sha256':result_sha,'production_modified':False}
  if name=='workspace.check':return self.check()
  if name=='workspace.sync':
   protect_candidate=args.get('protect_candidate',False)
   if not isinstance(protect_candidate,bool):raise ValueError('protect_candidate must be boolean')
   if protect_candidate:return self._sync_protected()
   # The stash namespace is shared across worktrees: serialize backup/rebase/apply.
   with (self.root/'.runtime/workspace-git.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    # Capture immutable pre-sync endpoints; HEAD may change during rebase.
    candidate_commit=git(path,'rev-parse','HEAD').strip()
    mainline_commit=git(self.root,'rev-parse','HEAD').strip()
    try:merge_bases=git(path,'merge-base','--all',candidate_commit,mainline_commit).splitlines()
    except subprocess.CalledProcessError as exc:
     if exc.returncode!=1:raise
     merge_bases=[]
    ahead_count,behind_count=map(int,git(path,'rev-list','--left-right','--count',candidate_commit+'...'+mainline_commit).split())
    backup=None;rebased=False;rebase_error=None
    rebase_conflicts=[];restore_conflicts=[];diagnostic_error=None
    if git(path,'status','--porcelain').strip():
     git(path,'stash','push','--include-untracked','-m','resident-sync-backup-'+self.sid)
     backup=git(path,'rev-parse','refs/stash').strip()
    try:
     git(path,'rebase',mainline_commit);rebased=True
    except subprocess.CalledProcessError:
     rebase_error='rebase conflict; rebase aborted; reconcile candidate commits with current source'
     # Abort clears the unmerged index, so retain paths before recovery.
     try:rebase_conflicts=[p for p in git(path,'diff','--name-only','--diff-filter=U','-z').split('\0') if p]
     except subprocess.CalledProcessError:diagnostic_error='could not read rebase conflict paths before abort'
     try:git(path,'rebase','--abort')
     except subprocess.CalledProcessError:pass
    restore_error=None
    if backup:
     try:git(path,'stash','apply',backup)
     except subprocess.CalledProcessError:
      restore_conflicts=[p for p in git(path,'diff','--name-only','--diff-filter=U','-z').split('\0') if p];restore_error='WIP restore incomplete; preserved backup_commit contains original edits; resolve conflicts or file collisions before continuing'
    conflicts=sorted(set(rebase_conflicts+restore_conflicts))
    return {**self.call('workspace.status',{}),'candidate_commit':candidate_commit,'mainline_commit':mainline_commit,'merge_base':merge_bases[0] if merge_bases else None,'merge_bases':merge_bases,'ahead_count':ahead_count,'behind_count':behind_count,'rebased':rebased,'rebase_error':rebase_error,'backup_commit':backup,'conflicts':conflicts,'rebase_conflicts':rebase_conflicts,'restore_conflicts':restore_conflicts,'diagnostic_error':diagnostic_error,'restore_error':restore_error,'note':'Commit endpoints and divergence describe pre-sync history; conflict paths record the failed attempt and may no longer be unmerged after abort; WIP backup retained in Git stash; all checks and peer reviews must be renewed'}
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
   changed=sorted(set(git(path,'diff','HEAD','--name-only').splitlines()+git(path,'ls-files','--others','--exclude-standard').splitlines()))
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
