# 派发前磁盘准入闸门（Dispatch Disk Gate）—— 平台自愈能力

## ① 案例介绍

**问题背景**：规划师第7班 2026-11-11 实测证据显示，近 24h 抽样 1200 条 FAIL 单中
1033 条（86.1%）result 里写着 `worktree 失败: ... No space left on device`，另有 136 条
`agent_lost`。按小时看高峰在 11-10T22（325 条）、T23（184）、11-11T02（218）。

**根因**：舰队的失败墙是**基础设施型**，不是科学型。同一时段 campaign_node 类 24h
成功 1278 / 失败 1811（成功率 41.4%），说明大量科学单被磁盘写满连坐判死——账面"算法做不出来"
是伪归因。

**目标**：派发（claim/worktree 创建）之前先问磁盘：可用空间低于阈值 → **拒绝派发并给出
结构化原因**（fail-closed），而不是让 worker 在 ENOSPC 上崩掉并把单子记成 FAIL。

**参数表**：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `min_free_gb` | 5.0 GB | 最低可用空间阈值 |
| `max_used_pct` | 90.0% | 最高使用率阈值 |
| 判定逻辑 | AND | 两条件同时满足才放行 |

## ② 怎么做的

**方法**：纯函数 `precheck_dispatch(path, min_free_gb, max_used_pct) -> dict`，基于
`shutil.disk_usage`（底层 `statvfs`）读取文件系统状态，无副作用、无全局状态、无 I/O
副作用。

**流程**：
1. 检查路径是否存在 → 不存在则 fail-closed 拒绝
2. 读取磁盘使用量 → 无法读取则 fail-closed 拒绝
3. 检查 `free_gb >= min_free_gb` → 不满足则记录原因
4. 检查 `used_pct <= max_used_pct` → 不满足则记录原因
5. 两条件均满足 → `ok=True`，否则 `ok=False` + reasons 列表

**关键设置**：
- 阈值参数化、可配置注入（零领域语义）
- 复用 `tools/sustainment/worktree_gc.py` 的 `disk_pressure` 逻辑（一致性）
- GC dry-run CLI：`--root <dir> --dry-run --json`，输出各类计数与预计释放空间
- PROTECT 类（dirty/unpushed/orphan/active）永不删

**工具链**：
- `tools/disk_gate.py`：核心模块（precheck_dispatch + run_gc_dryrun + CLI）
- `tests/test_dispatch_disk_gate.py`：18 个测试用例
- `artifacts/disk_gate/live_dryrun.json`：真实工作根实测数据
- `docs/fig1_gate_flow.png`：决策流程图

## ③ 计算结果

**实测磁盘状态**（2026-11-11T07:03Z）：

| 指标 | 值 |
|---|---|
| 总 worktree 数 | 3162 |
| 可用空间 | 239.71 GB |
| 使用率 | 73.10% |
| 闸门状态 | PASS（free_gb=239.71 >= 5.0, used_pct=73.10 <= 90.0） |

**Worktree 分类采样**（前 20 个）：

| 类别 | 计数 | 处置 |
|---|---|---|
| active | 12 | KEEP |
| dirty | 4 | PROTECT |
| orphan | 3 | PROTECT |
| finalized | 1 | ARCHIVE_REMOVE |

**推算**：
- 预计可释放空间：~9.79 GB（基于采样外推）
- PROTECT 类估算：~1106 个（永不删除）

**测试覆盖**：18/18 用例全绿

| 测试类 | 用例数 | 状态 |
|---|---|---|
| TestPrecheckDispatch | 12 | ✅ 全绿 |
| TestGcDryrun | 5 | ✅ 全绿 |
| TestDomainNeutrality | 1 | ✅ 全绿 |

## ④ 结果图

决策流程图见 `docs/fig1_gate_flow.png`：

```
Dispatch Request
       ↓
precheck_dispatch(path, min_free_gb, max_used_pct)
       ↓
free_gb >= min_free_gb AND used_pct <= max_used_pct?
       ↓
   ┌───┴───┐
   ↓       ↓
 PASS    FAIL-closed
   ↓       ↓
Proceed  Structured rejection
         (reasons + suggested action)
```

**为何无场量云图**：本任务是平台基础设施能力（磁盘准入闸门），非数值计算/物理仿真任务，
不涉及场量（速度场、压力场、温度场等）计算，因此无场量云图。流程图和计数条形图足以
表达闸门逻辑和 worktree 分类分布。

## ⑤ 注意点

1. **fail-closed 设计**：路径不存在或磁盘状态无法获取时，闸门拒绝而非放行——宁可误拒
   不可误放。
2. **零领域语义**：模块内不出现任何船/冰/求解器词条，域词扫描 0 命中。
3. **阈值可调**：`min_free_gb` 和 `max_used_pct` 均为参数，可根据实际磁盘容量调整。
4. **接线点**：本模块交付"可被接线的闸门 + 接线点定位"。若平台真实派发进程不在本仓内
   （部署侧），需在 `tools/agent/dispatch_helper.py` 的 `main()` 函数中、创建 worktree
   之前调用 `precheck_dispatch()`。
5. **GC 不重建**：本单不重建 GC（避免重复建设），只做两件事：把压力门接进派发准入路径 +
   用真实数据出证据。

## ⑥ 误差判定

本任务为平台基础设施能力开发，不涉及数值计算误差。关键验证指标：

| 验证项 | 基准/期望 | 实测 | 是否达标 |
|---|---|---|---|
| pytest 用例数 | ≥ 8 | 18 | ✅ |
| 测试通过率 | 100% | 100% (18/18) | ✅ |
| 域词扫描命中 | 0 | 0 | ✅ |
| dry-run 幂等 | 逐字节一致 | ✅ | ✅ |
| PROTECT 类删除 | 0 | 0 (delete_list=[]) | ✅ |
| live_dryrun.json | 含 required keys | ✅ | ✅ |

**Honest-FAIL 说明**：本单交付"可被接线的闸门 + 接线点定位 + 最小外部修改单"。平台真实
派发进程（`tools/agent/dispatch_helper.py` 的 `main()` 函数）的接线修改未在本单执行，
因为：
1. 派发进程涉及 owner 凭据和 API 调用，修改需独立审查
2. 本单聚焦闸门核心逻辑和测试验证
3. 接线修改建议：在 `dispatch_helper.py` 的 `main()` 中、`api("POST", "/tensor/missions", ...)`
   之前插入 `precheck_dispatch()` 调用，不通过则返回结构化拒绝

接线代码示例（供人工审查后合并）：
```python
# 在 dispatch_helper.py main() 中，api("POST", "/tensor/missions", ...) 之前插入：
from tools.disk_gate import precheck_dispatch

gate = precheck_dispatch(os.environ.get("WORKTREE_ROOT", "/root/tp-agent/work"))
if not gate["ok"]:
    print(json.dumps({"status": "blocked_by_disk_gate", "gate": gate}, ensure_ascii=False))
    return 4  # 退出码 4 表示磁盘闸门拒绝
```
