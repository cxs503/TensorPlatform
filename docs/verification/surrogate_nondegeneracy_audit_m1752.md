# 代理预测端点「非退化」审计（mission m1752 · tp-infra-1）

> EDIT_WHITELIST: `tools/audit/`, `tests/`, `docs/verification/`, `artifacts/m1752/`
> 工件：`artifacts/m1752/surrogate_probe_snapshot.json`（原始探针）·`surrogate_verdicts.json`（裁决）·`surrogate_verdicts.md`（表）·`fig_surrogate_nondegeneracy.png`（图）
> 可复算：`python3 tools/audit/surrogate_nondegeneracy_m1752.py --probe --check --figure`；`python3 -m pytest tests/test_surrogate_nondegeneracy_m1752.py -q` → **20 passed**

## ① 案例介绍（对象 / 参数 / 为什么是这块欠账）

平台 :8001 在物理引擎（`/calculations/*`, `/resistance/predict`）之外还挂了 **20 余个"AI / 代理模型预测"端点**。
OpenAPI 里 **24 个端点自带 `[MOCK]` 标签**（诚实），但仍有 **9 个预测端点无任何标签**；而能力注册表 `audit_capabilities`
把 `ai.predict` 记为 `NOT_SUPPORTED` —— **注册表、端点、标签三方互不校验**，无人回答"这些端点是否真的随输入变化"。

以往审计（阻力 vs ITTC、稳性 vs 闭式、敞水 vs B-series…）判的是**单工况精度**。
单点精度无法回答本问题：一个退化端点（输出恒定 / 加随机噪声）可以在标定点"恰好对上"而在全工况毫无意义。
所以本单判的不是精度，而是**信息是否流动**——这是任何"预测"能力的先决条件，且**不需要任何参考值**。

算例船（与平台引擎同一船型，量纲自洽）：

| 参数 | 值 | 参数 | 值 |
|---|---|---|---|
| L / B / T | 180 / 32 / 11 m | ∇ = L·B·T·Cb | 51 955 m³（平台校验偏差 0.0004%） |
| Cb | 0.82 | 试验航速 | 7.00 m/s → 9.88 m/s（+41%） |
| 排水量（质量） | 52 000 t | 航速输入 | 引擎用 13.6 / 19.2 kn（等价 m/s） |

## ② 怎么做的（方法 / 判据 / 工具链）

工具链：`tools/audit/surrogate_nondegeneracy_m1752.py`（探针 + 裁决 + 出表 + 出图）→
`tests/test_surrogate_nondegeneracy_m1752.py`（20 项离线回归 + 变异体）。

五条**模型无关、可机器裁决**的判据（常量集中在工具内单一来源，测试直接 import）：

| 编号 | 判据 | 阈值 | 为什么成立 |
|---|---|---|---|
| D1 | 确定性：同一请求两次 → 数值叶不变 | rel ≤ 1e-9 | 隐藏 `random()` 即不可复现，直接判死 |
| D2 | 输入敏感性：只改物理输入（航速）→ 输出必须动 | rel > 1e-6 | 输出不变 = 信息被销毁 |
| D3 | 物理标度：R(V) 有效指数 ∈ 允许带 | n_eff ∈ [1.5, 8] | 摩擦 ~V^1.8、兴波 ~V^4+，任何真阻力律落此带 |
| D4 | 跨引擎比（仅力类输出） | 报告比值 | 响应**不声明力的单位**，故只报比值不判对错 |
| L1 | 标签诚实：行为退化是否在 OpenAPI summary 声明 `[MOCK]` | — | 未声明的退化 = 借暴露面 OVERCLAIM |

**降噪/防伪设计（关键）**：
1. **数值叶指纹**：判据不认字段名，只比 `numeric_leaves(body)`（bool/字符串排除），故 `rt`/`Rt`/`total_resistance`/列表包裹一律可比。
2. **输出量显式登记**：`OUTPUT_KEYS` 逐端点登记被裁决的量（`rt`、`total_resistance`…），不做"抓最大数"的猜测——否则 Reynolds 数（∝V 恰好指数 1.00）会冒充阻力，第一次跑就踩到这个坑并修掉。
3. **正/负对照（防恒真）**：正对照 = 自带 `[MOCK]` 且注噪的 `ai-models/predict/resistance` 必须被判 `MOCK_STOCHASTIC`；负对照 = 真引擎 `calculations/resistance` 必须 D1/D2/D3 全过。二者同时成立才置 `detector_non_vacuous=True`；7 组合成变异体（恒定/噪声/错误标度/未声明/对照探针/不可达）各自落在自己的分类上。
4. **公平性对照**：被判退化的两个端点额外打"更强对照探针"（5 节点图 / 换 `prediction_type` / 改 horizon），防止"退化"只是我输入太简陋的假象——结论在全部 lever 下均不变。

## ③ 计算结果（数字由工件程序化产出）

探针：9 端点 × (base / base_repeat / speed_up / 对照) = 23 次实时请求，快照 sha256 前 16 位 `b6ece79e1afa5640`。

| 端点 | 角色 | 裁决 | D1 | D2 | D3 n_eff | 已声明 |
|---|---|---|---|---|---|---|
| `ai-models/predict/resistance` | 正对照 | **MOCK_STOCHASTIC** | FAIL（max rel 2.9e-3） | PASS | 1.97 | MOCK |
| `pinn/resistance` | 对照 | HEURISTIC_FORMULA_DECLARED_MOCK | PASS | PASS | 2.04 | MOCK |
| `calculations/resistance` | 负对照 | BEHAVES_AS_MODEL | PASS | PASS | 2.85 | — |
| `resistance/predict` | 主体 | BEHAVES_AS_MODEL | PASS | PASS | 2.20 | — |
| `propulsion/predict` | 主体 | BEHAVES_AS_MODEL | PASS | PASS | 3.00 | — |
| `lbm/predict` | 主体 | BEHAVES_AS_MODEL | PASS | PASS | 1.92 | — |
| `transformer/resistance` | 主体 | BEHAVES_AS_MODEL | PASS | PASS | 2.03 | — |
| `gnn/predict` | 主体 | **MOCK_DEGENERATE** | PASS | **FAIL（3 条 lever 全 0 变化）** | n/a | **未声明 ⚠️** |
| `digital-twin/predict` | 主体 | **MOCK_DEGENERATE** | PASS | **FAIL（2 条 lever 全 0 变化）** | 0.00 FAIL | **未声明 ⚠️** |

分类计数：`MOCK_DEGENERATE 2`、`MOCK_STOCHASTIC 1`、`HEURISTIC_FORMULA_DECLARED_MOCK 1`、`BEHAVES_AS_MODEL 5`；
`detector_non_vacuous=True`（正对照 caught、负对照 passed）。

**跨引擎比（力类输出 / 引擎 `rt`=1525.72 kN @ 7 m/s）**——同一船型、同一航速：

| 端点 | 输出量 | 值 | 比值 | 偏离 |
|---|---|---|---|---|
| `transformer/resistance` | resistance | 331.53 | 0.217 | −78.3% |
| `pinn/resistance` | resistance | 534.66 | 0.350 | −65.0% |
| `resistance/predict` | total_resistance | 231.77 | 0.152 | −84.8% |
| `lbm/predict` | Rt | 198.76 | 0.130 | −87.0% |

四个代理端点两两相差 **1.6–2.7 倍**，且都比自家物理引擎低 **65%–87%**（平台的 15% 验收带外 4–6 倍）。
响应体**不声明力的单位**，故只报比值；但 4 个端点彼此不一致这一点与单位无关——它不是换算问题，是模型不一致。

图 `artifacts/m1752/fig_surrogate_nondegeneracy.png`：
左 = 判据矩阵（D1/D2/D3/L1 × 9 端点，绿通过/红不通过，右侧标裁决）；右 = 航速 +41% 阶跃的输出响应曲线（对数纵轴，平线/抖动线即退化）。

## ④ 注意点（坑 / 局限 / 适用范围）

- **"抓最大数"会自我欺骗**：首版用 `max|numeric_leaf|` 选输出量，选中的是 `reynolds_number`（∝V 精确指数 1.00），把真引擎也判成标度异常。改为 `OUTPUT_KEYS` 显式登记 + 后缀解析后结论才成立——**判据必须点名被裁决的量**。
- **判"退化"必须穷尽 lever**：`gnn/predict` 首轮只改 `prediction_type` 就判退化，我加打 5 节点图对照后仍 0 变化，结论才站得住；工具里已把"取全部对照探针的最大变化"写成规则（`best_changed`）。
- **`performance/predict`（multipart 文件上传）与 `vibration/predict`（必填 query `ship_id`）无法被本电池驱动**，已作为 `interface_notes` 写进快照，属**未判**而非**通过**——不静默丢弃。
- **单位缺失是硬限制**：D4 只能给比值。平台若在响应里加 `unit` 字段，D4 就能升级为 15% 判据（建议改进项）。
- **本单不修任何端点**：`gnn/predict`、`digital-twin/predict` 的退化是**判定**不是**修复**；修复单的口径应是"输出必须随物理输入变化，或在 OpenAPI 声明 `[MOCK]`"。

## ⑤ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| D1 复现性（真引擎 rt） | 同请求重发 | 0 个数值叶变化（rel 0） | **是**（0 偏差） |
| D3 有效指数（真引擎） | 物理允许带 [1.5, 8] | n_eff = 2.85，落带内 | **是**（带内，非误差量） |
| 裁决可复算一致性 | 入库 `surrogate_verdicts.json` vs pytest 离线重算 | 9/9 端点分类一致、4 判据逐项一致 | **是**（0 不一致） |
| 代理端点 vs 引擎 R_T | 引擎 `rt`=1525.72 kN | 比值 0.130–0.350（−65%…−87%） | **否**（远超 15% 门；且单位未声明，比值只作诊断） |
| 判据非恒真 | 1 正对照 + 1 负对照 + 7 变异体 | 全部命中预期分类 | **是**（0 漏判/误判） |

诚实声明：**D4 的偏差数不作精度结论**（无单位），本单不主张"代理端点精度不合格"，只主张"它们彼此不一致且未随输入充分变化"。

## ⑥ 复现

```bash
python3 tools/audit/surrogate_nondegeneracy_m1752.py --probe --check --figure   # 实时探针 → 裁决 → 图
python3 -m pytest tests/test_surrogate_nondegeneracy_m1752.py -q                 # 20 passed（离线，无网络）
```

建议下一步（给后续单的口径，不在本单范围）：
1. `gnn/predict`、`digital-twin/predict` → 要么接真模型，要么在 summary 加 `[MOCK]`（L1 即可翻绿）；
2. 所有预测响应加 `unit` 字段 → D4 从"比值诊断"升级为 15% 硬判据；
3. 把 `audit_capabilities` 的 `ai.predict = NOT_SUPPORTED` 更正为 `MOCK`（24 个 `[MOCK]` 端点是既成事实），并把本工具的 `--check` 接进 CI 防回归。