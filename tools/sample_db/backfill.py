#!/usr/bin/env python3
"""
backfill.py — 从工件 JSON 抽样入 SQLite /root/tp-agent/sample_db.sqlite
表结构与 docs/sample_db/schema.md 对齐。
对 ≥3 个历史案例工件真实抽取演示。
"""
import json
import os
import sqlite3
import sys
import glob
from datetime import datetime

DB_PATH = '/root/tp-agent/sample_db.sqlite'
WORK_ROOT = '/root/tp-agent/work'

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS samples (
    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER,
    case_name TEXT,
    discipline TEXT,
    solver_type TEXT DEFAULT 'LBM',
    created_at TEXT,

    -- 工况参数
    reynolds REAL,
    mach REAL,
    froude REAL,
    tau_lb REAL,
    lattice TEXT,
    collision TEXT,
    boundary TEXT,
    n_steps INTEGER,
    grid_dims TEXT,
    n_cells INTEGER,
    u_lb REAL,
    nu_lb REAL,

    -- 几何编码
    geometry_type TEXT,
    geometry_params TEXT,
    geometry_sdf TEXT,
    Cb REAL,
    L_pp REAL,
    B REAL,
    T REAL,
    blockage_pct REAL,

    -- 结果向量
    results_json TEXT,
    cd_total REAL,
    cl_mean REAL,
    l2_rel_err REAL,
    max_abs_err REAL,
    mass_drift_pct REAL,
    elapsed_s REAL,

    -- 判据值+PASS 标志
    verdict TEXT,
    pass_flag INTEGER,
    gate_criteria TEXT,
    err_pct_max REAL,
    gate_threshold_pct REAL,
    within_gate INTEGER,

    -- 设备与精度指纹
    device TEXT,
    device_name TEXT,
    compile_mode TEXT,
    dtype TEXT,
    torch_version TEXT,
    finite INTEGER,
    diverged INTEGER,

    -- 谱系指针
    artifact_path TEXT,
    code_sha TEXT,
    model_version TEXT,
    parent_mission_id INTEGER,
    round INTEGER,
    source_repo TEXT,

    -- 扩展字段
    extra_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_mission ON samples(mission_id);
CREATE INDEX IF NOT EXISTS idx_case ON samples(case_name);
CREATE INDEX IF NOT EXISTS idx_discipline ON samples(discipline);
CREATE INDEX IF NOT EXISTS idx_verdict ON samples(verdict);
CREATE INDEX IF NOT EXISTS idx_device ON samples(device);
"""


def extract_mission_id(filepath):
    parts = filepath.split('/')
    for p in parts:
        if p.startswith('m') and p[1:].isdigit():
            return int(p[1:])
    return None


def classify_discipline(case_name):
    case_lower = (case_name or '').lower()
    multibody_kw = ['floating_body', 'maneuv']
    fluid_kw = ['couette', 'poiseuille', 'cavity', 'cylinder', 'sphere',
                'suboff', 'womersley', 'taylor', 'stokes', 'sod',
                'permeability', 'wigley', 'thermal', 'droplet',
                'acoustics', 'shear', 'startup', 'square', 'aircraft',
                'laplace', 'floating']
    for kw in multibody_kw:
        if kw in case_lower:
            return 'multibody'
    for kw in fluid_kw:
        if kw in case_lower:
            return 'fluid'
    return 'fluid'


def classify_verdict(data):
    v = data.get('verdict', data.get('verdict_main', data.get('overall_pass', data.get('status', None))))
    if v is None:
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


def verdict_to_pass_flag(verdict):
    return 1 if verdict == 'PASS' else 0


def safe_get(d, *keys, default=None):
    """Safe get from dict, skipping non-string keys."""
    for k in keys:
        if not isinstance(k, str):
            continue
        if k in d and d[k] is not None:
            return d[k]
    return default


def extract_numeric(d, *keys, default=None):
    """Extract numeric value from dict, skipping non-string keys."""
    v = safe_get(d, *keys, default=default)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def extract_sample(data, filepath, tier_data=None, grid_key=None):
    """Extract a single sample record from JSON data."""
    mid = extract_mission_id(filepath)
    case = data.get('case', data.get('title', data.get('kind', 'unknown')))
    case_name = str(case)[:200]
    discipline = classify_discipline(case_name)
    verdict = classify_verdict(data)

    # Extract physics params from nested dicts
    physics = data.get('physics', {})
    config = data.get('configuration', data.get('config', data.get('setup_info', {})))
    if not isinstance(physics, dict):
        physics = {}
    if not isinstance(config, dict):
        config = {}

    reynolds = extract_numeric(physics, 'Re') or extract_numeric(data, 'Re')
    mach = extract_numeric(data, 'Ma')
    tau_lb = extract_numeric(physics, 'tau') or extract_numeric(data, 'tau') or extract_numeric(config, 'tau')
    lattice = safe_get(data, 'lattice')
    collision = safe_get(data, 'collision')
    boundary = safe_get(data, 'boundary')

    n_steps = None
    for src in [data, physics, config]:
        if isinstance(src, dict):
            for k in ['n_steps', 'steps']:
                if k in src:
                    try:
                        n_steps = int(src[k])
                        break
                    except (ValueError, TypeError):
                        pass
            if n_steps:
                break

    # Grid info
    domain = data.get('domain', {})
    if not isinstance(domain, dict):
        domain = {}
    grid = data.get('grid', None)
    grid_dims = None
    if isinstance(grid, str):
        grid_dims = grid
    elif isinstance(grid, list):
        grid_dims = json.dumps(grid)
    elif isinstance(domain.get('grid'), str):
        grid_dims = domain['grid']

    n_cells = extract_numeric(domain, 'n_cells')
    u_lb = extract_numeric(physics, 'u_lb')
    nu_lb = extract_numeric(physics, 'nu_lb')

    # Geometry
    geometry = data.get('geometry', {})
    if not isinstance(geometry, dict):
        geometry = {}
    geometry_type = safe_get(geometry, 'type', default='parametric')
    geometry_params = json.dumps(geometry, ensure_ascii=False) if geometry else None

    blockage = None
    if tier_data and isinstance(tier_data, dict) and 'blockage_pct' in tier_data:
        blockage = tier_data['blockage_pct']
    elif 'blockage_pct' in data:
        blockage = data['blockage_pct']

    # Results
    results_raw = data.get('results', {})
    if tier_data and isinstance(tier_data, dict):
        results = tier_data
    elif grid_key and isinstance(results_raw, dict) and grid_key in results_raw:
        results = results_raw[grid_key]
    else:
        results = results_raw if isinstance(results_raw, dict) else {}

    results_json = json.dumps(results, ensure_ascii=False) if results else None

    cd_total = extract_numeric(results, 'Cd_total', 'cd_total', 'Cd', 'cd')
    cl_mean = extract_numeric(results, 'cl_mean', 'Cl_mean')
    l2_rel_err = extract_numeric(data, 'l2_rel_err') or extract_numeric(results, 'l2_rel_err')
    max_abs_err = extract_numeric(data, 'max_abs_err') or extract_numeric(results, 'max_abs_err')
    mass_drift = extract_numeric(data, 'mass_drift_pct') or extract_numeric(results, 'mass_drift_pct')

    elapsed = (extract_numeric(data, 'total_elapsed_s') or
               extract_numeric(data, 'elapsed_s') or
               extract_numeric(data, 'wall_clock_s') or
               extract_numeric(data, 'total_wall_clock_s'))
    if elapsed is None and isinstance(results, dict):
        elapsed = extract_numeric(results, 'elapsed_s', 'wall_s')

    # Device
    device = safe_get(data, 'device', 'sdaa_device')
    device_name = safe_get(data, 'device_name')
    compile_mode = safe_get(data, 'compile_mode')

    # Verdict details
    pass_flag = verdict_to_pass_flag(verdict)

    # Gate info
    gates = data.get('gates', data.get('criteria', {}))
    gate_criteria = json.dumps(gates, ensure_ascii=False) if gates and isinstance(gates, (dict, list)) else None
    gate_threshold = extract_numeric(data, 'gate_pct', 'tolerance_pct', default=3.0)

    # Error
    err_pct_max = None
    if isinstance(results, dict):
        err_dict = results.get('err_pct', {})
        if isinstance(err_dict, dict) and err_dict:
            try:
                err_pct_max = max(abs(v) for v in err_dict.values() if isinstance(v, (int, float)))
            except (ValueError, TypeError):
                pass
    if err_pct_max is None:
        err_pct_max = (extract_numeric(data, 'err_pct') or
                       extract_numeric(data, 'max_rel_err_masked_pct') or
                       extract_numeric(results, 'err_pct'))

    within_gate = 0
    if err_pct_max is not None and gate_threshold is not None:
        within_gate = 1 if abs(err_pct_max) <= gate_threshold else 0

    # Finite/diverged
    finite = 1 if data.get('finite', True) else 0
    diverged = 1 if data.get('diverged', False) else 0

    # Artifact path
    artifact_path = filepath.replace(WORK_ROOT, '')

    # Extra
    extra = {}
    if 'reference' in data:
        extra['reference'] = data['reference']
    if 'benchmark' in data:
        extra['benchmark'] = data['benchmark']
    if 'timestamp' in data:
        extra['timestamp'] = data['timestamp']
    if tier_data and isinstance(tier_data, dict) and 'tier' in tier_data:
        extra['tier'] = tier_data['tier']
    if grid_key:
        extra['grid_key'] = grid_key

    return {
        'mission_id': mid,
        'case_name': case_name,
        'discipline': discipline,
        'solver_type': 'LBM',
        'created_at': datetime.now().isoformat(),
        'reynolds': reynolds,
        'mach': mach,
        'tau_lb': tau_lb,
        'lattice': lattice,
        'collision': collision,
        'boundary': boundary,
        'n_steps': n_steps,
        'grid_dims': grid_dims,
        'n_cells': n_cells,
        'u_lb': u_lb,
        'nu_lb': nu_lb,
        'geometry_type': geometry_type,
        'geometry_params': geometry_params,
        'blockage_pct': blockage,
        'results_json': results_json,
        'cd_total': cd_total,
        'cl_mean': cl_mean,
        'l2_rel_err': l2_rel_err,
        'max_abs_err': max_abs_err,
        'mass_drift_pct': mass_drift,
        'elapsed_s': elapsed,
        'verdict': verdict,
        'pass_flag': pass_flag,
        'gate_criteria': gate_criteria,
        'err_pct_max': err_pct_max,
        'gate_threshold_pct': gate_threshold,
        'within_gate': within_gate,
        'device': device,
        'device_name': device_name,
        'compile_mode': compile_mode,
        'finite': finite,
        'diverged': diverged,
        'artifact_path': artifact_path,
        'extra_json': json.dumps(extra, ensure_ascii=False) if extra else None,
    }


def insert_sample(conn, sample):
    """Insert a sample into the database."""
    columns = list(sample.keys())
    placeholders = ', '.join(['?' for _ in columns])
    col_names = ', '.join(columns)
    values = [sample[c] for c in columns]

    sql = f"INSERT INTO samples ({col_names}) VALUES ({placeholders})"
    cur = conn.execute(sql, values)
    return cur.lastrowid



def _admission_decision(filepath, decision=None, mission_resolver=None, audit_log=None):
    """门缺陷 B1 收口：INSERT 前先过 src.sample_db 的三门准入（manifest / mission / checksum）。

    返回通过的 AdmissionDecision；不通过则 raise AdmissionRejected（结构化错误码 + 审计留痕），
    一行都不 INSERT。调用方若已在别处判过（如 ``src.sample_db.ingest``），可把 ``decision``
    传进来复用，避免重复判定与重复留痕。
    """
    if decision is not None:
        return decision
    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from src.sample_db.admission import admit_sample, require_admission
    dec = admit_sample(filepath, mission_resolver=mission_resolver, audit_log=audit_log)
    return require_admission(dec)


def backfill_file(conn, filepath, *, decision=None, mission_resolver=None, audit_log=None):
    """Backfill all samples from one JSON file（**带门**：未过准入的产物一律拒收）。"""
    _admission_decision(filepath, decision, mission_resolver, audit_log)
    with open(filepath) as f:
        data = json.load(f)

    samples_inserted = []

    # Base sample
    base = extract_sample(data, filepath)
    sid = insert_sample(conn, base)
    samples_inserted.append({'sample_id': sid, 'source': 'base', 'case': base['case_name']})

    # Tiers
    if 'tiers' in data and isinstance(data['tiers'], list):
        for tier in data['tiers']:
            s = extract_sample(data, filepath, tier_data=tier)
            sid = insert_sample(conn, s)
            samples_inserted.append({'sample_id': sid, 'source': f"tier:{tier.get('tier','?')}", 'case': s['case_name']})

    # Multi-grid results
    if 'results' in data and isinstance(data['results'], dict):
        grid_keys = [k for k in data['results'] if isinstance(k, str) and k.isdigit()]
        for gk in grid_keys:
            s = extract_sample(data, filepath, grid_key=gk)
            sid = insert_sample(conn, s)
            samples_inserted.append({'sample_id': sid, 'source': f"grid:{gk}", 'case': s['case_name']})

    return samples_inserted


def main(demo_files=None):
    """Main backfill function."""
    # Remove old DB if exists
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    # Create DB
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    if demo_files:
        files = demo_files
    else:
        patterns = [
            os.path.join(WORK_ROOT, 'm*', 'artifacts', 'sdaa_*.json'),
            os.path.join(WORK_ROOT, 'm*', 'runs', 'sdaa_*.json'),
            os.path.join(WORK_ROOT, 'm*', 'scripts', 'sdaa_*.json'),
        ]
        files = sorted(set(f for pat in patterns for f in glob.glob(pat)))

    all_inserted = []
    errors = []

    for f in files:
        try:
            inserted = backfill_file(conn, f)
            all_inserted.extend(inserted)
        except Exception as e:
            errors.append({'file': f, 'error': str(e)})

    conn.commit()

    # Get count
    cursor = conn.execute("SELECT COUNT(*) FROM samples")
    count = cursor.fetchone()[0]

    print(f"Inserted {len(all_inserted)} samples from {len(files)} files")
    print(f"Total rows in DB: {count}")
    print(f"Errors: {len(errors)}")
    if errors:
        for e in errors[:5]:
            print(f"  {e['file']}: {e['error']}")

    # Verify count matches
    assert count == len(all_inserted), f"Count mismatch: DB has {count}, inserted {len(all_inserted)}"

    conn.close()

    return {
        'db_path': DB_PATH,
        'total_samples': count,
        'files_processed': len(files),
        'errors': errors,
        'inserted': all_inserted,
    }


if __name__ == '__main__':
    # Demo mode: pick 3 representative files
    demo_files = [
        '/root/tp-agent/work/m10/artifacts/sdaa_couette_result.json',
        '/root/tp-agent/work/m62/artifacts/sdaa_suboff_re1000.json',
        '/root/tp-agent/work/m70/artifacts/sdaa_wigley_hydrostatics.json',
    ]

    # Check files exist
    available = [f for f in demo_files if os.path.exists(f)]
    if len(available) < 3:
        all_files = sorted(glob.glob(os.path.join(WORK_ROOT, 'm*', 'artifacts', 'sdaa_*.json')))
        available = all_files[:3]

    print(f"Demo files: {available}")
    result = main(demo_files=available)

    # Save demo result
    demo_output = {
        'demo_files': available,
        'total_samples': result['total_samples'],
        'files_processed': result['files_processed'],
        'errors': result['errors'],
        'inserted_samples': result['inserted'],
        'db_path': result['db_path'],
    }

    demo_path = '/root/tp-agent/work/m378/artifacts/backfill_demo.json'
    os.makedirs(os.path.dirname(demo_path), exist_ok=True)
    with open(demo_path, 'w') as f:
        json.dump(demo_output, f, indent=2, ensure_ascii=False)
    print(f"Demo output written to {demo_path}")
