# 战役证据交付协议 v1（campaign evidence protocol）

**目的**：让战役集成节点（N10 这一类）能**机器复算**上游节点的交付，而不是复述上游报告里的形容词。
协议不规定领域内容，只规定「数字放哪里」——所以它对任何战役、任何仓库同样成立。

## 1. 交付位

```
<work_root>/m<mission_id>/artifacts/<evidence_dir>/<node_key>.json
```

- `<work_root>`：战役工作根（例如 `/root/tp-agent/work`）；
- `<mission_id>`：节点自己的 mission；
- `<evidence_dir>`、`<node_key>`：由战役的来源配置（`data/campaign_integration/*.sources.json`）注入。

**只认这个位置**。工作树里散落的旧 artifact 不参与判定——它们的字段命名各异，
模糊匹配等于给「凑数」开口子。集成节点按此位置收集，收集不到就如实记录
`delivery.delivered=false`（缺就写缺，不补值）。

## 2. 文档结构（必含字段）

```jsonc
{
  "claim":     { "delivered": true },                  // 上游自述：确实交付
  "delivery":  { "delivered": true, "artifact_count": 1,
                 "artifacts": [{"path": "artifacts/study.json", "sha256": "…", "bytes": 1234}] },
  "pytest":    { "returncode": 0, "passed": 42, "failed": 0 },
  "acceptance":{ "declared_total": 5,                  // 必须等于战役注册表 metadata.acceptance_criteria 条数
                 "covered_values": 5,                  // 被可复算数值覆盖的条数
                 "claim_all_pass": true,               // 自述全绿；若覆盖数 < 声明数 → claim_mismatch
                 "items": [{"id": "a", "statement": "…", "measured": 0.8, "limit": 1.0}] },
  "interfaces": {                                      // 仅当本节点是某条接口契约的一端
    "ice-load-timeseries": {
      "axis": {"field": "t", "unit": "s", "samples": [0, 0.1, 0.2]},
      "quantities": {"ice_load": {"unit": "N", "samples": [1.0e5, 1.2e5, 1.4e5]}}
    }
  }
}
```

## 3. 集成节点怎么用它（三道反造假）

| 机制 | 做法 | 抓什么 |
|---|---|---|
| 缺就写缺 | 文档不存在 → 判据 `WITHHELD` | 「在飞」被写成「通过」 |
| 钉桩 | 检查可带 `sha256`（载荷规范化指纹） | 文档事后被改而钉桩未同步 |
| 互裁 | `claim.*` 与引擎复算对比，不一致记 `claim_mismatch` | 自述全绿但覆盖不全 / 声称交付却无 artifact |
| 反空真 | 接口契约任一侧无证据 → `WITHHELD`，不算 0 违反 | 「都没交付所以没矛盾」的假绿 |

## 4. 成熟化（feature_ledger）准入

只有**四道全过**的节点条目才允许写 `feature_ledger` 并记 `REAL`：

1. `delivery.delivered == true`；2. gate 判据通过率 ≥ 绿线；3. 无 `FAIL` 判据；4. 无 `claim_mismatch`。

其余条目进 `maturity_plan.json` 的 `deferred`（附具名原因），**不写 INSERT**。
每个 INSERT 都带 `evidence` JSON（artifact 路径 + sha256 + gate `input_digest`），
回滚脚本按 `slug + ledger_group` 精确删除。

## 5. 边界

- 协议**不**替代上游自己的验收：上游测试、领域阈值、物理判据仍归各仓 mission；
- 协议**不**对缺失做任何补全或推断（不猜单位、不猜顺序、不猜量名）；
- 版本：`v1`。新增字段须向后兼容（只加不改），否则提升版本号并同步
  `tools/campaign_integration/sources.py::build_spec` 的判据生成。