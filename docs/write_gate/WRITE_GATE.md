# 工作树写入边界门（p158w1-n07）：白名单 × 跨仓 × 凭证 三合一预提交拦截

> 平台共性机制，**零领域语义**。机器判据全部由 `tools/write_gate/acceptance.py` 跑出，
> 产物 `artifacts/write_gate/acceptance.json`（本文件所有数字由 `tools/write_gate/gen_doc.py`
> 从该产物注入，无手抄）。策略 sha256 `f380a2d3b3bbef75…`。

## 1. 案例介绍

**背景。** 平台采用 12 仓租户制：一个智能体在某个仓的工作树里工作，任务单会声明可写路径
白名单（`EDIT_WHITELIST`）。写入越界有三类真实事故面：

1. **白名单越界**：改动落到任务范围外的平台代码（等同未经授权的平台变更）；
2. **跨仓污染**：路径回溯/绝对路径/嵌套仓/符号链接把写入带进**别的仓**或本仓 `.git`
   （`git` 元数据被改 → 全局行为被劫持，且事后无审计）；
3. **凭证泄漏**：提交内容把密钥/口令写进历史（不可逆，必须前移到提交前拦）。

**对象。** 一次「写入事件」＝(`path`, `kind`, `content?`, `repo?`)。三道判据作用在同一
事件上，任一判红即整体 `FAIL`。

**参数表（策略字段，全部外置 `data/write_gate/policy.json`）。**

| 组 | 键 | 含义 | 现值 |
|---|---|---|---|
| `whitelist` | `allow_globs` / `deny_globs` / `read_only_globs` | 白名单 / 禁区 / 只读区（glob，`**` 跨段） | 默认 `["**"]`，由任务白名单覆盖 |
| `whitelist` | `case_sensitive` / `treat_deny_as` | 大小写敏感 / 禁区归类 | `true` / `cross_repo` |
| `cross_repo` | `forbid_absolute` `forbid_escaped_root` `forbid_git_internals` `forbid_nested_repo_markers` `check_symlinks` `require_repo_match` | 六项边界开关 | 全 `true` |
| `cross_repo` | `foreign_root_globs` / `repo_boundary_markers` | 外部仓根模式 / 仓边界标记 | `../**` `/ **` `~/**` / `[".git"]` |
| `secrets` | `rules[]`（id/pattern/allow/entropy） | 凭证规则（正则 + 行级豁免 + 熵闸） | 6 条规则 |
| `secrets.entropy` | `min_length` `min_entropy_bits_per_char` `min_digit_ratio` | 熵闸三参数 | 40 / 4.2 / 0.1 |
| `output` | `max_excerpt_chars` `redact_keep_prefix` | 脱敏粒度 | 60 / 4 |

## 2. 怎么做的

**工具链**：`tools/write_gate/`（`pathnorm` 规范化内核 → `whitelist` / `crossrepo` / `secrets`
三判据 → `gate` 编排 → `cli` 入口），`data/write_gate/*.json`（策略/预注册场景/正常写入语料），
`tests/test_write_gate.py`（50 条用例），`scripts/hooks/pre-commit`（钩子包装）。

**关键设计（三个决定成败的选择）**

1. **先规范化再判定，判据只有一套。** 十种绕过写法（前缀同形、`..` 回溯、点段、反斜杠、
   全角、百分号单/双层编码、NUL 截断、重复分隔符、大小写变体）不是十套规则，而是同一套
   判据的十种输入：`canonicalize()` 按固定顺序（去 `Cf` 格式字符 → NFKC → `\`→`/` →
   百分号解码 ≤2 轮 → 段分解/`..` 折叠）折成唯一规范形。**顺序本身是安全属性**，
   由测试锁定；双轮解码专门对付 `%252e%252e`。
2. **默认拒绝 + fail-closed。** 白名单是 allow-list：未命中即拦（`allow_list_miss`），
   `allow_globs` 为空会被策略校验拒绝（防"空名单=全放行"）；仓根不可读、策略缺失、路径
   无法规范化一律记 `invalid` 并置 `decision=unknown`（拦截，不"默认安全"）。
3. **命中必须脱敏。** 判据输出里只有「前缀 + 掩码 + 长度」，判决 JSON 可直接归档进 artifact
   而不成为泄漏渠道；测试 `test_secret_values_are_redacted` 对每条规则校验原文不出现在 JSON 中。

**判决 schema**：`tp.write_gate.verdict/v1` —— `verdict`∈{PASS,FAIL}、`decision`∈{allow,block,unknown}、
`violations[]`（`category/code/path/rule_id/line/excerpt`）、`advisories[]`（豁免与跳过留痕）、
`files[]`（每文件规范化结果 + 内容 sha256）、`policy{path,sha256}`、`totals.by_category`、
`digest`（内容寻址，无墙钟 → 可复算）。示例见 `artifacts/write_gate/verdict_sample.json`：
本次判决 `FAIL`，4 条拦截，分类计数 `{'whitelist': 1, 'cross_repo': 1, 'secret': 1, 'invalid': 0}`。

**接入方式**：`python3 -m tools.write_gate.cli staged`（读 `git diff --cached` + `git show :path`，
只读 git）作为 pre-commit 钩子（`--no-verify` 可绕，故只作开发自检）；CI 动作配成 required check
才不可绕（本节点只交付钩子与命令，**启用属治理动作**，不擅自挂全仓 CI）。退出码 0/2/3。

## 3. 计算结果（机器判据）

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 白名单越界拦截 | 10/10（100%） | 10/10 （100.00%） | PASS |
| 跨仓污染防护 | 5/5（100%） | 5/5 （100.00%），扩展场景 2/2 | PASS |
| 凭证泄漏捕获 | 3/3（100%） | 3/3 （100.00%），扩展规则 3/3 | PASS |
| 误报率 | ≤1% | 0/161 = 0.00% | PASS |
| 确定性/幂等 | 三次重跑逐字节一致 | 一致（digest `5031dddb4d673750…`） | PASS |
| fail-closed | 判不了必须拦 | 3/3 探针闭合 | PASS |
| 零领域语义 | 预注册词表零命中 | 命中 0 | PASS |
| pytest 回归 | 全绿 | `50 passed, 1 warning in 1.23s` | PASS |
| 性能 | 1000 变更 < 30s | 0.2145s（214.5 µs/变更） | PASS |

**白名单越界 10 种写法逐条明细**（全部拦截）：

| 场景 | 注入写法 | 判定 | 命中代码 | 分类 |
|---|---|---|---|---|
| `P01_prefix_sibling` | `tools/write_gate_evil/mod.py` | 拦截 | allow_list_miss | whitelist |
| `P02_parent_traversal` | `tools/write_gate/../../backend/api/leak.py` | 拦截 | allow_list_miss | whitelist |
| `P03_dot_segments` | `./tools/./../backend/leak.py` | 拦截 | allow_list_miss | whitelist |
| `P04_backslash` | `tools\write_gate\..\..\backend\leak.py` | 拦截 | allow_list_miss | whitelist |
| `P05_unicode_fullwidth` | `tools／write_gate／．．／backend／leak.py` | 拦截 | allow_list_miss | whitelist |
| `P06_percent_encoded` | `tools/write_gate/%2e%2e/%2e%2e/backend/leak.py` | 拦截 | allow_list_miss | whitelist |
| `P07_double_encoded` | `tools/write_gate/%252e%252e/backend/leak.py` | 拦截 | allow_list_miss | whitelist |
| `P08_nul_truncation` | `tools/write_gate/ok.py\u0000/../../../backend/leak.py` | 拦截 | path_nul_byte | invalid |
| `P09_doubled_separator` | `tools//write_gate//..//..//backend/leak.py` | 拦截 | allow_list_miss | whitelist |
| `P10_case_variant` | `Tools/Write_Gate/../Backend/leak.py` | 拦截 | allow_list_miss | whitelist |
| `C1_root_escape` | `../../etc/hosts` | 拦截 | root_escape | cross_repo |
| `C2_absolute_foreign` | `/tmp/write_gate_accept/write_gate_accept_leh4_jk9/other_repo/backend/x.py` | 拦截 | absolute_path, foreign_root | cross_repo |
| `C3_nested_repo_marker` | `vendor/subrepo/src/x.py` | 拦截 | nested_repo_marker | cross_repo |
| `C4_git_internals` | `.git/config` | 拦截 | git_internals | cross_repo |
| `C5_symlink_escape` | `link/secret.txt` | 拦截 | symlink_escape | cross_repo |
| `C6_repo_mismatch`（扩展） | `tools/a.py` | 拦截 | repo_mismatch | cross_repo |
| `C7_foreign_root_glob`（扩展） | `../m8000/backend/x.py` | 拦截 | foreign_root, root_escape | cross_repo |

**凭证规则明细**（前三条为机器判据指定泄漏面）：

| 规则 | 必检 | 命中规则 | 脱敏校验 |
|---|---|---|---|
| `private_key_block` | 是 | private_key_block | 通过 |
| `credential_assignment` | 是 | credential_assignment | 通过 |
| `url_credentials` | 是 | url_credentials | 通过 |
| `jwt_bearer` | 扩展 | jwt_bearer | 通过 |
| `cloud_access_key_id` | 扩展 | cloud_access_key_id | 通过 |
| `high_entropy_blob` | 扩展 | high_entropy_blob | 通过 |

误报语料 `data/write_gate/fp_corpus.json`（sha256 `7cfba4a6384188d4…`，
预注册 append-only）：161 条正常写入样本（122 条路径 + 39 条内容行，
含 sha256 摘要、环境变量引用、`${}` 占位、公钥头、中文注释、长 camelCase 标识符等易误报形态）。

## 4. 结果图

- `artifacts/write_gate/fig_criteria.png`：(a) 三类对抗场景检出率；(b) 误报预算
  （0/161，预算 1%）；(c) 示例判决的分类计数。
- `artifacts/write_gate/fig_pipeline.png`：判决流水与各级实测数字（规范化 → 三判据 → 判决 JSON）。

（本节点为机制类交付，没有"场量云图"这一物理量场；图 = 判据达成面 + 判决流水，
`schema`/`digest` 可逐条复算，见 acceptance.json。）

## 5. 注意点（踩过的坑 / 局限）

1. **长标识符误报真会发生。** 首轮跑批时 `CamelCaseIdentifierNameForRuntimeSchedulerBoundaryV3`
   以 4.32 bit/char 命中高熵规则（误报）。修法不是调高阈值（会漏随机串），而是加**结构判别子**：
   随机 base64 的数字占比通常 >20%，camelCase 长标识符约 2% → `min_digit_ratio=0.1`。
   修前误报率 1/161=0.62%（仍 ≤1% 但不可接受），修后 0/161。
2. **熵闸不能替代正则。** 纯 hex 摘要（sha256/md5）香农熵≈3.7 bit/char，天然低于 4.2 阈值，
   再加行级 `sha256|digest|checksum` 豁免形成双保险；这不是"调参到绿"，是两套独立判据。
3. **staged 模式扫整 blob，不是只看 diff 增行。** 更严（能拦住"历史遗留密钥被再次提交"），
   代价是可能对旧内容报警；`diff` 增行模式作为后续扩展留档，现状在文档里显式声明。
4. **符号链接/嵌套仓判据依赖真实文件系统**：纯路径模式（`paths`/`json`）若不传 `--root`
   则只做路径判据（`symlink_escape`、`nested_repo_marker` 不生效）——这是覆盖边界，不是漏洞。
5. **`.git` 只读是纪律而非能力限制**：门只读 git（`diff --cached` / `show :path`），
   不写索引、不写工作树、不改 `.git` 配置（测试断言索引前后一致）。
6. **钩子可被 `--no-verify` 绕过**，且本仓纪律禁止改 `.git` 配置，故钩子安装与 CI 启用
   都是治理动作；本节点只保证"调用即判定"。
7. **策略可调 ⇒ 可被治理层放宽**：白名单/规则集是配置，放宽会即时改变判决——因此判决里带
   `policy.sha256`，审计链能定位"是哪版策略放行的"。

## 6. 误差判定

本节点为**机制类**交付，没有连续物理量，关键量是"检出率/误报率/确定性"这类可数判据，基准就是
判据本身（100% 拦截、≤1% 误报）：

| 关键量 | 实测 | 基准 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| 白名单越界检出率 | 100.00%（10/10） | 100% | 0.00% | **是（0%）** |
| 跨仓污染防护率 | 100.00%（5/5） | 100% | 0.00% | **是（0%）** |
| 凭证捕获率 | 100.00%（3/3） | 100% | 0.00% | **是（0%）** |
| 误报率 | 0.00%（0/161） | ≤1% | −1.00%（低于预算） | **是（0% < 3%）** |
| 判决确定性 | 3/3 次逐字节一致，digest `5031dddb4d673750…` | 100% 一致 | 0 | **是** |

**总判决：PASS**（全部判据 通过）。
**声明**：上述"基准"是机器判据而非实验/文献参考值；本节点不含物理仿真量，故不存在与实验对标的
百分比误差；未做统计推断（样本量 161 的误报率 95% 置信上界约 1.9%，
仍高于 1% 预算，故误报结论只对预注册语料成立，扩语料可能暴露新误报——诚实的覆盖局限）。

## 7. 复现

```bash
python3 -m pytest tests/test_write_gate.py -q              # 50 passed
python3 tools/write_gate/acceptance.py --out artifacts/write_gate
python3 tools/write_gate/gen_figure.py
python3 tools/write_gate/gen_doc.py
# 本地自检（白名单 + 三判据）
python3 -m tools.write_gate.cli paths --root . --edit-whitelist "tools/write_gate/**" tools/write_gate/gate.py
```
