import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/factory'))
from progress_report import write_report


def test_report_preserves_measurements_and_hashes(tmp_path):
    folder=tmp_path/'artifacts';folder.mkdir()
    data={'scope':'isolated-test','baseline':{'count':14,'passed':False},'candidate':{'count':14,'passed':True}}
    (folder/'result.json').write_text(json.dumps(data))
    manifest=write_report(Path(__file__).resolve().parents[2],folder,{'id':7,'goal':'验证空数据语义','acceptance_criteria':['空数据不得通过']},{'agent_session_id':'session-a','model':'test'},data,'原始分析')
    report=(folder/'report.md').read_text()
    assert '空数据不得通过' in report and '"count": 14' in report
    assert '不能推断性能提升' in report and 'not-reviewed'==manifest['publication_review']
    assert not {'report.md','reproducibility.json'} & {x['name'] for x in manifest['evidence_files']}
    for ref in manifest['evidence_files']:
        assert hashlib.sha256((folder/ref['name']).read_bytes()).hexdigest()==ref['sha256']
    assert (folder/'analysis.md').read_text()=='原始分析'


def test_failed_report_has_no_fabricated_results(tmp_path):
    folder=tmp_path/'artifacts';folder.mkdir()
    manifest=write_report(Path(__file__).resolve().parents[2],folder,{'id':8,'goal':'失败任务'},{'agent_session_id':'s','model':'test'},{'scope':'execution-failed'},'未完成',failed=True)
    assert manifest['execution_status']=='failed'
    assert '未测' in (folder/'report.md').read_text()
