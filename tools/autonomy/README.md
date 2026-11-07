# 自主度退出仪表盘（IN20-R7）

渐退令的度量面：把「系统还需要人类多少」变成三个可复算的账目。**零领域语义**——本包只读平台记账表，不认识任何物理域概念。

```bash
# 基线（写工件 + 记分板 + 图 + 入编年史）
TP_DAEMON_ROOTS=/root/tp-agent \
python3 -m tools.autonomy.dashboard baseline \
  --window-hours 24 \
  --out artifacts/autonomy/autonomy_baseline_2026-11-07.json \
  --markdown docs/reports/autonomy_baseline_IN20_R7.md \
  --figure artifacts/autonomy/autonomy_baseline_IN20_R7.png \
  --chronicle --interactive-user-ids 2

# 回读趋势
python3 -m tools.autonomy.dashboard trend --limit 20

# 测试
python3 -m pytest tests/test_autonomy_dashboard.py -q
```

## 指标定义

| 指标 | 定义 | 数据源（SQL 见工件 `sql[]`） |
|---|---|---|
| **M1 无人类输入续航** | 评估时刻 − 最近一次人类侧动作时刻（小时） | `max(decided_at)` / `max(created_at)` 三源取最大 |
| **M2 人类干预次数/日** | 窗口内人类侧动作计数，按 `owner` / `coordinator` 分列，折算 24h | 同上三源，窗口 `[since, until)` |
| **M3 机制覆盖率** | `covered / 8`，八项常设职能：补给·调节·放门·合并·收割·记录·自愈·文档 | daemon 产物存在性 + 各职能活动证据 SQL |

### 角色类（actor class）判定规则

平台由两类身份驱动，本仪表盘在**数据层**而非叙述层分开它们：

- **交互通道身份**（`--interactive-user-ids`，默认 `2`）：人类 owner 与 AI 协调者的控制台通道 → 记为人类侧动作。
- **无人值守通道**（daemon 服务身份）：自动派发、自动放门、自愈等 → 不计入人类动作。

人类侧动作源：

| key | 角色类 | 语义 |
|---|---|---|
| `owner_adjudication` | owner | 人工审批落槌（`tensor_approval_requests.decided_by`） |
| `owner_dispatch` | owner | 交互通道下发任务（`tensor_dev_missions.created_by` + `origin='owner'`） |
| `coordinator_session` | coordinator | 协调者开新会话（`tensor_agent_sessions.started_by`） |

### 覆盖率判定（严格）

```
covered = daemon 产物存在 AND 窗口内活动证据 > 0
只有 daemon、无活动 → STUB   （装了但没跑/没留下证据）
只有活动、无 daemon → MOCK   （靠人工或仓外脚本在做）
```

daemon 搜索根：仓库 `ops/`、`tools/`，加 `TP_DAEMON_ROOTS`（`os.pathsep` 分隔）。
遍历有界（深度 4）且剪掉 `.git/`, `work/` 等，避免把兄弟工作树当成本平台 daemon。

## 证据分级

- `REAL`：查询成功且有数据。
- `WITHHELD`：查询失败或该源无任何记录 —— **绝不补零、绝不外推**；缺证据就报缺证据。
- 工件 `metrics.*.status` 与 `mechanisms[].grade` 逐项落账，SQL 原文进 `sql[]` 供 `psql` 复算。

## 编年史

`--chronicle` 追加一行 `chronicle_snapshots`，payload 里 `autonomy` 子对象含三指标与未覆盖项；
趋势查询：

```sql
SELECT ts, payload->'autonomy' FROM chronicle_snapshots
WHERE payload ? 'autonomy' ORDER BY ts DESC LIMIT 20;
```

## 已知局限

1. 角色类是**身份规则代理**：只要人类与协调者共用同一服务身份，二者无法在 SQL 层再分；本仓以「不同身份 = 不同通道」为界，规则落账在 `provenance.actor_class_rule`。
2. **记录**职能当前无仓内 daemon（采集器在仓外），故记 `MOCK`；把这行升到 `REAL` 需要把周期采集器落进仓库。
3. 覆盖率只证明「有证据」，不证明「质量」；`STUB` 项需人工复核是空转还是窗口无触发。
4. 图与记分板的数字全部由同一份工件渲染，不允许手抄。