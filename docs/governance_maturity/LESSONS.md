# Governance-suite lessons (mutation / gate-power / write-back)

**任务** p315w1-n10（mission 10864）　**仓库** TensorPlatform　**分支** agent/m10864
**作者** 建设公民智能体（m10864）　**日期** 2026-11-10　**复核状态** 待独立席位复判
**支撑产物** `tools/governance_maturity/`、`artifacts/governance_maturity/`、
`docs/P315W1-N10_governance_suite_maturity.md`

本文件按 knowledge-distillation 技能要求，把本节点踩到的坑蒸馏成可复用规则。每条给
出适用边界、证据、机制、做法、反例与回归护栏。**只增不改**：如遇反例，追加一节并注明
supersedes，不删除历史。

---

## L1　变异沙箱：绝不能把被测文件硬链接进沙箱

**规则**：变异测试的沙箱必须对**会被写入的文件**做真实复制（copy-on-write 语义），
不能硬链接。变异体重写文件时，硬链接会直接改写原始仓库文件。

**适用边界**：任何“复制工作树再原地改文件再跑测试”的 harness；只读挂载不受影响。

**证据**：本节点首轮 24 个分片全部失败或被污染。平台目标
`tools/metric_gate/expr_registry.py` 的 sha256 从 `44e31456…` 变成 `9140221a…`；
外部快照 `geometry.py` 从 `fcb89081…` 变成 `1a5323b3…`（`git status` 显示该文件被改，
`git diff --stat` 为 48 insertions / 104 deletions）。原因是 `os.link` 建出的沙箱文件与
源文件同 inode，`write_text(mutated)` 写穿到源文件；并发分片互相覆盖，变异体与测试结果
错配。恢复方式：`git checkout -- <path>` + 从上游仓重拷快照。

**机制**：`shutil.copy2`/`copytree(copy_function=shutil.copy2)` 建立独立 inode；
沙箱内 `target_file.write_text(mutated)` 只改副本。

**做法**：
1. 沙箱准备一律 copy2（先删掉任何 `os.link` 优化）。
2. 分片进程启动时把被测模块 sha256 写进账本；运行前再算一次，不一致直接 fail-closed。
3. 变异体清单（catalogue）与模块 revision 绑定；清单里的 `module_sha256` 与当前文件不符
   时抛 `ValueError`，禁止混用不同 revision 的清单。
4. 跑完把源文件按内存中的 pristine 文本复原，但仍以 copy 为前提（复原只是兜底）。

**反例 / 局限**：硬链接在“只读 + 不写”的场景（例如读取大语料）仍是合理优化；本规则只
约束“会被写入”的路径。若未来引入 overlay/bind-mount 沙箱，需重新验证写隔离。

**回归护栏**：`tools/governance_maturity/tests/test_mutation_scan.py::
test_sandbox_copies_do_not_alias_the_source` —— 断言沙箱文件 inode 与源不同，且写沙箱
不影响源文件。该用例在修复前必红。

---

## L2　外部仓是共享可写资源：测量必须钉快照

**规则**：跨仓变异/审计的输入必须按内容寻址固定（snapshot + sha256 清单），不能假设
“工作树外的仓库在测量期间不变”。

**适用边界**：任何读取其它仓（或其它 agent 工作树）文件的测量；单仓单进程写场景不必要。

**证据**：TensorBEM 检出位于 `/root/tensor-ecosystem/TensorBEM`，由多个任务并发使用。
首轮分片在 `run_shard` 处抛
`ValueError: mutant file was enumerated from a different module revision:
sha256:fcb89081… != sha256:13676d0d…`（fail-closed 生效，结果未被污染）；随后又因 L1 的
硬链接问题导致快照自身被改。

**机制**：把被测源文件复制到 `artifacts/governance_maturity/targets/<repo>/`，生成
`MANIFEST.json`（源路径、上游 `git rev-parse HEAD`、每文件 sha256、字节数、许可证文件）。
变异目标 JSON 的 `repo_root` 指向快照；账本记录同一 sha256。

**做法**：先钉快照再枚举；清单与账本 sha256 交叉校验；报告里写明上游 head 与许可证。

**反例 / 局限**：快照是“测量时刻”的证据，不是持续同步；上游修复后需重新钉快照并重跑，
不能把旧数字搬到新 revision 上。

**回归护栏**：`run_shard` 的 revision 校验（`ValueError`）+ 账本字段
`module_sha256`/`catalogue.module_sha256`；汇总脚本 `run_maturity.aggregate_mutation`
保留两者以便对账。

---

## L3　超时不是杀死；等价变异体必须存活

**规则**：变异计量里，(a) 测试命令超时单列，不计入 kill；(b) 语义等价变异（插入
`pass`、死分支）是负控，必须存活，其存活率就是仪器 precision。

**适用边界**：所有变异测试。若把超时当杀死，kill rate 会被未收敛/死循环型变异体抬高；
若没有等价负控，一个“杀死一切”的 harness 会拿满分。

**证据**：`artifacts/governance_maturity/mutation_ledger.json` 中 `timeout` 与
`killed` 分列；`overall.control_precision` 与 per-target precision 单独给出（本节点
见 summary.json 的 `d_equivalent_controls`）。

**做法**：状态机 `killed / survived / timeout / invalid / error`；分母只用
`killed + survived`；负控算子与语义算子分开枚举，控制数量上限采样（默认 48/目标）。

**反例 / 局限**：等价变异体的“等价”是相对测试语义而言；若测试本身依赖实现细节（如
行号、内部变量名），负控可能被杀，此时应先修测试而不是调低阈值。

**回归护栏**：`test_mutation_scan.py::test_render_insert_pass_is_behaviour_preserving`、
`test_render_dead_branch_is_behaviour_preserving`、`test_aggregate_counts_timeouts_separately`。

---

## L4　gate power 必须报“可探测边界”，不能只报 1.0

**规则**：判据 gate 的 gate_power 只对“工件自洽性”类腐败有意义；对“自洽但数值错误”
的观测（stealth 类）必须在报告中单列检出率（本节点为 0），且不计入 gate_power。

**适用边界**：任何以“重算/出处/单位/新鲜度”为基础的验收门。若上游没有独立复算来源，
gate 无法识别一个自洽的错误数值。

**证据**：`gate_power_ledger.json`：9 类腐败注入 427 次全部检出（gate_power=1.0，
干净语料误拒 0）；`stealth` 类注入 59 次检出 0，单列披露。另有 49/59 条判据被源侧标记
`decidable: false`（无法独立重推导），本门只能约束其一致性并列为数据质量待办。

**做法**：报告分三块——(1) 可探测类 gate_power；(2) 干净语料误拒率（负控）；(3) stealth
类检出率（边界披露）。恒真判据用“非负观测 + 对抗探针”检测并单列；检测器用合成恒真
判据做负控。

**反例 / 局限**：把 stealth 类算进 gate_power 会得到虚高或恒 0 的两极数字；把 gate_power
当成“判据正确率”是误读——它度量的是**门对腐化的敏感度**。

**回归护栏**：`test_gate_power.py::test_stealth_perturbation_is_a_documented_blind_spot`、
`test_always_true_detector_catches_degenerate_predicate`、
`test_gate_power_perfect_on_clean_corpus_with_controls`。

---

## L5　派生恒等式必须指向“另一个字段”，否则与重算同义

**规则**：写回门的 self-consistency 恒等式要断言“声明字段 = 表达式(输入)”，且目标字段
不能就是被重算的 value；否则恒等式与表达式重算恒等，永远抓不到不一致。

**适用边界**：KPI 回写、报表汇总、任何“派生量 + 声明量”双写。

**证据**：首版 `data/governance_maturity/writeback_registry.json` 的 identity 目标写成
`value`，与 metric 表达式重合，identity_violation 注入 5 次检出 0；改为
`declared_share` / `declared_total` 后检出 5/5。

**做法**：恒等式 `{name, expression, fields, target}`，`target` 指向输入命名空间里的
声明字段（或 `value`，但仅当表达式与 value 的生成式不同）；注入时让 value 与表达式一致、
只改声明字段。

**反例 / 局限**：字段太少时可能找不到独立目标字段；此时宁可把恒等式删掉，也不要保留
一个与重算同义的假检查。

**回归护栏**：`test_writeback_gate.py::test_identity_target_field_checked`、
`run_maturity.py` 的 identity_violation 注入类（检出率写进 summary 的
`e_writeback_gate`）。

---

## L6　中文字段 / 口令类环境坑（流程性，非算法）

- 平台 API 报告接口需要先 `POST /auth/login` 取 token；`/tmp/report.json` 用
  `mission_id: 10864`，图走 `figures:[{name,caption,png_base64}]`。
- 长任务（>2 分钟）用 `setsid nohup ... &` 起后台；前台 shell 被收割时进程组会连带被杀
  （本节点两次踩到，分片被迫重跑）。
- 工作树很脏（98 MB，含前端），不要整仓打包上集群；只 stage 需要的子目录。

---

## 复核与更新

- 本文件所有规则均在本节点实测中被触发/验证；证据路径可对账。
- 若后续节点发现反例：追加 “Lx-supersedes: …” 小节，写清条件与证据，不删除原文。
- 最近验证日期：2026-11-10（mission 10864 运行）。

---

## L7　基础设施失败 ≠ 杀死：把环境故障单列成 `infra_error`

**规则**：变异/基准测试里，测试命令非零退出不一定意味着“被测代码的变异被检测到”。磁盘满、
无法创建临时目录、内存不足等环境故障必须单列为 `infra_error`，从 kill rate 的分子与分母
同时剔除，并计数披露。

**适用边界**：任何在共享机器/容器里跑的子进程测量；单元测试断言本身不需要。

**证据**：本节点第二轮分片在磁盘 100% 满时运行，`pytest` 报
`FileNotFoundError: No usable temporary directory found`，89 个变异体被误记为 killed；
把 `No space left on device` / `No usable temporary directory` / `OSError: [Errno 28]` /
`MemoryError` 等签名识别为环境故障后，29 个已落盘结果被重分类为 `infra_error`，
合并账本 infra_error=14、负控 precision 从 0.75 回到 0.992（其余 1 个真实控制失败是
测试耦合源码行号）。

**机制**：`mutation_scan.classify_result(status, output)`：仅当 `status == "killed"` 且
输出含环境故障签名时降级为 `infra_error`；聚合时分母只用 `killed + survived`。

**反例 / 局限**：若被测代码本身可能抛 `OSError`/`MemoryError`（罕见的错误路径测试），
标记法会误判为环境故障；此时应改为“白名单 stderr 精确匹配 + 复跑一次确认”的两步判定。

**回归护栏**：`test_mutation_scan.py` 的聚合用例覆盖 `timeout`/`infra_error` 单列；
`mutation_ledger.json` 暴露 `infra_error` 与 `control_judged` 字段供报告对账。
