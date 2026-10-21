"""Generic local documentation evidence; no implicit industry or external network access."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from platform_evidence import sanitize

DOCUMENTS=['README.md','docs/engineering/AI_NATIVE_SDLC_AUDIT_2026-10-21.md','docs/engineering/AGENT_ENGINEERING_QUALITY_GATES.md']

def collect(root: Path):
    evidence=[];root=root.resolve();now=datetime.now(timezone.utc).isoformat()
    for i,name in enumerate(DOCUMENTS):
        entry={'id':f'LOCAL-{i+1}','kind':'platform-document','path':name,'verification':'unavailable','retrieved_at':now}
        path=(root/name).resolve()
        if path.is_relative_to(root) and path.is_file() and not (root/name).is_symlink() and path.stat().st_size<=1_000_000:
            raw=path.read_bytes();entry.update(verification='document-only-not-runtime',sha256=hashlib.sha256(raw).hexdigest(),content=sanitize(raw.decode('utf-8'))[:6000])
        evidence.append(entry)
    return evidence
