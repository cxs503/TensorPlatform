# criteria_drift —— 提案判据 → 验收报告的静默降标防线（平台共性层，零领域语义）

**一句话**：把"提案里写的验收线"和"验收报告里实际用的验收线"逐条对齐并量化差异；
阈值放宽 >10%、算子降级、方向/判据族替换、容差删除、未声明删条 → 判显著漂移，
写回 `campaign_nodes` 的 `status='blocked'` + `block_reason`，DAG 不再按"通过"激活下游。

```text
提案 JSON acceptance_criteria ──┐
                                ├─► parser ─► matching ─► compare ─► DriftAudit
验收报告 JSON criteria/waivers ──┘        (精确指派)   (量化漂移)   │
                                                                   ├─► report.md / metrics.json
                                                                   └─► campaign_integration ─► campaign_nodes.block_reason
```

## 组成

| 模块 | 职责 |
|---|---|
| `model.py` | 判据数据模型（kind/op/value/unit/tolerance/compound）+ 漂移行 + `DriftAudit`（漂移度、block_reason、审计 sha256） |
| `parser.py` | 判据文法：8 种判据模式、单位归一化（3% == 0.03）、多数字判据的"被断言界"选择、复合判据（`>30% 且 <50%`）识别、具名豁免（waivers）提取 |
| `lexicon.json` | **数据**词表：判据族关键词、算子短语、单位换算、通用度量名中英别名。改词表不改代码 |
| `config.json` | **判定策略**（外置可回滚）：放宽阈值 0.10、算子降级是否显著、匹配门限、兜底配对开关 |
| `matching.py` | 同义词折叠 + 度量名中英别名 + `kind:` 机制 token；**精确指派**（bitmask DP）防"抢亲"；唯一剩余项保守兜底 |
| `compare.py` | 逐条比对与漂移量化：放宽度、算子降级、方向/族替换、容差删除/放宽、未声明删条、复合判据少一侧；生成 `impact`/`action` |
| `campaign_integration.py` | 门控写回：`verdict_transition()`（纯函数）、`apply_sqlite()`（`campaign_nodes` 镜像）、`apply_via_api()`、PG 语句文本 |
| `corpus_synth.py` | 合成标注语料（改写/单位改写/重排 + 注入缩窄 + 硬负例 + 具名豁免），标签由**渲染文本回读**计算 |
| `real_corpus.py` | 真实语料：`data/campaigns/*/nodes.json` 的人类判据 + `docs/*.md` 判据段，构造式变异（10 族） |
| `evaluate.py` | 文档级/判据级混淆矩阵、Wilson CI、逐族/逐判据族分解、**消融** |
| `run_audit.py` | 端到端：指标 → 图 → 真实节点审计演示 → 门控演示 → `metrics.json` / `report.md` / `report_payload.json` |

## 用法

```bash
# 1) 跑全流程（写 artifacts/criteria_drift/）
python3 -m tools.criteria_drift.run_audit --out-dir artifacts/criteria_drift

# 2) 代码里审计一份提案/报告
from tools.criteria_drift import compare, campaign_integration
audit = compare.audit(proposal_json, report_json,
                      proposal_ref="p474w1-n02/proposal", report_ref="p474w1-n02/report")
if audit.block:
    print(audit.block_reason())         # criteria_drift:significant=2;max_relax=0.25;...
    print(audit.recommended_actions())

# 3) DAG 驱动直接调用（退出码 0=放行 / 3=漂移阻塞 / 4=节点不存在）
python3 -m tools.criteria_drift.gate_cli --proposal proposal.json --report report.json \
    --plan-id 1 --node-id p474w1-n02 --sqlite campaign.db --requested pass

# 4) 门控写回（本地镜像；PG 用 apply_via_api / 同义 SQL）
import sqlite3
conn = sqlite3.connect("campaign.db")
campaign_integration.apply_sqlite(conn, plan_id=1, node_id="p474w1-n02", audit=audit,
                                  requested_verdict="pass")   # -> status=blocked
```

## 判定口径（与任务书一致）

| 情形 | 判定 | 理由 |
|---|---|---|
| 上界放松 `(t_new-t_old)/\|t_old\| > 0.10`；下界对称 | significant | 任务书：阈值放宽 >10% |
| `3%` ↔ `0.03`、`≤` ↔ `<=`、重排、追加更严判据 | ok | 表面改写，不是降标 |
| `<` → `<=`、`>` → `>=`、`==` → 单边界 | significant | 算子降级（边界点由不合格变合格） |
| 上界 → 下界、判据族替换（ratio ↔ count/order/p-value） | significant | 原约束被替换 |
| ± 容差删除或放宽 >10% | significant | 区间判定退化为点判定 |
| 提案判据在报告中消失且无豁免 | significant | 等于取消该检查 |
| 消失但带 `reason` + `approved_by` 的具名豁免 | declared_waiver（不阻塞） | "未声明"才叫漂移；豁免是合法出口 |
| 定性判据（无数值）删条 | 人工复核项（不阻塞） | 无法机裁，宁少报不造假 |
| 复合判据只剩一侧 | significant | 少一条约束 |

## 与相邻机制的分界（不重复造轮子）

* `tools/criteria_governance`：管**阈值本身的出处**（改阈值必须同时改出处，CI 哈希钉桩）。
  本包管**同一提案在报告里被执行成什么**，两者互补：出处门管"提案写得对不对"，本包管"执行时有没有缩水"。
* `tools/capability_drift`：能力度量随时间的漂移；本包是**一次判决内**的判据对照。
* `tools/node_acceptance`：从工件里聚合出机器判决；本包消费"提案/报告两侧判据"并只输出漂移结论。

## 实测（`artifacts/criteria_drift/metrics.json`）

* 合成语料 240 篇 / 1207 条判据：文档级准确率 0.9958、FPR 0.0000；判据级准确率 0.9975。
* 真实文本（`data/campaigns/p106w2` 105 条人类判据 + 文档判据段）构造变异 753 例：准确率 1.0000、FPR 0.0000。
* 消融：去掉豁免识别 → 真实 FPR 0.2248；去掉同义词折叠 → 合成 FPR 0.0123。

## 已知坑（实测，勿踩）

1. **单位不归一化 = 每条判据都误报**：`3%` 和 `0.03` 必须在解析期折成同一比值。
2. **贪心匹配会制造假漂移**：同族两条判据会互相抢配偶 → 一条假放宽 + 一条假丢失。用精确指派。
3. **渲染也会丢严格性**：构造测试集时 `>` 被写成 `>=`，真值就废了；标签必须从渲染文本回读。
4. **复合判据**：`占比>30% 且 merge<50%` 只取一侧是降标，不是"改写"。
5. **残留假阴性**：同族同阈值的"度量替换"（`插值误差≤1%` → `动量守恒偏差≤1%`）与"度量改名"不可区分；
   需要提案的 `metric` 字段作为第二锚点（后续工作）。
6. **真实语料是构造式变异**：真值来自构造，不代表线上已发生事故；报告不夸大。