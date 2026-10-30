#!/usr/bin/env python3
"""
backfill_audit.py — 扫描 /srv/tensor/agent/work/ 下 m* 工件目录，
统计可抽取样本数的真实上界，输出 docs/sample_db/backfill_audit.md。
数字脚本统计零手抄。
"""
import json, os, glob, sys
from collections import Counter, defaultdict
from datetime import datetime

WORK_ROOT = '/srv/tensor/agent/work'

def find_sdaa_files():
    """Find all sdaa_*.json files under work/m*/"""
    patterns = [
        os.path.join(WORK_ROOT, 'm*', 'artifacts', 'sdaa_*.json'),
        os.path.join(WORK_ROOT, 'm*', 'runs', 'sdaa_*.json'),
        os.path.join(WORK_ROOT, 'm*', 'scripts', 'sdaa_*.json'),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    return sorted(set(files))

def extract_mission_id(filepath):
    """Extract mission_id from path like /srv/tensor/agent/work/m123/..."""
    parts = filepath.split('/')
    for p in parts:
        if p.startswith('m') and p[1:].isdigit():
            return int(p[1:])
    return None

def count_extractable_samples(data):
    """Count how many samples can be extracted from one JSON file."""
    count = 1  # base: the file itself is 1 sample
    
    # Check for tiers (multiple parameter sweeps)
    if 'tiers' in data and isinstance(data['tiers'], list):
        count += len(data['tiers'])  # each tier is an additional sample
    
    # Check for multi-grid results (e.g. wigley has "640" and "960" keys)
    if 'results' in data and isinstance(data['results'], dict):
        grid_keys = [k for k in data['results'] if isinstance(k, str) and k.isdigit()]
        if len(grid_keys) > 1:
            count += len(grid_keys) - 1  # extra beyond first
    
    # Check for sweep_results
    if 'sweep_results' in data and isinstance(data['sweep_results'], list):
        count += len(data['sweep_results'])
    
    # Check for runs
    if 'runs' in data and isinstance(data['runs'], list):
        count += len(data['runs'])
    
    return count

def classify_discipline(case_name):
    """Classify discipline based on case name."""
    case_lower = case_name.lower() if case_name else ''
    fluid_keywords = ['couette', 'poiseuille', 'cavity', 'cylinder', 'sphere', 
                      'suboff', 'womersley', 'taylor', 'stokes', 'sod', 
                      'permeability', 'wigley', 'thermal', 'droplet',
                      'acoustics', 'shear', 'startup', 'square', 'aircraft',
                      'laplace', 'floating']
    structure_keywords = []  # TBD
    multibody_keywords = ['floating_body', 'maneuv']
    
    for kw in multibody_keywords:
        if kw in case_lower:
            return 'multibody'
    for kw in fluid_keywords:
        if kw in case_lower:
            return 'fluid'
    return 'fluid'  # default: most LBM cases are fluid

def classify_verdict(data):
    """Normalize verdict to standard values."""
    v = data.get('verdict', data.get('verdict_main', data.get('overall_pass', data.get('status', None))))
    
    if v is None:
        # Try to infer from gates
        if data.get('all_pass') is True or data.get('all_gates_pass') is True:
            return 'PASS'
        if data.get('all_pass') is False:
            return 'FAIL'
        return 'UNKNOWN'
    
    if isinstance(v, bool):
        return 'PASS' if v else 'FAIL'
    if isinstance(v, dict):
        if v.get('pass') is True or v.get('overall') == 'PASS':
            return 'PASS'
        if v.get('pass') is False or v.get('overall') == 'FAIL':
            return 'FAIL'
        return 'UNKNOWN'
    
    v_str = str(v).upper().strip()
    if v_str in ('PASS', 'OK', 'TRUE'):
        return 'PASS'
    if v_str in ('FAIL', 'FALSE'):
        return 'FAIL'
    if 'HONEST' in v_str:
        return 'HONEST_FAIL'
    if 'IN_PROGRESS' in v_str:
        return 'IN_PROGRESS'
    return 'UNKNOWN'

def main():
    files = find_sdaa_files()
    print(f"Found {len(files)} sdaa_*.json files")
    
    total_samples = 0
    parse_errors = []
    case_types = Counter()
    disciplines = Counter()
    verdicts = Counter()
    missions_with_samples = set()
    device_counts = Counter()
    compile_modes = Counter()
    
    sample_details = []
    
    for f in files:
        mid = extract_mission_id(f)
        try:
            with open(f) as fh:
                data = json.load(fh)
            
            case = data.get('case', data.get('title', data.get('kind', 'unknown')))
            case_str = str(case)[:80]
            n_samples = count_extractable_samples(data)
            total_samples += n_samples
            missions_with_samples.add(mid)
            
            ctype = case_str.split()[0].split('_')[0] if case_str else 'unknown'
            case_types[ctype] += 1
            
            disc = classify_discipline(case_str)
            disciplines[disc] += 1
            
            verdict = classify_verdict(data)
            verdicts[verdict] += 1
            
            device = data.get('device', data.get('sdaa_device', ''))
            if device:
                device_counts[str(device)[:30]] += 1
            
            compile_mode = data.get('compile_mode', '')
            if compile_mode:
                compile_modes[str(compile_mode)[:30]] += 1
            
            sample_details.append({
                'file': f,
                'mission_id': mid,
                'case': case_str,
                'n_samples': n_samples,
                'discipline': disc,
                'verdict': verdict,
                'device': str(device)[:30],
                'has_reference': 'reference' in data or 'benchmark' in data,
                'has_gates': 'gates' in data or 'criteria' in data,
            })
        except Exception as e:
            parse_errors.append({'file': f, 'error': str(e)})
    
    # Generate audit report
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    report = f"""# 回填审计 Backfill Audit

> 生成时间: {now}
> 扫描路径: {WORK_ROOT}/m*/artifacts|runs|scripts/sdaa_*.json
> 统计方式: 脚本自动扫描，零手抄

## 总览

| 指标 | 数值 |
|------|------|
| 工件文件总数 | {len(files)} |
| 可解析文件 | {len(files) - len(parse_errors)} |
| 解析失败 | {len(parse_errors)} |
| **可抽取样本总数** | **{total_samples}** |
| 涉及任务数 (mission_id) | {len(missions_with_samples)} |

## 样本数计算规则

- 每个 JSON 文件 = 至少 1 个样本
- 含 `tiers` 数组: 每个 tier = 额外 1 个样本
- 含多网格 `results` (如 "640"/"960"): 每个额外网格 = 额外 1 个样本
- 含 `sweep_results` 数组: 每个元素 = 额外 1 个样本
- 含 `runs` 数组: 每个元素 = 额外 1 个样本

## 学科分布

| 学科 | 文件数 | 占比 |
|------|--------|------|
"""
    for disc, cnt in disciplines.most_common():
        report += f"| {disc} | {cnt} | {cnt/len(files)*100:.1f}% |\n"
    
    report += f"""
## 工况类型分布 (Top 15)

| 工况类型 | 文件数 |
|----------|--------|
"""
    for ct, cnt in case_types.most_common(15):
        report += f"| {ct} | {cnt} |\n"
    
    report += f"""
## 判定分布

| 判定 | 文件数 |
|------|--------|
"""
    for v, cnt in verdicts.most_common():
        report += f"| {v} | {cnt} |\n"
    
    report += f"""
## 设备分布

| 设备 | 文件数 |
|------|--------|
"""
    for dev, cnt in device_counts.most_common(10):
        report += f"| {dev} | {cnt} |\n"
    
    report += f"""
## 编译模式分布

| 模式 | 文件数 |
|------|--------|
"""
    for cm, cnt in compile_modes.most_common():
        report += f"| {cm} | {cnt} |\n"
    
    report += f"""
## 解析失败文件

"""
    if parse_errors:
        for pe in parse_errors:
            report += f"- `{pe['file']}`: {pe['error']}\n"
    else:
        report += "无解析失败文件。\n"
    
    report += f"""
## 详细样本清单

| # | 文件路径 | mission_id | 工况 | 样本数 | 学科 | 判定 | 有参考值 | 有门限 |
|---|----------|-----------|------|--------|------|------|----------|--------|
"""
    for i, sd in enumerate(sample_details, 1):
        report += f"| {i} | {sd['file'].replace(WORK_ROOT, '')} | {sd['mission_id']} | {sd['case'][:40]} | {sd['n_samples']} | {sd['discipline']} | {sd['verdict']} | {'✓' if sd['has_reference'] else ''} | {'✓' if sd['has_gates'] else ''} |\n"
    
    report += f"""
## 结论

- **当前可回填样本上界: {total_samples} 条**
- 距离指南目标 10,000 条还差 {10000 - total_samples} 条
- 需通过以下方式扩充:
  1. 批量重跑历史工况 (参数扫描)
  2. 新增工况覆盖更多船型/Re 范围
  3. 从验证报告 JSON 中提取额外样本
  4. 接入外部基准数据库 (ITTC, DTMB 等)
"""
    
    # Write audit report
    audit_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'sample_db', 'backfill_audit.md')
    # Adjust path for execution context
    audit_path = '/srv/tensor/agent/work/m378/docs/sample_db/backfill_audit.md'
    with open(audit_path, 'w') as f:
        f.write(report)
    print(f"Audit report written to {audit_path}")
    print(f"Total extractable samples: {total_samples}")
    
    # Also output JSON for programmatic use
    audit_json = {
        'generated_at': now,
        'total_files': len(files),
        'parseable_files': len(files) - len(parse_errors),
        'parse_errors': len(parse_errors),
        'total_extractable_samples': total_samples,
        'missions_with_samples': len(missions_with_samples),
        'disciplines': dict(disciplines),
        'case_types': dict(case_types.most_common(15)),
        'verdicts': dict(verdicts),
        'sample_details': sample_details,
    }
    json_path = '/srv/tensor/agent/work/m378/artifacts/backfill_audit.json'
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, 'w') as f:
        json.dump(audit_json, f, indent=2, ensure_ascii=False)
    print(f"Audit JSON written to {json_path}")

if __name__ == '__main__':
    main()
