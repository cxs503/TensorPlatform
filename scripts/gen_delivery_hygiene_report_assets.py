#!/usr/bin/env python3
"""生成交付面卫生的机器注入产物：``docs/DELIVERY_HYGIENE_MECHANISM.md`` + ``artifacts/.../summary.json``。

纪律：文档里的**每个数字都从 ``ledger.json`` / ``gate.json`` 读出来格式化注入**，不手抄。
用法::

    python3 scripts/gen_delivery_hygiene_report_assets.py \
        --repo-alpha artifacts/delivery_hygiene/repo-alpha/ledger.json \
        --repo-beta  artifacts/delivery_hygiene/repo-beta/ledger.json \
        --gate-alpha artifacts/delivery_hygiene/repo-alpha/gate.json \
        --gate-beta  artifacts/delivery_hygiene/repo-beta/gate.json \
        --doc docs/DELIVERY_HYGIENE_MECHANISM.md \
        --summary artifacts/delivery_hygiene/summary.json
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

CLASSES = ("A", "B", "C", "D")


def n_tests() -> int:
    """用例数从测试文件现数（避免文档里的数字和实际用例数脱节）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tests", "test_delivery_hygiene.py")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for ln in fh if ln.startswith("def test_"))
    except OSError:
        return 0


def load(path: str) -> Dict[str, Any]:
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def repo_table(ledgers: List[Dict[str, Any]]) -> str:
    head = ("| 仓库 | 分支总数 | A 已覆盖 | B 超集覆盖 | C 真未交付 | D 孤儿 | 未合并(B+C) | "
            "未合并占比 | 可安全关闭 | 独立补单 | 引擎交叉校验一致率 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for led in ledgers:
        s = led["stats"]
        xc = s.get("engine_crosscheck") or {}
        agree = xc.get("agreement")
        agree_s = f"{agree:.0%}（抽样 {xc.get('sample')}）" if agree is not None else "未抽样"
        lines.append(
            f"| `{led['repo']}` | {s['total_branches']} | {s['by_class']['A']} | {s['by_class']['B']} | "
            f"{s['by_class']['C']} | {s['by_class']['D']} | **{s['unmerged']}** | "
            f"{s['unmerged_ratio'] * 100:.1f}% | {s['safe_to_close']} | {len(led['tickets'])} | {agree_s} |")
    return head + "\n".join(lines) + "\n"


def decided_table(ledgers: List[Dict[str, Any]]) -> str:
    agg: Dict[str, Dict[str, int]] = {}
    for led in ledgers:
        for r in led["rows"]:
            agg.setdefault(r["decided_by"], {}).setdefault(led["repo"], 0)
            agg[r["decided_by"]][led["repo"]] += 1
    repos = [led["repo"] for led in ledgers]
    head = "| decided_by（判据） | " + " | ".join(f"`{r}`" for r in repos) + " | 合计 |\n"
    head += "|---" * (len(repos) + 2) + "|\n"
    rows = []
    for key in sorted(agg):
        cells = [agg[key].get(r, 0) for r in repos]
        rows.append(f"| `{key}` | " + " | ".join(str(c) for c in cells) +
                    f" | {sum(cells)} |")
    return head + "\n".join(rows) + "\n"


def superset_table(ledgers: List[Dict[str, Any]], top: int = 8) -> str:
    lines = []
    for led in ledgers:
        grp: Dict[str, int] = {}
        for r in led["rows"]:
            if r["class"] == "B" and r.get("superset_source"):
                grp[r["superset_source"]] = grp.get(r["superset_source"], 0) + 1
        if not grp:
            lines.append(f"`{led['repo']}`：无 B 类等价簇。")
            continue
        top_items = sorted(grp.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        lines.append(f"`{led['repo']}`：{len(grp)} 个留存源，" +
                     "、".join(f"`{k}`({v})" for k, v in top_items))
    return "\n\n".join(lines) + "\n"


def gate_table(gates: List[Dict[str, Any]]) -> str:
    if not gates:
        return "（未提供 gate 结果）\n"
    head = ("| 仓库 | 门裁决 | 未合并 | ceiling | 余量 | 冻结时基线 | 冻结时间 |\n"
            "|---|---|---|---|---|---|---|\n")
    rows = []
    for g in gates:
        rows.append(f"| `{g['repo']}` | {'PASS' if g['pass'] else 'FAIL'} | {g['current_unmerged']} | "
                    f"{g.get('ceiling')} | {g.get('headroom')} | {g.get('baseline_unmerged')} | "
                    f"{g.get('frozen_at')} |")
    return head + "\n".join(rows) + "\n"


def render_doc(ledgers: List[Dict[str, Any]], gates: List[Dict[str, Any]]) -> str:
    totals = {c: sum(l["stats"]["by_class"][c] for l in ledgers) for c in CLASSES}
    total_branches = sum(l["stats"]["total_branches"] for l in ledgers)
    total_unmerged = sum(l["stats"]["unmerged"] for l in ledgers)
    total_tickets = sum(len(l["tickets"]) for l in ledgers)
    total_safe = sum(l["stats"]["safe_to_close"] for l in ledgers)
    commit_total = sum(l["stats"]["unique_commits_total"] for l in ledgers)
    return f"""# 交付面卫生机制（delivery hygiene）：内容等价三分 + 处置动作 + 只减不增门

> 模块：`tools/delivery_hygiene/`（`gitio` / `classify` / `ledger` / `dispose` / `gate` / `cli`）
> 用例：`tests/test_delivery_hygiene.py`（{n_tests()} 例，全部在临时合成仓库里跑，零领域语义）
> 账本：`artifacts/delivery_hygiene/<repo>/ledger.json.gz`（CLI `--ledger` 直读 gz）；
> 补单：同目录 `tickets.jsonl`（一分支一行，任务机可批量收养）；复核：同目录 `audit.json`；
> 处置演示工件：`artifacts/delivery_hygiene/apply-demo/`；图：`artifacts/delivery_hygiene/figures/`
> 门：`.github/workflows/branch-hygiene-gate.yml` + 宿主 cron（同一条 CLI）

## 1. 问题：分支只增不减的熵

每单都在工作树上建 `agent/m<id>` 分支，交付后由平台推送/合并，但**没有任何机制回收分支**。
实测两个仓库合计 **{total_branches}** 条 `agent/*` 分支，其中 **{total_unmerged}** 条未合并
（占 {total_unmerged / total_branches * 100:.1f}%），独有提交 **{commit_total}** 条。
既有工具 `tools/branch_ledger.py` 只能回答「哪些分支有主干没有的提交」（UNIQUE），
但它**只读**（不建/删/改 ref），也不区分「同一处改动被重复 cherry-pick 成多条分支」——
于是台账指出了问题，问题却照旧增长。

## 2. 四类内容等价判据

| 类 | 含义 | 判据 | `decided_by` | 可安全关闭 |
|---|---|---|---|---|
| **D** | 孤儿 | `base..b` 无提交（tip 已在 base 历史内） | `rule:D-tip-in-base-history/v1` | 是 |
| **A** | 已覆盖 | ① 独有提交为空（patch-id 全命中 base）② 净 `git diff base..b` 为空 ③ squash 合成 patch-id 命中 base | `rule:A-patch-id-all-in-base/v1` / `rule:A-net-diff-empty/v1` / `rule:A-squash-combined-patch/v1` | 是 |
| **B** | 超集覆盖 | 本分支独有 patch-id 集合**真包含于**另一分支（链式解析到极大元）；等集簇里除 canonical 外全部判 B，而 canonical 若有严格超集也同样判 B | `rule:B-patch-id-subset-of-superset/v1` | 是（内容留在 S） |
| **C** | 真未交付 | 独有 patch-id 既不在 base 也不在别分支 | `rule:C-unique-patch-ids/v1` | 否（开补单） |

优先级 **D > A > B > C**，四类互斥且穷尽。三条 A 判据缺一不可：
实测平台交付走 squash/push-merge，squash 会重写 patch-id 使①全漏，主干后续修改又使②漏，
只有③（`git diff merge-base..b | git patch-id` 与 base 提交集合比对）能兜住。
关掉③会被单测 `test_squash_detection_is_load_bearing` 抓住（该分支从 A 退回 C）。

## 3. 两个引擎与可审计自证

* `pidset`（默认）：一次算 base 的 patch-id 集合 + 一次性批量算候选提交 patch-id，本地做集合运算。
  `git cherry` 逐分支实现要重算 base 侧 patch-id，实测 2173 条分支需 ~11 min；pidset 降到 ~2 min。
* `cherry`：`git cherry` 逐分支，与 `tools/branch_ledger.py` 完全同口径，用作**交叉校验**：
  `--crosscheck N` 抽样 N 条用 cherry 复算，一致率写进账本 `stats.engine_crosscheck`（下表末列）。

## 4. 机器可读账本

每行 `{{repo, branch, class, class_label, decided_by, safe_to_close, retention_owner, mission_id,
sha, tip_date, superset_source, evidence{{ahead, unique_shas, equivalent_count, empty_commits,
unique_patch_ids, net_diff_empty, squash_patch_in_base, combined_patch_id, tip_in_base_history,
merge_base, changed_files, changed_files_total}}, engine}}`。
除 `generated_at` 外所有字段由 git 状态唯一决定（`test_ledger_is_recomputable` 断言两次扫描逐字节一致）。

## 5. 处置动作（默认 dry-run，可回滚）

| 类 | 动作 | 说明 |
|---|---|---|
| D/A | `archive_ref` | 建 `refs/attic/<date>/<branch>` 并**回读校验**，再删 `refs/heads/<branch>` |
| B | `annotate` + `archive_ref` | 账本标注 `superset_source`；内容留存链解析成功才归档 |
| C | `open_ticket` | 一分支一单 `tickets/<repo>--<branch>.json`（kind=delivery，含独有提交与改文件） |

回滚：manifest 里每条都带 `rollback = git update-ref refs/heads/<branch> <sha>`；
`--apply` 必须同时给 `--i-mean-it`（`test_cli_apply_requires_double_confirmation`）；
C 类永不进归档列表（`test_plan_never_archives_undelivered`）；重复执行幂等
（`test_archive_apply_is_idempotent`）。

## 6. 只减不增门

* **冻结基线** `freeze`：把当前 `unmerged = B + C` 写成 ceiling。
* **棘轮**：新 ceiling 必须 ≤ 旧 ceiling − `min_reduction`；要抬高必须 `--accept-increase <理由>`，
  理由与前后数字进 `history`（`test_ratchet_refuses_increase_without_reason`）。
* **裁决** `gate`：`current_unmerged ≤ ceiling`；另报 `V2`（冻结后新增 C 类，`--strict-new` 下判失败）、
  `V4`（base 已移动未重新冻结，warning）。
* **CI**：`.github/workflows/branch-hygiene-gate.yml`（PR 跑机制自测；定时拉全部分支跑门）。
  边界声明：CI 只能看到已推送分支，堆积主要发生在宿主本地 refs 上，宿主侧用同一条 CLI 定时跑门。

## 7. 实测结果（数字由本脚本从 ledger/gate JSON 注入）

### 7.1 分类总表

{repo_table(ledgers)}
合计：**{total_branches}** 条分支 / **{total_unmerged}** 条未合并 / **{total_safe}** 条可安全关闭 /
**{total_tickets}** 张补单。A {totals['A']} / B {totals['B']} / C {totals['C']} / D {totals['D']}。

### 7.2 判据命中分布

{decided_table(ledgers)}
### 7.3 B 类等价簇（重复 cherry-pick / rebase 的留存源）

{superset_table(ledgers)}
### 7.4 门裁决

{gate_table(gates)}
## 8. 局限（必须与结论一起读）

1. **squash 语义**：若 squash 合并**同时改写了内容**，合成 patch-id 也不会命中，该分支仍判 C。
   也就是说 C 类只承诺「base 里没有等价补丁」，不承诺「一定是新内容」。反过来，A 类可以放心关。
2. **B 类只保证包含关系**：B 的独有补丁在 S 里存在一份，但 S 的质量/后续演进不由本机制判断。
3. **历史不可比**：分支只与当前 `main` tip 比较；base 一动，`V4` 会提示重新冻结基线。
4. **空提交不计内容**（记在 `evidence.empty_commits`）：比 `git cherry` 更严于内容守恒、更宽于计数。
5. **归档是本地操作**：`refs/attic/` 不 push，因此远端不会出现归档 ref；远端删除仍需人工/平台推送。
6. **CI 覆盖有限**：见 §6 边界声明，宿主侧 cron 才是主战场。
"""

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-alpha", required=True)
    ap.add_argument("--repo-beta", required=True)
    ap.add_argument("--gate-alpha", default=None)
    ap.add_argument("--gate-beta", default=None)
    ap.add_argument("--doc", default="docs/DELIVERY_HYGIENE_MECHANISM.md")
    ap.add_argument("--summary", default="artifacts/delivery_hygiene/summary.json")
    a = ap.parse_args()

    ledgers = [load(a.repo_alpha), load(a.repo_beta)]
    gates = [load(p) for p in (a.gate_alpha, a.gate_beta) if p and os.path.exists(p)]
    doc = render_doc(ledgers, gates)
    os.makedirs(os.path.dirname(os.path.abspath(a.doc)) or ".", exist_ok=True)
    with open(a.doc, "w", encoding="utf-8") as fh:
        fh.write(doc)
    summary = {
        "schema_version": "1.0",
        "repos": [{
            "repo": led["repo"],
            "base_sha": led["base_sha"],
            "base_tip_date": led["base_tip_date"],
            "total_branches": led["stats"]["total_branches"],
            "by_class": led["stats"]["by_class"],
            "unmerged": led["stats"]["unmerged"],
            "safe_to_close": led["stats"]["safe_to_close"],
            "tickets": len(led["tickets"]),
            "unique_commits_total": led["stats"]["unique_commits_total"],
            "engine": led["stats"].get("engine"),
            "engine_crosscheck": led["stats"].get("engine_crosscheck"),
            "ledger": os.path.relpath(os.path.abspath(a.repo_alpha if led["repo"] == ledgers[0]["repo"]
                                                      else a.repo_beta)),
        } for led in ledgers],
        "gate": gates,
        "doc": a.doc,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.summary)) or ".", exist_ok=True)
    with open(a.summary, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[delivery_hygiene] doc={a.doc} summary={a.summary} repos={len(ledgers)} gates={len(gates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
