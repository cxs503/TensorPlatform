# boundary — 平台边界门工具集

把「平台 vs 领域」的职责边界（治理 SPEC **§3.3**）从口头约定变成机器判决。

| 文件 | 作用 |
|---|---|
| `pr_gate_scan.py` | **PR 边界门**：扫 PR 新增行里的域词，命中平台代码路径即判越界；输出 JSON 判决（文件+行号） |
| `calibrate.py` | 校准跑批：复判历史 PR（负例 MUST 红 / 正例 MUST 绿）+ 合成机制用例，落 `artifacts/` |
| `gen_calibration_figure.py` | 由校准 JSON 出图（只读工件，不重算判决） |
| `adversarial_corpus.py` | 对抗语料生成器：6 类变异算子（同义/符号化/跨语言/容器内嵌/注释URL/长程引用），确定性可复现 |
| `adversarial_measure.py` | 对抗度量：召回率/误报率/混淆矩阵/漏检清单/机理归因/留出语料/词表留出探针 |
| `structural_gate.py` | 边界门 v1.1：基础门禁 + 结构性判据（遮蔽还原 R1 / 记号 R2+R2b / 引用解析 R3） |
| `gen_adversarial_figure.py` / `gen_adversarial_report.py` | 由 metrics 出图 / 出报告正文（数字零手抄） |

**零领域语义**：词条/弱词/豁免复合名/路径域全部在
`data/boundary/pr_boundary_lexicon.json`，代码不感知具体词；工具与测试源码里出现阻断词条
即被 `tests/test_pr_gate_scan.py` 判红。

```bash
# 复判已并入 PR
python3 tools/boundary/pr_gate_scan.py git --base <base> --head <head> --json verdict.json
# CI 里由 diff 判
git diff -U0 "$BASE" "$HEAD" | python3 tools/boundary/pr_gate_scan.py diff - --format text
# 本地按文件自检
python3 tools/boundary/pr_gate_scan.py paths tools/foo.py tests/test_bar.py --format text
# 校准 + 出图
python3 tools/boundary/calibrate.py --out artifacts/boundary/pr_gate_calibration.json
python3 tools/boundary/gen_calibration_figure.py
pytest tests/test_pr_gate_scan.py -q

# 对抗语料度量（检出保证）
python3 tools/boundary/adversarial_corpus.py --out data/boundary/adversarial_corpus.json
python3 tools/boundary/adversarial_measure.py --out artifacts/boundary/adversarial_metrics.json
git diff -U0 "$BASE" "$HEAD" | python3 tools/boundary/structural_gate.py diff -   # 加固后门禁
pytest tests/test_boundary_adversarial.py -q
```

退出码：`0`=PASS、`2`=FAIL（越界）、`3`=内部错误。

完整判决口径、CI/pre-merge 钩子取舍、校准数字与已知局限见
[`docs/boundary/PR_BOUNDARY_GATE.md`](../../docs/boundary/PR_BOUNDARY_GATE.md)。