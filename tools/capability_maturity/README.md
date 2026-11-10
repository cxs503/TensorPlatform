# tools/capability_maturity —— 能力成熟化升级框架（P250-N02）

平台能力从 **MOCK / STUB / WITHHELD → REAL** 的系统性升级路径、验收判据与证据链。
**零领域语义**：能力 id、等级、基准数值全部来自台账/JSON 工件注入，代码只处理等级、
判据、误差与证据摘要（仓库铁律：域词条/域算例不进 backend/tools 的代码与测试夹具，
夹具统一用合成域名 `synth.*`）。

## 五锚流水线

| 锚 | 模块 | 做什么 | 机器判据 |
|---|---|---|---|
| 审计锚 | `register.py` | 解析 `audit_capabilities` 的**同一份渲染正文** → 审计表 | A1：与工具输出对称差 = 0，21/21 行 |
| 路径锚 | `paths.py` | 每项未达 REAL 的能力出四阶路径 `algorithm→unit_test→benchmark→evidence_register` | A3：覆盖率 100% |
| 判据锚 | `criteria.py` | 冻结容差 15% / 用例下界 / 必需证据链 / 独立提供方 / 旧实现须超阈，并给 sha256 指纹 | 判据预声明不可事后调 |
| 基准执行器 | `executor.py` + `validators.py` | 实现/旧实现/独立参考三方跑同一批用例，逐例算误差 | A2：误差 ≤15% 且提供方不同 |
| 证据链 | `chain.py` + `engine.py` | 每件工件按内容 sha256 入账，缺件不许升级；REAL 能力 no-op（负控） | A4 / A5 |

## 用法

```bash
python3 -m tools.capability_maturity.run_acceptance   # 全流程 + 工件 + 图 + 验收报告
python3 -m pytest tools/capability_maturity/tests -q  # 单元测试
```

产物：
`artifacts/capability_maturity/`（audit_table / upgrade_paths / evidence_chain_report /
criteria_predeclaration / acceptance_report，全部带 sha256）与
`docs/reports/capability_maturity/*.png`、`docs/reports/P250_N02_CAPABILITY_MATURITY_UPGRADE.md`。

## 裁定规则（顺序即优先级，无默认放行）

```
already REAL        → no_op_already_real      （负控：等级不变）
域边界外（实现不在本仓）→ blocked_domain_boundary
判据未过            → rejected_criteria / rejected_not_independent / rejected_insufficient_cases
证据链缺件          → rejected_chain_incomplete
以上皆过            → promoted（等级 → REAL）
```

引擎**只升不降**：升级流水线无权降级；降级必须走台账 OVERCLAIM 对账路径，
避免「用升级掩盖退化」。

## 局限

1. 框架裁决的是「证据等级」，不是物理精度本身——能力真实精度取决于领域仓提交的基准数据质量。
2. 船舶域能力（20 项未达 REAL）实现位于领域仓，本仓只出路径与判据并显式标 `blocked`；
   本仓完成的 3 项升级是**框架验收对象**（合成域），不是船舶域能力声明。
3. 相对误差在参考值零点无定义，判据里预声明 `zero_reference_floor` 作零点尺度。
4. agent 侧 `audit_capabilities` 可能来自另一个检出的台账（跨检出差异），A1 按本仓同源口径裁决，
   差异登记在 `audit_table.json::live_tool_observation`。
