# 能力审计报告模板（由 `capability_audit report` 程序化填充）

> 本文件是可读模板，实际报告由 `tools/capability_audit/report.py` 依据
> `audit_results.json`（+ `anchor.json` / `verification.json`）自动生成到
> `artifacts/capability_audit/report.md`；文中所有数字均来自机器产物，禁止手抄。

## 元信息
| 字段 | 值 |
|---|---|
| mission_id | {{mission_id}} |
| 生成时刻 (UTC) | {{generated_at}} |
| 审计端点 | {{api_base}} |
| API 可达 | {{api_reachable}} |
| 审计能力数 | {{capabilities_audited}} |
| 探针总数 / 通过 / 失败 / 跳过 | {{checks_total}} / {{checks_passed}} / {{checks_failed}} / {{checks_skipped}} |

## 1. 判定总表
| 能力 | 端点 | 学科 | 自述等级 | 实测等级 | 不变性 | 解析 | 单调 | 虚标 |
|---|---|---|---|---|---|---|---|---|
{{verdict_table}}

## 2. 逐能力证据
{{per_capability_sections}}

## 3. 验收判据
| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| CLI 可复跑（audit/report） | 两个子命令 | {{cli_subcommands}} | {{cli_ok}} |
| 输入扰动不变性 | ≥5 能力 | {{invariance_covered}} | {{invariance_ok}} |
| 解析解对拍 | ≥3 能力 | {{analytic_covered}} | {{analytic_ok}} |
| 单调性三刀 | ≥3 能力 | {{monotonic_covered}} | {{monotonic_ok}} |
| Merkle 锚链可验证签名 | verify 全绿 | {{signature_ok}} | {{signature_verdict}} |

## 4. 图
{{figures}}

## 5. 防造假锚链
{{anchor_section}}

## 6. 局限（必须如实填写）
{{limitations}}

## 7. 误差判定（是否 <3%）
{{error_table}}