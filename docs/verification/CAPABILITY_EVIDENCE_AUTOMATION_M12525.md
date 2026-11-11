# 能力台账证据自动化采集管线（m12525 · IN387-PLAT02）

**结论摘要**：平台能力台账的"有证据"能力数从人工台账的 **3/21** 提升到 **17/21**（判据目标
≥15/21），全部 17 条各带一份机器可复算的 `acceptance.json`；等级判定与 21 例人工标注对抗
夹具的一致率 **21/21 = 100%**；人工复核为 REAL 的能力（`calc.stability`）**未降级**，且无任何
能力出现自动判级越过人工裁决（OVERCLAIM）。

---

## 1. 问题：自述等级无法自查

平台自述 21 项能力（`audit_capabilities`：REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED），但人工
对账台账 `artifacts/capability_ledger/capability_ledger_resolved.json` 显示 **只有 3 项有证据**
（`propeller.open_water`=MOCK、`calc.stability`=REAL、`calc.resistance`=WITHHELD），其余 18 项
`UNVERIFIED`。缺的不是"报告"，而是**可执行、可复算、可复现的证据链**：谁跑的测试、跑了几条、
通过率多少、判成了什么等级、凭什么。

此前两次尝试都停在半路：

* `backend/services/capability_evidence_auto.py` 只**静态扫工件**（`verified/`、`benchmarks/`），
  不执行测试，于是"没有工件的能力"永远拿不到证据；
* `src/capability_ledger_auto/` 有判级器与证据链，但缺少"能力→测试模块"的**配置化映射**，
  默认注册表 `config/capabilities.json` 的 `test_patterns` 是关键词而非可执行选择器，
  且没有任何逐能力验收工件产出。

## 2. 方法

四个部件，全部配置驱动（零领域语义：代码里不出现任何具体能力名/测试路径/阈值）：

| 部件 | 文件 | 职责 |
|---|---|---|
| 映射配置 | `config/capability_evidence_map.json` | `capability_slug → {test_modules, probes, code_globs, acceptance}`；装载时**校验测试模块真实存在**，禁止"映射一条不存在的测试"虚增覆盖率 |
| 执行与归属 | `tools/evidence_automation/runner.py` | 一次 pytest 批量跑完全部映射模块（junitxml），按 `classname` 自长向短匹配把用例归属回能力；失败/收集期错误/跳过分开计 |
| 运行时探针 | `tools/evidence_automation/probes.py` | 给"没有专属测试模块"的能力取证：git 管路、容器编排清单、反向代理配置、HTTP 探活、几何解析恒等式。每个探针自报 `criterion_kind`（`analytic_identity` 才有资格单独支撑 REAL），并附**负控制** |
| 判级与验收 | `tools/evidence_automation/grader.py` + `acceptance.py` + `collect.py` | 证据→等级；逐能力落 `acceptance.json`（判据+实测+理由+digest+sha256 旁车）；汇总覆盖率与 C1~C4 |

### 2.1 判级规则（每一支都写进产物的 `reasoning`）

1. 无映射/无实现/无执行证据 → `NOT_SUPPORTED`
2. 空壳标记覆盖过半实现文件（`raise NotImplementedError` / `TODO: implement`）→ `STUB`
3. 造假标记覆盖过半（`synthetic response` / `hardcoded value` / `np.random.random(`）→ `MOCK`
4. 有真实执行的测试：通过率 ≥ 阈值且零失败零错误 → `REAL`；通过率 < 阈值 → `MOCK`；
   介于两者 → `WITHHELD`
5. **收集期错误占主导（≥20%）→ `WITHHELD`**：缺工件/缺环境造成的 `error` 不是"实现造假"，
   不得据此判 MOCK（`ai.predict` 就是这一支：33 条测试 17 条 error → WITHHELD）
6. 只有探针：探针不可用（环境未起）→ `WITHHELD`；判据全过且 `analytic_identity` → `REAL`；
   仅 `behavioral` → `WITHHELD`；判据不过 → `MOCK`
7. **人工复核上限**：自动判级不得越过人工台账最新裁决（只降不升）——
   `propeller.open_water` 的映射测试 29/29 全绿，但人工裁决 MOCK，故仍判 MOCK（防 OVERCLAIM）

### 2.2 覆盖率口径（写在 `coverage.json` 里，可机器复核）

`covered` = 该能力的 `acceptance.json` 中**至少一项证据真实执行过**（测试 `decisive>0`
或探针 `available=True`）。静态扫描不算覆盖。

## 3. 结果

### 3.1 覆盖率与等级分布

| 指标 | 数值 |
|---|---|
| 能力总数 | 21 |
| 有可执行证据（covered） | **17**（目标 15） |
| 未覆盖 | 4：`dev.logs` / `dev.restart` / `methods.computation` / `misc.stubs` |
| 等级分布 | REAL 10 · WITHHELD 7 · NOT_SUPPORTED 3 · MOCK 1 |
| 标注集一致率（C2 构造集） | 21/21 = 100% |
| 人工裁决一致率（C2 真实） | 3/3 = 100%（裸判级同为 3/3：上限未改变任何结论） |
| C4 REAL 不降级 / 无越权 | 通过（checked=3，downgraded=[]，overclaimed=[]） |

未覆盖的 4 项**不是漏做**，而是本仓确实没有可执行取证面：`dev.logs`（无日志读取实现）、
`dev.restart`（只有 `start.sh`/`stop.sh`，无重启语义）、`methods.computation`（注册表声明
NOT_SUPPORTED 且无实现）、`misc.stubs`（自述即 STUB）。它们照样有 `acceptance.json`，
里面写明 `NOT_SUPPORTED/WITHHELD` 与理由——**如实表达缺失**，不补成 REAL。

### 3.2 逐能力证据（节选，全量见 `artifacts/capability_evidence/<slug>/acceptance.json`）

| 能力 | 等级 | 证据基础 | 实测 |
|---|---|---|---|
| `calc.stability` | REAL | 执行测试 | 20 条全绿（pass_rate 1.00） |
| `geometry.generate` | REAL | 执行测试 | 47 条全绿 |
| `geometry.list_models` | REAL | 执行测试 | 33 条全绿 |
| `shiphull.generate` | REAL | 解析探针 | Cb 恒等式相对误差 ≤1e-6、闭式 GZ vs 数值积分最大相对偏差 0.0、扰动 Cb 被拒 |
| `geometry.verify_official` | REAL | 解析探针 | 同上（几何一致性校验） |
| `dev.git` | WITHHELD | 行为探针 | HEAD 三路一致、40-hex、脏树解析（无对标误差 → 不给 REAL） |
| `dev.services` | WITHHELD | 行为探针 | 22 个服务清单解析，16 个声明 image/build |
| `dev.probe` | WITHHELD | 行为探针 | :8002/health 200（候选端口自动探测） |
| `ai.predict` | WITHHELD | 执行测试 | 33 条中 17 error（缺工件）→ 不作判负 |
| `propeller.open_water` | MOCK | 执行测试 + 人工上限 | 29 条全绿，但人工裁决 MOCK 生效 |

### 3.3 产物与审计链

```
artifacts/capability_evidence/<capability_slug>/acceptance.json          # 判据+实测+理由+content_digest
artifacts/capability_evidence/<capability_slug>/acceptance.json.sha256   # 整文件 sha256 旁车
artifacts/capability_evidence/coverage.json                              # 覆盖率 + C1~C4 + 两条一致率
artifacts/capability_evidence/coverage.json.sha256                       # sha256: d2812d17…
```

`coverage.json` 的 `content_digest = 37b6cce4…`（易变字段 `generated_at/run_metrics` 已排除，
同仓两次运行字节一致，见 `tests/test_evidence_automation_pipeline.py::test_collect_writes_acceptance_artifacts_and_is_reproducible`）。

## 4. 复现

```bash
python3 tools/evidence_automation/cli.py collect          # 全流程（约 110 s：批量跑映射测试）
python3 tools/evidence_automation/cli.py collect --skip-tests   # 只跑静态+探针（秒级）
python3 tools/evidence_automation/cli.py check            # 只跑判据 C1~C4（读已有产物）
python3 -m pytest tests/test_evidence_automation_pipeline.py tests/test_evidence_automation_probes.py -q
```

## 5. 局限（诚实声明）

1. **一致率的构造性**：标注集 21 例由本单手工编写，验证的是"实现是否符合平台等级定义"，
   **不是**"等级定义本身是否成立"。真实口径只有 3 例（人工台账有证据的 3 条），3/3=100%
   且裸判级与带上限判级一致（说明上限没有替判级"兜底"）。
2. **探针的能力半径**：`analytic_identity` 探针只覆盖几何闭式恒等式与数值独立解，不代表
   整条能力的全量验证；`dev.*` 四类探针只有行为证据，故按规则只能到 WITHHELD。
3. **测试归属依赖 junit `classname`**：pytest 不写 `file` 属性时用 `classname` 自长向短匹配，
   若将来出现"同一模块内嵌类导入外部测试"的写法，归属可能退化（已在 runner 里加回归护栏）。
4. **未覆盖的 4 项**：`dev.logs`/`dev.restart`/`methods.computation`/`misc.stubs` 仍无执行证据，
   需上游先补齐实现（本单不越界造实现）。
5. **人工上限的滞后性**：上限来自人工台账最新一条裁决，人工台账未更新的能力不设上限；
   这是"宁可少判 REAL，不可越权"的取舍，代价是可能有能力被历史裁决长期压在 MOCK。