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

## 自检

```bash
python3 -m pytest tests/test_criteria_governance.py -q      # 20 项，含 C1–C6 与两类负控
```