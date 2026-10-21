"""Actual container replay; writes new derived evidence, never modifies registered tasks."""
import sys,json
from pathlib import Path
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools/factory'))
from development import sandbox
from baseline_attribution import attribute,frozen_suite,baseline_with_tests
from progress_report import write_report
out=r/'.runtime/evidence-gates-validation';out.mkdir(exist_ok=True)
sys.path.insert(0,str(r/'scripts'))
from run_resident_agent import Client
from campaign import verified_json
package=verified_json(Client(),r,22,'independent-package-m20.json')
checks=sandbox(package)
(out/'replayed-package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2))
data={'scope':'replay-of-immutable-m20-with-m22-independent-tests-not-production','candidate':checks}
(out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
manifest=write_report(r,out,{'id':20,'goal':'重放任务20与任务22的不可变独立补充测试，验证新的证据门禁'},{'agent_session_id':'operator-replay-not-resident','model':'not-called'},data,'本次没有调用模型；使用既有附件实际重跑。',failed=not checks['process_passed'])
print(json.dumps({'replay_counts':checks.get('tests',{}).get('counts'),'tests_run':checks.get('tests',{}).get('tests_run'),'process_passed':checks['process_passed'],'gate_passed':manifest['quality_gate']['passed']}))
suite='''import unittest\nimport code\nclass T(unittest.TestCase):\n def test_old(self):self.assertEqual(code.old(),1)\n def test_new(self):self.assertEqual(code.new(),1)\n def test_fixed(self):self.assertEqual(code.fixed(),1)\n'''
base={'files':{'code.py':'def old():return 0\ndef new():return 1\ndef fixed():return 0\n','test_code.py':suite}}
head={'files':{'code.py':'def old():return 0\ndef new():return 0\ndef fixed():return 1\n','test_code.py':suite}}
b=sandbox(base);h=sandbox(head)
a=attribute(b,h,baseline_suite_sha256=frozen_suite(base),candidate_suite_sha256=frozen_suite(head))
assert len(a['new_failures'])==len(a['baseline_failures'])==len(a['resolved_failures'])==1
(out/'controlled-attribution.json').write_text(json.dumps({'baseline':b,'candidate':h,'attribution':a},indent=2))
print(json.dumps({'attribution':a}))
for name,src in [('empty','import unittest'),('skip','import unittest\n@unittest.skip("skip")\nclass T(unittest.TestCase):\n def test_skip(self):pass\n')]:
 c=sandbox({'files':{'code.py':'x=1','test_code.py':src}})
 assert not c['process_passed']
 (out/f'{name}-suite.json').write_text(json.dumps(c,indent=2))
print('zero and skipped suites rejected')
