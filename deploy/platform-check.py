#!/usr/bin/python3
"""Root-owned fixed offline platform regression runner; no caller-supplied commands."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
ROOT=Path('__FACTORY_WORKSPACE_ROOT__') # rendered by install_factory_executors.py
IMAGE='tensorplatform-factory-builder:local'

def validate(files):
 if not isinstance(files,dict) or not files or len(files)>1500:raise ValueError('invalid source snapshot')
 for name,content in files.items():
  p=Path(name)
  if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts) or not isinstance(content,str) or len(content.encode())>1000000:raise ValueError('invalid snapshot file')
 if sum(len(v.encode()) for v in files.values())>8000000:raise ValueError('snapshot too large')
 return files

def main():
 raw=sys.stdin.buffer.read(9000001)
 if len(raw)>9000000:raise ValueError('payload too large')
 files=validate(json.loads(raw)['files']);name='tensorplatform-platform-check-'+uuid.uuid4().hex
 with tempfile.TemporaryDirectory(prefix='tensorplatform-platform-check-') as temp:
  folder=Path(temp);folder.chmod(0o755)
  for path,content in files.items():
   p=folder/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content);p.chmod(0o444)
  # Existing regression tests cannot be erased or weakened by candidate edits.
  frozen={}
  for p in sorted((ROOT/'tests/factory').glob('*.py')):
   if p.is_symlink():raise ValueError('unexpected baseline symlink')
   raw_test=p.read_bytes();dest=folder/'tests/factory'/p.name;dest.parent.mkdir(parents=True,exist_ok=True)
   dest.chmod(0o644) if dest.exists() else None
   dest.write_bytes(raw_test);dest.chmod(0o444);frozen[p.name]=hashlib.sha256(raw_test).hexdigest()
  image=subprocess.check_output(['/usr/bin/docker','image','inspect',IMAGE,'--format','{{.Id}}']).decode().strip()
  (folder/'tokens').mkdir(exist_ok=True)
  args=['/usr/bin/docker','run','--pull','never','--rm','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','160','--memory','2g','--cpus','2','--user','65534:65534','--tmpfs','/tmp:rw,nosuid,nodev,size=256m','--tmpfs','/work/tokens:rw,nosuid,nodev,size=8m','--mount',f'type=bind,src={temp},dst=/work,readonly','--workdir','/work',image,'-m','pytest','tests/factory','-q','-p','no:cacheprovider']
  try:
   try:
    run=subprocess.run(args,capture_output=True,timeout=180);code=run.returncode;log=(run.stdout+run.stderr).decode(errors='replace')[-30000:];timed=False
   except subprocess.TimeoutExpired:code=-1;log='platform regression timed out';timed=True
   print(json.dumps({'snapshot_sha256':hashlib.sha256(raw).hexdigest(),'passed':code==0 and not timed and bool(re.search(r'\b[1-9][0-9]* passed\b',log)),'exit_code':code,'timed_out':timed,'log':log,'image':image,'frozen_test_sha256':frozen,'assurance':'frozen software regression plus candidate additions; independent semantic review and business acceptance separate'}))
  finally:subprocess.run(['/usr/bin/docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
if __name__=='__main__':main()
