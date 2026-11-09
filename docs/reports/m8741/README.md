# m8741 · 阈值口径注册表功能验证与成熟化确认

**任务性质**：重开 PR#874 管到底第1轮（原 PR 于 2026-11-05 由 owner 裁定关闭）

** mission 目标**：验证阈值口径注册表（threshold registry）功能的完整性与成熟度，确认所有判据（C1-C7）通过，文档齐备，测试全绿。

## 1. 背景

阈值口径注册表是平台共性机制的核心组成部分，解决三个关键问题：
1. **出处追溯**：每条验收阈值的来源（解析界/文献/规范/实测/启发式）
2. **依赖量登记**：阈值随什么参数变化（网格分辨率 h、阶数 p、样本数 N 等）
3. **可重推导**：给定参数值，能否计算机预测阈值并与实测对照

本功能在 m8263 中完成 v2 实现并合并入 main，本 mission 进行最终验证与成熟化确认。

## 2. 验证范围

### 2.1 文件清单（12 个核心文件）

| 文件路径 | 行数 | 职责 |
|---|---|---|
| `tools/criteria/__init__.py` | 1 | 工具包初始化 |
| `tools/criteria/threshold_registry.py` | 936 | 核心引擎：校验、派生、漂移门、覆盖率 |
| `tools/criteria/harvest.py` | 418 | 现网活文件确定性抽样入册 |
| `tools/criteria/probes.py` | 151 | 4 个纯数学合成算子探针 |
| `tools/criteria/gate_report.py` | 894 | C1-C7 判据报告生成 |
| `tools/criteria/publish_report.py` | 298 | 报告发布脚本 |
| `data/criteria/threshold_registry.json` | 901 | 56 条判据注册表 |
| `data/criteria/threshold_registry.lock.json` | 603 | 语义指纹锁文件 |
| `schemas/threshold_registry.schema.json` | 177 | JSON Schema 契约 |
| `tests/test_threshold_registry.py` | 593 | 52 个验收测试 |
| `docs/criteria/threshold_registry.md` | 228 | 用户文档（论文级六要素） |
| `.github/workflows/threshold-registry-gate.yml` | 103 | CI 工作流 |
| `Makefile` | +19/-1 | criteria-gate / criteria-harvest 目标 |

### 2.2 判据验证（C1-C7）

所有 7 条判据全部通过：

| 判据 | 描述 | 结果 | 关键数字 |
|---|---|---|---|
| C1 | 现网抽样 ≥30 条，source_type 缺失率 0 | ✅ PASS | 56 条入册，缺失率 0.0 |
| C2 | 解析判据实测指数与声明偏差 ≤0.3 | ✅ PASS | 4/4 条，最大偏差 0.053456 |
| C3 | 阈值篡改检出率 100% | ✅ PASS | 20/20 注入判红 |
| C4 | 格式变更假阳性率 0% | ✅ PASS | 20 次格式变更，误报 0 |
| C5 | 重推导闭环比值 ∈[0.5,2] 且同向 | ✅ PASS | 4 条判据全部通过 |
| C6 | 覆盖率账本完整 | ✅ PASS | 有来源 1.0 / 可派生 0.071 / 可裁 0.214 |
| C7 | 同义反复负控 + 标注说谎检测 | ✅ PASS | 降级 empirical + 剔除可裁集合 |

## 3. 测试执行

### 3.1 单元测试

```bash
$ python -m pytest tests/test_threshold_registry.py -v
============================= test session starts ==============================
collected 52 items

tests/test_threshold_registry.py::test_probes_measured_order_matches_declared PASSED
tests/test_threshold_registry.py::test_probe_errors_decrease_monotonically PASSED
tests/test_threshold_registry.py::test_validate_accepts_synthetic_registry PASSED
... (52 tests total)
======================== 52 passed, 1 warning in 1.03s =========================
```

**测试覆盖率**：
- 结构校验（JSON Schema + 交叉一致性）
- 派生一致性（指数反演 + 窗口敏感性）
- 漂移门（语义指纹 + 篡改检测 + 假阳性控制）
- 重推导闭环（比值带 + 同向性）
- 覆盖率账本（来源/可派生/可裁占比）
- 负控（同义反复 + 标注说谎）
- CLI 退出码

### 3.2 集成门

```bash
$ make criteria-gate
....................................................                     [100%]
52 passed, 1 warning in 0.81s
{
  "verdict": "CLEAR",
  "C1": "56 条入册（≥30）；source_type 缺失率 0.0；heuristic 44 条全部标不可裁",
  "C2": "4/4 条解析判据实测指数最大偏差 0.053456 ≤ 0.3",
  "C3": "20/20 注入判红（检出率 1.0）",
  "C4": "20 次格式变更误报 0（假阳性率 0.0）",
  "C5": "4 条判据的 threshold(h) 在 h/2、h/4 上比值 ∈[0.5, 2.0]，全部同向",
  "C6": "有来源阈值占比 1.0；可派生阈值占比 0.071429；可裁占比 0.214286；来源腐烂控制 OK→SHA_DRIFT",
  "C7": "同义反复判据被判 empirical 且剔除；标注说谎被抓到 = True",
  "report": "artifacts/criteria/threshold_registry_report.json"
}
```

## 4. 产物清单

### 4.1 代码产物

- ✅ 核心引擎：`tools/criteria/threshold_registry.py` (936 行)
- ✅ 入册器：`tools/criteria/harvest.py` (418 行)
- ✅ 探针：`tools/criteria/probes.py` (151 行)
- ✅ 报告生成：`tools/criteria/gate_report.py` (894 行)
- ✅ 发布脚本：`tools/criteria/publish_report.py` (298 行)
- ✅ 测试套件：`tests/test_threshold_registry.py` (593 行，52 个测试)

### 4.2 数据产物

- ✅ 注册表：`data/criteria/threshold_registry.json` (56 条判据)
- ✅ 锁文件：`data/criteria/threshold_registry.lock.json` (语义指纹)
- ✅ Schema：`schemas/threshold_registry.schema.json` (JSON Schema 契约)

### 4.3 文档产物

- ✅ 用户文档：`docs/criteria/threshold_registry.md` (228 行，论文级六要素)
- ✅ 本报告：`docs/reports/m8741/README.md`

### 4.4 CI/CD 产物

- ✅ GitHub Actions：`.github/workflows/threshold-registry-gate.yml`
- ✅ Makefile 目标：`criteria-gate` / `criteria-harvest`

### 4.5 运行时产物

- ✅ 验证报告：`artifacts/criteria/threshold_registry_report.json` (27K)
- ✅ 漂移检查：`artifacts/criteria/drift.json` (8.2K)
- ✅ 结构校验：`artifacts/criteria/validate.json`
- ✅ 可视化图表：`artifacts/criteria/figures/` (3 张 PNG)

## 5. 关键特性

### 5.1 零领域语义纪律

本模块与测试夹具中**不出现任何领域词条**：
- 所有"实测"来自纯数学合成算子（初等函数定积分 / 一阶初值问题的已知阶离散误差）
- 领域语义只能作为 `source_ref` 字符串（路径+sha+指针）注入
- 域词表若需过滤，从 `config/domain_word_denylist.json` 以数据形式读入，绝不硬编码

### 5.2 八件套机制

1. **注册表契约** (`schemas/threshold_registry.schema.json`)
2. **入册器** (`tools/criteria/harvest.py`)
3. **派生一致性引擎** (C2：指数反演 + 窗口敏感性)
4. **重推导闭环** (C5：threshold(h) 预测 vs 实测)
5. **来源指针腐烂检查** (source_ref sha256 验证)
6. **可裁/不可裁判定** (含同义反复负控 C7)
7. **语义指纹漂移门** (C3/C4：篡改检测 + 假阳性控制)
8. **覆盖率账本** (C6：来源/可派生/可裁占比)

### 5.3 安全特性

- **表达式安全求值**：白名单 AST 解释器，只允许 `+ - * / **` 与括号、常量 C、变量 h
- **JSONC 支持**：注册表允许注释，注释不进语义指纹
- **数值归一化**：12 位有效数字，避免浮点表示差异导致假阳性

## 6. 局限性与后续工作

### 6.1 当前局限

1. **source_type 保守性**：配置值与代码常量出处无法自动认定时一律判 `heuristic`，导致 `decidable_ratio` 只有 0.214
2. **depends_on 为空**：依赖量未登记，`derivable_ratio` 只有 0.071
3. **C2 实测来自合成算子**：不是平台真实求解器，对真实求解器阶数的裁决属于各领域仓的验证单
4. **漂移门覆盖范围**：只覆盖注册表内的阈值，未入册的散落字面量不在防区
5. **语义指纹粒度**：数值按 12 位有效数字归一，相对改动小于 ~1e-12 的阈值篡改看不见

### 6.2 后续工作建议

1. **扩大入册**：逐条给出 C·h^p 的常数出处，提升 `derivable_ratio`
2. **领域仓对接**：各领域仓提交真实求解器的验证单，与合成算子探针互补
3. **未入册阈值扫描**：开发工具扫描代码中未入册的散落字面量
4. **文献/规范自动提取**：从 PDF/HTML 文献中自动提取阈值并生成 `source_ref`

## 7. 复现指令

```bash
# 1. 入册（从现网活文件重新抽样）
python -m tools.criteria.harvest --out data/criteria/threshold_registry.json

# 2. 生成锁文件
python -m tools.criteria.threshold_registry lock \
    --registry data/criteria/threshold_registry.json \
    --out data/criteria/threshold_registry.lock.json

# 3. 运行门检查
python -m tools.criteria.threshold_registry gate \
    --registry data/criteria/threshold_registry.json \
    --lock data/criteria/threshold_registry.lock.json \
    --out artifacts/criteria/gate_cli.json

# 4. 生成报告
python -m tools.criteria.gate_report

# 5. 运行测试
python -m pytest tests/test_threshold_registry.py -v

# 6. 一键执行（上述全部）
make criteria-gate
```

## 8. 结论

✅ **功能完整**：12 个核心文件齐备，2698 行代码 + 593 行测试  
✅ **测试全绿**：52/52 单元测试通过  
✅ **判据全过**：C1-C7 七条判据全部 PASS  
✅ **文档齐备**：用户文档 + 本报告 + 内联文档字符串  
✅ **CI 集成**：GitHub Actions 工作流 + Makefile 目标  
✅ **零领域语义**：严格遵守平台共性机制纪律  

**成熟度判定**：阈值口径注册表功能已达到生产就绪（production-ready）状态，可作为平台验收流程的核心基础设施长期使用。

---

**审计链**：本报告所有数字可回放到 `artifacts/criteria/threshold_registry_report.json`，sha256 见 `data/criteria/threshold_registry.lock.json`。

**mission 状态**：✅ 完成（awaiting_pr_merge）
