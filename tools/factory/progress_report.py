"""Evidence-preserving progress reports; no inference or invented measurements."""
from evidence_gates import collect_claims, evaluate, make_test_figure
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone

POLICY = '''每次任务交付必须提交论文式进展报告：摘要、研究问题与验收目标、背景与来源、方法与变更、实验设置、结果、讨论与局限、结论与下一步、复现材料。逐项对应验收标准与证据；报告适用时应包含基线/对照、定量指标、图表、误差定义、样本量与不确定性。没有执行的实验写“未执行”，不编造图表、引文、提升比例、训练或业务收益。设计交付只能证明设计已产出。区分实测、静态分析、假设与待验证；论文式结构不等于已达到发表或独立验收标准。'''


def write_report(root, folder, mission, resident, data, narrative, *, failed=False):
    """Keep original narrative and machine evidence alongside a verifiable envelope."""
    try:
        revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = 'unavailable', None
    claims=collect_claims(folder,data) if (folder/'result.json').exists() else []
    claims.extend(data.get('evidence_claims',[]))
    figures=list(data.get('figures',[]))
    try:figures.extend(make_test_figure(folder,data))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        figures.append({'caption':'required test chart failed','generation_error':type(e).__name__})
    gates=evaluate(folder,claims,figures)
    (folder/'evidence-claims.json').write_text(json.dumps({'claims':claims,'figures':figures},ensure_ascii=False,indent=2))
    (folder/'evidence-gates.json').write_text(json.dumps(gates,ensure_ascii=False,indent=2))
    failed=failed or not gates['passed']
    (folder/'analysis.md').write_text(narrative, encoding='utf-8')
    inputs = [{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size}
              for p in sorted(folder.iterdir()) if p.is_file() and p.name not in {'report.md','reproducibility.json'}]
    manifest = {'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
        'mission_id':mission['id'],'agent_session_id':resident['agent_session_id'],
        'model':mission.get('model',resident.get('model')),'scope':data.get('scope'),
        'execution_status':'failed' if failed else 'delivered-pending-acceptance',
        'code_revision':revision,'tracked_worktree_dirty':dirty,'python':platform.python_version(),
        'evidence_files':inputs,'quality_gate':gates,'publication_review':'not-reviewed',
        'verification_command':'sha256sum analysis.md result.json',
        'reproduction_limit':'哈希校验仅确认附件一致性，不代表复现业务实验；实验命令、数据授权和环境以原始结果为准。'}
    (folder/'reproducibility.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    checks = {k:data[k] for k in ['baseline','candidate','verified','inference','usage','latency_seconds'] if k in data}
    rows='\n'.join(f"| {r['name']} | {r['size_bytes']} | `{r['sha256']}` |" for r in inputs)
    criteria=mission.get('acceptance_criteria') or []
    objectives=json.dumps(criteria,ensure_ascii=False,indent=2) if criteria else '任务未提供独立的结构化验收条目；以任务目标和原始交付内容逐项审核。'
    figures_markdown='\n\n'.join('!['+f['caption']+']('+f['image']['name']+')' for f in figures if 'image' in f)
    text=f'''# 开发进展报告：任务 #{mission['id']}

## 摘要

任务目标：{mission['goal']}

交付状态：{manifest['execution_status']}。证据范围：{data.get('scope','未执行完成')}。本报告尚未经过学术同行评审。

## 1. 研究问题与验收目标

{objectives}

## 2. 背景、方法与具体工作

以下为智能体原始分析，结论须结合后续实测证据审核。

{narrative}

## 3. 实验设置与复现条件

- 执行器版本：`{revision}`；已跟踪文件存在修改：{dirty}。
- Python：{manifest['python']}；模型：{manifest['model']}。
- Session：`{resident['agent_session_id']}`。
- 原始输入、来源和实验参数见下列附件；未记录的条件视为未知。

## 4. 计算结果与原始测量

结构化证据门禁：{gates["passed"]}；逐条复算结果见 evidence-gates.json。检查范围是结构化声明、图片解码和来源哈希；正文语义与科学正确性仍须独立审核。

以下仅转录执行器已保存的检查与调用统计，不将模型叙述当作实测。

```json
{json.dumps(checks,ensure_ascii=False,indent=2) if checks else '{}'}
```

{figures_markdown}

未出现在原始结果中的性能、准确率、业务收益和生产验证均为未测。无有效数值序列时不生成示意性结果图。

## 5. 对照、误差与讨论

有基线或独立复跑时，以以上原始字段为准；否则本次未完成对照实验，不能推断性能提升。模型调用耗时不是业务算法性能。固定回归用例通过率不是现场泛化精度；设计产物不等于功能实现。误差定义、样本量或不确定性缺失时，定量达标结论暂不成立。

## 6. 局限与结论

本次交付仅覆盖所列 scope。验收状态以平台裁定为准，报告生成和附件校验不代替独立验收。分析中的业务假设、来源限制、数据依赖与下一步保留于第2节及 result.json；未验证项须在后续任务中补齐。

## 7. 证据目录与复现材料

| 附件 | 字节数 | SHA256 |
| --- | ---: | --- |
{rows}

完整版本、时间和证据清单见 reproducibility.json。可用 `sha256sum` 核对下载附件；复跑实验须使用原始结果中记录的命令、输入与环境。此清单不包含报告自身，避免循环哈希。
'''
    (folder/'report.md').write_text(text,encoding='utf-8')
    return manifest
