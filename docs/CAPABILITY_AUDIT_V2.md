# 能力台账自动审计 v2 —— 可执行证据面（WITHHELD → REAL 的机器闭环）

> 规则集 `cav2-rules-v1`；工件：`artifacts/capability_audit_v2/`；代码：`tools/capability_audit_v2/`；
> 配置（域内容以数据注入）：`config/capability_audit_v2/spec.json`、`config/capability_audit_v2/upgrade_paths.json`。
> 复跑：`python3 -m tools.capability_audit_v2.cli audit --root . --spec config/capability_audit_v2/spec.json
> --labels artifacts/capability_ledger/capability_ledger_resolved.json --paths config/capability_audit_v2/upgrade_paths.json
> --out artifacts/capability_audit_v2 --repro-runs 2`

## 1. 为什么要 v2（v1 的天花板在哪）

v1（`tools/capability_audit_gate`，静态管线）只看 AST 结构信号 + 落盘基准工件，**不执行任何代码**。
它把 21 项能力判成 20 WITHHELD + 1 REAL，与人工复核一致率 **95.24%**（20/21）。
唯一分歧项是一个"测试全绿但内核是手拟合"的能力：静态管线只能拒绝下判决（WITHHELD），
而人工复核依据在线探针判 **MOCK**。这说明 v1 的缺口不是规则写错，而是**证据面缺一层**：
"测试通过"本身不是能力证据，除非测试真的断言了能证伪该能力的量。

v2 补的就是这一层：**执行**声明的测试与不变量探针，把"跑过 / 跑绿 / 数值关系成立"变成结构化证据。

## 2. 方法与证据面

| 证据面 | 谁执行 | 产物 | 能证明什么 |
|---|---|---|---|
| 静态结构（v1） | AST 扫描 | 空壳占比、造假标记、数值断言数 | 结构上像不像真实现 |
| 基准工件（v1） | 读 JSON 工件 | worst_error_pct vs 容差 | 与参考值差多少 |
| **pytest 选择器（v2）** | 子进程 `pytest --junitxml` | 用例数/通过/失败/环境错误、数值断言数、用例名 sha256 | 声明绑定的测试是否真的跑通 |
| **不变量探针（v2）** | 进程内调用声明目标 | 恒等式残差、敏感度展布、决定性 | 实现是否满足**可证伪的数值关系** |
| **对照探针（v2）** | 同上，作用于"已知合格"的候选内核 | 同上 | 探针本身是否会把合格内核判错（方法学自检） |

三类检查（通用语义，表达式与阈值全部来自配置）：

* `identity`：`|expr| ≤ tolerance`（例：效率恒等式残差）；
* `sensitivity_required` / `sensitivity_forbidden`：**控制其余参数固定**，测输出对指定输入的相对展布；
  `required` 取各组最小值（最弱敏感度），`forbidden` 取最大值——这样"另一个变量在变"不会把展布撑大，
  让"零敏感"的实现蒙混过关（find#1）；
* `determinism`：同参数二次调用逐位一致。

## 3. 判定级联（`cav2-rules-v1`，先于观测声明）

| 序 | 条件 | 结果 |
|---|---|---|
| 1 | 静态：绑定目标缺失 / 实现缺失 | NOT_SUPPORTED |
| 2 | 静态：空壳占比超阈 | STUB |
| 3 | 探针证伪数 ≥ 1 | **MOCK** / `EXEC_INVARIANT_VIOLATED` |
| 4 | 静态：≥2 独立造假信号族 | MOCK |
| 5 | 基准（解析/恒等类）越界 | MOCK（沿用 v1） |
| 6 | pytest 断言失败（非环境错误） | WITHHELD / `EXEC_TEST_FAILED` |
| 7 | pytest 环境错误 / 采集失败 / 超时 | WITHHELD / `EXEC_ENV_ERROR` |
| 8 | 静态 WITHHELD（基准越界/未绑定/无数值断言） | WITHHELD（沿用静态理由码） |
| 9 | 静态 REAL 且（执行未绑定 或 通过且数值断言达标） | REAL |
| 10 | 其余 | WITHHELD / `INSUFFICIENT` |

两条诚实性纪律：

* **探针只做否定**：探针全过不足以判 REAL（没有对标基准就不是 REAL），只记
  `PROBE_ALL_PASS_NO_REFERENCE` 警告；
* **环境错误不指控能力**：宿主缺 provider / 缺依赖导致的失败判 `EXEC_ENV_ERROR`，
  绝不当成 MOCK（find#2）。

## 4. 结果（数字全部机器写入 `artifacts/capability_audit_v2/`）

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| C1 覆盖率 | 100% | **21/21 = 100%** | PASS |
| C2 与人工复核一致率 | ≥95% | **100%**（21/21）；静态 v1 **95.24%**；多数类基线 **90.48%** | PASS |
| C3 MOCK/STUB 升级路线 | 100% 有算法引用+步骤 | **1/1 项**，2 条算法引用（含发表回归与试验规程），4 步（每步含可执行 verification + acceptance） | PASS |
| C4 零领域语义 | 0 命中 | **0**（扫 26 个新代码文件，denylist 由 `config/domain_word_denylist.json` 注入） | PASS |
| C5 两次运行 sha256 | 相等 | `ec84d6430505e4c7…` 两次一致；**另一进程独立重跑**同样得到该摘要（`cross_process_repro.json`） | PASS |

**v2 相对 v1 的增量就是一条**：分歧项从 WITHHELD 变成 MOCK（与人工复核一致），
一致率 95.24% → 100%（+4.76pp；相对多数类基线 +9.52pp）。

关键执行证据（同一张台账里，程序化写入）：

| 能力 | pytest | 探针 | 判级 |
|---|---|---|---|
| `calc.stability` | 44 passed（数值断言 44 处所在文件） | 未声明 | REAL（静态基准 3% 容差内 + 执行通过） |
| `calc.resistance` | **ENV_ERROR**（provider 未注册，`ProviderNotRegistered`） | 未声明 | WITHHELD（不指控能力） |
| `propeller.open_water` | **39 passed（全绿）** | `I_pitch_response = 0.0`、`I_blade_response = 0.0`（阈值 1e-3）→ 2 条证伪 | **MOCK** |

对照探针（作用于同仓的候选内核）：`I_pitch_response = 7.83e-3`、`I_blade_response = 5.31e-3`、
恒等式残差 `3e-10`，全部通过——**同一套检查既能证伪手拟合内核，也不会误杀合格内核**（判别裕度 7.8× / 5.3×）。

"测试不判别"信号：台账 `summary.non_discriminating_tests = 1` —— 该能力的测试套件全绿、
但探针在关键数值关系上证伪了它。这是可自动检出的"测试存在但不判别"模式，比"有没有测试"更有信息量。

## 5. 升级路线（MOCK → REAL，机器校验）

`config/capability_audit_v2/upgrade_paths.json` 里每条路径必须满足：`upgrade_class` ∈
{algorithm_replacement, parameterization, solver_integration}、≥1 条 `algorithm_refs`（citation + locator）、
≥3 步且每步 `verification` 必须出现**仓内真实存在的路径或选择器**（否则视为"写了一句话"）。
缺 MOCK/STUB 路径 ⇒ CLI 退出码 2（禁止静默遗漏）。

本波 MOCK 项（1 项）的路线：以已入库的发表回归替换服务端手拟合内核 → 重跑不变量探针（须零证伪）→
补 ≥8 点敞水基准工件并绑定（worst ≤3%）→ 冻结基线开 CI 回归门。

WITHHELD 的 19 项另给**证据补齐动作**（模板 + 配置覆盖），逐条落在 `roadmap.json.evidence_completion`。

## 6. 踩过的坑（findings）

* **find#1 敏感度必须控制其余变量**：第一版按"全部点"算展布，`j` 的变化把展布撑到 133%，
  连"只随 j 变化"的手拟合内核都×过×了检查（假阴性）。改为按"其余参数固定"分组、取组内展布后，
  手拟合内核精确落在 `0.0`，候选内核 0.78%——判别力来自分组，不来自阈值。
* **find#2 环境错误 ≠ 能力造假**：`calc.resistance` 的 44 个绑定用例在宿主上因 provider 未注册而失败
  （44 个 error）。若按"测试失败 ⇒ 有问题"判 MOCK，就把宿主接线问题洗成了领域结论。改为按
  `execution.env_markers` 分类为 `EXEC_ENV_ERROR`，等级保持 WITHHELD 并留痕。
* **find#3 测试全绿 ≠ 有证据**：被复核为 MOCK 的能力有 39 个通过的用例。它的测试是在"存在性/单调性"
  层面断言，没有触及能证伪该实现的关键数值关系。结论：**测试通过只算"执行证据"，不算"数值验证"**，
  REAL 仍要求对标基准 + 数值断言阈值。
* **find#4 探针阈值是事后定的**：`1e-3` 是在观察到"手拟合实现 = 0.0、候选内核 0.53%–0.78%"之后
  选的中位数；这不是预注册阈值，是本节点的诚实缺口。缓解：台账同时给出**原始数值**与对照数值，
  任何复核者可以换阈值重判，结论（0 vs 0.5%+）不依赖 1e-3 这个具体取值。
* **find#5 子进程测试的启动成本**：每能力一个 pytest 子进程，仓库根 conftest 会拖慢到 ~2 s/次；
  在配置里给 `confcutdir` / `minimal_plugins` 才能把合成夹具压到 ~0.2 s。真实能力的执行保持默认
  （不关插件），以免为了跑得快而改变被测环境。

## 7. 局限（honest）

1. **可执行证据只覆盖 3/21**：只有 3 项能力声明了 pytest 选择器、1 项声明了探针；其余 18 项仍是
   静态判定（WITHHELD）。覆盖率 100% 指"每项都有判定与理由码"，不是"每项都有执行证据"。
2. **一致率的分母问题**：19/21 的人工标签就是 WITHHELD，多数类基线已 90.48%，所以 100% 的判别力
   主要来自 2 个非多数类项（REAL、MOCK）与 1 个可执行项。报告必须把基线与增益一起读。
3. **探针不是一个通用求解器**：它只核验声明的不变量；表达式的正确性、参数域的选择仍是人/域负责，
   配置写错会得到"正确地错的结论"（对照探针是对冲，不是证明）。
4. **可复现性是同机同解释器的**：台账摘要排除时间戳/耗时/绝对路径，同进程两次运行与**另一进程**
   独立重跑都逐位一致（`cross_process_repro.json`）；跨机器/跨 Python 版本的浮点差异未验证
   （探针数值 round 到 10 位，理论上足够但未做跨机实测）。
5. **升级路线是"可执行"，不是"已完成"**：本节点只交付路径与验收命令，算法替换本身是后续单。