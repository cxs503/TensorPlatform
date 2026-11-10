# cross_domain_integration —— 跨域工件集成与回归基线门

零领域语义的机制包：把一波战役里各节点/各仓的工件收成一份**可机器复算的账**，
校验跨仓接口契约（单位/参考系/格式），并对上一波做回归比对。

领域内容（节点清单、量名、单位、基线指针与数值）全部经 manifest JSON 注入；
本包代码只认结构。换波次只换 manifest，不改代码。

## 一句话跑法

```bash
# 1) 主门: 工件汇总 + 契约 + 基线 + 判据 + 图 → 报告 JSON
python -m tools.cross_domain_integration.cli \
  --manifest artifacts/<campaign>/manifest.json \
  --out artifacts/<campaign>_report.json \
  --figures-dir artifacts/figures \
  --doc docs/<REPORT>.md \
  --generated-utc 2026-11-10T15:55:00Z          # 固定时刻 → 同一 body sha256

# 2) 报告 markdown 由模板渲染 (数字程序化注入, 零手抄)
python -m tools.cross_domain_integration.docrender \
  --report artifacts/<campaign>_report.json \
  --template docs/<REPORT>.template.md \
  --out docs/<REPORT>.md

# 3) 测试
python -m pytest tests/test_cross_domain_integration.py -q
```

退出码：`0` 门跑通（判据可 FAIL，报告如实记录）；`2` manifest/工件读失败；
`3` `--strict` 且判据未全满足。

## manifest 要点 (`tp.cdi.manifest/v1`)

```jsonc
{
  "schema": "tp.cdi.manifest/v1",
  "campaign": {"key": "...", "plan_id": 1, "wave": 3, "started_utc": "2026-11-10T06:53:14Z",
               "baseline": {"key": "...", "plan_id": 2, "wave": 2}},
  "scan": {"work_root": "/abs/path"},                  // 各节点工作树根
  "contract": {"allowed_frames": ["none", "body", "earth", "local-vertical"]},
  "doc": {"path": "docs/X.md", "sections": {"case": "案例介绍", "method": "方法", ...}},
  "criteria_applicable": ["I1", "I2", "I3", "I4", "I5"],  // 可裁剪 (审计类 manifest 常裁掉 I3/I4)
  "nodes": [{
    "ext_id": "...", "mission_id": 1, "repo": "...", "worktree": "m1234",
    "required_artifacts": ["artifacts/x.json"],           // 硬承诺 (缺即不完整)
    "expected_artifacts": ["artifacts/*x*.json"],         // 期望 (glob)
    "quantities": [{
      "name": "relative_error", "unit": "%", "frame": "none", "tolerance_rel": 0.1,
      "adapter": {"artifact": "artifacts/x.json", "pointer": "/gates/G1",
                  "scale": 100.0, "unit_in_artifact": "1"},   // legacy 逃生口
      "baseline": {"worktree": "m0987", "artifact": "artifacts/y.json",
                   "pointer": "/gates/G1", "unit": "%", "value": 0.0696}
    }]
  }]
}
```

约定：`baseline.value` 是"声明值"，运行时从基线工件读真值并比对；
不一致即报 `baseline_value_drift`（基线不是手抄的）。

## 工件信封契约 (`tp.cdi.envelope/v1`)

生产者按此交付即自动获得 C1 检查与实测单位/参考系校验（不再需要 manifest adapter）：

```json
{
  "schema": "tp.cdi.envelope/v1",
  "node_ext_id": "...", "mission_id": 1, "repo": "...",
  "git_head": "<40 hex>", "generated_utc": "2026-11-10T15:00:00Z",
  "quantities": {"relative_error": {"value": 1.03, "unit": "%", "frame": "none"}},
  "artifacts": [{"path": "artifacts/x.json", "sha256": "..."}]
}
```

约定路径：`artifacts/<node_ext_id>/envelope.json`。

## 检查项

| code | 含义 | 结论口径 |
|---|---|---|
| C0.declaration_unit / _frame | manifest 声明自洽（单位可解析、参考系合法） | 工件未落盘时唯一可查的一面 |
| C1.required_keys / schema / node_binding / mission_binding / timestamp / repo | 信封结构契约 | FAIL 即违约 |
| C2.unit_parse | 工件声明单位可解析 | 未知符号 = FAIL（不做静默放行） |
| C3.dimension | 与参考单位同量纲（kN vs N 允许） | 量纲不同 = FAIL |
| C4.frame | 参考系 ∈ 允许集 | 越界 = FAIL |
| C5（跨生产者） | 同一量名跨仓：同量纲 + 同参考系 | 前缀/尺度差异记 `scale_variants` 警告，不判 FAIL |
| C6.schema/unit/timestamp/mission | 无信封工件的历史元数据键存在率 | 量化"契约漂移"，逐件 PASS/FAIL |

NA 语义：不适用（如 legacy 工件不带单位标签时 C2/C3/C4 记 NA），**不计入通过率分母**
——不拿 manifest 声明冒充实测。

## 判据自检

| 判据 | 阈值 |
|---|---|
| I1 工件完整性 | 全部节点 `required` 齐 + 无 `expected` 缺口 |
| I2 契约一致性 | 通过率 1.0 且无违约（检查数 0 记 FAIL） |
| I3 回归可比 | ≥1 条量"基线值 + 当前值"双侧齐全（缺值 = WITHHELD，不算通过） |
| I4 报告六要素 | 报告 markdown 含 manifest 声明的小节标题 |
| I5 产物与图 | 报告 + ≥1 图 PNG 带 sha256 |

## 审计链

* 每件工件记 `bytes` + `sha256` + git 状态 + 提交 epoch，并做**新鲜度归属**
  （未跟踪，或最近提交 epoch ≥ `campaign.started_utc`），工作树里上一波的旧文件进
  `stale_excluded` 不计入完整性；
* 报告 `seal.body_sha256` = 报告 body 的规范 JSON sha256（覆盖除 seal 外全部字段），
  图件 sha256 同列入 seal；
* 同输入 + 同 `--generated-utc` → 同一 body sha256（确定性，可复核）。