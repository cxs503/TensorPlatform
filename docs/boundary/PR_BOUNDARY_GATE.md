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