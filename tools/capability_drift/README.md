# tools/capability_drift —— 能力回归检测（Capability Drift Monitor）

代码变更后自动**识别受影响的求解器能力 → 重跑验证案例 → 与基线数字对比 → 超阈告警 →
`git blame` 定位引入退化的提交**。这是平台级共性机制，**零领域语义**（仓库铁律）：

- 能力 id、案例（pytest node）、阈值、基线数字、豁免记录全部来自 `capdrift/` 下的 JSON 工件；
- 代码只处理「变更→范围」「子进程+指标 JSON」「相对误差 vs 阈值」「HMAC 审批」「blame 解析」
  「事件/健康度渲染」，不含任何物理量名、船型、算例；
- 测试夹具统一用合成能力名 `synth.*`（一维阻尼 Jacobi 迭代核），可注入退化、可 blame 定位。

## 目录

| 模块 | 职责 |
|---|---|
| `model.py` | 注册表解析（能力/案例/指标/判据/基线/豁免）、sha256 指纹、路径→能力触发匹配 |
| `trigger.py` | `git diff --name-only` → 受影响能力（精确 / 目录前缀 / glob） |
| `runner.py` + `plugin.py` | 每案例一个 pytest 子进程，指标经插件落 JSON（拒绝 NaN/inf） |
| `detector.py` | 相对误差 vs `rel_tol`（默认 5%）；失败/缺数/无基线 → 黄（不可验证≠绿）；豁免校验 |
| `attribution.py` | 声明式 blame 定位点 → 嫌疑 commit（新→旧，去重） |
| `alerts.py` | 平台事件（含退化列表+数字对比+嫌疑 commit），jsonl 落盘 + 可选 POST（离线不丢） |
| `dashboard.py` | 绿/黄/红健康度、历史 jsonl、三张图（漂移/趋势/耗时），ASCII 标注 |
| `approvals.py` + `baseline.py` | owner HMAC 审批（key 只在环境变量），基线更新留 sha256 审计 |
| `sandbox.py` | 验收用合成沙箱仓（退化注入口 = 每个能力一条参数行） |
| `acceptance.py` / `run_acceptance.py` | A1–A8 验收跑批 → `artifacts/capability_drift/` + 报告 |
| `ci.py` | PR/push 门：退化 → 退出码 1；无受影响能力 → 退出码 0 |

## 工件格式（领域仓注入，本仓只给合成示例 `config/capability_drift/demo/`）

`registry.json`

```json
{"schema_version": 1, "repo_root": "..",
 "capabilities": [{"id": "<capability id>", "solver": "<solver repo>",
   "cases": [{"id": "<case>", "node": "verif/test_x.py::test_x", "metrics": ["<metric>"]}],
   "metrics": ["<metric>"],
   "paths": ["solver/", "shared/*.h"],
   "blame": [{"path": "solver/kernel.py", "pattern": "^RELAX = "}]}]}
```

`gates.json`

```json
{"schema_version": 1, "defaults": {"rel_tol": 0.05, "abs_floor": 1e-12},
 "capabilities": {"<capability id>": {"<metric>": {"rel_tol": 0.05}}}}
```

`baseline.json`（由 `baseline.update_baseline` 写，须 owner 签名）

```json
{"schema_version": 1, "updated_at": "...", "approval": {"kind": "baseline_update", "...": "...", "signature": "..."},
 "values": {"<capability id>": {"<metric>": 1.234}}}
```

`whitelist.json`（已知退化的显式豁免，须 owner 签名 + 到期时间）

```json
{"schema_version": 1, "waivers": [{"capability": "<capability id>", "metric": "<metric>",
  "max_rel_err": 0.06, "reason": "accuracy-for-speed trade-off", "approver": "owner",
  "approved_at": "...", "expires_at": "...", "signature": "..."}]}
```

案例侧只需一行上报数字：

```python
from tools.capability_drift.plugin import record
record("<metric>", value)   # 必须来自真实计算
```

## 用法

```bash
# 1) CI / 本地：只查受影响能力（无命中则不重跑）
python3 -m tools.capability_drift.ci --registry-dir capdrift --repo . --base origin/main
python3 -m tools.capability_drift.ci --registry-dir config/capability_drift/demo --repo . \
        --changed-files-file /tmp/changed.txt --events art/events.jsonl --history art/history.jsonl

# 2) 库调用
python3 - <<'PY'
from tools.capability_drift import model, pipeline
bundle = model.load_bundle("capdrift")
run = pipeline.monitor(bundle, changed_files=["solver/kernel.py"], workers=4)
print(run.report.health(), [v.capability for v in run.report.red])
print(pipeline.attribute_run(run, bundle))
PY

# 3) 验收 A1–A8（合成沙箱真实注入退化；约 2 分钟）
TMPDIR=/dev/shm python3 -m tools.capability_drift.run_acceptance
python3 -m pytest tools/capability_drift/tests -q
```

## 判定语义（不放行任何"没验证"的状态）

```
案例通过 + 数字在阈值内            → green
相对误差 > rel_tol                 → red（告警；有有效签名豁免则 waived）
案例失败/超时/指标缺失             → yellow（不可验证）
无基线（首跑）                     → yellow + no_baseline（不告警，提示先标定）
豁免签名无效/过期/超上限           → 当作不豁免（fail-closed）
```

## 验收判据（战役 p360w1-n10）

| 判据 | 内容 | 量法 |
|---|---|---|
| A1 | 注入退化（参数 0.60→0.65）→ 关键数字退化 >5% 且告警 + blame 命中 | 沙箱真实注入 + monitor |
| A2 | 无害变更（文档/注释/空白/死代码）零告警，FPR <1% | 100 个无害变体 |
| A3 | 10 个验证案例整轮重跑 <10 min | 全量 monitor 墙钟 |
| A4 | 基线更新须 owner 审批，签名后 `baseline.json` sha256 变化 | 4 类非法尝试 + 1 次合法 |
| A5 | `git blame` 定位引入退化的 commit，准确率 >80% | 10 次独立注入 |
| A6 | 相对误差 >5% 告警、<5% 静默，阈值可调 | 判据层 + 端到端小/大注入 |
| A7 | pytest ≥15 全绿 | 本包测试（当前 55 项） |
| A8 | 成熟化节点/分支提交/论文级报告/honest FAIL 不覆盖 | 报告 + 工件 |

## 局限

1. 受检对象必须是"能把关键数字上报出来"的验证案例；没有数字的测试无法判退化。
2. 触发是路径级启发式，文件改名/移动需同步 `registry.json` 的 `paths`/`blame`。
3. blame 定位依赖声明式定位点；一个 commit 同时改多个定位点只能给候选集（按时间排序）。
4. 平台事件投递是"尽力而为"（无队列/重试）；告警本体始终落 jsonl。
5. 白名单豁免是记录已知退化，不是修复；过期即失效。