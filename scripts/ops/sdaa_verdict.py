"""判据抽取阶梯（供 scripts/ops/sdaa_sweep_audit.py 使用）。

分级（强 -> 弱），每级只用 API 文本中的显式判据语句，不做语义猜测：
  A 级 全局裁决语句：判定[：]/verdict.pass=/overall/结论
  B 级 门控汇总语句：all N gates PASS、N/N PASS、ALL PASS、全部判据通过、诚实报告 FAIL、honest-FAIL
  C 级 弱证据：某侧词频 >=2 且另一侧为 0（标 W，报告中标为「弱证据」）
同一级出现 PASS / FAIL 两个方向 -> CONFLICT（如实暴露，不由人工口径裁决）。
"""
import re

A_PATTERNS = [
    re.compile(r'判定\s*[:：]\s*(HONEST-?FAIL|honest-?FAIL|FAIL|PASS|WITHHELD)', re.I),
    re.compile(r'(?:verdict\.?pass|all_pass)\s*[=:]\s*(True|False|true|false)'),
    re.compile(r'overall\s*[:=：]?\s*(HONEST-?FAIL|FAIL|PASS)', re.I),
    re.compile(r'(?:结论|诊断结论)[^\n]{0,40}?(HONEST-?FAIL|honest-?FAIL|PASS|FAIL)', re.I),
    re.compile(r'verdict\s*[=:]\s*(PASS|FAIL)', re.I),
    re.compile(r'升级为\s*(PASS|FAIL|WITHHELD)'),
]

B_PATTERNS = [
    re.compile(r'all\s+(\d+)\s+gate[^\n]{0,20}PASS', re.I),
    re.compile(r'(\d+)\s*/\s*(\d+)\s*PASS'),
    re.compile(r'all\s+pass', re.I),
    re.compile(r'ALL\s+PASS', re.I),
    re.compile(r'全部[^\n]{0,8}(?:PASS|通过)'),
    re.compile(r'(?:全部)?判据(?:全部|均|全)[^\n]{0,6}(?:PASS|通过)'),
    re.compile(r'诚实(?:地)?报告\s*FAIL'),
    re.compile(r'验证(?:已)?通过'),
    re.compile(r'主判据\s*[:：]?\s*PASS', re.I),
    re.compile(r'NOT_SUPPORTED|NOT_RUN', re.I),
]


def _norm(tok):
    t = (tok or '').upper()
    if 'HONEST' in t:
        return 'FAIL'
    if t == 'TRUE':
        return 'PASS'
    if t == 'FALSE':
        return 'FAIL'
    if t in ('NOT_RUN', 'NOT_SUPPORTED'):
        return 'WITHHELD'
    return t


def _tier_a(text):
    hits = []
    for rx in A_PATTERNS:
        for mm in rx.finditer(text):
            hits.append(('A', _norm(mm.group(1)), mm.group(0).strip()[:70]))
    return hits


def _tier_b(text):
    hits = []
    for rx in B_PATTERNS:
        for mm in rx.finditer(text):
            g = mm.groups()
            if len(g) == 2 and g[0] and g[1]:
                if int(g[0]) != int(g[1]):
                    continue
            tok = mm.group(0)
            if re.search(r'NOT_RUN|NOT_SUPPORTED', tok, re.I):
                label = 'WITHHELD'
            elif re.search(r'报告\s*FAIL', tok, re.I):
                label = 'FAIL'
            else:
                label = 'PASS'
            hits.append(('B', label, tok.strip()[:70]))
    return hits


def _tier_c(text):
    p = len(re.findall(r'PASS', text)) + len(re.findall(r'验证通过', text))
    f = len(re.findall(r'FAIL', text)) + len(re.findall(r'honest-?FAIL', text, re.I))
    if p >= 2 and f == 0:
        mm = re.search(r'[^\n]{0,40}PASS[^\n]{0,20}', text)
        return [('C', 'PASS', (mm.group(0) if mm else 'PASS').strip()[:70])]
    if f >= 2 and p == 0:
        mm = re.search(r'[^\n]{0,40}FAIL[^\n]{0,20}', text)
        return [('C', 'FAIL', (mm.group(0) if mm else 'FAIL').strip()[:70])]
    return []


def resolve_text(text):
    """返回 (verdict, rule, evidence)；verdict ∈ PASS/FAIL/WITHHELD/CONFLICT。"""
    for tier, fn in (('A', _tier_a), ('B', _tier_b)):
        hits = fn(text)
        labels = {h[1] for h in hits}
        if len(labels) == 1:
            return labels.pop(), tier, hits[0][2]
        if len(labels) > 1:
            return 'CONFLICT', f'{tier}-conflict', ' | '.join(f'{h[1]}:{h[2]}' for h in hits[:3])
    hits = _tier_c(text)
    if hits:
        return hits[0][1], 'C-weak', hits[0][2]
    return 'WITHHELD', 'no-token', ''