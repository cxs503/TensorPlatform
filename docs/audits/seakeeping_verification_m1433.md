# 耐波性验证：m1433 证据链状态与 m1649 独立复算（补悬挂引用）

> 本文件补齐 `backend/services/seakeeping.py:6` 与 `backend/api/domain/ship_seakeeping.py:98` 所引用的路径。
> 原始 m1433 验证文档在合入时被质量门禁滤除（合入提交 `4d2af6b` 仅含 4 个代码文件，
> 提交信息自述"52 个文件被门禁滤除"），故此前这两处引用为**悬挂引用**：声称 machine-verified 却无工件可查。

## 1. m1433 原件状态：不可恢复

不在树内，`git log --all` 亦无该路径历史。**本文件不伪造其内容**：m1433 当时的结论
（含"legacy `/seakeeping/rao` 忽略 wave_frequency/heading/speed"）本席**未复现、不背书**。
其可核查入口是 m1433 交付的 `tools/audit_seakeeping_endpoints.py`（审计 legacy 端点，需该端点存在）；
本轮探测 `/api/v1/seakeeping/rao` 为 404，即该 legacy 端点当前不存在，D1–D3 类断言 = **WITHHELD**。

## 2. 替代证据：m1649 独立复算（可复现）

| 项目 | 内容 |
|---|---|
| 判据套件 | `tools/benchmarks/seakeeping_anchors.py`，11 项，独立数值路线 |
| 台账 | `artifacts/m1649/seakeeping_anchor_ledger.json` |
| 测试 | `tests/test_seakeeping_anchor_independence.py`（16 passed，含判据自反性注入） |
| 结论 | PASS 10/10，受门禁最差误差 1.695e-04；默认网格量化偏差 3.382e-03 |
| 全量说明 | `docs/audit/m1649_seakeeping_verification_debt.md` |
| 图件 | `docs/asset/m1649/fig1_seakeeping_anchors.png`、`fig2_seakeeping_spectra.png` |

## 3. 分级建议（替代原 docstring 的单标 REAL）

- 模型内部一致性/解析极限/缩比不变性/谱矩恒等 → **REAL**（11 项机器可裁）。
- 试验或实船 RAO 绝对精度 → **WITHHELD**（仓内无耐波试验基线；κ/ζ 与船型无关）。
- 修复前的线上可达性 → **NOT_SUPPORTED**（路由从未挂载，实测 404；m1649 已在 `backend/main.py`
  接线，待服务重启后复核）。
