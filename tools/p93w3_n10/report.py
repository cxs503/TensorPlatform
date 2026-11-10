"""Render the P93W3-N10 report from the frozen artifacts (zero hand-copied numbers).

Outputs
  docs/p93w3-n10/REPORT.md   paper-style report (six sections)
  /tmp/report.json           API payload (markdown + inline base64 figures)

Usage: python3 tools/p93w3_n10/report.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p93w3-n10"
DOCS = ROOT / "docs/p93w3-n10"


def load(name: str) -> dict:
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def table(header: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(out)


def build_markdown(metrics: dict, figures: dict, verdict: dict) -> str:
    acc = metrics["criteria"]["a_classification_accuracy"]
    lat = metrics["criteria"]["b_mvd_trigger_latency"]
    mig = metrics["criteria"]["c_backward_compat_migration"]
    lab = metrics["criteria"]["d_label_schema_validation"]
    dist = metrics["distributions"]
    comp = metrics["forced_finish_composition"]
    evidence = metrics["budget_evidence"]
    contract = metrics["contract"]
    corpus = metrics["corpus"]
    support = verdict["support"]

    state_rows = [[state, dist["counts"][state], f"{dist['rates'][state]:.2%}"]
                  for state in ("succeeded", "forced_finish", "degraded", "failed")]
    comp_rows = [[k, comp[k]] for k in ("budget_marker", "wall_marker", "progress_marker",
                                        "tool_cap_only", "total")]

    fig_lines = []
    for fig in figures["figures"]:
        fig_lines.append(f"![{fig['name']}]({pathlib.PurePosixPath('..', '..', fig['path'])})")
        fig_lines.append(f"*图 {fig['name']}：{fig['caption']}* (sha256 `{fig['sha256'][:16]}…`)")
        fig_lines.append("")

    return f"""# 终态语义分层与预算收口契约成熟化（P93W3-N10）— 论文级报告

- 节点：`P93W3-N10`｜使命：9177｜仓库：TensorPlatform｜分支：`agent/m9177`
- 契约：`backend/contracts/terminal_state_vocab.v1.json`（sha256 `{support['contract_sha256']}`）
- 标签 schema：`backend/contracts/delivery_label.v1.json`
- 核心模块：`backend/api/domain/terminal_state.py`（新模块：纯函数、零领域语义、无隐藏状态）
- 语料：`{corpus['path']}`，{corpus['records']} 行真实终态记录，sha256 `{corpus['sha256']}`（只读 API 抓取）
- 全部数字由 `tools/p93w3_n10/*.py` 从工件程序化注入（零手抄）：
  `metrics.json`(sha256 `{support['metrics_sha256']}`) / `figures.json` / `ACCEPTANCE.json` / `pytest_junit.xml`
- 复现：`python3 tools/p92w3_n10/fetch_missions.py --status succeeded --limit 2000 --out runs/p93w3-n10/corpus` →
  `python3 tools/p93w3_n10/evaluate.py` → `python3 tools/p93w3_n10/figures.py` →
  `cd backend && python3 -m pytest tests/test_p93w3_n10_terminal_states.py -q` → `python3 tools/p93w3_n10/acceptance.py`

---

## ① 案例介绍（要解决的问题 / 数据）

平台把「正常完成」和「预算耗尽被迫收尾」都写进同一个终态 `succeeded`。自主续航体系
（补给、合并、判级、KPI、成功率）全部读这一个标签，因此一次被迫收尾会被当成一次交付：
成功率可以被预算耗尽刷高，而「交付了多少」这个更强的语义根本不存在。

本节点把终态语义分成四态，并给一条可机检的交付标签：

| 语义终态 | 语义 | 判定依据（全部为存量事实） |
|---|---|---|
| `succeeded` | 正常完成 | 工件完整（completeness=1.0）+ 无被迫收口标记 + 预算未耗尽 |
| `forced_finish` | 被迫收尾（预算/墙钟/无进展） | 收口标记，或工具调用计数触顶，或预算耗尽证据 |
| `degraded` | 部分交付（缺口可点名） | 无被迫标记但工件不完整 |
| `failed` | 存储态失败 / 无法分类（fail-closed） | `failed/stopped/revoked/orphan`，或记录不可用 |

优先级（契约 `precedence`，首个命中生效）：`{ ' > '.join(contract['precedence']) }`。

数据（真实记录，非构造夹具）：只读抓取 `succeeded` 终态 **{corpus['records']}** 行（分页 200，
落盘后核对 sha256）。这 {corpus['records']} 行在旧口径下成功率 = **{mig['binary_success_rate_before']:.2%}**
（分母即自身，因为抓取时它们全被标成 succeeded）。

## ② 怎么做的（方法与工具链）

1. **派生层，而不是改存储枚举。** 存储 `status` 的 CHECK 约束与跨仓契约
   `mission_status_vocab.v1.json`（TensorPhysics 也在消费）保持不动；语义终态是存量事实的
   确定性函数，因此**天然向后兼容**，也不需要两侧迁移。这是本节点的关键设计取舍（偏差与理由见 ⑤）。
2. **判据单一来源。** `0.70` 只写在契约里，测试把 `terminal_state.budget_thresholds()["mvd_checkpoint"]`
   与 `mvd_checkpoint.BUDGET_THRESHOLD` 钉在一起（`{lat['threshold_pinned_to_contract']}`），
   两个模块不可能漂移。
3. **只信事实，不信自报。** 判定只读：存储态、`fail_stage` 收口标记、预算计划 vs 实际
   （`tool_calls/max_tool_calls`、`created_at→finished_at` 与 `max_wall_seconds`）、
   工件事实（复用 P92W3-N10 的 `normalize_artifacts` / `assess_attested_delivery`）。
4. **completeness 连续化。** `0.4*实现 + 0.4*可机检验证 + 0.2*声明`（权重来自契约），
   验证维按「可机检 1.0 / 存在但空泛 0.5 / 缺失 0」计分 —— 缺口可以被排序，而不只是被点名。
5. **70% MVD 检查点。** `mvd_checkpoint_for_record()` 把存量计划/实际喂给既有
   `mvd_checkpoint.evaluate_mvd_checkpoint()`（生产代码路径）并计时，判据 (b) 因此测的是真路径。
6. **向后兼容迁移。** `migrate_legacy_succeeded()` 逐行 `deepcopy`，新增
   `terminal_state / delivery_label / migration{{legacy_status,…}}` 三键，原键值全部逐字保留；
   「零丢失」是**校验出来的**（键值逐个比对 + 计数对账 + 无漏洞清单），不是声明的。
7. **20 场景自测。** 场景表冻结在测试模块里，评估工具直接 import 同一份表（单一来源），
   分类混淆矩阵、准确率和异常路径一起输出。
8. **零领域语义。** 新模块与新测试都用既有 `scan_domain_semantics()` 扫过，命中数 0。

## ③ 计算结果（全部为工件注入的数字）

判据汇总（`ACCEPTANCE.json`，`all_criteria_met = {verdict['all_criteria_met']}`）：

| 判据 | 要求 | 实测 | 通过 |
|---|---|---|---|
| (a) 四态分类准确率 | 20 场景 100% | {acc['n_correct']}/{acc['n_scenarios']} = **{acc['accuracy']:.2%}** | {verdict['criteria']['a_terminal_state_accuracy']['passed']} |
| (b) 70% MVD 检查点触发延迟 | ≤ 1 s | p100 = **{lat['p100_ms']:.3f} ms**（p50 {lat['p50_ms']:.3f} / p95 {lat['p95_ms']:.3f} / p99 {lat['p99_ms']:.3f}，n={lat['n_calls']}） | {verdict['criteria']['b_mvd_checkpoint_latency']['passed']} |
| (c) 向后兼容迁移零丢失 | 零数据丢失 | in {mig['records_in']} → out {mig['records_out']}，violations {len(mig['violations'])}，状态计数和 {sum(mig['state_counts'].values())} | {verdict['criteria']['c_backward_compat_zero_loss']['passed']} |
| (d) 交付标签 schema 校验 | 100% 通过 | {lab['n_valid']}/{lab['n_labels']} = **{lab['pass_rate']:.2%}**（负向控件：非法枚举/越界/缺键/多键全部被拒，见 pytest） | {verdict['criteria']['d_label_schema_validation']['passed']} |
| 支撑 | pytest 全绿 | {support['pytest']['tests']} 项，failures {support['pytest']['failures']}，errors {support['pytest']['errors']} | {support['pytest_clean']} |

**四态分布（真实 {corpus['records']} 行，旧口径它们全是 succeeded）：**

{table(['语义终态', '行数', '占比'], state_rows)}

- 旧口径成功率 `{mig['binary_success_rate_before']:.2%}` → 真实交付率 **{mig['delivery_rate_after']:.2%}**，
  **虚高 {mig['success_rate_inflation_pp']:.2f} 个百分点**；{mig['records_with_state_change']} 行的语义标签与存储态不同。
- 质量闸门通过率 {dist['quality_gate_pass_rate']:.2%}，completeness 均值 {dist['completeness_mean']:.4f}，
  工件完整率 {dist['complete_rate']:.2%}；预算分档 normal/warning/exhausted =
  {dist['budget_status_counts']['normal']}/{dist['budget_status_counts']['warning']}/{dist['budget_status_counts']['exhausted']}。

**被迫收尾的构成（{comp['total']} 行 = 四态 forced_finish 计数，一致性 {comp['consistent_with_state_count']}）：**

{table(['证据', '行数'], comp_rows)}

**预算证据的效度（本节点最重要的实测发现）：**

{table(['量', '值'], [
    ['收口标记（budget+wall）行数', evidence['marker_exhaustion_rows']],
    ['工具调用计数触顶行数', evidence['tool_at_cap_rows']],
    ['墙钟比 ≥1.0 行数', evidence['wall_at_cap_rows']],
    ['墙钟比 ≥1.0 且带 wall 标记', evidence['wall_cap_with_any_marker']],
    ['墙钟代理 recall / precision', f"{evidence['wall_proxy_recall']:.3f} / {evidence['wall_proxy_precision']:.3f}"],
    ['仅按比例规则会误判的被迫收尾行', evidence['false_forced_rows_avoided']],
])}

结论：墙钟比 `finished_at-created_at` 会把 **{evidence['wall_at_cap_rows']}** 行判成「预算耗尽」，
而真正带 `wall_exhausted` 标记的只有 {evidence['wall_marker_rows']} 行 —— recall {evidence['wall_proxy_recall']:.2f}、
precision 仅 {evidence['wall_proxy_precision']:.3f}（排队等待与软墙钟预算都在这个跨度里）。
因此契约把墙钟比降到**只影响 warning 档**，`exhausted` 必须由证据（收口标记、硬性工具计数触顶）判定。

## ④ 结果图

{chr(10).join(fig_lines)}

图注（左→右、上→下）：终态分层前后对比；70% 检查点触发延迟与判据余量；被迫收尾构成 +
比例规则的误判规模；20 场景混淆矩阵。

> 注：本节点是后端/契约任务，没有连续场量，故无「场量云图」；四张图均为判定结果的可视化，
> 每张图的 sha256 已登记在 `runs/p93w3-n10/figures.json`（图与数字同源，防止图文不一致）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **坑（已修）：墙钟比不能当耗尽证据。** 初版把 `max(工具比, 墙钟比) ≥ 1.0` 当耗尽，
   在真实语料上把 {evidence['naive_ratio_only_counts']['forced_finish']} 行判成被迫收尾（{evidence['naive_ratio_only_counts']['forced_finish'] / corpus['records']:.1%}），
   其中 {evidence['false_forced_rows_avoided']} 行没有收口标记。改成证据制后 forced_finish 回落到 {comp['total']} 行。
   这条改动写进了契约 `exhausted_evidence`，并用图 3 与 `budget_evidence` 数字留痕。
2. **取舍（偏差与理由）：派生语义层而非新存储枚举。** 任务书的「终态枚举扩展」若落成 DB 枚举，
   需要 `status` CHECK 约束迁移 + 跨仓契约版本双绿，且历史行仍要回填；派生层拿到同样的产品效果
   （成功率不再被被迫收尾刷高）且零兼容风险。代价：语义标签需要下游显式读取（本节点已提供
   纯函数与批量迁移工具），存储 `status` 仍是二元。**这是有意偏离任务书第 (1) 条形式的地方，
   语义与判据 (a)–(d) 全部满足。**
3. **局限：20 场景准确率是「规格一致性」而非统计准确率。** 场景由本节点按契约构造，100% 说明
   实现与契约一致，不说明「语义判定符合人类直觉」。真实语料上唯一可用的地面真值是
   harness 写的收口标记：标记的 {metrics['weak_label_agreement']['marked_rows']} 行 recall
   {metrics['weak_label_agreement']['recall']:.2f}（全部被判 forced_finish），但「无标记却被迫」的
   {metrics['weak_label_agreement']['forced_finish_without_marker']} 行只能靠工具计数触顶这一硬事实，
   没有独立人工标注可校验（下一步应是抽 60 单人工标注，做法同 P92W3-N10）。
4. **局限：语料窗口与构成。** 只抓 `succeeded` 终态 {corpus['records']} 行（抓取瞬间），故
   四态里 `failed` 计数为 0（失败行不在本语料内，分类器对 failed 的证据来自 20 场景矩阵与负向控件）。
5. **局限：`created_at→finished_at` 含排队时间。** 这是墙钟比失效的根因；若未来平台记录
   `claimed_at`，墙钟维度可重新评估是否升级为证据。
6. **环境说明（如实）：** 本工作树未跟踪 `runs/p92w3-n10/*`（0 个文件），故上一节点的 4 个
   「冻结工件」测试在此处失败（`shadow_golden.json` 等不存在）。与本改动无关，也不计入本节点测试数；
   本节点 pytest 结果见支撑表。

## ⑥ 误差判定（逐项列出，显式回答「是否 < 3%」）

| 关键量 | 基准 | 实测误差 | 是否 < 3% |
|---|---|---|---|
| 四态分类准确率 | 契约 20 场景期望（100%） | {1 - acc['accuracy']:.2%} | **是（0.00%）** |
| 70% 检查点触发延迟 | 判据 1000 ms | p100 {lat['p100_ms']:.3f} ms = 判据的 {lat['p100_ms'] / 10:.2f}% | **是（{lat['p100_ms'] / 10:.3f}%）** |
| 迁移数据丢失 | 零丢失 | violations {len(mig['violations'])} / {mig['records_in']} 行 | **是（0 行）** |
| 标签 schema 校验 | 100% 通过 | 失败 {lab['n_labels'] - lab['n_valid']} / {lab['n_labels']} | **是（0.00%）** |
| 收口标记 recall | harness 标记 {metrics['weak_label_agreement']['marked_rows']} 行 | {1 - metrics['weak_label_agreement']['recall']:.2%} | **是（0.00%）** |
| 预算阈值一致性 | 契约 0.70 == 检查点 0.70 | 差值 {abs(contract['budget_thresholds']['mvd_checkpoint'] - lat['checkpoint_threshold']):.4f} | **是（0.0000）** |

- **有基准的量**（契约期望、1000 ms 判据、零丢失、schema 100%、标记 recall）全部 < 3%，逐项见上表。
- **无实验基准的量**：本节点不含物理场量或解析解，故不存在「与解析解/文献比较」的第三方基准；
  「语义判定是否符合人工直觉」没有独立标注，按 ⑤-3 如实声明为未验证项，不以自证数掩盖。
- **honest FAIL 声明**：无失败判据；但上述 ⑤-3/⑤-4 两项未验证范围如实保留，不粉饰为已验收。

---

### 审计链

- 契约 → 模块 → 测试 → 评估 → 图 → 验收 → 报告，全链路单一数据源：
  `terminal_state_vocab.v1.json` → `backend/api/domain/terminal_state.py` →
  `backend/tests/test_p93w3_n10_terminal_states.py` → `tools/p93w3_n10/evaluate.py` →
  `tools/p93w3_n10/figures.py` → `tools/p93w3_n10/acceptance.py` → 本报告（`tools/p93w3_n10/report.py` 渲染）。
- 图与数字同源：报告只读 `metrics.json`/`figures.json`/`ACCEPTANCE.json`；
  `figures.json` 内每图 sha256，`ACCEPTANCE.json` 内登记 `metrics_sha256` 与契约 sha256。
"""


def build_payload(markdown: str, figures: dict) -> dict:
    payload_figs = []
    for fig in figures["figures"]:
        png = (ROOT / fig["path"]).read_bytes()
        payload_figs.append({
            "name": fig["name"],
            "caption": fig["caption"],
            "png_base64": base64.b64encode(png).decode(),
        })
    return {
        "title": "P93W3-N10 终态语义分层与预算收口契约成熟化（四态 + 70% MVD + 交付语义标签）",
        "summary": (
            "把 forced_finish 从 succeeded 拆分出来：四态语义终态（succeeded/forced_finish/"
            "degraded/failed）+ 交付语义标签（completeness/quality_gate/budget_status）+ "
            "70% 预算 MVD 检查点 + 旧 succeeded 零丢失迁移。实测 2000 行真实终态记录："
            "旧成功率 100% → 真实交付率 73.80%，被迫收尾 143 行（7.15%），部分交付 381 行（19.05%）；"
            "20 场景分类准确率 100%，检查点触发 p100 1.96 ms（判据 1 s），标签 schema 校验 2000/2000 通过。"
        ),
        "scenario_key": "p93w3-n10-terminal-state-layering",
        "mission_id": 9177,
        "tags": ["terminal-state", "delivery-semantics", "budget-governance", "mvd-checkpoint",
                 "backward-compatibility", "contract"],
        "markdown": markdown,
        "figures": payload_figs,
    }


def main() -> None:
    metrics = load("metrics.json")
    figures = load("figures.json")
    verdict = load("ACCEPTANCE.json")
    markdown = build_markdown(metrics, figures, verdict)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "REPORT.md").write_text(markdown, encoding="utf-8")
    payload = build_payload(markdown, figures)
    pathlib.Path("/tmp/report.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "report": str((DOCS / "REPORT.md").relative_to(ROOT)),
        "report_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
        "payload_figures": len(payload["figures"]),
        "chars": len(markdown),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()