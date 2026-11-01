# 科研能力注册表 v1 (Research Capability Registry v1)

## 概述

能力注册表提供**可发现、可组合、可验证**的求解器能力描述体系，
填补 agent_directory 只答「谁在干什么」不答「什么求解器能做什么、参数边界在哪、凭什么信」的空白。

## 架构

```
capability/
├── capability_card.schema.json    # JSON Schema（机器可校验）
├── cards/                         # 能力卡（5 张首批）
│   ├── CAP-001-tensorlbm-cylinder-re100.json
│   ├── CAP-002-tensorlbm-suboff-resistance.json
│   ├── CAP-003-tensorlbm-multiphase-wave10.json
│   ├── CAP-004-tensorcad-dtmb5415.json
│   └── CAP-005-tensorplatform-report-generation.json
├── red_team/
│   └── red_team_demo.json         # 红队演示记录
└── README.md
```

## 能力卡字段

| 字段 | 必填 | 说明 |
|------|------|------|
| card_id | ✓ | 唯一标识 (CAP-XXX) |
| name | ✓ | 人类可读名称 |
| version | ✓ | 语义化版本 |
| repo | ✓ | 来源仓库 |
| interface | ✓ | 输入/输出/格式 |
| applicability | ✓ | 参数域/流态/几何/维度 |
| dependencies | ✓ | 代码版本/环境 |
| resource_requirements | ✓ | GPU/CPU/内存/存储 |
| **validation_evidence** | ✓ | **报告ID+冻结门md5+验证状态（必填）** |
| failure_modes | ✓ | 已知失效模式 |
| created_at | ✓ | 创建时间 |
| status | ✓ | 生命周期状态 |

## 校验门（必须可红）

1. **缺验证证据字段 → 红**：`validation_evidence` 为必填，缺则注册失败
2. **假报告ID → 红**：`report_id` 必须在 `tensor_reports` 表中存在

红队演示见 `red_team/red_team_demo.json`，两条均 PASS。

## 首批 5 卡

| 卡号 | 能力 | 仓库 | 验证状态 | 关键判据 |
|------|------|------|----------|----------|
| CAP-001 | TensorLBM 2D 圆柱 Re=100 | TensorLBM | IN_PROGRESS | C_D∈[1.28,1.42], St∈[0.160,0.172] |
| CAP-002 | TensorLBM SUBOFF 阻力 | TensorLBM | PASS | τ 唯一治愈旋钮，四树锚 1.1290741584063078 |
| CAP-003 | TensorLBM 多相 Wave-10 | TensorLBM | HONEST_FAIL | 液滴入水未过验收阶梯（诚实声明） |
| CAP-004 | TensorCAD DTMB 5415 几何 | TensorCAD | PASS | 偏差 mean 0.236m = 0.17%L |
| CAP-005 | TensorPlatform 报告生成 | TensorPlatform | PASS | 三层体系，角色门禁 |

## API 端点

- `GET /api/capability/cards` — 列出所有能力卡
- `GET /api/capability/cards/{id}` — 获取指定卡
- `GET /api/capability/cards/search` — 搜索能力卡
- `GET /api/capability/schema` — 获取 JSON Schema
- `POST /api/capability/validate` — 验证能力卡（红队门）

## 与 agent_directory 挂接

席位 claim 前可查询能力：
```
GET /directory → 增 capabilities 段
或独立只读端点: GET /api/capability/cards
```

## 验证

```bash
# 批量验证所有卡
python3 scripts/validate_capability_card.py --batch

# 红队演示
python3 scripts/validate_capability_card.py --red-team

# pytest
python3 -m pytest tests/capability/test_capability_registry.py -v
```

## 局限

- 首批 5 卡仅覆盖部分学科（阻力/几何/报告/多相），操纵/稳性/结构/振动噪声/绿色航行/冰区航行待补充
- 验证证据 md5 为占位符（报告尚未冻结），实际使用时需替换为冻结门真实 md5
- 数据库查询为离线模拟，生产环境需对接 tensor_reports 表
