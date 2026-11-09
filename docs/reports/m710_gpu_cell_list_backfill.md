# 补报单 · Mission 710「GPU cell list 原生空间搜索」— 证据审计与独立结论（honest-WITHHELD）

> **补报性质**：回填报告（report-backfill），由 m8098 在派单 `reconcile_sha256=e0609dabe3ca898a` 下执行。
> 单 710 于 2026-10-31T04:07:02.601292+00:00 创建、2026-10-31T04:36:39.168542+00:00 终态 `succeeded`，但库中无已发布报告，故触发补报。
> **结论先说**：本单不是可复核的成功交付，也不是可证伪的 honest-FAIL，而是**证据链断裂（WITHHELD）**：
> 三条机器验收判据没有任何存活的可审计数值工件，且宿主无 CUDA 设备。本报告不复述原单自述数字，
> 只报告审计脚本当场能复算的事实。

---

## 1. 案例介绍（物理背景 / 几何 / 参数表）

### 1.1 物理背景

DEM（离散元）接触检测的瓶颈是邻域搜索。战役 plan 1 / 节点 V03 / M4 / performance 域的目标是把均匀网格
cell list（空间哈希）下推到 GPU kernel：格子键计算、排序、半模板邻居对展开、距离过滤全部在 device 上完成，
以支撑碎冰场 10^5 颗粒级规模。本节点的核心主张是"GPU 相对 CPU 的搜索加速比"，属**性能主张**，
必须由 benchmark 数值工件支撑，而非由"测试通过"支撑。

### 1.2 机器验收判据（DB 原文，脚本正则解析）

| 判据 | 本次核验结果 |
|---|---|
| `gpu_speedup_vs_cpu >= 10 [ratio]` | WITHHELD | 无任何存活的实测数值工件 |
| `pytest_test_gpu_cell_list == PASS` | WITHHELD | 测试文件未入库，仅 result 一条字符串 |

单记录元数据（DB 直读，非自述）：

| 字段 | 值 |
|---|---|
| mission_id | `710` |
| repo / base_ref | `TensorDEM` / `main` |
| runner / claimed_by / model | `native` / `tp-ver-3` / `None` |
| origin | `owner` |
| created_at → finished_at | `2026-10-31T04:07:02.601292+00:00` → `2026-10-31T04:36:39.168542+00:00` |
| status / fail_stage | `succeeded` / `None` |
| head_commit | `None`（空：交付从未形成 commit） |
| report_id / report_owed | `None` / `False` |

### 1.3 声称的交付面（作为被审计对象记录，未获复核）

原单自述交付 `src/tensordem/gpu_cell_list.py`、`scripts/benchmark_gpu_cell_list.py`、18 个用例，
工件清单共 6 个路径（changed_files ∪ on_disk_changes），存活情况见 §3.1。
本节点为**性能节点**，不引入新几何/新本构，故不存在"几何 Cb/L/B 先行验证"对象。

---

## 2. 怎么做的（审计方法 / 流程 / 工具链）

补报不是重做实验，而是对原单证据链做独立审计，四步全脚本化（`scripts/m710_backfill_audit.py`）：

1. **单记录取证**：`psql` 直读 `tensor_dev_missions.id=710`，程序化解析验收判据与工件清单，不依赖转述。
2. **入库核验**：在 TensorDEM 镜像 `/opt/mm-mirror/TensorDEM.git` 上 `git log --all --` 查 m710 的 4 个代码/文档路径，
   并 `git cat-file` 核验既有补报所引 commit `d3b6c914` 的真实身份（type=`不存在`）。
3. **工件可得性扫描**：对 5 个 TensorDEM 检出逐一探测 6 个声明路径。
4. **算力面取证**：`torch.cuda.is_available()` / `device_count()` / `nvidia-smi`，判定"GPU 加速比"
   判据在本宿主上是否**原则上可测**。

判据先于动手：输出是二值——每判据"有存活数值工件→可复核"或"无→WITHHELD"，无第三种含糊结论。
工具链：Python 3.11.6 / psql / git / matplotlib；宿主 `loongarch64`，`Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38`。

---

## 3. 计算结果（数字表，脚本注入）

### 3.1 声明工件可得性

| 声明路径 | 现存副本数 / 扫描根数 | 判定 |
|---|---|---|
| `artifacts/m710/speedup_plot.png` | 0 / 5 | **已丢失** |
| `docs/dem3d_gpu_cell_list.md` | 0 / 5 | **已丢失** |
| `runs/m710/benchmark_results.json` | 0 / 5 | **已丢失** |
| `scripts/benchmark_gpu_cell_list.py` | 0 / 5 | **已丢失** |
| `src/tensordem/gpu_cell_list.py` | 0 / 5 | **已丢失** |
| `tests/test_gpu_cell_list.py` | 0 / 5 | **已丢失** |

**可回收工件数 = 0 / 6。** 扫描根：`/opt/mm-verify/TensorDEM`, `/root/tensor-ecosystem/TensorDEM`, `/root/ice_docs2/TensorDEM`, `/root/ice_docs/TensorDEM`, `/tmp/_work/TensorDEM`。

### 3.2 git 入库核验

| 检查项 | 结果 |
|---|---|
| 镜像 HEAD | `` |
| `git log --all -- <m710 的 4 个路径>` | `空（无任何提交）` |
| `git log --all --grep='Merge #710'` | `5d05a1a Merge #710 (m6820) via merge-master` |
| `commit d3b6c914` 类型 | `不存在` |
| `artifacts/dem3d_neighbor_scale` / `neighbor_search.py` | `空` |

解读：m710 的交付**从未进入 TensorDEM 任何分支**（与 `head_commit=null` 互证）。因此本单既无 git
可复现的代码面，也无 git 可追溯的 benchmark 面。

### 3.3 算力面（决定"GPU 加速比"是否可测）

| 量 | 值 |
|---|---|
| torch 版本 | `2.7.1` |
| torch.version.cuda | `None` |
| `torch.cuda.is_available()` | **`False`** |
| `torch.cuda.device_count()` | **`0`** |
| `nvidia-smi` | `None`（未安装） |

结论：本宿主**无可用 CUDA 设备**。`gpu_speedup_vs_cpu >= 10` 若确曾在本机测得，其"GPU"路径只能是
CPU 上的回退/伪装执行（MOCK 级风险）；若在别处测得，则数值工件必须存活才能复核，而存活数为 0/6。

### 3.4 既有补报 9360 的归因核验（重要更正）

库中 `tensor_reports.id=9360`（mission_id=710，qa_status=`pending`，标题「补报单 · Mission 710: GPU Cell List Native Spatial Search for DEM Contact Detection」）把**另一单**成果写成了 m710：

| 证据 | 内容 |
|---|---|
| 9360 引用的 merge | `5d05a1a Merge #710 (m6820) via merge-master` → `#710` 是 **PR 号**，其主体为 **m6820** |
| 9360 引用的 commit | `d3b6c914`（`artifacts/dem3d_neighbor_scale/` + `src/tensordem/neighbor_search.py`） |
| 该 commit 路径 ∩ m710 声明工件 | **0 个**（交集为空） |
| m710 声明工件 | `artifacts/m710/speedup_plot.png`, `docs/dem3d_gpu_cell_list.md`, `runs/m710/benchmark_results.json`, `scripts/benchmark_gpu_cell_list.py` … |

即 9360 汇报的是 m6820 的**向量化 CPU cell list**（PR #710 合并的那单），m710 是**第 2 次尝试的 GPU 原生版**，
两者文件集不相交。"PR 编号 == mission 编号"的巧合是错位直接成因。

---

## 4. 结果图

![图 1](m710_evidence_audit.png)

**图 1（程序生成，sha256 见 manifest）**：(a) 六个声明工件在 5 个 TensorDEM 检出中的存活副本数——全 0；
(b) 三条机器验收判据的核验状态——全 `WITHHELD`。

**为什么没有场量云图/与解析解比较曲线**：本节点是**性能节点**（邻居搜索加速比），本身不产出场量；
原应作为结果图交付的 `artifacts/m710/speedup_plot.png` 与 `runs/m710/benchmark_results.json` 均已随
worktree 消失（§3.1）。本报告拒绝用重画/示意曲线冒充原实验数据，改以证据可得性图替代并写明缺图原因。

---

## 5. 注意点（坑 / 局限 / 适用范围）

1. **PR 号 ≠ mission 号（本次最大坑）**：`Merge #710 (m6820)` 使前一位补报者把 m6820 的 CPU 向量化 cell list
   报告挂到 mission 710（报告 9360）。补报必须用 `result.changed_files` 做集合比对，不能凭 PR 标题猜。
2. **auto-approve 放行了无工件交付**：本单以"18 passed"一条字符串通过验收，未校验 `head_commit` 为空、
   未校验 benchmark 数值工件存在——门禁缺口。建议验收门硬约束：判据必须指向可寻址数值工件(path+sha256)。
3. **worktree 回收 = 证据永久丢失**：未入 git 的工件等于不存在；性能节点的 benchmark JSON/图必须先 commit。
4. **本机无 CUDA**（device_count=0，无 nvidia-smi）：任何"GPU 加速比"若在本机产出即测量面污染。
   适用范围：本结论只否定**可审计性**，不否定"GPU 版在真机可行"的可能性——那需要新节点/新工件/新 commit 证明。
5. **不覆盖、不改库**：本报告不修改 9360、不删任何库记录；更正以 §3.4 为准，处置权交治理门。

---

## 6. 误差判定（逐项，显式回答 <3%）

| 关键量 | 基准/门槛 | 实测值 | 是否 <3% |
|---|---|---|---|
| `gpu_speedup_vs_cpu` | ≥ 10（验收门槛，非物理基准） | 无法取得（工件 0 存活） | **不适用（NOT_EVALUABLE）** |
| `force_consistency_gpu_vs_cpu` | < 1e-10 N | 无法取得 | **不适用（NOT_EVALUABLE）** |
| `pytest_test_gpu_cell_list` | PASS | 仅 result 字符串"18 passed"，测试文件未入库 | **不适用（NOT_EVALUABLE）** |

**显式回答"是否 <3%"**：**不能回答"是"。** 本单没有任何可复核的实测数值，故不存在可比较的基准/参考值，
3% 判据在此**无定义**。按证据分级，m710 三条判据一律 **WITHHELD**（既非 PASS 也非 FAIL）。
唯一可判定的硬事实：**工件可回收 0/6**、**CUDA 设备数 0**、**m710 代码路径 git 提交数 0**。

### 处置建议（给治理门）

1. 将 m710 的 `report_id` 指向本报告；9360 应标记**归因错误**（它实际对应 mission 6820 的交付面）。
2. 若战役仍需 V03，请**新开一单**并把判据改为可寻址工件：`runs/<id>/benchmark_results.json` + GPU 设备名 +
   `torch.cuda.is_available()==True` + 加速比曲线 png，并在 GPU 真机执行；不得把本单"已完成"当作能力已存在。
3. 能力台账口径：V03 = **NOT_DELIVERED / EVIDENCE-WITHHELD**，不计入已验证能力。

---

## 附录 A · 产物与复现

产物：`artifacts/m710_backfill/evidence_audit.json`（全部原始证据）、`m710_evidence_audit.png`、
`manifest.json`（sha256 清单）、本报告。
复现：`python3 scripts/m710_backfill_audit.py`（需 psql 可读 `tensorplatform` 库 + TensorDEM 镜像）。
报告正文由脚本模板注入 DB/git/文件系统直读结果，**零手抄**。
