# SN-01 回填：窗口内 failed=17 / honest-FAIL=0，负结果未入 S-N 账本

- 质询 occ_id：`SN_TRACK:SN-01@2026-11-07#4`（mission 5312）
- 结论：**负结果入账率 ≈ 0，判定为未入账（分支 c/b 混合）**；先出度量再决定是否立单，本单按"证据不足立单"关闭。

## 证据（探针原文）

| 信号 | 值 | 来源 |
|---|---|---|
| missions_failed_window（7 日内 failed 终态） | 17 | `GET /tensor/missions?limit=200` |
| missions_succeeded_window | 153 | 同上 |
| missions_honest_fail_window（result 含 FAIL 判定） | **0** | 同上 |
| playbook_entries | 15 | `/root/tp-agent/AGENT_BOOTSTRAP.md`（PLAYBOOK 节） |

## 判定

- 17 件 failed 单里，`result` 无一条带显式 FAIL verdict ⇒ 分支 (c)「随 worktree 丢弃」风险成立：
  失败只体现在 status=failed，不携带失败原因/教训字段。
- PLAYBOOK 有 15 条判例，但**无一条以 mission_id 形式回指**，无法机械核对"哪件失败进了账"。
- 因此"负结果入账率"当前不可计算（分子不可定），这是**度量缺口**而非单纯质量缺口。

## 处置（为何不立即立单）

立"经验蒸馏修补单"需要先有可计算的入账率；在 failed 单不携带 verdict/lesson 字段前，
任何蒸馏管线都只能靠人工挑。故本单记为 **closed（负结论 + 度量缺口）**，并把缺口写入机制文档，
待 IN27-H6（判例蒸馏）落地时以 `mission.result.lessons` 字段为前提对接。

## 建议字段（下期质询复核用）

`mission.result.lessons: [{kind: "trap|playbook", text, evidence_path}]` —— 有它则入账率 = 
`count(result.lessons != []) / count(status=failed)`，可机器判定。