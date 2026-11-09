# criteria_governance —— 判据治理机制（平台共性层，零领域语义）

**用途**：给平台上的"判据"（acceptance criteria）一个单一事实源与成熟化验收链：
入册 → 分类（source_type）→ 派生（可复算）→ 漂移门（防篡改）→ 成熟度评分。

> 铁律：本包代码里**不出现任何领域词条/阈值/算例**。判据集是数据工件
> （`data/criteria_governance/*.criteria.json`），物理/几何参数写工件里，机制只读工件。

## 组成

| 模块 | 职责 |
|---|---|
| `kernels.py` | 通用数学：张量矩、有限对称群（B_dim/O_h）、不变子空间维数、细化扫描拟合、体积分算例、剪切网格度量算子 |
| `probes.py` | 探针注册表：名字 + 参数 → 测量值（原理不懂领域，只认参数） |
| `registry.py` | 阈值注册表：required fields / source_type / cohort 校验、派生链可追溯、负控剔除、KPI |
| `derivation.py` | 派生一致性引擎：执行探针，**自己重算**观测阶，比对声明指数与容差 |
| `drift_gate.py` | CI 漂移门：规范化 JSON sha256 钉桩 + 数值叶子逐点对照；篡改/假阳性计量 |
| `maturity.py` | 四维成熟度（覆盖率/可派生率/漂移检出率/1−FPR）+ 雷达图 |
| `run_integration.py` | 集成运行器：产出验证包、钉桩、report.json、图、论文级 md |
| `schema_v2.py` | **v2** 判据 schema：来源引用/依赖量/表达式/复核日期校验、可裁口径、语义与装饰投影、表达式白名单求值 |
| `engine_v2.py` | **v2** 注册表与派生引擎：观测阶重算、`threshold(h)` 重推导闭环、"阈值=实测"降级、覆盖率账本 |
| `live_harvest.py` | **v2** 现网抽样：AST 取平台既有阈值常量 + 取证窗口定 `source_type`（词表在数据工件里） |
| `drift_gate_v2.py` | **v2** 来源绑定感知漂移门 + 篡改/假阳性注入计量 |
| `probes_linear.py` | **v2** 合成线性算子探针（中心差分 / 复合梯形的已知阶） |
| `gen_synthetic_bundle.py` | 生成合成阶次判据工件（阈值 = SF·C·h^p，程序化计算零手抄） |
| `run_v2.py` | **v2** 集成运行器：注册表/账本/钉桩/注入/图/论文级报告 |

## 接入一套新判据（3 步，代码零改动）

1. 在 `data/criteria_governance/<name>.criteria.json` 写 7 条（或任意条）判据，字段：
   `id/title/statement/cohort/source_type/metric/threshold{op,value,unit}/derivation{method,declared_exponent?,reference,parents,tolerance}/probe{name,params}`。
2. 若需要新探针，在 `probes.py` 加一个函数并注册进 `PROBES`（参数一律从 `params` 读）。
3. 在 `run_integration.SPEC_FILES` 注册工件路径；跑
   `python3 tools/criteria_governance/run_integration.py`。

`cohort` 是"申报进入哪个队列"：`cohort=analytic` 而 `source_type!=analytic`（或派生方法落在经验族）
的条目会被**自动剔除并告警**，队列计数不上升，且仍留在覆盖率分母里（防止"剔除即提升覆盖率"）。

## 已知坑（实测）

* **Q≡0 的相对残差**：无迹判据里用 `max|Q|` 当分母会把 1e-16 舍入噪声放大成 O(1) 假残差；
  尺度必须外部给定（本实现取 `|tr M2|`）。
* **不支持不代表不受限的差分格式**：剪切网格上的度量算子必须显式展开 `G_ij ∂²_{ξiξj}`
  （含混合导数与符号）；漏项错误会表现为"误差收敛到一个错的常数"，用 s=0 退化对照最易发现。
* **周期域与剪切不兼容**：`f(ξ1, ξ2+sξ1)` 只有当 `s·kL = 2πm` 时才在 ξ1 上周期；否则边界跳变污染拟合。
* **matplotlib 无 CJK 字体**：图内标签用 ASCII，中文留在报告表格里。

## v2：阈值是 dependency 的函数（m8263）

v1 只问"判据合规吗、探针过吗"；v2 再问"**阈值凭什么取这个数**"：

1. 每条判据登记 `source_ref`（DOI / 标准条款 / 文件 sha256+locator）、
   `depends_on[{param, exponent}]`、可选 `derivation.expression`（如 `SF*C*h**2`）与 `review_date`。
2. 派生一致性引擎**自己**从 `hs/errors` 序列重算观测阶，比对声明指数（容差 0.3）。
3. 重推导闭环：在 h、h/2、h/4 三档上比较"实测误差 / threshold(h) 预测"，要求 h/2、h/4 的比值 ∈ [0.5, 2]。
4. 漂移门 v2：只认**出处**为改阈值的合法理由——
   阈值改而出处不变 = 红；出处与阈值同改 = 可追溯修订（放行）；只改注释/排版 = 必须安静。
5. 可裁口径收紧：`heuristic`（专家判断）与 `empirical`（拟合实测）一律不进可裁集合；
   阈值被"照着实测抄下来"的（`<=` 且阈值≈实测）自动降级为 `empirical`。
6. 现网抽样：`data/criteria_governance/live_sampling_plan.json` 声明文件+符号，
   值/行号由 AST 取、出处由**取证窗口**（声明行 + 紧邻注释）判定，词表也放在数据里；
   证据不足一律 `heuristic`，绝不臆造出处。抽到的常量挂 `source_constant` 探针（回读源码），
   裁决"注册值 == 源码值"。

### 运行

```bash
python3 -m tools.criteria_governance.gen_synthetic_bundle     # 重生成合成阶次判据工件
python3 tools/criteria_governance/run_v2.py                   # 产出 artifacts/criteria_governance_v2/
python3 -m pytest tests/test_criteria_governance_v2.py -q     # C1–C7 + CI 钉桩，24 项
```

`artifacts/criteria_governance_v2/drift_pins.json` 是**CI 钉桩**：注册表语义哈希 + 出处绑定哈希。
`tests/test_criteria_governance_v2.py::test_c3_committed_pins_match_registry` 会把它和现网注册表逐条比对，
所以"改了阈值却没更新出处也没更新钉桩"在 CI 上是红的。

### v2 已知坑（实测）

* **恒等式判据不是拟合**：`==` 型判据（秩=1、残差=0）实测恰好等于阈值是**正确**，
  所以 C7 的"阈值=实测"检测只对 `<=` 且阈值为正的误差上界型判据生效，否则会大面积误降级。
* **回读类探针天然等于阈值**：`source_constant` 探针的测量值**就是**注册值（来源绑定），
  必须豁免拟合检测，否则整批现网条目会被误降级（本节点第一版就这么翻车）。
* **取证窗口要允许隔空行、但要被代码行截断**：只往上看连续注释会把不相关出处算到阈值头上；
  反过来遇空行就停会漏掉"注释—空行—常量"的常见排版。
* **取证词表属于数据**：`ITTC/IACS/ISO…` 这类标准编号族是领域词，必须放进
  `live_sampling_plan.json` 的 `provenance_patterns`，否则 `tools/evidence_domain_scan.py` 会判红。

## 自检

```bash
python3 -m pytest tests/test_criteria_governance.py tests/test_criteria_governance_v2.py -q   # 44 项
```