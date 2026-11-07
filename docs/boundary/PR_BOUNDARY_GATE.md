# PR 边界门（IN17-B1）：SPEC §3 的机器落地

> 上游依据：`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md` **§3.3 边界判断规则**
> 第 2 条——「它是否依赖某个领域的理论、术语、专业流程或判据？若是，归领域系统」。
> 本节点把这条规则从「review 靠人看」变成**机器可判**：PR 加行里出现领域词条落在
> 平台代码路径上 → 判越界。
>
> 词条清单**不在代码里**：全部外置到 `data/boundary/pr_boundary_lexicon.json`
> （`--lexicon` 可换），工具本身零领域语义。

## 1. 判决口径（一句话版）

| 维度 | 规则 | 落点 |
|---|---|---|
| 扫什么 | 只扫 PR **新增行**（`+`）；删掉域词是治理成果，不判红 | `parse_added_lines()` |
| 哪里算越界 | `backend/ src/ api/ tools/ tests/ scripts/ optimizer/ schemas/ contracts/ deploy/ ops/ frontend*/`，以及仓根散件代码 | `scopes.blocked_prefixes` |
| 哪里放行 | 观察数据白名单：`docs/reports/ docs/papers/ docs/audits/ artifacts/ runs/` 且扩展名 ∈ `.md/.json/.csv/.tsv/.txt/.log/.yaml/.yml/.png/.svg/.pdf/.html` | `scopes.observation_prefixes` + `observation_extensions` |
| **白名单不豁免代码** | 白名单目录内的 `*.py/*.sh/*.js/...` 仍按代码扫描 → **伪造域代码 MUST 红** | `classify()` → `blocked_whitelist_code` |
| 词边界 | `word`：前后不得紧跟字母/数字（`-`/`_` 视为边界）→ `xxx_part` 命中、`xxxletters` 不命中；`substring`：中文等无词边界语言按子串；`dash_prefix`：词条以 `-` 结尾表"连字符复合名前缀" | `Term.patterns()` |
| 专有名词豁免 | 命中所在的**整复合名**在 `exempt_compounds` 里 → 放行但记入 `exempted[]`（留痕可审计）；`<stem>-<真领域名词>` 不豁免 | `scan_line()` |
| 弱域词 | 多义/通用术语（advisory）只提示不阻断，供 review 参考 | `advisory_terms` |
| 判决输出 | JSON：`verdict` ∈ {PASS,FAIL}、`violations[]`（file/line/column/term_id/matched/snippet）、`advisories[]`、`exempted[]`、`files[]`（每文件分类）、`lexicon{path,sha256}`（审计链） | `ScanResult.to_json()`，schema `tp.boundary.pr_gate_scan/v1` |
| 退出码 | 0=PASS，2=FAIL，3=内部错误（词表缺失/参数错） | `main()` |

匹配的边界语义只在**工具代码**里；「哪些词算域词」只在 **data 配置**里。二者分离是本节点
对「零领域语义」纪律的技术兑现方式，并由 `tests/test_pr_gate_scan.py::test_tool_and_test_sources_carry_no_real_term`
机器校验（工具与测试源码里出现任何阻断词条即红）。

## 2. 用法

```bash
# 复判一个已并入 PR（base=PR 的第一父提交，head=合并提交）
python3 tools/boundary/pr_gate_scan.py git --base <base_sha> --head <head_sha> --json verdict.json

# CI 内：由统一 diff 判（管道喂 stdin）
git diff -U0 "$BASE_SHA" "$HEAD_SHA" | python3 tools/boundary/pr_gate_scan.py diff -

# 本地开发自检 / 钩子：按文件判
python3 tools/boundary/pr_gate_scan.py paths tools/foo/bar.py tests/test_x.py --format text

# 校准跑批（历史 PR 复判 + 合成机制用例），结果落 artifacts/
python3 tools/boundary/calibrate.py --out artifacts/boundary/pr_gate_calibration.json
python3 tools/boundary/gen_calibration_figure.py    # 校准图
```

## 3. 接入方式：CI 动作 vs pre-merge 钩子（取舍）

| | **CI 动作（选定）** | pre-merge / pre-push 本地钩子（备选，不选） |
|---|---|---|
| 强制力 | 在服务端跑，任何人 push 的 PR 都过门；配成 required status check 即不可绕 | 客户端钩子，`git commit --no-verify` / `git push --no-verify` 直接绕过 |
| 安装成本 | 仓库内一份 workflow，零人工 | 每个克隆要手工装钩子；本仓纪律**禁止改 `.git` 配置**，无法由仓库自身保证装上 |
| 反馈延迟 | push→CI 数十秒~分钟 | 秒级（最大优势） |
| 证据 | 判决 JSON 作 artifact 归档，可回溯/可审计 | 只在本机 stdout，事后无痕迹 |
| 历史依赖 | 需 `fetch-depth: 0`（或显式给 base/head sha），否则复判不了 | 本机历史一般完整 |

**决策：CI 动作作为唯一强制防线**（判决 JSON 归档 = 审计链）；本地钩子只作为开发自检的
推荐补充，不写进仓库（依赖它会被绕过，且与"不改 `.git` 配置"纪律冲突）。
**本节点只做文档化接入**：workflow 片段如下，**启用属于治理动作**（需 owner 批准后由
平台维护者落 `.github/workflows/`），避免单节点擅自把新门禁挂到全仓 CI 上造成集体阻塞。

```yaml
# .github/workflows/pr-boundary-gate.yml  （待启用）
name: pr-boundary-gate
on: pull_request
jobs:
  boundary:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}          # 需要完整历史才能 diff
      - name: PR boundary gate (SPEC §3)
        run: |
          python3 tools/boundary/pr_gate_scan.py git \
            --base "${{ github.event.pull_request.base.sha }}" \
            --head "${{ github.event.pull_request.head.sha }}" \
            --json artifacts/boundary/pr_gate_verdict.json --format text
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: pr-boundary-verdict, path: artifacts/boundary/pr_gate_verdict.json}
```

## 4. 校准（判据数字，程序化注入，零手抄）

判据：**负例 MUST 红、正例 MUST 绿、白名单路径伪造域代码 MUST 红**。语料见
`data/boundary/pr_gate_calibration_corpus.json`，结果见
`artifacts/boundary/pr_gate_calibration.json`（由 `tools/boundary/calibrate.py` 生成）。

| 用例 | 类型 | 期望 | 实测 | 阻断命中 | 弱词提示 | 豁免留痕 |
|---|---|---|---|---|---|---|
| PR #608 | 历史 PR（域测试写进 `tests/`） | FAIL | **FAIL** | 92 | 6 | 0 |
| PR #610 | 历史 PR（域算例写进 `tools/`） | FAIL | **FAIL** | 17 | 10 | 2 |
| PR #611 | 历史 PR | PASS | **PASS** | 0 | 0 | 0 |
| PR #612 | 历史 PR（含专有名词引用） | PASS | **PASS** | 0 | 0 | 2 |
| PR #613 | 历史 PR | PASS | **PASS** | 0 | 0 | 0 |
| PR #618 | 历史 PR | PASS | **PASS** | 0 | 0 | 0 |
| PR #619 | 历史 PR（仅弱词） | PASS | **PASS** | 0 | 14 | 0 |
| 合成：代码路径命中 | 机制 | FAIL | **FAIL** | 1 | 0 | 0 |
| 合成：白名单内伪造代码 | 机制（判据项） | FAIL | **FAIL** | 1 | 0 | 0 |
| 合成：白名单观察数据 | 机制 | PASS | **PASS** | 0 | 0 | 0 |
| 合成：豁免复合名 | 机制 | PASS | **PASS** | 0 | 0 | ≥1 |
| 合成：非豁免复合名 | 机制 | FAIL | **FAIL** | 1 | 0 | 0 |
| 合成：词边界反例 | 机制 | PASS | **PASS** | 0 | 0 | 0 |
| 合成：仅删除行 | 机制 | PASS | **PASS** | 0 | 0 | 0 |
| 合成：CJK 子串 | 机制 | FAIL | **FAIL** | 2 | 0 | 0 |

**15/15 用例命中期望**；`pytest tests/test_pr_gate_scan.py` 13 项全绿，其中负例 ≥3
（PR #608、PR #610、白名单伪造代码）。

指标口径：判据里的"MUST 红/绿"用 `verdict` 字段（=退出码 0/2）判定，不用"命中数>0"这种
模糊口径；校准语料里 `unavailable`（浅克隆缺历史）**不计入通过**，直接判校准未完成。

## 5. 语义陷阱与设计取舍

1. **多义词不能当阻断词**：如"阻力/电阻"、"推进"、"极坐标"在平台语境里都有合法用法；
   把多义词设为阻断会立刻误伤正例（PR #619 的能力卡字面值），所以词表分成
   `terms`（阻断）与 `advisory_terms`（提示）。判据可被正例校核，因此不能靠"宽正则兜底"。
2. **专有名词 ≠ 领域语义**：仓名/工件名（`ship-*` 专有名词形态）是对外部实体的引用。
   豁免按**整复合名精确匹配**，且命中进 `exempted[]` 留痕——避免"豁免"变成静默放行。
3. **白名单是数据白名单，不是路径白名单**：同一个 `docs/reports/` 下 `.md` 放行、`.py` 判红，
   否则"把域代码贴到报告目录"就成了绕过通道。
4. **只判加行**：只罚"新引入"，不罚"历史存量"，否则治理 PR 自己会被判红（PR #610 的
   docs 报告行属于观察数据，权限也来自白名单而非"存量豁免"）。
5. **词表本身放在 `data/`**：`self_exempt_paths` 显式列出，避免扩词表时自伤；词表 sha256
   进判决 JSON，改词表会自动让校准工件失效（见用例 13）。

## 6. 已知局限（如实记录）

1. **域外路径不判**：`docs/architecture/`、`config/`、`agent_memory/`、`agent_work/` 等既非代码域
   也非观察白名单的路径记为 `out_of_scope`，不参与判决。若要扩域，改 `data/` 的
   `blocked_prefixes` 即可（不需改代码）。
2. **语义不可判**：词法是"术语音"而非"语义理解"——用同义词改写、或把域逻辑命名为
   `compute_thing_v2` 可以躲过词表；本门是**低成本下限门**，不替代 review 与
   `tools/audit/boundary_gates.py` 的 import/常量门（G1/G3）。
3. **中文子串误伤风险**：中文词根按子串匹配，可能命中普通词汇复合；目前域词根集中在
   高特异度词上，若扩词表需回到校准语料复跑（用例 12 会强制发现漂移）。
4. **只扫文本 diff**：二进制/重命名文件不判；`git diff` 需 base/head 可达（浅克隆会 `unavailable`）。
5. **未启用为必需检查**：见 §3，启用需 owner 批准后由平台维护者落 CI；本节点不擅自挂门禁。

## 7. 复现（产物清单）

```
data/boundary/pr_boundary_lexicon.json            词表（阻断/弱词/豁免/路径域）
data/boundary/pr_gate_calibration_corpus.json     校准语料（7 历史 PR + 8 合成机制）
tools/boundary/pr_gate_scan.py                    门本体（CLI + 判定 API）
tools/boundary/calibrate.py                       校准跑批（写 artifacts）
tools/boundary/gen_calibration_figure.py          校准图
tests/test_pr_gate_scan.py                        13 项 pytest（含负例 ≥3）
artifacts/boundary/pr_gate_calibration.json       校准判决（含词表 sha256）
artifacts/boundary/pr_gate_selfcheck_paths.json   对本节点自身源码（paths 模式）自检判决
artifacts/boundary/pr_gate_selfcheck_branch.json  对本节点整条分支 diff（git 模式）自检判决
artifacts/boundary/figures/pr_gate_calibration.png 校准图
```

## 5. 检出保证（P20-N08）：对抗语料上的召回率 / 误报率实测

§4 的校准是「手写判例全绿」——它证明门禁在**已知**判例上不错，但不给检出保证。
P20-N08 用**对抗语料**把保证量化（语料作者 ≠ 门禁作者，故对作者盲；种子是三单真实清洗过的渗漏形态：

`IN17-B2`/#608、`IN17-B3`/#610、`IN17-B5`/#616）：

| 项 | 值 |
|---|---|
| 语料 | 6 类变异算子 × (26 正 + 10 负) = 216 条，种子 `20261107` |
| 语料哈希 | `8ef53c885b1a5c2920ff8edb658844c37267e6bc2c0951b6bf4a8a7782af104a` |
| 基线（本文件 `pr_gate_scan.py`）整体 | 召回 **55.1%** · 误报 **0.0%** · 精确率 100.0% |
| 加固层（`structural_gate.py`）整体 | 召回 **76.3%** · 误报 **0.0%** |
| 留出语料（换种子、不调参） | 召回 **76.3%** · 误报 **0.0%** |

逐类矩阵（负样本 60 条，两层零误报）：

| 变异类 | n | 基线召回 | 加固召回 | 基线误报 | 加固误报 |
|---|---|---|---|---|---|
| `synonym_rewrite` | 36 | 50.0% | 50.0% | 0.0% | 0.0% |
| `symbol_notation` | 36 | 50.0% | 88.5% | 0.0% | 0.0% |
| `cross_language` | 36 | 50.0% | 50.0% | 0.0% | 0.0% |
| `block_embedding` | 36 | 61.5% | 84.6% | 0.0% | 0.0% |
| `comment_url` | 36 | 69.2% | 100.0% | 0.0% | 0.0% |
| `long_range_reference` | 36 | 50.0% | 84.6% | 0.0% | 0.0% |
| **整体** | 216 | 55.1% | 76.3% | 0.0% | 0.0% |

结论与引用规则见 [`边界声明.md`](边界声明.md)：**边界门检出能力不足**（基线 55.1% < 70%），
PASS 不得被引用为「平台无域语义」。

## 6. 加固层（P20-N08 / E4）：三条结构性判据

`tools/boundary/structural_gate.py` = 本文件的基础门禁 + 以下判据（判据内容全部在
`data/boundary/structural_rules.json`，工具代码零域词）：

| 规则 | 判据 | 目标盲区 | 对召回的作用 |
|---|---|---|---|
| `R1_unmask` | NFKC/去格式字符/百分号解码/同形字折叠/数字替换 + 跨相邻行断词拼接（命中须跨拼接边界） | 遮蔽（`comment_url`） | 69.2% → 100% |
| `R2_notation` | 方形系数、L/B、Re_τ、主尺度三元组、主尺度 dict（length+beam+draft 三键） | 符号化（`symbol_notation`） | 50% → 88.5% |
| `R2b_key_cooccurrence` | ±4 行窗口内 ≥1 特征键且合计 ≥2 几何键的赋值形态 | 容器（表格/yaml/docstring） | 61.5% → 84.6% |
| `R3_reference` | 路径 + 节号锚 → 读被引用工件 → 携带阻断词条即判引用越界 | 长程引用 | 50% → 84.6% |

**加固次序纪律**：结构性判据只对基础门禁**未命中**的行生效；每条规则的收紧都用**负样本实测**决定
（例：`dimension_dict` 只要 `length`+`beam` 时画布尺寸误报 2 条，加 `draft` 三键后归零）。
整体误报上升 **0.0pp**（预算 2.0pp）。

**加固后仍 <0.6 的两类**是 `synonym_rewrite` 与 `cross_language`——词表覆盖度/语种问题，
结构判据无解；留出法探针（把同义/外文映射一半写进临时词表）显示：扩张集召回 1.00、留出集 0.00，
即「只能靠词表增长，且对未枚举措辞无保证」。

## 7. 接入（加固后命令行与基础门禁同参，可直接替换）

```yaml
      - name: PR boundary gate (SPEC §3, v1.1 = base + structural)
        run: |
          git diff -U0 "${{ github.event.pull_request.base.sha }}" "${{ github.event.pull_request.head.sha }}" \
            | python3 tools/boundary/structural_gate.py diff - \
                --json artifacts/boundary/pr_gate_verdict.json --format text
```

退出码与基础门禁一致（`0`=PASS、`2`=FAIL、`3`=内部错误），判决 JSON 兼容并新增
`structural.rules` / `structural.violations` / `totals.base_violations|structural_violations` 留痕。
启用仍属治理动作（§3）：本节点只提供可替换命令行与实测数字，不擅自改 `.github/workflows/`。

## 8. 复现（P20-N08 产物清单）

```
data/boundary/adversarial_corpus_seeds.json     种子数据（三单真实渗漏形态 + 同义/外文/数值/引用槽位池）
data/boundary/adversarial_corpus.json           216 条语料（确定性生成，哈希见 §5）
data/boundary/structural_rules.json             结构性判据数据（R1/R2/R2b/R3 的形态与取舍理由）
tools/boundary/adversarial_corpus.py            6 类变异算子（结构算子，零域词）
tools/boundary/adversarial_measure.py           召回/误报/漏检/机理/留出语料/留出法探针
tools/boundary/structural_gate.py               加固门禁（CLI 与基础门禁同参）
tools/boundary/gen_adversarial_figure.py        报告图（只读 metrics）
tools/boundary/gen_adversarial_report.py        报告正文（数字程序化注入）
tests/test_boundary_adversarial.py              14 项判据（C1-C5 + E4 + 零域词自检 + CLI）
artifacts/boundary/adversarial_metrics.json     度量明细（含逐条漏检清单）
artifacts/boundary/adversarial_recall_figure.png 报告图
docs/boundary/边界声明.md                        检出能力声明（honest 条款落点）
docs/reports/m5282_boundary_adversarial_recall.md 论文级报告
```
