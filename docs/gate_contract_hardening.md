# 验收门证据契约成熟化：gate.json 强制化 · 缺失即 WITHHELD · 注入式负控把假通过率压到 0

> 节点：IN68W2-N10 ｜ 仓：TensorPlatform ｜ 分支：agent/m9737 ｜ 零领域语义（判据/阈值/单位/工件全部经 config/artifacts 注入，代码只认结构）

## ① 案例介绍（物理背景 → 证据背景）

本单不是算例，而是**结论可信度的判定函数**。平台上的每一次「达标」都是某个量
对阈值的关系（相对误差百分比、参考值偏差、收敛阶下界……），历史上这些判据以「作者自述 + 日志」形式进入验收：

* 数字可能来自手抄（工件里根本没有这个数）；
* 阈值可能被生产者事后改宽（自证清白）；
* 判据可能被删掉（过不了就不报）；
* 工件可能是门外的任意文件（sha256 替它背书）。

于是「验收通过」与「证据成立」之间没有可机器复算的桥。本单把桥建出来：每份验收必须附 ``gate.json``（判据 ID / 阈值 / 实测值 / 单位 / 工件路径+sha256 / 运行命令 / 环境指纹），平台侧 ``gate_verify`` 在 dev_acceptance 前强制复算，缺任何一环 → **WITHHELD**（不知道 ≠ 通过），并自动派补证单。

plan 258 的 9 个节点作为回填对象（本波结构性清偿「门缺失」欠账）：回填覆盖率 100%，门数 9，判定分布 {'FAIL': 2, 'PASS': 5, 'WITHHELD': 2}，「无登记表却 PASS」的静默通过 0 例。

## ② 怎么做的（方法/流程/关键设置）

### 2.1 判定是函数，不是声明

```
grade = f(gate.json, criteria_registry.json, artifacts)   # 纯确定性、离线、无 wall clock
```

自上而下任一完整性缺口 → WITHHELD；否则任一阈值越线 → FAIL；全达标 → PASS。本波把判据从 13 条扩到 **19 条**，新增 6 条全部 fail-closed：

| 新增判据 | 问题码 | 防的是什么 |
|---|---|---|
| 权威登记表在位 | registry_missing | 删掉登记表 = 免检通道 |
| 登记表指纹锚定 | registry_tampered | 生产者顺手改宽阈值 |
| 判据集合完整 | criterion_dropped | 删掉过不了的判据 |
| 判据 id 不重复 | criterion_duplicate | 同一判据重复声明刷条数 |
| 工件不越出门口 | artifact_path_escape | 绝对路径 / `..` / 软链外逃 |
| 判定对象唯一 | gate_ambiguous | 同一 mission 塞第二份更宽松的门 |

### 2.2 前置钩子（不改审批器签名）

``tools/agent/auto_approver.py::_gate_front_verdict`` 在机器自动批之前调用 ``acceptance.precheck_mission``：PASS→allow_auto_approve，FAIL→reject，WITHHELD→escalate（并附补证单 dedupe_key）。钩子异常时返回 None（门挂了不拖死审批流），该降级行为有专门的测试。

### 2.3 注入式负控（不是把代码改坏，而是把证据改坏）

九类伪证据 × 每类 12 例 = **108 例**，逐例判定入矩阵；假通过 0 例（假通过率 0.0%）。同时保留**阳性对照**：干净门必须 PASS（否则「一律 WITHHELD」也能刷出假通过率 0）。

检测矩阵摘要 sha256 = ``7a7e45287c3ec21481a7700b36302be5dc17e0b84dd86a636e80bbad9c2762c0``（矩阵变了摘要必变）。

### 2.4 跨宿主一致性（A/B/C/D 四命题）

* **A 异构指纹回填**：用 4 套「别家机器签名」（x86_64/aarch64/loongarch64/Darwin）重新回填同一份 claims 再复算 → 逐门 ``verdict_digest`` 必须相同；
* **B 判定不读宿主**：``verify`` 路径无 ``platform`` / ``sys.byteorder`` 读取（结构性质）；
* **C 数值可移植**：证据中每个实测数字经 C 运行库 ``%.17g`` 往返（snprintf→strtod）位级相等；
* **D 远端实测件**：两份 ``verify_all.json``（不同机器）逐门摘要比对。

### 2.5 工具链与复算命令

```bash
python3 tools/gate_contract/plan258_audit.py         # 回填+复算+负控+台账+C4/C7 汇总
python3 tools/gate_contract/cli.py crossarch --gate-root artifacts/gate_contract/plan258 \
        --registry artifacts/gate_contract/plan258/criteria_registry.json \
        --claims config/gate_contract/plan258_claims.json --out artifacts/gate_contract
python3 tools/gate_contract/hardening_figures.py     # 图 1–4
python3 tools/gate_contract/hardening_report_doc.py   # 本文件
python3 -m pytest tests/test_gate_contract_hardening.py -q
```

## ③ 计算结果（数字表，程序化注入零手抄）

### 3.1 机器验收判据逐条

| 判据 | 目标 | 实测 | 工件 |
|---|---|---|---|
| C1 plan 258 九节点回填 | 100% | 100%（9/9 门） | artifacts/gate_contract/backfill_summary.json |
| C2 九类伪证据各 ≥10 例 | 漏检 0 | 108 例 / 假通过 0 | artifacts/gate_contract/negcontrol_summary.json |
| C3 无门恒等降级 WITHHELD | 异常 0 | 3/3 withheld，异常 0 | artifacts/gate_contract/summary.json#C3 |
| C4 跨宿主判定一致率 | 100% | A 100%（36 次比较）/ C 24 值往返坏 0 | artifacts/gate_contract/crossarch.json |
| C5 台账可回溯 | 1.0 | 15/15 = 1.0 | artifacts/gate_contract/ledger.db |
| C6 零领域语义 | 0 命中 | 0 命中 | artifacts/gate_contract/domain_scan_new_files.json |
| C7 硬化探针 | 全 withhold | 6/6，fail-open [] | artifacts/gate_contract/summary.json#C7 |

### 3.2 九类伪证据判定矩阵

| 伪证据类 | 例数 | 判定分布 | 假通过 |
|---|---|---|---|
| criterion_dropped | 12 | WITHHELD×12 | 0 |
| log_only | 12 | WITHHELD×12 | 0 |
| measured_handcopied | 12 | WITHHELD×12 | 0 |
| missing_unit | 12 | WITHHELD×12 | 0 |
| no_gate | 12 | WITHHELD×12 | 0 |
| path_escape | 12 | WITHHELD×12 | 0 |
| registry_omitted | 12 | WITHHELD×12 | 0 |
| sha_mismatch | 12 | WITHHELD×12 | 0 |
| threshold_widened | 12 | WITHHELD×12 | 0 |

### 3.3 fail-closed 硬化探针（每条新判据的最小反例）

| 探针 | 判定 | 问题码 |
|---|---|---|
| registry_omitted | WITHHELD | registry_missing |
| criterion_dropped | WITHHELD | criterion_dropped |
| criterion_duplicate | WITHHELD | criterion_duplicate |
| path_escape | WITHHELD | artifact_path_escape |
| gate_ambiguous | WITHHELD | gate_ambiguous |
| registry_tampered | WITHHELD | registry_tampered |

### 3.4 plan 258 九节点门判定

| gate_id | mission | 判定 | 判据数 | 问题码 |
|---|---|---|---|---|
| plan258-IN68-N01 | 7182 | PASS | 3 | - |
| plan258-IN68-N02 | 7183 | WITHHELD | 3 | pointer_unresolved |
| plan258-IN68-N03 | 7184 | PASS | 2 | - |
| plan258-IN68-N04 | 7185 | FAIL | 3 | - |
| plan258-IN68-N05 | 7186 | WITHHELD | 2 | value_not_finite |
| plan258-IN68-N06 | 7187 | PASS | 3 | - |
| plan258-IN68-N07 | 7188 | FAIL | 3 | - |
| plan258-IN68-N08 | 7189 | PASS | 3 | - |
| plan258-IN68-N09 | 7190 | PASS | 3 | - |

### 3.5 逐判据实测/阈值（回填自各自工件）

| gate_id | 判据 | 阈值 | 单位 | 实测 | 状态 |
|---|---|---|---|---|---|
| plan258-IN68-N01 | C1 | 1 | % | 4.40963e-14 | pass
| plan258-IN68-N01 | C2 | [0.02, 0.08] | - | [0.02, 0.08] | pass
| plan258-IN68-N01 | C3 | 5 | % | 6.58979e-10 | pass
| plan258-IN68-N02 | C1 | 10 | % | 9.03 | pass
| plan258-IN68-N02 | C2 | 5 | % | 0.54 | pass
| plan258-IN68-N02 | C3 | 3 | % | None | withheld
| plan258-IN68-N03 | C1 | 1e-06 | m | 3.14825e-08 | pass
| plan258-IN68-N03 | C2 | 0.05 | - | 0.022222 | pass
| plan258-IN68-N04 | C1 | 1e-10 | - | 2.21689e-12 | pass
| plan258-IN68-N04 | C2 | 1e-10 | - | 1 | fail
| plan258-IN68-N04 | C3 | 1e-12 | - | 4.44089e-16 | pass
| plan258-IN68-N05 | C1 | 1e-06 | - | 0 | pass
| plan258-IN68-N05 | C2 | 0.95 | - | None | withheld
| plan258-IN68-N06 | C1 | 1 | % | 0 | pass
| plan258-IN68-N06 | C2 | 2 | dB | 0.646 | pass
| plan258-IN68-N06 | C3 | 2 | dB | 1.57 | pass
| plan258-IN68-N07 | C1 | 5 | % | 0.381772 | pass
| plan258-IN68-N07 | C2 | 10 | % | 9.07834 | pass
| plan258-IN68-N07 | C3 | 10 | % | 36.82 | fail
| plan258-IN68-N08 | C1 | 1e-06 | - | 4.01285e-16 | pass
| plan258-IN68-N08 | C2 | 1e-06 | - | 8.96898e-18 | pass
| plan258-IN68-N08 | C3 | 0.05 | - | 3.7044e-15 | pass
| plan258-IN68-N09 | C1 | 1e-09 | - | 5.07531e-16 | pass
| plan258-IN68-N09 | C2 | 0.15 | m | 2.01276 | pass
| plan258-IN68-N09 | C3 | 0.001 | kg | 0 | pass

### 3.6 跨宿主一致性明细

| 宿主签名 | 比较门数 | 摘要相同 | 失配 |
|---|---|---|---|
| x86_64 / Linux / py3.11.4 | 9 | 9 | 0 |
| aarch64 / Linux / py3.12.1 | 9 | 9 | 0 |
| loongarch64 / Linux / py3.11.2 | 9 | 9 | 0 |
| x86_64 / Darwin / py3.13.0 | 9 | 9 | 0 |

* gate.json 字节差异案例数：36（指纹确实变了，判定却没变）
* libc 往返：24 值 / 坏 0（格式 ``%.17g``，libc.so.6）
* 远端实测件：未接入（本轮无 x86 物理机/CI 工件；不得把模拟当实测）

## ④ 结果图

![图1 负控矩阵](../artifacts/gate_contract/figures/fig1_negative_control_matrix.png)

*图 1 九类注入式伪证据 ×12 例的判定分布；PASS 段为空 = 假通过率 0。*

![图2 硬化探针](../artifacts/gate_contract/figures/fig2_hardening_probes.png)

*图 2 本波新增 6 条 fail-closed 判据的最小反例探针，全部落 WITHHELD。*

![图3 跨宿主一致性](../artifacts/gate_contract/figures/fig3_cross_host_consistency.png)

*图 3 四套异构宿主签名回填后逐门摘要一致（蓝=相同，红=失配为空）+ libc 往返。*

![图4 plan258 判据裕度](../artifacts/gate_contract/figures/fig4_plan258_margins.png)

*图 4 plan 258 九节点逐判据 measured/threshold 裕度（对数轴）与三值判定；×=阈值越线。*

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **宿主污染判定输入（本波实测发现并修复）**：回填早期把 ``run_cmd`` 写成绝对路径，同一份证据换目录树/换机器摘要就变——跨架构比对永远对不上。改为 ``<OUT>`` 占位符后，A 命题从 0% 直接变成 100%。这不是噪声，是判定函数被宿主污染。
2. **「没有登记表」曾是免检通道**：``verify_gate(registry=None)`` 早期直接跳过阈值权威比对，PASS 仍可给出。现在默认 ``require_registry=True``，缺表即 WITHHELD；审计用逃生门必须显式写 ``require_registry=False``。
3. **判据删减比阈值改宽更隐蔽**：只检查「门里的判据在登记表里」是不够的，必须反向检查「登记表的判据都在门里」，否则删判据即可免责。
4. **路径逃逸**：工件路径允许绝对/`..`/软链时，门外的任意文件都能被 sha256 背书；现在一律要求留在门目录内。
5. **判定对象唯一性**：同一 mission 两份门时「谁排在前面谁生效」，加门即可改判定——现在多门即 WITHHELD。
6. **局限（诚实申报）**：
   * 跨架构维度：本机与集群均为 loong64，x86 物理机/CI 工件未接入，D 命题 未接入（本轮无 x86 物理机/CI 工件；不得把模拟当实测）；跨架构结论依靠 A（异构指纹实测）+ B（判定不读宿主的构造性证明）+ C（libc 往返）外推，报告中标为**外推**而非实测。 仓库内 ``.github/workflows`` 已有 x86 侧作业面，接入后 C4 可升级为实测。
   * 负控类别是**本波定义**的九类，不是穷尽集；新增攻击面应同步新增类（矩阵摘要会变，因此历史结果不可被静默替换）。
   * 「权威登记表」自身可信性靠指纹锚定（policy.registry_sha256），锚的保管在治理层，不在代码内——这是制度边界不是技术缺口。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准（真值来源） | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 假通过率 | 注入标签已知（每例都确定是伪证据） | 0.0% （0/108） | 0 | 是（精确 0） |
| 有门时降级异常数 | 真值 0 异常 | 0 | 0 | 是 |
| 回填覆盖率 | 目标 100% | 100% | 0 | 是 |
| 跨宿主摘要一致率 | 真值 100%（判定与宿主无关） | 100% | 0 | 是 |
| 数字往返一致率 | 真值 100%（IEEE-754 双精度） | 100%（坏 0） | 0 | 是 |
| 台账回溯率 | 真值 1.0（工件仍在磁盘） | 1.0 | 0 | 是 |

**结论**：六项关键量对基准误差均为 0（判据是计数/比例，基准即注入真值，不存在数值离散）；唯一未取证项是 x86 侧的**架构实测**（跨架构一致率目前为构造性外推 + loong64 实测），已在 ⑤.6 如实标注，不计入「已实测」。

## 审计链（每个数字可溯源）

```
runs:  python3 tools/gate_contract/plan258_audit.py
claims: config/gate_contract/plan258_claims.json  (9 nodes)
gates:  artifacts/gate_contract/plan258/*/gate.json  + criteria_registry.json
judge:  artifacts/gate_contract/verify_all.json      (每门 grade/issues/criteria)
negctl: artifacts/gate_contract/negcontrol_summary.json (逐例 grade+codes)
cross:  artifacts/gate_contract/crossarch.json        (A/C/D)
ledger: artifacts/gate_contract/ledger.db             (逐判据一行，SQL 可回溯)
summary:artifacts/gate_contract/summary.json          (C1–C7)
```

