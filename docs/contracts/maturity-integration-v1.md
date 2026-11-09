# 战役成熟化整合契约 v1（maturity integration contract）

适用面：任何战役 DAG 的收尾/整合节点（把上游节点已验证的能力整合成可交付工程输出）。
本契约定义**四个可机器复算的接口**——规格、证据、裁判、台账——以及报告面。
实现：`tools/maturity_gate/`（机制层）+ `backend/api/maturity_gate.py`（HTTP 无状态面）。
本文件是契约唯一真源；`tools/maturity_gate/contracts.py` 会逐节校验它是否写全
（清单见 `data/maturity_gate/contracts.manifest.json`）。

## 1. 端点契约

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/v1/tensor/campaigns/maturity-gate` | 提交「战役规格 + 证据载荷」，返回三值裁定 + 台账双射 + 阻塞清单 |
| GET | `/api/v1/tensor/campaigns/maturity-gate/spec-schema` | 规格结构自描述（比较器枚举、裁定规则） |

端点全量标识（供契约覆盖率检查逐条匹配）：

```
POST /api/v1/tensor/campaigns/maturity-gate
GET /api/v1/tensor/campaigns/maturity-gate/spec-schema
```

请求体（`contracts/maturity-gate.v1.schema.json`）：

```json
{
  "spec_id": "p54w3-maturity-integration",
  "criteria_pass_threshold": 0.8,
  "nodes": [{"node_key": "...", "repo": "...", "mission_id": 8411,
             "capability": "...", "criteria": [{"criterion_id": "a", "statement": "...",
               "checks": [{"doc": "<evidence_docs 的键>", "pointer": "criteria.a.values.rel_error",
                           "comparator": "le", "threshold": 0.1,
                           "sha256": "<可选钉桩>", "claim": "criteria.a.pass"}]}]}],
  "evidence_docs": {"<doc 键>": {"...": "证据载荷"}}
}
```

响应体关键字段：`gate_schema_version` / `input_digest` / `nodes[]`（判据与检查明细）/
`node_summaries[]`（通过率、存疑原因、自述冲突）/ `campaign`（三值裁定、阻塞项）/
`ledger`（条目 + 双射）/ `contracts`（完整性）。

**无状态**：服务端不读盘、不落库、不缓存。`input_digest` = 规格规范化 +
全部被读证据文档 sha256 的 sha256；同一输入无论来自磁盘还是 HTTP，逐字节相同。

## 2. 证据契约

上游节点把「可复算数字」写成一份文档（结构见 `contracts/maturity-evidence.v1.schema.json`），
按平台约定放在本仓 `artifacts/p54w3_evidence/<node_key>.json`（或节点已交付的机器可读结果文档，
在规格里显式映射）：

| 字段 | 含义 |
|---|---|
| `node_key` / `repo` / `commit` | 与战役节点一致；`commit` 让证据与代码树对齐 |
| `criteria.<id>.values.<name>` | 判据取值（数字/布尔/数组），`<name>` 由该判据自然定义 |
| `criteria.<id>.pass` | 上游自述结论，**只用于互裁**，不作为判定依据 |
| `pytest.{returncode,passed,failed}` | 「全链测试通过」判据的唯一取值来源（不认报告文字） |
| `criteria.<id>.artifact` / `.method` | 产出该数字的脚本/表格与方法一句话（可追溯） |

规格里的 `checks[].sha256` 可选；一旦声明，文档被改动即 `FAIL(evidence_integrity_mismatch)`。
**指纹口径**：`sha256(canonical_json(载荷))`——键排序 + 无冗余空白的 UTF-8 JSON。
刻意不用文件字节：字节指纹会被格式化/换行改动打翻，也会让「磁盘路径」与「内存裁判路径」
算出不同指纹（"线上绿、离线红" 的经典来源）；规范化后两条实现路径必然同值。

## 3. 裁定语义

判据 = 检查的**合取**：任一检查 FAIL → 判据 FAIL；否则任一检查 WITHHELD → 判据 WITHHELD；
全 PASS 才 PASS。通过率 = 通过判据数 / **声明判据数**（拆句刷分无效；少声明只会少交付）。

战役级三值：

* `PASS`：每个节点都已交付可复算证据，且 `pass_ratio >= criteria_pass_threshold`（缺省 0.8）。
* `FAIL`：任一已交付节点低于绿线，或出现 `claim_mismatch`（自述与复算相反）。失败不可被抵消、不可覆盖。
* `WITHHELD`：无低绿线节点，但仍有节点零证据（在飞/未交付）。**不知道 ≠ 通过**。

## 4. 错误码与降级

| 情况 | 表现 | 理由 |
|---|---|---|
| 规格不合法（未知键/未知比较器/重复 id/空 checks） | HTTP 400 `spec_invalid` | 配置错误必须与判据失败可区分，不得静默降级 |
| 证据文档缺失 | 判据 `WITHHELD` + `evidence_doc_missing` | 不把「没交」伪装成「没过」 |
| 取值指针缺失 | `WITHHELD` + `pointer_missing` | 上游字段命名不符时不做模糊映射 |
| 文档不可解析 | `WITHHELD` + `evidence_doc_unreadable` | 坏文件不等于零值 |
| 取值非数值/布尔 | `WITHHELD` + `value_not_numeric` | 禁止拿字符串硬比 |
| 钉桩失配 | `FAIL` + `evidence_integrity_mismatch` | 证据被事后美化必须显形 |

## 5. 台账契约

`tools.maturity_gate/ledger.py` 由判定体生成能力台账（`kind=campaign_capability_ledger`）：
每节点一条 `entry_id = cap::<node_key>`，含 `evidence_level ∈ {REAL, PARTIAL, WITHHELD}`、
`pass_ratio`、`evidence_refs[]`（文档 + sha256）、`withheld_reasons`、`claim_mismatches`。

`verify_bijection()` 双向校验：`unmapped_nodes`（节点没条目）/ `orphan_entries`（条目没节点）/
`duplicate_node_entries` / `duplicate_entry_ids` / `duplicate_capabilities`。
**没有证据就没有 REAL**；WITHHELD 表示本期未交付可复算证据，不等于「无此能力」。

## 6. 版本与兼容

* 规格 `schema_version` 与 `gate_schema_version` 均为 `1.0`；引擎只接受 `1.0`（不认识即报错，不猜）。
* 比较器枚举：`le` / `lt` / `ge` / `gt` / `eq`（数值带相对容差、布尔判身份）/ `between` / `nonempty`。
  新增比较器属**契约变更**：需同时升级 `spec.py` 的 `COMPARATORS`、本文件第 6 节与规格版本号。
* 证据文档 `evidence_schema_version = 1.0`；新增字段向后兼容（`additionalProperties: true`），
  删除/改名字段必须升版本。
* 报告面（第六节）与台账字段名一并冻结：报告生成器与台账构造器只读判定体，不读上游报告文字。

## 7. 最小可用示例

```bash
# 1) 写规格（数据工件，不是代码）：data/maturity_gate/<campaign>.spec.json
# 2) 上游各仓写证据：artifacts/p54w3_evidence/<node_key>.json
# 3) 一次跑完裁定 + 台账 + 契约 + 报告
python3 -m tools.maturity_gate.cli --work-root /root/tp-agent/work \
  --spec data/maturity_gate/p54w3_campaign.spec.json

# 4) HTTP 面同一判定（无状态，可在 CI 里对任意仓的证据载荷复算）
curl -s -X POST localhost:8010/api/v1/tensor/campaigns/maturity-gate \
  -H 'Content-Type: application/json' -d @payload.json
```

产物：`artifacts/p54w3_maturity/gate.json`（判定体）、`p54w3_maturity_ledger.json`（台账）、
`p54w3_maturity_contracts.json`（契约完整性）、`p54w3_maturity_report_check.json`（六要素检查）、
`docs/P54W3-N08_maturity_integration.md`（报告）。