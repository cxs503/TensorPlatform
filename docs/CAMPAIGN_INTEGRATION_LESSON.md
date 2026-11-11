# 教训蒸馏：战役集成门的三条硬规矩（campaign-integration v1）

- **规则（最小可执行形态）**：集成节点判上游交付时，只认三条——①**缺就写缺**（没有证据就记
  `WITHHELD`，不补值、不猜单位）；②**反空真**（任一侧无证据的接口契约记 `WITHHELD`，不许把
  「两边都没交付所以没矛盾」记成 0 违反）；③**REAL 四道准入**（有 artifact + 判据过绿线 +
  无 `FAIL` + 无 `claim_mismatch`，才允许写 `feature_ledger`）。
- **适用边界**：任何「多节点战役 + 上游交付需要被机器复算」的集成/验收节点（跨仓通用）。
  不适用：单点物理计算本身的精度判定（那要有实验/解析基准，见各仓 own mission）。
- **出处**：mission 13929 / 战役 p491w2 plan 9380 / 分支 `agent/m13929` /
  判定体 `artifacts/p491w2_maturity/gate.json`（`input_digest` 见文件）/
  报告 id 13402、13407 / 再次验证时间 **2026-11-11**。

## 机制（为什么这样写）

| 现实观察（本次实测） | 若不设防会怎样 | 机制 |
|---|---|---|
| 9 个上游节点里 2 个有 artifact、0 个符合证据协议 | 「有东西」被当成「数可复算」→ 假绿 | 协议块检查（`claim/delivery/pytest/acceptance`）+ 判据只读协议字段 |
| 4 条跨节点接口契约两侧全无证据 | 「0 违反」被记成 PASS → 空真洗绿 | 任一侧缺接口证据 → 契约 `WITHHELD`；总体 PASS 要求全部契约真被检查 |
| 1 条报告自述 `Mission m13845` 却挂在 mission 13925 下，且同题报告跨 4 个 mission 重复 | 按报告计分 → 报告链错配直接伪造交付 | 报告只作辅助信号；注册表契约查 `report_mission_mismatch` / `report_duplicate_across_missions` |
| mission 完成态既有 `done` 又有 `completed` | 自述互裁误报/漏报 | 完成态由配置 `defaults.completed_states` 声明，不写死单一字符串 |

## 复现 / 操作程序（下一次集成节点怎么用）

```bash
# 1) 抓注册表快照（凭据只从环境读）
python3 -c "from tools.campaign_integration.fetch import fetch_snapshot; \
from tools.campaign_integration.sources import write_json; \
write_json(fetch_snapshot(<plan_id>), 'data/campaign_integration/<campaign>.missions.json')"
# 2) 配来源（work_root / evidence_dir / 额外 glob）与接口契约工件
# 3) 一次跑完：收集 → 证据层裁定 → 契约 → 台账单 → SQL 冒烟 → 报告
python3 -m tools.campaign_integration.cli --snapshot … --sources … --contracts …
# 4) 发布报告
python3 post_report_<campaign>_n<k>.py
```

判据与阈值来自工件（`data/campaign_integration/*.json`），代码零领域语义；
`tools/campaign_integration` 的 domain-scan 命中数为 0（本次实测）。

## 反例与局限（别过度外推）

- **空真反例**：两节点都未交付时，接口契约「违反 0 条」是真话但无信息量。把它当 PASS 是本机制
  唯一真正想杀死的行为。
- **本任务无连续量基准**：「是否<3%」不适用——集成门只有计数类判据；任何把计数说成 3% 精度
  的表述都是假的。
- **不做上游转述**：证据字段命名各异时，本机制一律 `WITHHELD`，不模糊匹配（宁可少给分）。
- **SQL 未在线上库执行**：仅在同一列结构的会话级 TEMP 表上验证语法/幂等/回滚干净；
  线上写入与回滚需 owner 侧 DBA 动作。

## 回归守卫（可机器检测）

- `tests/test_campaign_integration_gate.py::test_interface_contract_withheld_when_a_side_missing`
  —— 任一侧缺证据必须 `WITHHELD`（防止退回「未检 = 0 违反」）。
- `…::test_end_to_end_claim_mismatch_is_caught` —— 自述交付为真但实测为假必须被互裁抓到。
- `…::test_maturity_requires_all_four_gates` / `test_maturity_sql_is_idempotent_and_rollback_covers`
  —— 准入四道与回滚覆盖。
- `…::test_maturity_sql_smoke_on_scratch_temp_table` —— SQL 幂等 + 回滚残差 0（无 psql 时 skip，
  不假装通过）。
- `…::test_real_campaign_gate_keeps_anti_fake_pass_invariants` —— 对提交的判定体重放：
  无证据的判据不得为 `PASS`、`WITHHELD` 不得写 `REAL`、零 REAL 时 SQL 为空操作。

## 评审状态

- 自检：18 例 pytest 全绿（含真战役重放与 SQL 冒烟），报告六要素自检 `complete=true`。
- 独立复判：**待评**（S×E 由独立席位给出；本文不预填等级）。