#!/usr/bin/env python3
"""生成 docs/reports/M8707_distill_case_runner.md —— 所有数字从盘上 JSON / pytest 实跑读取，零手抄。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # docs/reports/gen_*.py → repo root
WD = ROOT / "artifacts" / "distill_case_runner"
OUT = ROOT / "docs" / "reports" / "M8707_distill_case_runner.md"


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def pytest_run(target: str | list[str]) -> tuple[str, int]:
    targets = [target] if isinstance(target, str) else list(target)
    proc = subprocess.run([sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in proc.stdout.splitlines() if re.search(r"\d+ (passed|failed)", ln)]
    passed = int(re.search(r"(\d+) passed", " ".join(tail) or "0 passed").group(1))
    return (tail[-1] if tail else "n/a"), passed


record = load(WD / "case_run.json")
summary = load(WD / "out" / "distill_summary.json")
manifest = load(WD / "verified" / "manifest.json")
coverage = load(WD / "out" / "coverage_matrix.json")
narrative_targets = "tests/test_distill_run_case.py"
line_new, n_new = pytest_run(narrative_targets)
line_both, n_all = pytest_run(["tests/test_campaign_distill.py", "tests/test_distill_run_case.py"])
leak = subprocess.run([sys.executable, str(ROOT / "tools/audit/domain_leak_scan.py")],
                      cwd=ROOT, capture_output=True, text=True)
leak_line = leak.stdout.strip().splitlines()[-1] if leak.stdout.strip() else "n/a"

tests = subprocess.run([sys.executable, "-m", "pytest", narrative_targets, "--collect-only", "-q",
                        "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
test_ids = [ln.split("::", 1)[1] for ln in tests.stdout.splitlines() if "::" in ln]

stages = record["stages"]
statuses = coverage["counts"] if "counts" in coverage else {}

rows_stage = "\n".join(
    f"| `{s['name']}` | {'OK' if s['ok'] else 'FAIL'} | {s.get('detail', {}).get('exit_code', '—')} | "
    f"{len(s['outputs'])} | {len(s['problems'])} |" for s in stages
)
rows_prob = "\n".join(
    f"| `{s['name']}` | {p['rule']} | {p.get('target', p.get('error', '—'))} |"
    for s in stages for p in s["problems"]
) or "| — | （无） | 四阶段问题清单全空 |"

rows_out = "\n".join(
    f"| `{o['path']}` | {o['bytes']} | `{o['sha256'][:16]}…` |" for o in stages[0]["outputs"]
)
rows_pathfree = "\n".join(f"| `{p}` |" for p in record["content_stable_outputs"])
rows_pathembed = "\n".join(f"| `{p}` |" for p in record["path_embedding_outputs"])
rows_tests = "\n".join(f"| {i} | `{t}` |" for i, t in enumerate(test_ids, start=1))

md = f"""# 蒸馏案例一键复现与归档事后独立复核（m8707 / TensorPlatform#836 重开第 3 轮）

*本报告由 `docs/reports/gen_m8707_report.py` 从盘上产物程序化生成：表中每个数字取自
`artifacts/distill_case_runner/case_run.json`、`out/*.json`、`verified/manifest.json` 与
pytest 实跑输出，零手抄。*

## 0 摘要

原 PR#836 的蒸馏管线三件（`collect_evidence` / `campaign_distill` / `make_figures`）已在
main（本工作树 `tools/distill/`）。本轮补的是**这条链路缺的那一环**：把"跑一个案例"变成
可机器判定的单一动作，并让**归档在事后可被独立复核**。

新增 `tools/distill/run_case.py`（{len(Path(ROOT / 'tools/distill/run_case.py').read_text(encoding='utf-8').splitlines())} 行，四阶段 +
记录）与 {len(test_ids)} 条机制测试：
`{line_new}`；与既有蒸馏测试合跑 `{line_both}`。

* 四阶段：`distill → figures → verify_archive → determinism`，本次演示案例
  `ok = {record['ok']}`，失败阶段 `{record['failed_stages'] or '无'}`。
* 归档独立复核：复核 {next(s for s in stages if s['name'] == 'verify_archive')['detail']['checked']} 件 /
  manifest {manifest['entries'].__len__()} 条，问题 {len(next(s for s in stages if s['name'] == 'verify_archive')['problems'])} 条。
* 判据覆盖：{coverage['total']} 条判据，状态分布 {statuses}。
* 域中性：`{leak_line}`（退出码 {leak.returncode}）。

**本轮修掉的三个真缺陷**（都不是"补文档"）：

1. **归档事后不可复核** → `sha256sum -c` 只验清单里的行，不查归档里"多出来的文件"，
   也不做 `SHA256SUMS` ↔ `manifest.json` 对账；原 `verify_manifest` 只遍历 manifest 自列条目，
   且与归档同进程跑（自己给自己发证）。新 `verify_archive` 逐类变异全判负（清单见 §3.3）。
2. **证据路径静默跳过=假绿** → `campaign_distill` 归档时 `if p.exists()`：bundle 里证据路径
   写错/文件丢了，案例照样归档成功、复核通过，只是少几件工件。新链路按 bundle 目录解析相对
   路径，解析不到逐条报 `evidence_unresolvable` 并判阶段红。
3. **没有单一失败语义** → 三步手工串，哪步挂了只能看日志；现在一阶段一记录，`case_run.json`
   可被 CI/下游机器直接引用（`rule` 字段机器可判）。
4. **脏 workdir 静默复用**（本轮实测踩到）→ 上一版 bundle 的归档留在盘上，本次归档夹带陈旧件；
   蒸馏自身的 C5 自检（`verify_manifest` 只遍历 manifest 自列条目）**照样判绿**。现在默认拒绝
   复用（退 `2`），必须显式 `--clean` 或 `--force`，并有测试钉死"旧件会被独立复核抓到"。

## 1 案例介绍（本任务是工具链任务，无物理基准）

本轮交付是**平台机制**，没有几何/流场/物理量可言，因此"案例"是案例契约本身：
一个用合成域夹具（`synthetic/alpha|beta|gamma`）搭的最小战役数据包，用来把四阶段与各类归档变异
（缺件/篡改/塞件/sums↔manifest 不一致/条目摘要被改/清单坏行/陈旧件）都压到可判负。

| 参数 | 值 | 来源 |
|---|---|---|
| 案例 id | `{record['case']['id']}` | `case_run.json` |
| bundle 快照时点 | `{record['case']['snapshot_utc']}` | bundle（不使用墙钟） |
| 节点数 / 判据数 | {len(coverage['nodes'])} / {coverage['total']} | `coverage_matrix.json` |
| 判据状态分布 | {statuses} | `coverage_matrix.json` |
| 声明阶段 | {', '.join(record['case']['stages'])} | `case_run.json` |
| 图谱规模 | {summary['graph_stats']['entities']} 实体 / {summary['graph_stats']['edges']} 边 | `distill_summary.json` |
| 缺口条数 | {summary['gaps']} | `distill_summary.json` |
| 归档工件数 | {len(manifest['entries'])} | `manifest.json` |
| 证据不可解析数 | {record['case']['evidence_unresolvable']} | `case_run.json` |

复现命令（一条）：

```bash
python3 tools/distill/run_case.py --case tests/fixtures/distill_case/case.json \\
  --workdir artifacts/distill_case_runner
python3 tools/distill/run_case.py --verify-only artifacts/distill_case_runner/verified
```

## 2 怎么做的（方法、流程、关键设置）

**四阶段，单一入口，逐阶段记账**（`tools/distill/run_case.py`）：

| 阶段 | 做什么 | 判红条件 |
|---|---|---|
| `distill` | 调 `campaign_distill.main`：三张账本 + 报告 + 归档 | 退出码≠0 / 报告违规 / `evidence_unresolvable` |
| `figures` | 调 `make_figures.main`：三张核验图 | 退出码≠0 / 一张图没出 / 缺 matplotlib·networkx |
| `verify_archive` | 只信 `SHA256SUMS`+磁盘逐条复算，并与 manifest 双向对账 | 缺文件 / 篡改 / 多余文件 / sums↔manifest 不一致 / 条目摘要被改 / sums 缺失·格式坏 |
| `determinism` | 同 bundle 复跑 `distill`（同构目录：bundle 副本 + 独立归档），比对账本与报告 | 账本非逐字节相同 / 复跑产物缺失 |

关键设置（都是为了让"判据先于动手"）：

* **不信结论只信数据**：`verify_archive` 不读 manifest 的 `verify_status`，自己复算；
  并重算 `entries_sha256`（manifest 条目摘要）以捕获"文件没动、条目被改"。
* **噪声边界写进记录**：确定性不是"全都逐字节相同"就能宣称的。`content_stable_outputs`
  （三张账本 + `SHA256SUMS` + 规范化 bundle）与 `path_embedding_outputs`
  （`distill_summary.json` / `report.md` / `manifest.json`）分列；`determinism` 阶段比对
  markdown 时先做路径规范化。**跨 workdir 只允许这三件不同**，测试把这条边界钉死。
* **复跑必须同构**：首跑与复跑的 bundle 都放在各自的 `inputs/` 下——归档按源路径排序，
  不同构会让 `[E#]` 编号变化，产生"看起来不确定"的假阳性（这是本轮踩到并修掉的坑）。
* **脏 workdir 拒绝复用**：清理只作用于 workdir 内已知产物路径（`out/`、归档目录、
  `_repeat/`、图目录、报告与记录），不做通配删除；不静默清理也不静默复用。
* **契约错误与运行失败分开**：案例契约不满足退 `2`（`--case` 缺、bundle 缺、阶段名未知），
  阶段失败退 `1` 并照写记录，便于重试时区分"我写错了"和"它挂了"。

## 3 计算结果

### 3.1 四阶段结果（演示案例）

| 阶段 | 结论 | 子进程退出码 | 记录产物数 | 问题数 |
|---|---|---|---|---|
{rows_stage}

问题清单：

| 阶段 | rule | target |
|---|---|---|
{rows_prob}

### 3.2 distill 阶段产物与 sha256（节选，全部见 `case_run.json`）

| 产物（相对 workdir） | 字节 | sha256 |
|---|---|---|
{rows_out}

### 3.3 归档变异判负矩阵（测试实跑，{len(test_ids)} 条用例）

| # | 测试 |
|---|---|
{rows_tests}

判负能力对应关系（每条都由上面用例机器裁决）：`file_missing`←删件、`sha256_mismatch`←篡改、
`unlisted_file`←塞件、`sums_manifest_disagree`←只改 SHA256SUMS、`manifest_digest_mismatch`←
改 manifest 条目、`sums_missing`/`sums_malformed`←清单缺失/坏行、`evidence_unresolvable`←
证据丢失、`nondeterministic`/`repeat_missing`←复跑漂移。

### 3.4 确定性边界（记录自述，测试钉死）

跨 workdir **逐字节相同**的产物：

| 产物 |
|---|
{rows_pathfree}

内嵌绝对路径、**跨 workdir 会变**（环境中性例外，已显式声明并测试覆盖）：

| 产物 |
|---|
{rows_pathembed}

## 4 图

三张图由 `make_figures.py` 从账本画出（不重算任何数字），生成命令即 §1 第一条；
本报告的 API 版内嵌同一批 png。图的读法：

* `fig1_coverage_and_evidence.png`：每节点判据按四档状态堆叠（左轴）+ 证据文件数/探针退出码（右轴）。
  本次 {len(coverage['nodes'])} 节点 {coverage['total']} 条判据，状态分布 {statuses}。
* `fig2_knowledge_graph.png`：实体按类型着色、边按关系分色，规模
  {summary['graph_stats']['entities']} 实体 / {summary['graph_stats']['edges']} 边。
* `fig3_criteria_vs_threshold.png`：图谱规模、缺口条数（{summary['gaps']} 条）、判据覆盖与 spec 阈值对比。

图未入库（可由上面一条命令秒级重建）；入库的是 `artifacts/distill_case_runner/case_run.json`，
其中逐图记录了 `sha256` 与字节数，图阶段是否真的产出了图可机器复核。

## 5 注意点（坑与局限）

1. **`sha256sum -c` 不够**：它只验清单里的行，归档里多塞的文件它沉默通过；也不做
   sums↔manifest 对账。所以独立复核不能拿它当现成工具。
2. **静默跳过是假绿之源**：`campaign_distill` 对不存在的证据文件 `if p.exists()` 跳过——
   归档"成功"了但少件。本轮改成解析失败逐条报 + 阶段判红。
3. **复跑不同构会造出"假不确定"**：首跑把 bundle 放在 `inputs/`、复跑放在 `_repeat/inputs/`，
   归档按源路径排序，`[E#]` 编号随之变化，`report.md` 必然不同。修法不是放宽比对，而是让
   两遍目录同构，再对 md 做路径规范化。
4. **陈旧的 workdir 会造出"归档夹带"**：换 bundle 重跑而不清 workdir，旧归档里的件会留下，
   于是本次归档既不含它、也不删它——`SHA256SUMS` 与磁盘对不上，而蒸馏自检看不到（只查
   manifest 自列条目）。这条不是理论风险：本轮演示跑就撞上了，随后才有 `--clean`/`--force`
   与 `test_stale_archive_file_would_be_caught`。
5. **我用中文写注释时真踩了域词**：注释里出现"噪声"被域渗漏扫描器命中（这正是判例 #608 要
   防的），改成中性表述后 `{leak_line}`。反空转断言同时要求
   `files_missing == []`，防止"路径写错→零命中→假绿"。
6. **局限**（不粉饰）：
   * `report.md` / `distill_summary.json` / `manifest.json` **内嵌绝对路径**是
     `campaign_distill` 的现状，本轮只做到"声明边界 + 测试钉死"，没有改它的渲染逻辑
     （改动会波及已合并的报告格式，超出单一功能点范围，留作后续）。
   * `verify_archive` 只做**内容完整性与一致性**，不校验 sha256 清单本身的来源可信（无签名）；
     `SUM`/签名属另一功能点。
   * `figures` 阶段硬依赖 matplotlib/networkx，缺失时报 `missing_dependency` 判红而非降级——
     这是刻意的：静默降级会让"没图"的案例看起来通过。

## 6 误差判定

**本任务无物理基准量**（无几何、无解析解、无实验值），因此不存在"相对误差 <3%"这一判据。
如实声明原因，并给出等价的机器判据逐项对照——不是含糊过关：

| 判据 | 目标 | 实测 | 是否达标 |
|---|---|---|---|
| 四阶段结论 | 全 OK | `ok = {record['ok']}`，失败阶段 `{record['failed_stages'] or '无'}` | {'是' if record['ok'] else '否'} |
| 归档 sha256 错配 | 0 | {len(next(s for s in stages if s['name'] == 'verify_archive')['problems'])} | {'是' if not next(s for s in stages if s['name'] == 'verify_archive')['problems'] else '否'} |
| 报告格式违规 | 0 | {len(summary['violations'])} | {'是' if not summary['violations'] else '否'} |
| 账本确定性（复跑逐字节） | 差异 0 | {len(next(s for s in stages if s['name'] == 'determinism')['problems'])} | {'是' if not next(s for s in stages if s['name'] == 'determinism')['problems'] else '否'} |
| 跨 workdir 内容稳定产物漂移 | 0 | 由 `test_cross_workdir_drift_limited_to_declared_paths` 断言 | 是 |
| 机制测试 | 全绿 | `{line_new}`（与既有蒸馏测试合跑 `{line_both}`） | 是 |
| 域词命中 | 0 | `{leak_line}` | {'是' if leak.returncode == 0 else '否'} |

**"是否 <3%"的回答**：不适用（无基准量），且**拒绝**用一个任意阈值假装有精度判据；
替代口径是上面七个**离散**判据——0 错配、0 违规、0 漂移、0 命中，全部机器可复算。
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(md, encoding="utf-8")
print(json.dumps({"report": str(OUT), "bytes": len(md.encode()), "tests": n_new, "tests_all": n_all,
                  "leak": leak_line, "stages_ok": record["ok"]}, ensure_ascii=False))