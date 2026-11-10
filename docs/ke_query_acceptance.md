# KE Query 验收测试记录（mission 10316 / FEAT-TK-FEEDBACK）

- 执行时间：2026-11-10T11:43:55
- 仓库/分支：TensorPlatform / agent/m10316
- KE 引擎：`http://127.0.0.1:8021`，`GET /` 返回 `{"service":"TensorKnowledge Engine API","version":"1.0.0"}` → **引擎在线**。
- **工具可用性声明（诚实）**：本 harness 会话未暴露 `run_ke_query` 工具（可用工具仅 list_dir/read_file/write_file/run_shell/call_ship_api/audit_capabilities/run_cluster/finish），无法按工具名直接调用。为保证验收单可执行且结果真实，下列调用全部由脚本直连 `run_ke_query` 所包装的同一后端 **KE Engine HTTP API**；子命令语义与单子要求一一对应（search→`/search`；task-kit→`/search`+`/query/claim-evidence` 组装 claim+evidence kit；lineage→`/query/chain`、`/ko/{id}`、`/query/claim-evidence`）。**没有伪造任何返回。**

## 调用记录

| # | 子命令 | 查询/参数 | 命中数 | 命中 id（前若干） |
|---|---|---|---|---|
| 1 | search | q="free energy" | 10 | tkc:oss-lbm:fing-chemical-potential-sign-equivalence, tkc:oss-lbm:ludwig-ch-fd-evolution, tkc:oss-lbm:ludwig-fe-vtable-contract, tkc:oss-lbm:ludwig-force-methods, tkc:oss-lbm:ludwig-sigma-droplet-calibration |
| 2 | search | q="lattice" domain="lbm" | 0 | （无） |
| 3 | search | q="lattice" domain="lattice-Boltzmann-method"（别名纠正重试） | 10 | tkc:oss-lbm:fing-chemical-potential-sign-equivalence, tkc:oss-lbm:fing-free-energy-calibration, tkc:oss-lbm:fing-guo-forcing-chemical-potential |
| 4 | task-kit | goal="phase field free energy calibration"（字面整串） | 0 | （无） |
| 5 | task-kit | goal="phase field free energy calibration"（分词 phase-field 后组装 kit） | 8 | tkc:oss-lbm:fing-chemical-potential-sign-equivalence, tkc:oss-lbm:fing-free-energy-calibration, tkc:oss-lbm:fing-guo-forcing-chemical-potential, tkc:oss-lbm:fing-mobility-fixed-relaxation, tkc:oss-lbm:fing-validation-no-angle-measurement |
| 6 | lineage | ko_id="tkc:oss-lbm:fing-chemical-potential-sign-equivalence" | 2 | tke:oss-lbm:fing-mu-fingering[supports], tke:oss-lbm:fing-mu-validation[supports] |
| 7 | lineage | ko_id="tke:oss-lbm:fing-mu-fingering" | 1 | tke:oss-lbm:fing-mu-fingering kind=evidence domain=None |

## 关键返回摘要

1. **search `free energy`**：10 条；首条 `tkc:oss-lbm:fing-chemical-potential-sign-equivalence`（claim，domain=lattice-Boltzmann-method）。
2. **search `lattice` domain=lbm**：0 条 — **0 命中**。原因：KE 的 domain 枚举值是全称 `lattice-Boltzmann-method`，没有 `lbm` 别名。纠正重试 domain=`lattice-Boltzmann-method` 得 10 条（前 3：tkc:oss-lbm:fing-chemical-potential-sign-equivalence, tkc:oss-lbm:fing-free-energy-calibration, tkc:oss-lbm:fing-guo-forcing-chemical-potential）。**缺陷登记**：run_ke_query 应对 `lbm` 做 domain 别名映射，否则按学科缩写查询静默 0 命中。
3. **task-kit goal="phase field free energy calibration"**：字面整串检索 0 条（KE 分词器要求 `phase-field` 连字符形式，整串不命中）；分词为 `phase-field` 后 8 条，kit 首 claim=`tkc:oss-lbm:fing-chemical-potential-sign-equivalence`，其 evidence 首条（即首个 `tke:` id）=`tke:oss-lbm:fing-mu-fingering`。**缺陷登记**：task-kit 应内置分词/同义词归一，直接吃自然语言 goal。
4. **lineage ko_id=`tkc:oss-lbm:fing-chemical-potential-sign-equivalence`**：`/query/chain` 返回 claim + 2 条 evidence 边，complete=True；边：tke:oss-lbm:fing-mu-fingering[supports], tke:oss-lbm:fing-mu-validation[supports]。
5. **lineage ko_id=`tke:oss-lbm:fing-mu-fingering`**：`/ko/{id}` 返回 evidence 对象（kind=evidence，domain=None；source=None），反查所属 claim=`tkc:oss-lbm:fing-chemical-potential-sign-equivalence`。

## 结论

- KE 引擎在线，5 类要求的查询语义全部可执行，返回真实卡片 id（`tkc:` claims / `tke:` evidence）。
- 未暴露 `run_ke_query` 工具本身 → 工具接线验收 **WITHHELD**（无法验证工具层）；后端契约验收 **REAL**。
- 发现 2 个真实可用性缺陷：①`domain=lbm` 无别名归一 → 0 命中；②goal 自然语言整串无分词归一 → 0 命中。建议在 run_ke_query 层做别名表 + 分词回退。

## 原始返回（可复核）

```json
{
  "search_free_energy_count": 10,
  "search_lattice_lbm_count": 0,
  "search_lattice_full_domain_count": 10,
  "taskkit_literal_count": 0,
  "taskkit_tokenized_count": 8,
  "first_tkc": "tkc:oss-lbm:fing-chemical-potential-sign-equivalence",
  "first_tke": "tke:oss-lbm:fing-mu-fingering",
  "kit_claim": "tkc:oss-lbm:fing-chemical-potential-sign-equivalence",
  "chain_complete": true,
  "chain_links": [
    "tke:oss-lbm:fing-mu-fingering",
    "tke:oss-lbm:fing-mu-validation"
  ],
  "evidence_kind": "evidence",
  "evidence_domain": null
}
```
