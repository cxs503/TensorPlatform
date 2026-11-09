# domains/full_performance_pipeline —— 全性能评估流程端到端集成（IN61-N11）

本目录是**领域目录**：7 个物理域模块（失速/污底/导航/风载/砰击/拖曳/甲板积水）、
合成船型与海况、接口契约、验证基准、集成报告全在这里；`backend/`、`tools/`、`tests/`
不新增任何域词条（TensorPlatform 铁律：域数据经 API/配置/工件注入）。

## 目录

| 文件 | 作用 |
|---|---|
| `contracts.py` | 契约层：`Field/Contract`（名/单位/类型）、JSON-Schema 导出、逐边 `check_edge`（native/adapted/unit_mismatch/missing）、单位换算白名单 `ADAPTERS`、运行期载荷校验（缺字段/NaN/Inf） |
| `modules_core.py` | N04 导航（大圆/恒向线/航时）、N05 风载（视风合成+三分量+参考点力矩）、N02 污底（ITTC-57 摩擦线 + ITTC-78 粗糙度补贴）、N01 失速（波浪增阻动量通量渐近 + 功率平衡解析根 + Cf(V) 固定点） |
| `modules_ext.py` | N08 砰击（附加质量率压力 + 高斯超越概率）、N09 拖曳（悬链线张力/系柱拉力利用率）、N10 甲板积水（自由液面修正→有效 GM→平衡横倾）、`PlaceholderModule`（证据门自检用 MOCK/STUB 包装） |
| `providers.py` | `CFG-vessel` / `CFG-environment` 配置注入节点（passthrough，审计时与上游数据流分开统计） |
| `pipeline.py` | DAG 接线（17 边 / 1 反馈）、输入组装与来源溯源、欠松弛固定点迭代、契约审计、证据统计、全性能 KPI 汇总 |
| `references.py` | 验证基准：解析解/文献值/自洽恒等式逐项对照 + `spread_items()`（无基准项显式声明不满足 <3%） |
| `synthetic.py` + `config/synthetic_pipeline_config.json` | 合成船型 SYNTH-A/B/C、海况 SEA-1/2/3、全部经验系数（零真实船数据） |
| `report.py` | 集成报告：4 项机器判据 + 证据统计 + 证据门自检 + 6 张图 + 论文级 Markdown |
| `run_all.py` | 一键复算入口 |
| `tests/test_integration_pipeline.py` | 24 个 pytest 用例（契约/物理锚点/端到端/证据门/报告） |

## 复现

```bash
python3 domains/full_performance_pipeline/run_all.py          # 出 artifacts/IN61-N11/ + docs/
python3 -m pytest domains/full_performance_pipeline/tests -q  # 24 passed
```

产物：
- `artifacts/IN61-N11/integration_report.json`（判据数字 + 契约矩阵 + 证据统计 + 验证表 + 溯源哈希）
- `artifacts/IN61-N11/fig1..fig6*.png`
- `docs/IN61-N11_full_performance_pipeline.md`（论文级六要素报告）

## 契约判据（机器可算）

| 判据 | 口径 | 实测 |
|---|---|---|
| J1 端到端链路 | 9 节点全部跑通、载荷校验通过、反馈固定点残差 < 1e-7 m/s | 17 边贯通，残差 ~1e-7 |
| J2 数据流延迟 | 端到端纯数值计算 < 1000 ms | 最坏 ~30 ms |
| J3 接口契约 | 每个模块 requires 字段必须由上游 provides 匹配或有明确配置注入 | 63 字段：58 原生 + 5 config，缺失/错配 0 |
| J4 证据统计 | 报告含 REAL/WITHHELD/MOCK/STUB 占比，且门禁能识别占位 | 字段 REAL 87 / WITHHELD 13 / MOCK 0 / STUB 0；毒化自检 gate_ok=True |

## 证据纪律（本包的写法约定）

- 物理式优先取**可自行推导**的形式：球面几何、短波动量通量、附加质量率、静水力学、悬链线解析解；
  引用式（ITTC-57/ITTC-78）只在能用解析极限/文献值核对时才标 REAL。
- **经验系数一律外置**到 `config/*.json` 并标 `WITHHELD`（风压系数、污损生长率、波浪反射效率、
  砰击 σ_v 幅值比与阈值速度）——它们决定绝对量级，不决定实现正确性。
- 模块级证据 = 逐字段最弱项，防止单点 REAL 掩盖整体未验证。
- 无实验基准的量必须在 `references.spread_items()` 里显式列出"不满足 <3%"，不许用自洽性冒充精度。

## 已知局限

- 7 个模块是本节点在 `domains/` 下**新建实现**（IN61 上游节点的实现未随本仓落盘），
  因此只验证"这些契约下的端到端可用性"，不为上游历史证据等级背书。
- 波浪增阻为渐近模型（κ∈[0.20,0.50] 使量级离散 ±43%）、风压系数未标定、污损生长为线性占位、
  砰击峰值系数未标定 → 四者模块级 WITHHELD，需要模型试验/CFD/实船时序数据才能升级。
- 甲板积水为准静态自由液面 + 静平衡横倾（无时域进水/排水与横摇耦合）；拖曳无动态张力放大；
  砰击无结构响应耦合。