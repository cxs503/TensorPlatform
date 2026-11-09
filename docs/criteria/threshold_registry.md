# 验收阈值口径注册表与派生一致性门（P171W1-N06）

**一句话**：把平台的每一条验收阈值从"当年恰好能过的那条线"改造成
`(quantity, comparator, threshold(h,p,N,ε), source_ref)` 的**可追溯、可重推导**记录，
并让"改阈值而不改出处"在 CI 上必然变红。

## 1. 问题与判据

平台每一句"验收通过"都挂在一个数上。历史上这些数以字面量散落，没人能回答：
(1) 它从哪来；(2) 它随什么变；(3) 给定分辨率 h 它应当是多少。三问缺失的后果是
阈值在某个分辨率/样本量上静默失效，而门禁照旧变绿。

本节点交付八件套（见 `tools/criteria/threshold_registry.py` 模块头）：
注册表契约 `schemas/threshold_registry.schema.json`、入册器 `tools/criteria/harvest.py`、
派生一致性引擎、重推导闭环、来源指针腐烂检查、可裁/不可裁判定（含同义反复负控）、
语义指纹漂移门、覆盖率账本。**零领域语义**：实测来自四个纯数学合成算子探针
（`tools/criteria/probes.py`），领域语义只以 `source_ref` 字符串注入；域词表从
`config/domain_word_denylist.json` 以数据读入。

## 2. 方法

- **入册（C1）**：从现网活文件确定性抽样（seed=7810）：
  JSON 数值叶子（`config/`、`schemas/`）+ `tools/**/*.py` 模块级常量 + 4 条解析误差界。
  每条记 `source_ref = <路径>#sha256:<hex>#<指针>`，可机器验真。
- **派生一致性（C2）**：对声明 O(h^p) 的解析判据，跑 h=0.25…0.0078 六档，用
  log|E|–log h 最小二乘反演实测指数，要求 `|实测-p| ≤ 0.3`。
- **漂移门（C3/C4）**：注册表语义指纹（去注释、数值按 12 有效位归一）入锁文件
  `data/criteria/threshold_registry.lock.json`；`threshold` 变而 `source_ref` 未变 → RED。
- **重推导闭环（C5）**：`threshold(h)=C·h^p` 投到 h/2、h/4，与该档实测误差比值须落在
  `[0.5, 2.0]` 且与实测同向。
- **覆盖率账本（C6）**：`sourced_ratio`（指针可验）与 `derivable_ratio`（有 C·h^p）。
- **负控（C7）**：注入一条"阈值写死成恰好等于实测误差"的判据（且谎称 analytic），
  引擎必须强制判 `empirical` 并剔除出可裁集合，同时结构校验必须抓到"可裁标注说谎"。

### 2.1 一条判据长什么样

```json
{
  "criterion_id": "analytic/euler_growth/bound",
  "quantity": "discretization error bound of probe euler_growth",
  "comparator": "<=",
  "threshold": 0.16989261427869032,
  "source_type": "analytic",
  "source_ref": "tools/criteria/probes.py#sha256:8ef2f60464d624bd4591d513b2116999220955778ca7a41577144d4f283a618e#probe:euler_growth",
  "source_note": "显式 Euler，y'=y，先验界 (e/2)h；常数 C 由该问题标准误差展开给出，可手算复核",
  "depends_on": [
    {
      "param": "h",
      "exponent": 1.0
    }
  ],
  "derivation": {
    "expression": "C*h**1",
    "coefficient": 1.3591409142295225,
    "reference_h": 0.125
  },
  "probe": "euler_growth",
  "review_date": "2026-11-09",
  "notes": "解析来源：先验误差界，可重推导 threshold(h)",
  "decidable": true
}

{
  "criterion_id": "json/config/proposal_gate.json#/contract/min_criteria",
  "quantity": "config/proposal_gate.json/contract/min_criteria",
  "comparator": ">=",
  "threshold": 3.0,
  "source_type": "heuristic",
  "source_ref": "config/proposal_gate.json#sha256:780c42631df4c8ec84adf4c7f76e068f3098438cf15509c53a7ba8613df3cd53#/contract/min_criteria",
  "source_note": "配置值，源文件内无文献/规范/解析出处 → 保守判 heuristic（不可裁）",
  "depends_on": [],
  "derivation": null,
  "probe": null,
  "review_date": "2026-11-09",
  "notes": "自动入册（json 抽源）；比较符由键名规则推断",
  "decidable": false
}
```

`source_ref` 的指针段是可复核的落点（JSON Pointer / 符号名+行号 / 探针 id）；
`decidable` 是**必须与引擎判定一致**的显式标注——标注说谎会被结构校验判红。

## 3. 结果（全部程序化注入，见 `artifacts/criteria/threshold_registry_report.json`）

| 判据 | 判定 | 数字 |
|---|---|---|
| C1 | PASS | 56 条入册（≥30）；source_type 缺失率 0.0；heuristic 44 条全部标不可裁 |
| C2 | PASS | 4/4 条解析判据实测指数最大偏差 0.053456 ≤ 0.3 |
| C3 | PASS | 20/20 注入判红（检出率 1.0） |
| C4 | PASS | 20 次格式变更误报 0（假阳性率 0.0） |
| C5 | PASS | 4 条判据的 threshold(h) 在 h/2、h/4 上比值 ∈[0.5, 2.0]，全部同向 |
| C6 | PASS | 有来源阈值占比 1.0；可派生阈值占比 0.071429；可裁占比 0.214286；来源腐烂控制 OK→SHA_DRIFT |
| C7 | PASS | 同义反复判据被判 empirical 且剔除；标注说谎被抓到 = True |

### 3.1 来源类别分布（C1）

| source_type | 条数 |
|---|---|
| analytic | 4 |
| literature | 0 |
| standard | 8 |
| empirical | 0 |
| heuristic | 44 |

`heuristic`=44 与 `empirical`=0 全部显式标 `decidable=false`
（漏标 0 条）；`source_type` 缺失率 0.0。

### 3.2 派生一致性（C2）

| criterion_id | 声明 p | 实测 p | 偏差 | R² | 判定 |
|---|---|---|---|---|---|
| `analytic/euler_growth/bound` | 1 | 0.9465 | 0.0535 | 0.999391 | PASS |
| `analytic/midpoint_exp/bound` | 2 | 1.9996 | 0.0004 | 1.000000 | PASS |
| `analytic/simpson_exp/bound` | 4 | 3.9982 | 0.0018 | 1.000000 | PASS |
| `analytic/trapezoid_exp/bound` | 2 | 1.9998 | 0.0002 | 1.000000 | PASS |

最大偏差 0.053456（容差 0.3）。

**拟合窗口敏感性**（衰减指数是窗口的函数，引擎必须把这个依赖说清楚）：
固定下拟合档数 levels=6，只挪最粗档 h0：

| probe | 声明 p | h0 | 实测 p | 绝对偏差 | 相对偏差 | 触及舍入地板 |
|---|---|---|---|---|---|---|
| `euler_growth` | 1 | 0.25 | 0.94654 | 0.05346 | 5.35% |  |
| `euler_growth` | 1 | 0.0625 | 0.98557 | 0.01443 | 1.44% |  |
| `euler_growth` | 1 | 0.015625 | 0.99632 | 0.00368 | 0.37% |  |
| `midpoint_exp` | 2 | 0.25 | 1.99957 | 0.00043 | 0.02% |  |
| `midpoint_exp` | 2 | 0.0625 | 1.99997 | 0.00003 | 0.00% |  |
| `midpoint_exp` | 2 | 0.015625 | 2.00000 | 0.00000 | 0.00% |  |
| `simpson_exp` | 4 | 0.25 | 3.99824 | 0.00176 | 0.04% |  |
| `simpson_exp` | 4 | 0.0625 | 3.99718 | 0.00282 | 0.07% |  |
| `simpson_exp` | 4 | 0.015625 | 3.86477 | 0.13523 | 3.38% | 是 |
| `trapezoid_exp` | 2 | 0.25 | 1.99975 | 0.00025 | 0.01% |  |
| `trapezoid_exp` | 2 | 0.0625 | 1.99998 | 0.00002 | 0.00% |  |
| `trapezoid_exp` | 2 | 0.015625 | 2.00000 | 0.00000 | 0.00% |  |

一阶探针在粗窗口（h0=0.25）上只测到 0.94654（相对偏差 5.35%），窗口收细到
h0=1/64 后收敛到 0.99632（0.37%）：这是 **O(h) 前渐近修正项**的已知行为，
不是引擎误差。反方向也有边界：`simpson_exp`（h0=0.015625） 处的最小误差已低于双精度
舍入地板（1.33e-15），拟合斜率被舍入残差污染、偏差反而回升——**高阶探针的窗口必须按阶数选，
不能一律取最细**。因此 C2 的容差取**绝对** 0.3（而非相对百分比），并且 `fit_window` 必须与判定
一起落盘：否则"我的阶数是 1"这句话在粗窗口下根本无法复核。

### 3.3 重推导闭环（C5）

| criterion_id | 表达式 | 预测/实测比值 | 判定 |
|---|---|---|---|
| `analytic/euler_growth/bound` | C*h**1 | h=0.0625: 1.057 ; h=0.03125: 1.029 | PASS |
| `analytic/midpoint_exp/bound` | C*h**2 | h=0.0625: 1.582 ; h=0.03125: 1.582 | PASS |
| `analytic/simpson_exp/bound` | C*h**4 | h=0.0625: 1.583 ; h=0.03125: 1.582 | PASS |
| `analytic/trapezoid_exp/bound` | C*h**2 | h=0.0625: 1.582 ; h=0.03125: 1.582 | PASS |

### 3.4 漂移门控制（C3/C4/C7）

- C3：20/20 次"阈值 ×1.10 而 source_ref 不变"被判红
  （检出率 1.0）。
- C4：20 次"只动格式/注释/键序/数字写法"的提交，误报
  0（假阳性率 0.0；解析失败 0）。
- C7：注入判据被判 `empirical`、`decidable=False`，
  剔除出可裁集合 = True，"标注说谎"被抓到 =
  True；正控（真解析判据）仍 `decidable=True`。
- 来源腐烂控制：对 `analytic/euler_growth/bound` 的被引文件加一个字节，
  指针状态 OK → SHA_DRIFT。

## 4. 图

![c2_derivation_consistency.png](artifacts/criteria/figures/c2_derivation_consistency.png)

*C2：四条解析判据的实测离散误差随 h 衰减（点线=按声明阶数外推）。拟合指数与声明 p 的最大偏差见报告 max_deviation。*

![c5_rederivation_closure.png](artifacts/criteria/figures/c5_rederivation_closure.png)

*C5：把 threshold(h)=C·h^p 投到 h/2、h/4 两档，与该档实测误差的比值全部落在 [0.5,2] 且与实测同向。*

![controls_and_coverage.png](artifacts/criteria/figures/controls_and_coverage.png)

*Left: correct-verdict rate of four controls (C3 drift detection 20/20, C4 zero false alarm on 20 format-only variants, C7 tautology negative control, source-ref rot). Right: C6 coverage ledger (sourced / derivable / decidable).*

## 5. 注意点与局限

1. **`source_type` 的保守性**：JSON 契约里的 `maximum`/`minimum` 判 `standard`（契约即出处），
   配置值与代码常量的出处无法自动认定时**一律判 `heuristic`**。方向上是安全的
   （宁可低估可裁面），代价是 `decidable_ratio` 只有 0.214286——
   这是本次审计的**结论**而非缺陷：现网大部分阈值目前确实没有可追溯依据。
2. **`depends_on` 为空 = 依赖量未登记**（不是"常数阈值"）。因此 `derivable_ratio`
   只有 0.071429。要把这个数抬上去，得逐条给出 C·h^p 的常数出处。
3. **`sourced_ratio`=1.0 是"抽样时点"的数**：它靠 sha256 保持诚实，
   被引文件改动而引用未更新即掉到 SHA_DRIFT（见 §3.4 控制）。
4. **C2 的实测来自合成算子**（初等函数定积分 / 一阶初值问题），不是平台真实求解器：
   本节点只证明"引擎能正确反演指数"，对真实求解器阶数的裁决属于各领域仓的验证单。
5. 漂移门只覆盖**注册表内**的阈值。未入册的散落字面量不在防区——扩大入册是后续工作。
6. 删除判据为 YELLOW（非阻塞），因为清理合并本身合法；但删除会让相应能力失去判据，
   账本里 `n_total` 会掉，报告侧可据 C6 数字发现。
7. **语义指纹的量化粒度**：数值按 12 位有效数字归一（这是 C4「换数字写法不算改动」
   的代价）。因此相对改动小于 ~1e-12 的阈值篡改看不见——远小于任何工程意义，但属于本门的
   已声明盲区。
8. **漂移门覆盖字段**：`threshold/comparator/quantity/depends_on/derivation/probe/decidable`
   任一变化而无 `source_ref` 变更即红；`review_date` 单改视为复核（不红）。
   反事实由 `tests/test_threshold_registry.py::test_drift_red_on_decidable_label_flip` 钉死。
9. **未知探针不崩门**：判据绑定了不存在的探针时，派生/闭环给出 `UNCHECKED_UNKNOWN_PROBE`
   并判红（`test_unknown_probe_is_reported_not_crashing`），不允许"探针找不到 ⇒ 跳过检查"。

## 5.1 审计链（本报告所有数字可回放到这些字节）

| 产物 | bytes | sha256 |
|---|---|---|
| `data/criteria/threshold_registry.json` | 43299 | `d9e1695d18ac7286d9c1031333ba0269130f0f0bf1d7672c4c1b2cdab81682f6` |
| `data/criteria/threshold_registry.lock.json` | 28112 | `d1f15bd4b121155ce07db5818308840f10bf840b9eb7ea04630713ad9a03aac3` |
| `schemas/threshold_registry.schema.json` | 6073 | `8f3b803c5296712ec48908c7b42e59fe6ab463f5d3b9b2b0a88d5aed05a6d3b7` |
| `tools/criteria/threshold_registry.py` | 38640 | `23110399c2ff932051e368e7790c5d5f529fe14861f20f5b6fbd2583bbfe22b6` |
| `tools/criteria/probes.py` | 5565 | `8ef2f60464d624bd4591d513b2116999220955778ca7a41577144d4f283a618e` |
| `tools/criteria/harvest.py` | 17238 | `c8c129b3ae2d3bd9005ae0f168f1a0756213529d7afbed21daae8c291769a71f` |
| `tools/criteria/gate_report.py` | 40307 | `fd81c0bf1ff2883d4be5c2833bc5ada17788d1d0aef8770993d1520223c948f8` |
| `tests/test_threshold_registry.py` | 23140 | `a68473e23d70fa6c3a6f0e88b04d5a83cdd2ae9430f998ac7dc9e36e32cbb366` |
| `artifacts/criteria/figures/c2_derivation_consistency.png` | 100235 | `58ffbbb3ebcc22d61a699f0444742a83c940609dc3f05ecaa1ff1845489b89f3` |
| `artifacts/criteria/figures/c5_rederivation_closure.png` | 64707 | `89c12fa44106a027cc191e83c1a3971130b275d11ed59770c357cbfdb41088c2` |
| `artifacts/criteria/figures/controls_and_coverage.png` | 69063 | `16c1aa1f04ac251a784df409178df8663208a7ec435b5af0ceef38a88b3570e6` |

## 6. 复算

```bash
python -m tools.criteria.harvest --out data/criteria/threshold_registry.json
python -m tools.criteria.threshold_registry lock --registry data/criteria/threshold_registry.json
python -m tools.criteria.threshold_registry gate --registry data/criteria/threshold_registry.json \
    --lock data/criteria/threshold_registry.lock.json
python -m tools.criteria.gate_report          # 跑 C1–C7 并写报告/图/本文档
python -m pytest tests/test_threshold_registry.py -q
```
