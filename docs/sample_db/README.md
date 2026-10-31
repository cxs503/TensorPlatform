# 样本数据库 Sample DB

> 对标指南「知识库 ≥1 万条样本」，本模块提供样本 schema、回填管线、及到 1 万条的量化路径。

## 文件结构

```
docs/sample_db/
├── schema.md              # 样本记录 schema 定义
├── backfill_audit.md      # 可回填量审计报告
└── README.md              # 本文档

tools/sample_db/
├── backfill.py            # 回填器原型（JSON → SQLite）
└── backfill_audit.py      # 审计扫描脚本

artifacts/
├── backfill_demo.json     # 回填演示证据
└── backfill_audit.json    # 审计原始数据
```

## Schema 概要

样本记录包含 7 大字段组：

| 字段组 | 核心字段 | 说明 |
|--------|---------|------|
| 基础标识 | `sample_id`, `mission_id`, `case_name`, `discipline`, `solver_type` | 样本唯一标识与分类 |
| 工况参数 | `reynolds`, `mach`, `tau_lb`, `lattice`, `n_steps`, `grid_dims` | 计算工况 |
| 几何编码 | `geometry_type`, `geometry_params`, `Cb`, `L_pp`, `B`, `T` | 几何描述（参数/SDF/网格） |
| 结果向量 | `results_json`, `cd_total`, `cl_mean`, `l2_rel_err`, `elapsed_s` | 计算结果 |
| 判据标志 | `verdict`, `pass_flag`, `gate_criteria`, `err_pct_max`, `within_gate` | 质量门限判定 |
| 设备指纹 | `device`, `device_name`, `compile_mode`, `finite`, `diverged` | 运行环境与精度 |
| 谱系指针 | `artifact_path`, `code_sha`, `parent_mission_id`, `round` | 溯源信息 |

详见 [schema.md](schema.md)。

## 回填管线

```
工件 JSON (sdaa_*.json)
    │
    ▼
backfill_audit.py ──→ 扫描统计 → backfill_audit.md
    │
    ▼
backfill.py ──→ 解析+抽取 → SQLite (sample_db.sqlite)
    │
    ▼
SELECT COUNT(*) ──→ 验证行数一致性
```

### 使用方式

```bash
# 审计扫描（统计可回填量）
python3 tools/sample_db/backfill_audit.py

# 回填演示（3 个案例）
python3 tools/sample_db/backfill.py

# 全量回填
python3 tools/sample_db/backfill.py  # 修改 demo_files=None
```

## 当前回填状态

| 指标 | 数值 |
|------|------|
| 工件文件总数 | 90 |
| 可解析文件 | 90 |
| **可抽取样本上界** | **125** |
| 涉及任务数 | ~70 |
| 学科覆盖 | fluid (100%) |

详见 [backfill_audit.md](backfill_audit.md)。

## 到 1 万条的量化路径

### 现状差距

- 当前: **125 条**
- 目标: **10,000 条**
- 缺口: **9,875 条**

### 扩充策略

| 策略 | 预估样本数 | 优先级 | 说明 |
|------|-----------|--------|------|
| 1. 参数扫描回填 | ~3,000 | P0 | 对现有 90 个工况做 Re/网格/τ 扫描，每工况 30-50 变体 |
| 2. 新增船型工况 | ~2,000 | P0 | Wigley/KCS/KVLCC2/DTMB5415 等标准船型，多 Fr/多装载 |
| 3. 验证报告提取 | ~1,500 | P1 | 从 validation_report*.json 提取历史验证数据 |
| 4. 外部基准接入 | ~2,000 | P1 | ITTC/DTMB 公开数据库，标准化后入库 |
| 5. 结构/多体扩展 | ~1,000 | P2 | 当前以流体为主，需补充结构振动、多体耦合样本 |
| 6. 网格收敛序列 | ~500 | P2 | 每个工况的网格收敛序列（3-5 级网格） |

### 时间估算

| 阶段 | 工作量 | 预计产出 |
|------|--------|---------|
| Phase 1 (参数扫描) | 2 周 | +3,000 条 |
| Phase 2 (船型扩展) | 3 周 | +2,000 条 |
| Phase 3 (外部接入) | 2 周 | +3,500 条 |
| Phase 4 (学科扩展) | 3 周 | +1,500 条 |
| **合计** | **10 周** | **≥10,000 条** |

### 质量保障

- 每条样本必须有 `artifact_path` 溯源
- `verdict` 必须明确（PASS/FAIL/HONEST_FAIL/UNKNOWN）
- `results_json` 保留原始数据，支持后续再分析
- 定期运行 `backfill_audit.py` 监控样本增长

## 下一步

1. [ ] 实现参数扫描回填管线（策略 1）
2. [ ] 接入标准船型几何库（策略 2）
3. [ ] 解析 validation_report*.json（策略 3）
4. [ ] 设计结构/多体样本 schema 扩展（策略 5）
5. [ ] 建立样本质量监控 dashboard

---

## 入库准入（强制，2026-10-31 起）

样本库是优化库 / 代理模型的直接训练数据源，因此入库前必须过**三门**（全过才准入，任一不过 →
结构化错误 + JSONL 审计留痕）：

1. **RunManifest 有效** —— `run_manifest.json`（或产物内嵌 `manifest` 键）+ 契约
   `contracts/tensorphysics-run-manifest-v1.schema.json` 通过 + `validation_status='passed'`；
2. **来源 mission 终态 succeeded** —— 平台档案（`GET /tensor/missions/{id}`）；解析不出即拒（fail-closed）；
3. **关键文件 sha256 复算通过** —— `manifest.outputs[]` 的 `checksum_sha256` 命中 + `size_bytes` 一致
   + `uri` 不越出 run 目录。

```bash
python3 -m src.sample_db.ingest <artifact.json> --db /tmp/samples.sqlite --api \
    --audit outputs/sample_db_admission_audit.jsonl     # 0=准入 3=拒绝
python3 -m src.sample_db.inspect <artifact.json> --run-dir <dir> --mission-id 811 --api  # 只体检
```

细节、拒绝码表、m811 正例体检与残留缺口：[`docs/e2e1_b1/README.md`](../e2e1_b1/README.md)。
**批量回填请走 `src.sample_db.ingest`，不要直接调 `tools/sample_db/backfill.py`**（裸入口尚未接线，
由 strict xfail 用例看守）。
