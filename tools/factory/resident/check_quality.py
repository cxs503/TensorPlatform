"""Trusted identical regression cases for each independent model candidate."""
import importlib.util
import json
import pandas as pd
spec = importlib.util.spec_from_file_location('candidate', '/work/candidate.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
s = m.DataQualityService()
checks = []
def check(name, fn):
    try:
        assert fn(), name
        checks.append({'name': name, 'passed': True})
    except Exception as e:
        checks.append({'name': name, 'passed': False, 'error': type(e).__name__})
for name, frame in [('zero_by_zero', pd.DataFrame()), ('no_rows', pd.DataFrame(columns=['energy','output'])), ('no_columns', pd.DataFrame(index=[0,1]))]:
    for dimension in ['completeness', 'uniqueness']:
        def valid(f=frame, d=dimension):
            r = getattr(s, 'check_'+d)(f)
            return r['score'] == 0 and r['status'] == 'fail'
        check(name+'_'+dimension, valid)
    check(name+'_overall', lambda f=frame: s.run_quality_check(f, 'test')['status'] == 'poor' and s.run_quality_check(f, 'test')['overall_score'] == 0)
clean = pd.DataFrame({'energy':[10.,20.,30.], 'output':[1.,2.,3.]})
check('clean_completeness', lambda: s.check_completeness(clean)['score'] == 1 and s.check_completeness(clean)['status'] == 'pass')
check('clean_uniqueness', lambda: s.check_uniqueness(clean)['score'] == 1 and s.check_uniqueness(clean)['status'] == 'pass')
missing = pd.DataFrame({'a':[1.,None], 'b':[None,2.]})
check('missing_fraction', lambda: s.check_completeness(missing)['score'] == .5 and s.check_completeness(missing)['status'] == 'fail')
duplicate = pd.DataFrame({'a':[1,1,2]})
check('duplicate_fraction', lambda: abs(s.check_uniqueness(duplicate)['score'] - 2/3) < 1e-10 and s.check_uniqueness(duplicate)['status'] == 'fail')
check('report_persisted', lambda: s.get_quality_report('test') is not None)
print(json.dumps({'passed': all(c['passed'] for c in checks), 'checks': checks, 'count':len(checks)}))
