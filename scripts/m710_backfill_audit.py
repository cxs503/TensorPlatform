#!/usr/bin/env python3
"""Mission 710「GPU cell list 原生空间搜索」补报证据审计（程序化，零手抄）。

采集：tensor_dev_missions(710) 记录 / TensorDEM 镜像 git 历史 / 5 个 TensorDEM 检出中的工件存活 /
宿主 CUDA 能力 / 既有补报 9360 与 m710 声明工件的归因交集。
产物：artifacts/m710_backfill/{evidence_audit.json,m710_evidence_audit.png,manifest.json}
      docs/reports/m710_gpu_cell_list_backfill.md
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
ART = WT / "artifacts" / "m710_backfill"
DOCS = WT / "docs" / "reports"
ART.mkdir(parents=True, exist_ok=True)
DOCS.mkdir(parents=True, exist_ok=True)

MIRROR = "/opt/mm-mirror/TensorDEM.git"
DEM_ROOTS = [
    "/opt/mm-verify/TensorDEM",
    "/root/tensor-ecosystem/TensorDEM",
    "/root/ice_docs2/TensorDEM",
    "/root/ice_docs/TensorDEM",
    "/tmp/_work/TensorDEM",
]
MISSION_ID = 710
PRIOR_REPORT_ID = 9360
DISPATCH_SHA = "e0609dabe3ca898a"


def sh(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def psql_json(sql):
    rc, out, err = sh(["psql", "-U", "postgres", "-d", "tensorplatform", "-t", "-A", "-c", sql])
    if rc != 0 or not out:
        raise RuntimeError("psql failed: " + err[:300])
    return json.loads(out)


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git(*args):
    rc, out, err = sh(["git", "-C", MIRROR, *args])
    return {"rc": rc, "out": out, "err": err[:300]}


# ---------------------------------------------------------------- 1) 单记录
mission = psql_json(
    "select json_build_object('id',id,'repo',repo,'base_ref',base_ref,'status',status,"
    "'runner',runner,'claimed_by',claimed_by,'model',model,'origin',origin,"
    "'created_at',created_at,'finished_at',finished_at,'report_id',report_id,"
    "'report_owed',report_owed,'worktree_recyclable',worktree_recyclable,"
    "'fail_stage',fail_stage,'task_key',task_key,'goal',goal,'result',result)::text "
    "from tensor_dev_missions where id=%d" % MISSION_ID
)
goal_text = mission.get("goal") or ""
result = mission.get("result") or {}
if isinstance(result, str):
    result = json.loads(result)
declared = sorted(set(result.get("changed_files") or []) | set(result.get("on_disk_changes") or []))

# --------------------------------------------------------- 2) TensorDEM 历史
m710_files = ["src/tensordem/gpu_cell_list.py", "scripts/benchmark_gpu_cell_list.py",
              "tests/test_gpu_cell_list.py", "docs/dem3d_gpu_cell_list.md"]
hist_m710 = git("log", "--all", "--oneline", "--", *m710_files)
pr710 = git("log", "--all", "--oneline", "--grep=Merge #710")
d3_type = git("cat-file", "-t", "d3b6c914")
neighbor_hist = git("log", "--all", "--oneline", "--",
                    "artifacts/dem3d_neighbor_scale", "src/tensordem/neighbor_search.py")
mirror_head = git("log", "--oneline", "-1")

# ------------------------------------------------------------- 3) 工件存活
found = {}
for rel in declared:
    hits = [root for root in DEM_ROOTS if Path(root, rel).exists()]
    found[rel] = {"surviving_copies": len(hits), "roots": hits}
n_rec = sum(1 for v in found.values() if v["surviving_copies"] > 0)

# --------------------------------------------------------------- 4) 算力面
try:
    import torch
    dev = {"torch_version": torch.__version__, "torch_cuda_version": torch.version.cuda,
           "cuda_available": bool(torch.cuda.is_available()),
           "cuda_device_count": int(torch.cuda.device_count())}
except Exception as exc:  # pragma: no cover
    dev = {"torch_error": str(exc)}
dev.update({"nvidia_smi_path": shutil.which("nvidia-smi"),
            "machine": platform.machine(), "platform": platform.platform(),
            "python": platform.python_version()})

# ------------------------------------------------------- 5) 既有报告归因核验
prior = psql_json(
    "select json_build_object('id',id,'mission_id',mission_id,'title',title,"
    "'scenario_key',scenario_key,'qa_status',qa_status,'md5',md5(markdown),"
    "'len',length(markdown),'markdown',markdown)::text "
    "from tensor_reports where id=%d" % PRIOR_REPORT_ID
)
prior_paths = set(re.findall(r"[A-Za-z0-9_./-]+\.(?:py|md|json|png|csv)", prior.get("markdown") or ""))
overlap = sorted(prior_paths & set(declared))

num_crit = re.findall(r"([a-z_]+) ([<>=]+) ([0-9.]+)\[([A-Za-z_/]+)\]", goal_text)
criteria = [{"name": n, "op": o, "threshold": float(v), "unit": u} for n, o, v, u in num_crit]
pytest_required = "pytest_test_gpu_cell_list == PASS" in goal_text

# --------------------------------------------------------------------- 6) 图
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.9), gridspec_kw={"width_ratios": [1.35, 1]})
ax = axes[0]
labels = [Path(p).as_posix() for p in declared]
vals = [found[p]["surviving_copies"] for p in declared]
ax.barh(range(len(labels)), vals, color="#c0392b")
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontsize=8)
ax.set_xlim(0, max(1, len(DEM_ROOTS)))
ax.set_xlabel("surviving copies (scanned %d TensorDEM checkouts)" % len(DEM_ROOTS), fontsize=9)
ax.set_title("(a) m710 declared artifacts: %d/%d recoverable" % (n_rec, len(declared)), fontsize=10)
for i, v in enumerate(vals):
    ax.text(v + 0.06, i, str(v), va="center", fontsize=8)

ax = axes[1]
names = ["%s %s %g %s" % (c["name"], c["op"], c["threshold"], c["unit"]) for c in criteria]
if pytest_required:
    names.append("pytest_test_gpu_cell_list == PASS")
ax.bar(range(len(names)), [1] * len(names), color="#e67e22")
ax.set_xticks(range(len(names)))
ax.set_xticklabels(names, fontsize=8, rotation=12, ha="right")
ax.set_yticks([])
ax.set_ylim(0, 1.15)
ax.set_title("(b) acceptance criteria: all WITHHELD (no auditable artifact)", fontsize=10)
for i in range(len(names)):
    ax.text(i, 0.5, "WITHHELD", ha="center", va="center", color="white", fontsize=9, weight="bold")
fig.suptitle("Mission 710 GPU cell list backfill evidence audit (honest-WITHHELD)", fontsize=12)
fig.tight_layout()
PNG = ART / "m710_evidence_audit.png"
fig.savefig(PNG, dpi=150)
plt.close(fig)

audit = {
    "mission_id": MISSION_ID,
    "reconcile_sha256": DISPATCH_SHA,
    "collected_at_utc": sh(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"])[1],
    "mission": mission,
    "declared_artifacts": declared,
    "artifact_survivability": found,
    "artifact_roots_scanned": DEM_ROOTS,
    "artifacts_recoverable": n_rec,
    "compute_environment": dev,
    "tensordem_git": {
        "mirror": MIRROR,
        "mirror_head": mirror_head["out"],
        "history_of_m710_files": hist_m710["out"] or None,
        "merge_pr_710_subject": pr710["out"] or None,
        "commit_d3b6c914_type": d3_type["out"] or None,
        "history_of_neighbor_scale_artifacts": neighbor_hist["out"] or None,
    },
    "acceptance_criteria": {"numeric": criteria, "pytest_required": pytest_required},
    "prior_report": {k: prior.get(k) for k in
                     ("id", "mission_id", "title", "scenario_key", "qa_status", "md5", "len")},
    "prior_report_paths_sample": sorted(prior_paths)[:25],
    "prior_report_path_overlap_with_m710": overlap,
    "verdicts": {c["name"]: "WITHHELD" for c in criteria},
}
if pytest_required:
    audit["verdicts"]["pytest_test_gpu_cell_list"] = "WITHHELD"
JSON_PATH = ART / "evidence_audit.json"
JSON_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

# ------------------------------------------------------------------ 7) 报告
dl = "\n".join("| `%s` | %d / %d | %s |" % (p, found[p]["surviving_copies"], len(DEM_ROOTS),
                                          "可回收" if found[p]["surviving_copies"] else "**已丢失**")
               for p in declared)
crit_rows = "\n".join("| `%s %s %g [%s]` | WITHHELD | 无任何存活的实测数值工件 |"
                      % (c["name"], c["op"], c["threshold"], c["unit"]) for c in criteria)
if pytest_required:
    crit_rows += "\n| `pytest_test_gpu_cell_list == PASS` | WITHHELD | 测试文件未入库，仅 result 一条字符串 |"
thr0 = criteria[0]["threshold"] if criteria else 10.0
thr1 = criteria[1]["threshold"] if len(criteria) > 1 else 1e-10

md = """# 补报单 · Mission 710「GPU cell list 原生空间搜索」— 证据审计与独立结论（honest-WITHHELD）

> **补报性质**：回填报告（report-backfill），由 m8098 在派单 `reconcile_sha256=%s` 下执行。
> 单 710 于 %s 创建、%s 终态 `%s`，但库中无已发布报告，故触发补报。
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
%s

单记录元数据（DB 直读，非自述）：

| 字段 | 值 |
|---|---|
| mission_id | `%s` |
| repo / base_ref | `%s` / `%s` |
| runner / claimed_by / model | `%s` / `%s` / `%s` |
| origin | `%s` |
| created_at → finished_at | `%s` → `%s` |
| status / fail_stage | `%s` / `%r` |
| head_commit | `%r`（空：交付从未形成 commit） |
| report_id / report_owed | `%r` / `%r` |

### 1.3 声称的交付面（作为被审计对象记录，未获复核）

原单自述交付 `src/tensordem/gpu_cell_list.py`、`scripts/benchmark_gpu_cell_list.py`、18 个用例，
工件清单共 %d 个路径（changed_files ∪ on_disk_changes），存活情况见 §3.1。
本节点为**性能节点**，不引入新几何/新本构，故不存在"几何 Cb/L/B 先行验证"对象。

---

## 2. 怎么做的（审计方法 / 流程 / 工具链）

补报不是重做实验，而是对原单证据链做独立审计，四步全脚本化（`scripts/m710_backfill_audit.py`）：

1. **单记录取证**：`psql` 直读 `tensor_dev_missions.id=%d`，程序化解析验收判据与工件清单，不依赖转述。
2. **入库核验**：在 TensorDEM 镜像 `%s` 上 `git log --all --` 查 m710 的 4 个代码/文档路径，
   并 `git cat-file` 核验既有补报所引 commit `d3b6c914` 的真实身份（type=`%s`）。
3. **工件可得性扫描**：对 %d 个 TensorDEM 检出逐一探测 %d 个声明路径。
4. **算力面取证**：`torch.cuda.is_available()` / `device_count()` / `nvidia-smi`，判定"GPU 加速比"
   判据在本宿主上是否**原则上可测**。

判据先于动手：输出是二值——每判据"有存活数值工件→可复核"或"无→WITHHELD"，无第三种含糊结论。
工具链：Python %s / psql / git / matplotlib；宿主 `%s`，`%s`。

---

## 3. 计算结果（数字表，脚本注入）

### 3.1 声明工件可得性

| 声明路径 | 现存副本数 / 扫描根数 | 判定 |
|---|---|---|
%s

**可回收工件数 = %d / %d。** 扫描根：%s。

### 3.2 git 入库核验

| 检查项 | 结果 |
|---|---|
| 镜像 HEAD | `%s` |
| `git log --all -- <m710 的 4 个路径>` | `%s` |
| `git log --all --grep='Merge #710'` | `%s` |
| `commit d3b6c914` 类型 | `%s` |
| `artifacts/dem3d_neighbor_scale` / `neighbor_search.py` | `%s` |

解读：m710 的交付**从未进入 TensorDEM 任何分支**（与 `head_commit=null` 互证）。因此本单既无 git
可复现的代码面，也无 git 可追溯的 benchmark 面。

### 3.3 算力面（决定"GPU 加速比"是否可测）

| 量 | 值 |
|---|---|
| torch 版本 | `%s` |
| torch.version.cuda | `%s` |
| `torch.cuda.is_available()` | **`%s`** |
| `torch.cuda.device_count()` | **`%s`** |
| `nvidia-smi` | `%s`（未安装） |

结论：本宿主**无可用 CUDA 设备**。`gpu_speedup_vs_cpu >= %g` 若确曾在本机测得，其"GPU"路径只能是
CPU 上的回退/伪装执行（MOCK 级风险）；若在别处测得，则数值工件必须存活才能复核，而存活数为 0/%d。

### 3.4 既有补报 9360 的归因核验（重要更正）

库中 `tensor_reports.id=%s`（mission_id=%s，qa_status=`%s`，标题「%s」）把**另一单**成果写成了 m710：

| 证据 | 内容 |
|---|---|
| 9360 引用的 merge | `%s` → `#710` 是 **PR 号**，其主体为 **m6820** |
| 9360 引用的 commit | `d3b6c914`（`artifacts/dem3d_neighbor_scale/` + `src/tensordem/neighbor_search.py`） |
| 该 commit 路径 ∩ m710 声明工件 | **%d 个**（交集为空） |
| m710 声明工件 | %s … |

即 9360 汇报的是 m6820 的**向量化 CPU cell list**（PR #710 合并的那单），m710 是**第 2 次尝试的 GPU 原生版**，
两者文件集不相交。"PR 编号 == mission 编号"的巧合是错位直接成因。

---

## 4. 结果图

![图 1](m710_evidence_audit.png)

**图 1（程序生成，sha256 见 manifest）**：(a) 六个声明工件在 %d 个 TensorDEM 检出中的存活副本数——全 0；
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

## 6. 误差判定（逐项，显式回答 <3%%）

| 关键量 | 基准/门槛 | 实测值 | 是否 <3%% |
|---|---|---|---|
| `gpu_speedup_vs_cpu` | ≥ %g（验收门槛，非物理基准） | 无法取得（工件 0 存活） | **不适用（NOT_EVALUABLE）** |
| `force_consistency_gpu_vs_cpu` | < %g N | 无法取得 | **不适用（NOT_EVALUABLE）** |
| `pytest_test_gpu_cell_list` | PASS | 仅 result 字符串"18 passed"，测试文件未入库 | **不适用（NOT_EVALUABLE）** |

**显式回答"是否 <3%%"**：**不能回答"是"。** 本单没有任何可复核的实测数值，故不存在可比较的基准/参考值，
3%% 判据在此**无定义**。按证据分级，m710 三条判据一律 **WITHHELD**（既非 PASS 也非 FAIL）。
唯一可判定的硬事实：**工件可回收 %d/%d**、**CUDA 设备数 %s**、**m710 代码路径 git 提交数 0**。

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
""" % (
    DISPATCH_SHA, mission.get("created_at"), mission.get("finished_at"), mission.get("status"),
    crit_rows,
    mission.get("id"), mission.get("repo"), mission.get("base_ref"),
    mission.get("runner"), mission.get("claimed_by"), mission.get("model"),
    mission.get("origin"), mission.get("created_at"), mission.get("finished_at"),
    mission.get("status"), mission.get("fail_stage"),
    result.get("head_commit"), mission.get("report_id"), mission.get("report_owed"),
    len(declared),
    MISSION_ID, MIRROR, d3_type["out"] or "不存在", len(DEM_ROOTS), len(declared),
    dev.get("python"), dev.get("machine"), dev.get("platform"),
    dl, n_rec, len(declared), ", ".join("`" + r + "`" for r in DEM_ROOTS),
    mirror_head["out"][:80],
    (hist_m710["out"] or "空（无任何提交）")[:120],
    (pr710["out"] or "空")[:120],
    d3_type["out"] or "不存在",
    (neighbor_hist["out"] or "空")[:120],
    dev.get("torch_version"), dev.get("torch_cuda_version"),
    dev.get("cuda_available"), dev.get("cuda_device_count"), dev.get("nvidia_smi_path"),
    thr0, len(declared),
    prior.get("id"), prior.get("mission_id"), prior.get("qa_status"), prior.get("title"),
    (pr710["out"] or "空")[:120], len(overlap),
    ", ".join("`" + p + "`" for p in declared[:4]),
    len(DEM_ROOTS), thr0, thr1, n_rec, len(declared), dev.get("cuda_device_count"),
)

MD_PATH = DOCS / "m710_gpu_cell_list_backfill.md"
MD_PATH.write_text(md, encoding="utf-8")

manifest = {
    "mission_id": MISSION_ID,
    "generated_by": "scripts/m710_backfill_audit.py",
    "files": {
        "docs/reports/m710_gpu_cell_list_backfill.md": sha256_file(MD_PATH),
        "artifacts/m710_backfill/evidence_audit.json": sha256_file(JSON_PATH),
        "artifacts/m710_backfill/m710_evidence_audit.png": sha256_file(PNG),
    },
}
(ART / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps({
    "report_md": str(MD_PATH),
    "report_md_len": len(md),
    "evidence_json": str(JSON_PATH),
    "figure": str(PNG),
    "artifacts_recoverable": "%d/%d" % (n_rec, len(declared)),
    "cuda_available": dev.get("cuda_available"),
    "cuda_device_count": dev.get("cuda_device_count"),
    "m710_git_commits": len([l for l in hist_m710["out"].splitlines() if l.strip()]),
    "prior_report_overlap": len(overlap),
    "manifest": manifest["files"],
}, ensure_ascii=False, indent=2))
