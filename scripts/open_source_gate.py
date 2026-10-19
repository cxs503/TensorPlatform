#!/usr/bin/env python3
from pathlib import Path
import re,sys
root=Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve()
skip={'.git','node_modules','dist','__pycache__'}
forbidden={'uploads','backups','secrets','tokens','memory','logs'}
rules={'organization brand':r'昆仑|kunlun','customer/domain identity':r'九江|沧州|西昌|亚新|配碳|烧结|高炉','steel-related content':r'钢铁|钢厂|钢企|炼钢|钢材|高炉|烧结|铁水|钢水|焦炭|焦粉|steel|sinter|blast[\s_-]*furnace|coke[\s_-]*oven','deployment address':r'120\.195\.38\.88','embedded credential':r'admin123|kunlun123'}
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
