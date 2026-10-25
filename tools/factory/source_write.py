"""SHA-guarded source writes shared by isolated development workspaces."""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

def digest(raw):
 return hashlib.sha256(raw).hexdigest()

def write_source(path, args, *, lock_path, max_bytes, replacement_max_bytes=1000000):
 """Write content, or replace exactly one literal occurrence in existing UTF-8.

 All callers sharing a workspace must use the same advisory lock. Atomic rename
 prevents partial files; the SHA check rejects stale cooperative writers.
 External editors that ignore the lock are outside that serialization contract.
 """
 path=Path(path);lock_path=Path(lock_path)
 lock_path.parent.mkdir(parents=True,exist_ok=True)
 with lock_path.open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if path.is_symlink():raise ValueError('symlink source path')
  before=path.read_bytes() if path.exists() else None
  actual=digest(before) if before is not None else None
  expected=args.get('expected_sha256')
  partial='replacement' in args
  if partial and (not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected)):
   raise ValueError('replacement requires expected_sha256 for an existing file')
  if expected!=actual:raise ValueError('file changed; read current SHA before writing')
  if partial:
   if 'content' in args:raise ValueError('choose content or replacement, not both')
   replacement=args['replacement']
   if not isinstance(replacement,dict) or set(replacement)!={'old','new'}:
    raise ValueError('replacement requires exactly old and new')
   old=replacement['old'];new=replacement['new']
   if not isinstance(old,str) or not old or not isinstance(new,str):
    raise ValueError('replacement old must be nonempty text and new must be text')
   if before is None:raise ValueError('replacement requires an existing file')
   text=before.decode('utf-8');first=text.find(old)
   if first<0:raise ValueError('replacement has no match')
   if text.find(old,first+1)>=0:raise ValueError('replacement must have exactly one match')
   raw=(text[:first]+new+text[first+len(old):]).encode('utf-8')
   limit=replacement_max_bytes
  else:
   content=args['content']
   if not isinstance(content,str):raise ValueError('content must be text')
   raw=content.encode('utf-8');limit=max_bytes
  if len(raw)>limit:raise ValueError('source file exceeds size limit')
  path.parent.mkdir(parents=True,exist_ok=True)
  mode=stat.S_IMODE(path.stat().st_mode) if before is not None else 0o644
  temporary=None
  try:
   fd,temporary=tempfile.mkstemp(prefix='.source-write-',dir=path.parent)
   with os.fdopen(fd,'wb') as output:
    os.fchmod(output.fileno(),mode)
    output.write(raw);output.flush();os.fsync(output.fileno())
   # Also detect noncooperating changes observed before rename; no claim of an
   # OS-level compare-and-swap against editors that ignore our advisory lock.
   if path.is_symlink():raise ValueError('symlink source path')
   current=path.read_bytes() if path.exists() else None
   if current!=before:raise ValueError('file changed during write')
   os.replace(temporary,path);temporary=None
  finally:
   if temporary is not None:os.unlink(temporary)
  return digest(raw)
