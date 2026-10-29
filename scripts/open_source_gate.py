#!/usr/bin/env python3
from pathlib import Path
import re,sys
root=Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve()
skip={'.git','node_modules','dist','__pycache__'}
forbidden={'uploads','backups','secrets','tokens','memory','logs'}
rules={'organization brand':r'\u6606\u4ed1|kun(?:lun)','customer/domain identity':r'\u4e5d\u6c5f|\u6ca7\u5dde|\u897f\u660c|\u4e9a\u65b0|\u914d\u78b3|\u70e7\u7ed3|\u9ad8\u7089','steel-related content':r'\u94a2\u94c1|\u94a2\u5382|\u94a2\u4f01|\u70bc\u94a2|\u94a2\u6750|\u9ad8\u7089|\u70e7\u7ed3|\u94c1\u6c34|\u94a2\u6c34|\u7126\u70ad|\u7126\u7c89|steel|sinter|blast[\\s_-]*furnace|coke[\\s_-]*oven','deployment address':r'120[.]195[.]38[.]88','embedded credential':r'admin(?:123)|kun(?:lun)123'}
bad=[]
for p in root.rglob('*'):
 rel=p.relative_to(root)
 if rel.as_posix()=='scripts/open_source_gate.py' or any(x in skip for x in rel.parts): continue
 if any(x in forbidden for x in rel.parts): bad.append(f'forbidden path: {rel}'); continue
 if not p.is_file() or p.stat().st_size>5_000_000: continue
 try: text=p.read_text(encoding='utf-8')
 except (UnicodeDecodeError,PermissionError): continue
 for label,pattern in rules.items():
  if re.search(pattern,text,re.I): bad.append(f'{label}: {rel}')
if bad: print('\n'.join(sorted(set(bad)))); raise SystemExit(1)
print('open-source gate: PASS')
