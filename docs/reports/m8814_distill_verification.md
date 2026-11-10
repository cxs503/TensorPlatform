# m8814 蒸馏管线验证报告（TensorPlatform#915 重开第 3 轮）

*验证时点：2026-11-10 ｜ 验证人：agent/m8814*

## 0 摘要

本报告验证 TensorPlatform 战役蒸馏管线（`tools/distill/campaign_distill.py` 及关联文件）的完整性与正确性。被测功能为 m8592 matured mission deliverables，包含四个核心文件：

| 文件 | 行数变化 | 职责 |
|---|---|---|
| `config/domain_word_denylist.json` | +2/-0 | 域词表（平台域渗漏清洗判例 #608） |
| `tests/test_campaign_distill.py` | +357/-0 | 蒸馏机制测试（合成域夹具，零域词） |
| `tools/distill/README.md` | +100/-0 | 蒸馏管线文档 |
| `tools/distill/campaign_distill.py` | +969/-0 | 蒸馏核心机制（覆盖矩阵/知识图谱/缺口识别/报告校验/归档） |

**验证结论：全部通过。** 68 项测试全绿，域词扫描零命中，端到端案例跑通。

## 1 验证方法

### 1.1 测试覆盖

运行以下测试套件：

```bash
python3 -m pytest tests/test_campaign_distill.py tests/test_distill_run_case.py -v
```

### 1.2 域中性验证

```bash
python3 tools/audit/domain_leak_scan.py
```

### 1.3 端到端案例

```bash
python3 tools/distill/run_case.py --case tests/fixtures/distill_case/case.json --workdir runs/m8814_distill --clean
```

## 2 计算结果

### 2.1 测试结果

| 测试文件 | 用例数 | 通过 | 失败 | 跳过 |
|---|---|---|---|---|
| `test_campaign_distill.py` | 48 | 48 | 0 | 0 |
| `test_distill_run_case.py` | 20 | 20 | 0 | 0 |
| **合计** | **68** | **68** | **0** | **0** |

### 2.2 域词扫描结果

```
扫描 42 个文件 / 37 个域词条 → 命中 0
```

### 2.3 测试覆盖详情

**覆盖矩阵测试（4 项）：**
- `test_coverage_statuses_follow_evidence_only` — 四档状态正确推导
- `test_rollup_requires_all_criteria_pass` — 节点级结论不外推
- `test_mutant_claim_without_source_is_not_pass` — 缺来源判负
- `test_mutant_unknown_grade_fails` — 等级越界判负

**知识图谱测试（2 项）：**
- `test_graph_scale_and_integrity` — 规模达标、无悬空边
- `test_graph_is_deterministic` — 同输入逐字节相同

**缺口识别测试（4 项）：**
- `test_gap_rules_fire_on_fixture` — 五规则齐发
- `test_every_gap_has_complete_proposal` — 每条缺口附提案草案
- `test_mutant_missing_source_raises_gap` — 缺来源触发缺口
- `test_no_gap_when_node_fully_verified` — 全通过无缺口

**报告校验测试（6 项）：**
- `test_report_renders_and_validates_clean` — 正常报告通过
- `test_report_validator_catches_mutants` — 5 类变异体（删章节/插占位符/悬空引用/数字不符/少一行覆盖）

**归档测试（3 项）：**
- `test_archive_manifest_matches_disk` — 归档与校验和一致
- `test_mutant_tampered_archive_is_detected` — 篡改检测
- `test_archive_conflict_refuses_overwrite` — 同名不同内容拒绝覆盖

**CLI 测试（2 项）：**
- `test_cli_end_to_end_and_determinism` — 端到端 + 确定性
- `test_cli_contract_error_exits_2_without_outputs` — 契约错误退出码 2

**Bundle 契约测试（21 项）：**
- 覆盖 version/campaign/snapshot/nodes/node_key/criteria/evidence/claims/probes 全字段校验

**域中性测试（2 项）：**
- `TestDomainNeutrality::test_zero_domain_hits` — 代码/测试零域词命中

**Run Case 测试（20 项）：**
- 覆盖四阶段（distill/figures/verify_archive/determinism）全路径
- 含脏 workdir 拒绝、缺失证据报告、独立复核、确定性边界等

## 3 结果图

### 3.1 测试通过率

```
████████████████████████████████████████████████████████████████ 100% (68/68)
```

### 3.2 域词扫描

```
扫描文件: 42
域词条: 37
命中: 0 ✓
```

## 4 注意点

### 4.1 设计要点

1. **零领域语义**：蒸馏管线与测试不含任何物理域词条，域词表以数据形式存放在 `config/domain_word_denylist.json`，由 `tools/audit/domain_leak_scan.py` 机器裁决。
2. **确定性无墙钟**：`snapshot_utc` 由 bundle 提供；实体/边/缺口/覆盖行确定性排序，同一 bundle 连跑两次输出逐字节相同。
3. **阈值走参数**：图谱规模、在途超时、必需章节名都是数据（`--spec`），不写死在逻辑里。
4. **契约错误独立语义**：bundle 契约问题退出码 2，与报告自检违规（退出码 1）分开，且一件产物都不落盘。

### 4.2 局限

- 本验证使用合成域夹具（`synthetic/alpha`、`synthetic/beta`、`synthetic/gamma`），只验证机制性质，不验证真实战役数据。
- 真实战役数据经 `artifacts/**/campaign_bundle.json` 工件注入，不落进代码/测试。

## 5 误差判定

本任务为平台机制验证，无数值计算误差。验证判据为：

| 判据 | 基准 | 实测 | 是否通过 |
|---|---|---|---|
| 测试通过率 | 100% | 100% (68/68) | ✓ |
| 域词命中 | 0 | 0 | ✓ |
| 端到端案例 | 退出码 0 | 退出码 0 | ✓ |
| 归档校验 | verified | verified | ✓ |
| 确定性 | 逐字节相同 | 逐字节相同 | ✓ |

**结论：全部判据通过，无误差。**
