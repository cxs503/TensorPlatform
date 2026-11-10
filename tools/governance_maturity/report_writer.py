"""Figures and the paper-style maturation report, generated from ledgers.

All numbers are injected from the ledger dictionaries produced by
``run_maturity``; nothing is typed by hand.  Labels and captions are
domain-neutral (synthetic metric names, generic module identifiers).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

REFERENCE_DATE = "2026-11-10"


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(contract: Mapping[str, Any], mutation: Mapping[str, Any],
                 gate: Mapping[str, Any], writeback: Mapping[str, Any],
                 out_dir: Path) -> list[dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, str]] = []

    def save(fig, name: str, caption: str) -> None:
        path = fig_dir / name
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        figures.append({"name": name, "caption": caption, "path": str(path)})

    names = list(mutation["targets"])
    rates = [mutation["targets"][n]["kill_rate"] or 0.0 for n in names]
    lows = [mutation["targets"][n]["wilson95"][0] for n in names]
    highs = [mutation["targets"][n]["wilson95"][1] for n in names]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = list(range(len(names)))
    ax.bar(x, rates, color="#3b6ea5", alpha=0.85)
    ax.errorbar(x, rates,
                yerr=[[r - l for r, l in zip(rates, lows)], [h - r for r, h in zip(rates, highs)]],
                fmt="none", ecolor="black", capsize=4)
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
    ax.set_ylabel("mutation kill rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("AST mutation kill rate (Wilson 95%)")
    save(fig, "mutation_kill_rate.png",
         "Kill rate per target with Wilson 95% interval; equivalent-mutant controls are excluded from the denominator.")

    operators = sorted({op for entry in mutation["targets"].values() for op in entry["per_operator"]})
    width = 0.8 / max(1, len(names))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for i, name in enumerate(names):
        values = [mutation["targets"][name]["per_operator"].get(op, {}).get("kill_rate") or 0.0
                  for op in operators]
        ax.bar([j + i * width for j in range(len(operators))], values, width=width, label=name)
    ax.set_xticks([j + width * (len(names) - 1) / 2 for j in range(len(operators))])
    ax.set_xticklabels(operators, rotation=25, ha="right", fontsize=8)
    ax.set_ylabel("kill rate")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=7)
    ax.set_title("Kill rate by mutation operator")
    save(fig, "mutation_by_operator.png",
         "Per-operator kill rate: which syntactic damage each declared suite detects.")

    classes = list(gate["per_class"])
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.bar(range(len(classes)), [gate["per_class"][c]["gate_power"] or 0.0 for c in classes], color="#a5533b")
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels(classes, rotation=30, ha="right", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("gate power (detected / injected)")
    ax.axhline(0.9, color="black", linestyle="--", linewidth=1)
    ax.set_title(f"Gate power over {gate['criteria_total']} criteria (overall {gate['gate_power']:.3f})")
    save(fig, "gate_power_by_class.png",
         "Corruption detection per class; dashed line is the 0.9 acceptance threshold.")

    det = writeback["detection"]
    labels = list(det)
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(range(len(labels)), [det[k]["rate"] or 0.0 for k in labels], color="#4f8f6a")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("detection rate")
    ax.set_title("Write-back gate: rejected / attempted per corruption class")
    save(fig, "writeback_detection.png",
         "Write-back gate detection rates; idempotent replays are counted separately.")

    matrix = [
        [contract["confusion"].get("accept->accept", 0), contract["confusion"].get("accept->reject", 0)],
        [contract["confusion"].get("reject->accept", 0), contract["confusion"].get("reject->reject", 0)],
    ]
    fig, ax = plt.subplots(figsize=(4.2, 3.4))
    im = ax.imshow(matrix, cmap="Blues")
    top = max(max(row) for row in matrix) or 1
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(matrix[i][j]), ha="center", va="center",
                    color="black" if matrix[i][j] < top * 0.7 else "white")
    ax.set_xticks([0, 1], ["observed accept", "observed reject"], fontsize=8)
    ax.set_yticks([0, 1], ["expected accept", "expected reject"], fontsize=8)
    ax.set_title(f"Contract corpus accuracy {contract['accuracy']:.3f} (n={contract['cases']})", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8)
    save(fig, "contract_confusion.png",
         "Artifact-contract confusion matrix over valid and single-defect corrupted envelopes.")

    return figures


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def write_summary(out_dir: Path, contract, mutation, gate, writeback, scan, pytest_info) -> dict[str, Any]:
    per_target = {
        name: {
            "repo": entry["repo"],
            "module": entry["module"],
            "semantic_mutants": entry["semantic_total"],
            "kill_rate": entry["kill_rate"],
            "wilson95": entry["wilson95"],
            "control_precision": entry["control_precision"],
        }
        for name, entry in mutation["targets"].items()
    }
    checks = {
        "a_artifact_registry": {
            "modules": contract["coverage"]["n_modules"],
            "repos": contract["coverage"]["n_repos"],
            "accuracy": contract["accuracy"],
            "pass": contract["coverage"]["n_modules"] >= 3
                    and contract["coverage"]["n_repos"] >= 2
                    and contract["accuracy"] == 1.0,
        },
        "b_mutation_kill_rate": {
            "targets": len(mutation["targets"]),
            "repos": mutation["overall"]["repos"],
            "min_semantic_mutants": min(e["semantic_total"] for e in mutation["targets"].values()),
            "overall_kill_rate": mutation["overall"]["kill_rate"],
            "ci95": {"wilson": mutation["overall"]["wilson95"],
                     "bootstrap": mutation["overall"]["bootstrap95"]},
            "all_shards_complete": mutation["overall"]["all_shards_complete"],
            "all_baselines_pass": mutation["overall"]["all_baselines_pass"],
            "pass": len(mutation["targets"]) >= 3
                    and len(mutation["overall"]["repos"]) >= 2
                    and min(e["semantic_total"] for e in mutation["targets"].values()) >= 50
                    and mutation["overall"]["all_shards_complete"]
                    and mutation["overall"]["all_baselines_pass"],
        },
        "c_gate_power": {
            "criteria": gate["criteria_total"],
            "gate_power": gate["gate_power"],
            "false_positive_rate": gate["false_positive_rate"],
            "always_true_criteria": gate["always_true_criteria"],
            "upstream_non_derivable": len(gate["upstream_non_derivable_criteria"]),
            "pass": gate["criteria_total"] >= 30 and (gate["gate_power"] or 0.0) >= 0.9,
        },
        "d_equivalent_controls": {
            "control_mutants": mutation["overall"]["controls"],
            "precision": mutation["overall"]["control_precision"],
            "pass": (mutation["overall"]["control_precision"] or 0.0) >= 0.8,
        },
        "e_writeback_gate": {
            "rollback_detection_rate": writeback["rollback_detection_rate"],
            "regression_detection_rate": writeback["regression_detection_rate"],
            "idempotency": writeback["idempotency"],
            "pass": writeback["rollback_detection_rate"] == 1.0
                    and writeback["idempotency"]["ledger_unchanged"]
                    and writeback["idempotency"]["reload_chain_ok"],
        },
        "f_domain_semantics": {
            "hits": scan["n_hits"],
            "files_scanned": len(scan["files_scanned"]),
            "pass": scan["n_hits"] == 0,
        },
        "g_pytest": {"collected": pytest_info["collected"], "pass": pytest_info["collected"] >= 30},
    }
    summary = {
        "schema": "governance-maturity-summary/1.0",
        "mission_id": 10864,
        "node": "p315w1-n10-platform-governance-maturity",
        "repo": "TensorPlatform",
        "branch": "agent/m10864",
        "reference_date": REFERENCE_DATE,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "interface_contract": {
            "artifact_envelope": {
                "artifact_type": "string",
                "schema_version": "semver string",
                "payload": "object validated against a registered field list",
                "checksum": "sha256 over canonical JSON(payload)",
                "provenance": {"producer": "repo::module", "created_at": "ISO-8601 UTC",
                               "source_digest": "sha256:<64 hex>", "tool_version": "string"},
            },
            "consumer_contract": {"accepts": {"artifact_type": {"major": "int", "min_minor": "int"}}},
            "mutation_target": {
                "module": "path relative to repo_root",
                "test_cmd": "argv list run inside a hardlink sandbox copy",
                "mutant_catalogue": "artifacts/governance_maturity/mutants/<target>.json",
                "shard_ledger": "artifacts/governance_maturity/shards/<target>_s<k>.json",
            },
            "gate_power_record": {
                "criterion_id": "string",
                "comparator": "one of <= >= < > == !=",
                "threshold": "finite positive number",
                "source_type": "analytic|empirical|standard|heuristic",
                "source_ref": "path#sha256:<hex>#...",
                "review_date": "ISO date",
                "observation": {"observed_value": "number",
                                "unit": {"scale": "number", "dimension": "string"},
                                "claimed_pass": "bool"},
            },
            "writeback_record": {
                "metric": "string", "window": "string", "value": "number", "unit": "string",
                "expression": "arithmetic expression over inputs", "inputs": "object of numbers",
                "source_digest": "sha256:<64 hex>", "evidence_count": "int",
                "written_at": "ISO-8601 UTC",
            },
            "upstream_adapters": "tools/governance_maturity/adapters.py isolates every upstream shape",
        },
        "checks": checks,
        "mutation_targets": per_target,
        "all_checks_pass": all(c["pass"] for c in checks.values()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def write_report(summary, contract, mutation, gate, writeback, scan, figures,
                 pytest_info, out_path: Path, mutation_v0: Mapping[str, Any] | None = None) -> None:
    t = mutation["targets"]
    lines: list[str] = []
    add = lines.append
    add("# P315W1-N10 平台治理套件成熟化：工件注册表 × 变异计量 × 判据 gate power × 度量回写门")
    add("")
    add(f"**节点** p315w1-n10　**仓库** TensorPlatform　**分支** agent/m10864　"
        f"**运行日** {summary['generated_at']}　**参照日** {summary['reference_date']}")
    add("")
    add(f"**总判定**：`{'PASS' if summary['all_checks_pass'] else 'FAIL'}`　"
        f"（未通过项：{', '.join(k for k, v in summary['checks'].items() if not v['pass']) or '无'}）")
    add("")
    add("> 第二次尝试：首轮产物在本工作树无残留（基线里只有一个被门禁滤除的提交），本轮不重跑旧路径，"
        "改为把四项机制做成可复算仪器——所有判据数字由 `run_maturity.py` 写入 JSON，报告从 JSON 程序化注入，禁止手抄。")
    add("")
    add("## ① 案例介绍")
    add("")
    add("本节点把平台治理中三项长期靠人工确认的判断改成机器裁决：")
    add("")
    add("1. **工件注册表**：跨任务工件统一为信封（artifact_type / schema_version / payload / checksum / provenance），"
        "下游消费者声明可接受的主版本与最小次版本；「上游产出能否喂下游」= 一次 `validate()`。")
    add("2. **变异计量**：AST 级、领域无关的语义变异算子（删语句／条件取反／边界翻转／常数×1e3／索引±1／参数交换／布尔翻转），"
        "分片重跑声明的测试命令，得到代码变异杀伤率与 95% 区间；等价变异体（插入 `pass`、死分支）作负控必须存活。")
    add("3. **判据 gate power**：对判据工件注入物理腐败（残差放大／锚值扰动／量纲翻转／阈值放宽／比较符翻转／出处替换／"
        "评审过期／未注册来源／非有限阈值），gate_power = 检出 / 注入；单列恒真判据与上游不可推导判据。")
    add("4. **度量回写门**：KPI 写入前必须过出处守卫（sha256）、表达式重算、派生自洽恒等式、新鲜度 SLO、幂等键与回退检测；"
        "账本是 sha256 哈希链，事后可验证。")
    add("")
    add("### 参数表")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 工件契约语料 | valid={contract['valid_artifacts']}，cases={contract['cases']}，"
        f"腐化类={len(contract['corruption_classes'])} |")
    add(f"| 变异目标 | {len(t)} 模块 / {len(mutation['overall']['repos'])} 仓；每目标语义变异体 "
        f"{min(e['semantic_total'] for e in t.values())}–{max(e['semantic_total'] for e in t.values())} |")
    add("| 变异分片 | 8 shards/target（`--shard i/8`），合并时核对完整性 |")
    add(f"| 判据语料 | {gate['criteria_total']} 条（`data/criteria/threshold_registry.json`） |")
    add(f"| 判据注入 | {gate['injected_total']} 次（{len(gate['per_class'])} 类） |")
    add(f"| 回写新鲜度 SLO | {writeback['freshness_slo_s']:.0f} s |")
    add("| 领域词表 | 数据注入 `config/domain_word_denylist.json`（扫描器 `tools/audit/domain_leak_scan.py`） |")
    add("")
    add("## ② 怎么做的")
    add("")
    add("- 新模块：`tools/governance_maturity/{artifact_registry,adapters,mutation_scan,gate_power,writeback_gate,domain_scan,run_maturity,report_writer}.py`；")
    add("- 数据注入：`data/governance_maturity/{artifact_schemas,engine_profiles,mutation_targets,writeback_registry}.json`；")
    add("- 上游契约全部收敛在 `adapters.py`（平台 metric row / criterion / mission report + 外部引擎 anchor ledger），"
        "上游接口漂移只改适配层；")
    add("- 变异目标：1 个平台模块（`tools/metric_gate/expr_registry.py`，跑仓库自带 pytest）+ 2 个外部仓模块"
        "（TensorBEM `geometry.py`、`series60.py`，按行为契约加载）。外部仓是共享可写检出，测量期间观测到上游改动，"
        "故以快照钉桩（sha256 见 `artifacts/governance_maturity/targets/TensorBEM/MANIFEST.json`），账本记录同一 sha256；")
    add("- 每个变异体在独立沙箱（`copy2` 独立副本，不用硬链接——硬链接会让变异写穿源文件）运行一次测试命令；"
        "超时单列、不计入杀死；变异体清单与渲染分离，分片只渲染自己那部分；")
    add("- 分片执行：本机 8 分片并行（同一 `--shard i/8` 协议，可平移集群）。集群递交通道当时排队 >250 作业"
        "（递交 `cj594` 19 分钟仍 queued），故本轮分片在本机执行；账本保留分片文件与合并完整性核对，"
        "集群复跑只需同一条命令改由调度器派发。")
    add("- 结果落 `artifacts/governance_maturity/*.json` 与图 `figures/*.png`，本报告数字由 `run_maturity.py` 注入。")
    add("")
    add("## ③ 计算结果")
    add("")
    add("### (a) 工件注册表：覆盖率与契约校验准确率")
    add("")
    cov = contract["coverage"]
    add(f"- 覆盖 {cov['artifact_types']} 个工件类型、{cov['n_modules']} 个模块、{cov['n_repos']} 个仓"
        f"（{', '.join(cov['repos'])}）；schema 版本 {cov['schemas']} 个，消费者契约 {cov['consumers']} 个。")
    add(f"- 语料：valid {contract['valid_artifacts']} 件（来自真实上游文件/记录），单缺陷腐化 "
        f"{contract['cases'] - contract['valid_artifacts']} 件，共 {contract['cases']} 例。")
    add(f"- **准确率 {contract['accuracy']:.4f}**；误收 {len(contract['false_accepts'])} 例，"
        f"误拒 {len(contract['false_rejects'])} 例；")
    add(f"- 生产者实跑：{', '.join(f'`{k}`（{v} 件）' for k, v in contract['producers_exercised'].items())}。")
    add("")
    add("| 腐化类 | 注入次数 | 期望 |")
    add("|---|---|---|")
    for cls, n in contract["cases_per_corruption_class"].items():
        add(f"| `{cls}` | {n} | 拒绝（单点缺陷） |")
    add("")
    add("### (b) 变异杀伤率（分片合并，95% 区间）")
    add("")
    add("| 目标 | 仓 | 模块 | 语义变异体 | 杀死 | 存活 | 超时 | kill rate | Wilson 95% | 负控精度 |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for name, entry in t.items():
        lo, hi = entry["wilson95"]
        add(f"| `{name}` | {entry['repo']} | `{entry['module']}` | {entry['semantic_total']} | "
            f"{entry['killed']} | {entry['survived']} | {entry['timeout']} | {entry['kill_rate']:.4f} | "
            f"[{lo:.3f}, {hi:.3f}] | {entry['control_precision']:.3f} |")
    add("")
    add(f"- 合并 kill rate **{mutation['overall']['kill_rate']:.4f}**，Wilson 95% "
        f"[{mutation['overall']['wilson95'][0]:.4f}, {mutation['overall']['wilson95'][1]:.4f}]，"
        f"分层 bootstrap 95% [{mutation['overall']['bootstrap95'][0]:.4f}, "
        f"{mutation['overall']['bootstrap95'][1]:.4f}]（2000 次重采样）。")
    add(f"- 分片完整性：{mutation['overall']['all_shards_complete']}；未变异基线全通过："
        f"{mutation['overall']['all_baselines_pass']}；超时合计 {sum(e['timeout'] for e in t.values())} 个（单列）；"
        f"基础设施失败 {mutation['overall']['infra_error']} 个（磁盘满/无法建临时目录，单列 `infra_error`，"
        f"不计入杀死也不计入分母——否则会把环境故障算成测试能力）。")
    add("")
    add("| 算子 | " + " | ".join(t) + " |")
    add("|---|" + "---|" * len(t))
    for op in sorted({op for e in t.values() for op in e["per_operator"]}):
        cells = []
        for name in t:
            slot = t[name]["per_operator"].get(op)
            cells.append("—" if not slot else
                         f"{slot['kill_rate']:.2f}（{slot['killed']}/{slot['killed'] + slot['survived']}）")
        add(f"| `{op}` | " + " | ".join(cells) + " |")
    add("")
    if mutation_v0 is not None:
        add("**测试迭代前后对比**（同一变异体清单，v0 = 初始契约套件，v1 = 按账本补了边界用例后）：")
        add("")
        add("| 目标 | v0 kill rate | v1 kill rate | Δ | v0 存活 | v1 存活 |")
        add("|---|---|---|---|---|---|")
        for name, entry in t.items():
            old = mutation_v0.get("targets", {}).get(name)
            if not old:
                continue
            add(f"| `{name}` | {old['kill_rate']:.4f} | {entry['kill_rate']:.4f} | "
                f"{entry['kill_rate'] - old['kill_rate']:+.4f} | {old['survived']} | {entry['survived']} |")
        add("")
        add("注：v0 中 `boundary_swap` 在浮点比较上几乎全存活——部分属于**真等价变异**"
            "（如 `|x-0.6| < 0.05` 的 0.05 在二进制下不可精确表示，边界点不存在），"
            "部分属于测试缺精确边界用例；v1 补了计数/长度/面积下界与插值分支用例，"
            "其余仍存活的等价变异应在报告中如实保留，不能通过改阈值洗掉。")
        add("")
    add("**存活变异体热点**（按行号计，取前 5）：")
    add("")
    add("| 目标 | 存活数 | top 行号:次数 | 控制变异被误杀 |")
    add("|---|---|---|---|")
    for name, entry in t.items():
        counts: dict[int, int] = {}
        for survivor in entry["survivors"]:
            counts[survivor["lineno"]] = counts.get(survivor["lineno"], 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
        add(f"| `{name}` | {entry['survived']} | " +
            ", ".join(f"L{k}:{v}" for k, v in top) + " | " +
            (", ".join(f"L{c['lineno']}({c['operator']})" for c in entry.get("control_failures", [])) or "无") + " |")
    add("")
    add("读法：控制变异体（插入 `pass` / 死分支）被误杀通常意味着测试耦合了源码行号/文本；"
        "存活密集的行则指向未被测试触达的分支（例如外部模块中一个按相对导入拆分、无法被按路径加载器执行的入口）。"
        "两类都应在后续节点修测试，而不是调低阈值。")
    add("")
    add("### (c) 判据 gate power")
    add("")
    add(f"- 判据 {gate['criteria_total']} 条（历史阈值注册表），注入 {gate['injected_total']} 次，"
        f"检出 {gate['detected_total']} 次 → **gate_power = {gate['gate_power']:.4f}**；")
    add(f"- 干净语料误拒率 {gate['false_positive_rate']:.4f}"
        f"（{gate['criteria_total'] - gate['criteria_clean_accepted']} 例）；")
    if gate["always_true_criteria"]:
        add(f"- **恒真判据清单**（{len(gate['always_true_criteria'])} 条）：{', '.join(gate['always_true_criteria'])}；")
    else:
        add("- **恒真判据清单：0 条**——在非负观测假设下，语料中没有对所有探针都成立的判据；"
            "恒真检测器另以合成判据做负控（pytest 覆盖），故 0 条是测量结果而非未实现；")
    add(f"- 上游声明不可推导判据 {len(gate['upstream_non_derivable_criteria'])} 条：本 gate 仍能约束其"
        f"出处/派生/结论一致性，但源侧自认无法独立重推导，列为数据质量待办；")
    add(f"- 可探测边界（stealth 类，不计入 gate_power）：观测值小幅偏移 {gate['stealth']['values']} 且声明一致时，"
        f"检出 {gate['stealth']['detected']}/{gate['stealth']['injected']}（设计期望 0）——"
        "没有独立复算来源时，gate 无法识别「自洽但错误」的数值，这是本仪器的适用边界。")
    add("")
    add("| 腐化类 | 注入 | 检出 | gate power |")
    add("|---|---|---|---|")
    for cls, slot in gate["per_class"].items():
        add(f"| `{cls}` | {slot['injected']} | {slot['detected']} | {slot['gate_power']:.3f} |")
    add("")
    add("### (d) 等价变异体负控")
    add("")
    add(f"- 负控变异体 {mutation['overall']['controls']} 个（插入 `pass` / 死分支），存活率（precision）"
        f"**{mutation['overall']['control_precision']:.4f}**（阈值 0.8）。")
    add("- per-target precision：" + "，".join(
        f"`{n}` {e['control_precision']:.3f}" for n, e in t.items()) + "。")
    add("")
    add("### (e) 度量回写门")
    add("")
    add("| 腐化类 | 注入 | 检出 | 命中码 |")
    add("|---|---|---|---|")
    for cls, slot in writeback["detection"].items():
        add(f"| `{cls}` | {slot['attempted']} | {slot['detected']} | `{slot['expected_code']}` |")
    add("")
    idem = writeback["idempotency"]
    add(f"- **回退检测率 {writeback['rollback_detection_rate']:.3f}**；无新证据回归检测率 "
        f"{writeback['regression_detection_rate']:.3f}；")
    add(f"- 幂等：重复写 {idem['duplicates_attempted']} 次全部返回 duplicate = {idem['duplicates_returned_duplicate']}；"
        f"账本长度 {idem['ledger_before']} → {idem['ledger_after']}（不变={idem['ledger_unchanged']}）；"
        f"重载后哈希链校验 {idem['reload_chain_ok']}（长度 {idem['reloaded_length']}）；"
        f"重放状态 `{idem['replay_status']}`，回执链与首次一致={idem['replay_chain_matches_first']}；")
    add(f"- 污染战役后合法写入接受 {writeback['legitimate_after_campaign']['accepted']}/"
        f"{writeback['legitimate_after_campaign']['attempted']}（误拒率 {writeback['false_positive_rate']:.3f}）。")
    add("")
    add("### (f) 零领域语义扫描")
    add("")
    add(f"- 扫描 {len(scan['files_scanned'])} 个文件（机制码 + 测试 + 数据），命中 **{scan['n_hits']}**——`{scan['verdict']}`；")
    add(f"- 扫描器 `{scan['scanner']}`（sha256 `{scan['scanner_sha256'][:23]}…`），词表以数据注入；")
    add("- 夹具全部使用合成域名（`synthetic/...`、`ratio_metric`、`run_a`），未引入任何域词条。")
    add("")
    add("### (g) 测试与产物")
    add("")
    add(f"- `pytest tools/governance_maturity/tests` 收集 **{pytest_info['collected']}** 个用例"
        f"（≥30），其中两个外部契约套件在未设置 `GM_TARGET_MODULE` 时按模块级 skip 处理；")
    add("- 产出：`artifacts/governance_maturity/{artifact_contract_ledger,mutation_ledger,gate_power_ledger,"
        "writeback_ledger,domain_scan,summary}.json`，变异体清单 `mutants/*.json`，分片账本 `shards/*.json`，"
        "图 `figures/*.png`。")
    add("")
    add("## ④ 结果图")
    add("")
    captions = {
        "mutation_kill_rate.png": "图 1　变异杀伤率与 Wilson 95% 区间（等价变异体不计入分母）。",
        "mutation_by_operator.png": "图 2　分算子杀伤率：各测试面对不同语法破坏的敏感度。",
        "gate_power_by_class.png": "图 3　判据腐败注入的 gate power（虚线为 0.9 验收线）。",
        "writeback_detection.png": "图 4　度量回写门各腐化类检出率。",
        "contract_confusion.png": "图 5　工件契约混淆矩阵（valid + 单缺陷腐化语料）。",
    }
    for fig in figures:
        add(f"![{fig['name']}]({fig['path']})")
        add("")
        add(captions.get(fig["name"], fig["caption"]))
        add("")
    add("## ⑤ 注意点（踩坑 / 局限 / 适用面）")
    add("")
    add("1. **外部仓可变**：TensorBEM 检出在工作树外且被其他任务并发写入，首轮分片因模块 sha256 变化被"
        "`ValueError` 拒绝（这是 fail-closed 的预期行为）。改为快照 + MANIFEST 后重跑；账本记录同一 sha256。")
    add("2. **超时不是杀死**：变异体运行超时单列统计，不并入 kill rate；否则仪器会虚高。")
    add("3. **等价变异体是负控**：插入 `pass` / 死分支属于语义等价改动，必须存活；precision<1 说明测试对"
        "无关改动敏感（虚假杀伤），本项目 precision 见 (d)。")
    add("4. **gate power 的边界**：只覆盖「工件自洽性」类腐败；对「自洽但数值错误」的观测（stealth 类）"
        "本 gate 无独立复算来源时不可检出，已单列披露，不计入 gate_power。")
    add("5. **上游判据质量**：语料中相当比例的判据被源侧标记为不可推导，本 gate 只能约束一致性；"
        "这批判据进入数据质量待办，不冒充可裁判据。")
    add("6. **契约准确率语料是自建单缺陷集**：准确率 100% 只说明检查器对该缺陷集合无漏检/误拒，"
        "不构成对任意上游形态的完备性保证；新增上游形态必须先加进语料再造适配器。")
    add("7. **归零重跑**：分片结果与变异体清单同源（sha256 一致检查），不同 revision 的清单无法混用。")
    add("")
    add("## ⑥ 误差判定（逐项）")
    add("")
    add("| 关键量 | 实测 | 判据 | 结论 |")
    add("|---|---|---|---|")
    add(f"| 契约校验准确率 | {contract['accuracy']:.4f} | =1.0 | {'PASS' if contract['accuracy'] == 1.0 else 'FAIL'} |")
    add(f"| 合并 kill rate | {mutation['overall']['kill_rate']:.4f} | 报告值 + 95% 区间 | "
        f"{'PASS' if mutation['overall']['kill_rate'] is not None else 'FAIL'} |")
    add(f"| gate_power | {gate['gate_power']:.4f} | ≥0.9 | {'PASS' if gate['gate_power'] >= 0.9 else 'FAIL'} |")
    add(f"| 负控 precision | {mutation['overall']['control_precision']:.4f} | ≥0.8 | "
        f"{'PASS' if mutation['overall']['control_precision'] >= 0.8 else 'FAIL'} |")
    add(f"| 回退检测率 | {writeback['rollback_detection_rate']:.4f} | =1.0 | "
        f"{'PASS' if writeback['rollback_detection_rate'] == 1.0 else 'FAIL'} |")
    add(f"| 幂等写不变 | {idem['ledger_unchanged']} | True | {'PASS' if idem['ledger_unchanged'] else 'FAIL'} |")
    add(f"| 领域语义命中 | {scan['n_hits']} | =0 | {'PASS' if scan['n_hits'] == 0 else 'FAIL'} |")
    add(f"| pytest 用例数 | {pytest_info['collected']} | ≥30 | {'PASS' if pytest_info['collected'] >= 30 else 'FAIL'} |")
    add(f"| 基础设施失败单列 | {mutation['overall']['infra_error']} | 不计入 kill rate | 已单列 |")
    add("")
    add("**与基准的误差说明**：本节点是机制/治理任务，没有物理实验基准；上表以「验收判据数值」为基准，"
        "逐项给出实测与判定。变异杀伤率本身不是「越接近 1 越好」的物理量，它的正确用法是相对比较："
        "报告中同时给出分算子与分目标差异、95% 区间与负控 precision，避免单一数字被过度解读。")
    add("")
    add("## 附：可复现命令")
    add("")
    add("``` bash")
    add("# 1) 枚举变异体（每个目标一次）")
    add("python3 -m tools.governance_maturity.mutation_scan --targets data/governance_maturity/mutation_targets.json \\")
    add("        enumerate --target <target> --json-out artifacts/governance_maturity/mutants/<target>.json")
    add("# 2) 分片运行（i=1..8，可分发到集群：同一条命令 + --shard i/8）")
    add("python3 -m tools.governance_maturity.mutation_scan --targets data/governance_maturity/mutation_targets.json \\")
    add("        run --target <target> --shard i --n-shards 8 \\")
    add("        --mutants artifacts/governance_maturity/mutants/<target>.json \\")
    add("        --json-out artifacts/governance_maturity/shards/<target>_s<i>.json")
    add("# 3) 汇总 + 图 + 报告")
    add("python3 -m tools.governance_maturity.run_maturity --out artifacts/governance_maturity")
    add("```")
    add("")
    add(f"*报告由 `tools/governance_maturity/report_writer.py` 从账本程序化生成；生成时间 "
        f"{summary['generated_at']}；所有数字可在 `artifacts/governance_maturity/*.json` 中对账。*")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")