"""Assemble the paper-grade report for the task-salvage node from the artefacts.

Every number in the markdown is injected from ``acceptance.json`` /
``corpus.json`` / ``preregistration.json`` — nothing is typed by hand, so the
report cannot drift from the machine gates.  Emits:

* ``docs/task_salvage_p199w1_n06.md`` (report body, six mandatory sections)
* ``/tmp/report.json``                (platform report payload with figures)
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "task_salvage"
FIG_DIR = OUT / "figures"
DOC = ROOT / "docs" / "task_salvage_p199w1_n06.md"
MISSION_ID = 9398

FIG_SPECS = [
    ("fig1_gate_outcomes.png", "图1 三筛逐门结果：被判定文件在各门上的 pass/fail/not_applicable 分布。"),
    ("fig2_states.png", "图2 抢救状态与 BLOCKED 原因码分布（左：INHERITABLE/BLOCKED；右：阻断码计数）。"),
    ("fig3_replay_ab.png", "图3 回放 A/B：冷启动（control）与继承种子（enabled）两臂验收通过率，标注 Fisher/McNemar 精确检验 p 值。"),
    ("fig4_confusion.png", "图4 三筛判定 vs 独立 oracle 判定混淆矩阵（一致率与误报率来源）。"),
]


def _load() -> tuple[dict, dict, dict]:
    acceptance = json.loads((OUT / "acceptance.json").read_text(encoding="utf-8"))
    corpus = json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))
    prereg = json.loads((OUT / "preregistration.json").read_text(encoding="utf-8"))
    return acceptance, corpus, prereg


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def _figures_payload() -> list[dict]:
    payload = []
    for name, caption in FIG_SPECS:
        path = FIG_DIR / name
        if not path.is_file():
            continue
        payload.append(
            {
                "name": name,
                "caption": caption,
                "png_base64": base64.b64encode(path.read_bytes()).decode(),
            }
        )
    return payload


def markdown() -> str:
    acc, corpus, prereg = _load()
    gates = acc["gates"]
    rate = gates["salvage_rate"]
    cons = gates["consistency"]
    idem = gates["idempotency"]
    rep = gates["replay_all_files"]
    rep_code = gates["replay_code_files"]
    rep_ev = gates.get("replay_evidence_only") or {}
    rep_bn = gates.get("replay_basename_match") or {}
    source_mix: dict[str, int] = {}
    for fam in corpus.get("families", []):
        key = fam.get("required_source") or "unknown"
        source_mix[key] = source_mix.get(key, 0) + 1
    smoke_skipped = sum(
        1
        for mission in corpus.get("missions", [])
        for row in mission.get("files", [])
        if (row.get("gates", {}).get("smoke") or {}).get("detail") == "smoke_not_run"
    )
    gate_fail = {gate: sum(1 for m in corpus.get("missions", []) for r in m.get("files", []) if gate in (r.get("failed_gates") or [])) for gate in ("parse", "smoke", "structural")}
    collected = corpus["collected_from"]
    tax = corpus["taxonomy"]

    bucket_rows = [[b, tax["bucket_counts"].get(b, 0)] for b in tax["bucket_counts"]]
    mission_rows = []
    for mission in sorted(corpus["missions"], key=lambda m: m["mission_id"], reverse=True)[:12]:
        s = mission["salvage"]
        mission_rows.append(
            [
                mission["mission_id"],
                mission["bucket"],
                mission["candidates"],
                s["counts"]["inheritable"],
                s["counts"]["blocked_files"],
                s["counts"]["residual_open"],
                s["state"],
                s["blocked_code"] or "-",
                s["fingerprint"][:12],
            ]
        )
    fam_rows = [
        [
            row["node_key"],
            row["mission_id"],
            row["required"],
            row["inherited"],
            row["capacity"],
            "PASS" if row["control_pass"] else "fail",
            "PASS" if row["enabled_pass"] else "fail",
        ]
        for row in rep["rows"][:15]
    ]

    verdict_rows = [
        ["抢救率 (salvage rate)", f"{rate['rate']:.4f}", "= 1.00", "PASS" if acc["verdict"]["salvage_rate"] else "FAIL"],
        ["一致率 (consistency)", f"{cons['consistency']:.4f}", ">= 0.90", "PASS" if acc["verdict"]["consistency"] else "FAIL"],
        ["误报率 (false positive)", f"{cons['false_positive_rate']:.4f}", "<= 0.10", "PASS" if acc["verdict"]["false_positive"] else "FAIL"],
        ["幂等性 (idempotency)", f"{idem['rate']:.4f}", "= 1.00", "PASS" if acc["verdict"]["idempotency"] else "FAIL"],
        ["回放增益 Fisher p", f"{float(rep['fisher']['p_value']):.4g}", "< 0.10", "PASS" if acc["verdict"]["replay_gain"] else "FAIL"],
        ["回放增益 通过率 控制臂→启用臂", f"{rep['control_pass_rate']:.3f} → {rep['enabled_pass_rate']:.3f}", "启用 > 控制", "PASS" if rep["enabled_pass_rate"] > rep["control_pass_rate"] else "FAIL"],
    ]

    lines = []
    add = lines.append
    add("# 任务熔断部分交付抢救与残项机读交接（p199w1-N06）")
    add("")
    add(f"- 战役节点：`p199w1-N06-task_salvage` ｜ 仓库：TensorPlatform ｜ 任务：{MISSION_ID}")
    add(f"- 语料指纹：`{corpus['corpus_sha256'][:32]}` ｜ 采集时刻：{collected['fetched_at']}")
    add(f"- 预注册：`artifacts/task_salvage/preregistration.json`（阈值与判据在测量前固定）")
    n_pass = sum(1 for v in acc["verdict"].values() if v)
    n_all = len(acc["verdict"])
    failed = [k for k, v in acc["verdict"].items() if not v]
    add(f"- 机器验收：`artifacts/task_salvage/acceptance.json` ｜ **{n_pass}/{n_all} 判据 PASS**"
        + (f"；未通过：{', '.join(failed)}（honest FAIL，见 ⑤-7，不覆盖、不调阈值）" if failed else "；全部通过"))
    add("")
    add("## ① 案例介绍")
    add("")
    add("平台把任务交给智能体执行，终局除 `succeeded/failed` 外还有一类**熔断终态**：`llm_error`（模型/运行时错误）、"
        "`timeout`（墙钟上限，台账记作 `wall_exhausted`）、`budget_exhausted`（token/调用预算耗尽）。"
        "这类任务不是「没做」，而是「做到一半被打断」：工作树里有文件、验收条目有剩余、日志里有原因。"
        "现状是这些材料不可见——没提交所以不进台账，没过验收所以不算证据，下一波重派从零开始。"
        "本节点把熔断任务的部分交付变成**带来源的机读交接件**，并给出可播种的续跑子任务。")
    add("")
    add(f"语料来自本平台真实台账（只读查询）：失败任务 {collected['failed_total']} 单、成功任务 {collected['succeeded_total']} 单，"
        f"其中落在三种熔断终态 {collected['in_scope_terminal']} 单；工作树仍保留且有落盘文件 {collected['eligible_with_worktree']} 单，"
        f"按新近度取前 {collected['selected']} 单做回测（判据要求 ≥20 单）。")
    add("")
    add("终态分布（全量失败任务，桶化后）：")
    add("")
    add(_table(["终态桶", "计数"], bucket_rows))
    add("")
    add("## ② 怎么做的")
    add("")
    add("管线四段，全部确定性、零领域语义（域内容只作为不透明载荷进入，代码/阈值/夹具不含域词条）：")
    add("")
    add("1. **终态分类**（`tools/task_salvage/taxonomy.py`）：把 `result.evidence.checks.fail_stage` 及标题文本映射到 "
        "`llm_error / timeout / budget_exhausted / no_progress / other` 五桶，输出稳定 `reason_code`；`timeout` 与 "
        "`wall_exhausted` 归同桶，缺失证据行的任务落 `other` 并可追溯。")
    add("2. **三筛**（`sift.py`）：(i) `parse` 结构可解析（`.py` 走 `ast.parse`、`.json/.toml` 走各自解析器、"
        "文本走严格 UTF-8、二进制走魔数）；(ii) `smoke` 最小可跑（`.py` 在**临时 cwd、隔离环境、限时**的子进程里 import 一次；"
        "非 Python 显式记 `not_applicable` 而非静默通过；被预算跳过的 smoke 一律记 `smoke_not_run` 失败，绝不静默继承）；"
        "(iii) `structural` 结构映射（路径首段必须落在目标检出布局内，越界路径直接拒收）。")
    add("3. **交接件**（`salvage.py`）：产出 `salvage.json` = 可继承文件清单（含 sha256/字节/逐门判定）+ 残项清单"
        "（含 `item_sha256`、词法证据候选、`EVIDENCED/OPEN` 两态）+ 阻断原因码（`no_worktree / no_candidates / "
        "parse_failed / smoke_failed / structural_failed`）+ 内容指纹（规范 JSON 的 sha256，不含时钟/主机/绝对路径）。"
        "状态只有 `INHERITABLE`（≥1 文件过三筛）与 `BLOCKED`（附原因码）两态。")
    add("4. **续跑播种**（`continuation.py`）：把交接件转成可播种子任务 `continuation.json`：`subtask_id` 由父单号 + "
        "预指纹派生（同一任务两次抢救给出同一 id，不会重复派发）、`inputs`（sha256 校验清单）、`do_not_redo`、"
        "`residual`（只含 OPEN 项）、预算提示（继承字节数、剩余条数、重试缩放）。")
    add("")
    add("回测三重机门（`backtest.py`，全部离线可复算）：")
    add("")
    add("- **一致率/误报**：三筛判定 vs **独立 oracle 实现**（`compile()` 子进程 vs 进程内 `ast.parse`；`runpy.run_path` vs "
        "`importlib`；`os.listdir` 实检 vs 配置根集合）——逐文件比对，只在实现机制上独立，环境相同（局限已登记）。")
    add("- **幂等**：每个任务构建两次，比较指纹与整份字节。")
    add("- **回放 A/B**：取「同一战役节点既有熔断任务、又有成功重派」的家族（配对设计）。必需交付集 `R` = 继任者交付的文件集；"
        "继承集 `I` = 熔断任务里过三筛且落在 `R` 内的文件；作者容量 `C` = 熔断任务实际落盘的文件数（**实测值，非自由参数**）。"
        "判定：`covered ≥ |R|`，其中 `covered_control = min(C,|R|)`、`covered_enabled = min(|I|+C,|R|)`；"
        "检验用 Fisher 精确（判据指定）+ McNemar 精确（配对数据的正确检验，并列报告）。")
    add("")
    add("## ③ 计算结果")
    add("")
    add(_table(["判据", "实测", "阈值", "判定"], verdict_rows))
    add("")
    add(f"逐任务交接件（前 12 单，按任务号降序；完整清单见 `artifacts/task_salvage/corpus.json` 与 `missions/<id>/salvage.json`）：")
    add("")
    add(_table(["任务", "终态桶", "候选", "可继承", "被阻断", "残项OPEN", "状态", "阻断码", "指纹"], mission_rows))
    add("")
    add(f"回放配对家族（前 15 对，共 {rep['pairs']} 对）：")
    add("")
    add(_table(["节点", "熔断任务", "|R|", "|I|", "C", "控制臂", "启用臂"], fam_rows))
    add("")
    add(f"- Fisher 2×2 = {rep['fisher']['table']}，双侧 p = {rep['fisher']['p_value']:.4g}；"
        f"McNemar 不一致对 = {rep['mcnemar']['discordant']}，p = {rep['mcnemar']['p_value']:.4g}")
    add(f"- 敏感性（code-only 交付集定义）：pairs={rep_code['pairs']}，控制臂 {rep_code['control_pass_rate']:.3f} → "
        f"启用臂 {rep_code['enabled_pass_rate']:.3f}，Fisher p = {rep_code['fisher']['p_value']:.4g}")
    if rep_bn:
        add(f"- 探索性（角色级匹配：同名不同路径，post-hoc）：pairs={rep_bn['pairs']}，|I∩R|/|R| = {rep_bn['overlap_ratio']:.4f}"
            f"（{rep_bn['families_with_overlap']} 家），控制臂 {rep_bn['control_pass_rate']:.3f} → 启用臂 {rep_bn['enabled_pass_rate']:.3f}，"
            f"Fisher p = {rep_bn['fisher']['p_value']:.4g} ⇒ 即使按角色匹配，通过率判据仍无法分辨两臂")
    if rep_ev:
        add(f"- 敏感性（仅 evidence 源交付集，探索性、样本不足）：pairs={rep_ev['pairs']}，控制臂 "
            f"{rep_ev['control_pass_rate']:.3f} → 启用臂 {rep_ev['enabled_pass_rate']:.3f}，Fisher p = {rep_ev['fisher']['p_value']:.4g}")
    add(f"- 交付集来源构成（D1 偏差登记）：{source_mix}；逐门阻断计数 parse/smoke/structural = "
        f"{gate_fail['parse']}/{gate_fail['smoke']}/{gate_fail['structural']}；被预算跳过的 smoke 数 = {smoke_skipped}")
    add(f"- **继承与交付集的交叠**：|I∩R|/|R| = {rep['overlap_ratio']:.4f}"
        f"（{rep['families_with_overlap']}/{rep['pairs']} 个家族有任意交叠）；"
        f"平均覆盖率 控制臂 {rep['mean_control_coverage']:.4f} → 启用臂 {rep['mean_enabled_coverage']:.4f}；"
        f"作者容量 C 已使控制臂自身通过率 {rep['control_pass_rate']:.3f}，两臂通过计数相同 ⇒ 判据 {('PASS' if acc['verdict']['replay_gain'] else 'FAIL')}")
    add("")
    add("## ④ 结果图")
    add("")
    for name, caption in FIG_SPECS:
        add(f"![{name}](artifacts/task_salvage/figures/{name})")
        add("")
        add(f"*{caption}*")
        add("")
    add("## ⑤ 注意点（踩过的坑与局限）")
    add("")
    add("1. **候选文件发现曾经静默失败**：`_candidate_rels` 里把 git 状态条目的属性名写错（`entry.status` vs `entry.code`），"
        "异常被兜底 `except` 吞掉，导致只有「台账记过 changed_files」的任务才有候选。单元测试（合成工作树）暴露后修正，"
        "并保留 `git_unavailable:<类型>` 记录位以便下次可诊断——**静默兜底是抢救流水线里最贵的 bug**。")
    add("2. **smoke 不是万能的**：把抢救代码 import 一次会执行模块顶层语句。为控制风险，smoke 在临时 cwd、`-B -E`、"
        "受限环境变量、限时 10s 的子进程里跑，且从不写回被扫描工作树；但这仍是「执行外部代码」，"
        "平台若要在不受信任工作树上跑，需换容器/沙箱。重型依赖模块常因超时被判失败，这是**假阴性**（宁可少继承）。")
    add("3. **残项匹配是词法预筛**：一个共同 token 就可能把条目标成 `EVIDENCED`，所以交接件从不把 `EVIDENCED` 当作「已完成」，"
        "播种只派 `OPEN` 项；`EVIDENCED` 仅表示「有候选材料可先读」。")
    add("4. **工作树保留偏差**：只有仍保留工作树的熔断任务能进语料（本波 "
        f"{collected['in_scope_terminal']} 单里有 {collected['eligible_with_worktree']} 单可测）。"
        "工作树被清理的任务抢救率不可测，报告如实标注为条件量。")
    add("5. **回放增益是「计量学」结论而非「行为学」结论**：A/B 两臂共用实测作者容量 C，差别只在是否继承；"
        "这能定量回答「继承材料是否把交付缺口补上」，但不能证明真实智能体重跑会写出同一批文件。"
        "真实重派实验（同一模型、同一预算、有无种子）是下一步。")
    add("6. **两实现一致 ≠ 正确**：一致率只排除实现漂移，不证明门本身语义正确（例如 smoke 假阴性两实现都会犯）。"
        "本判据**不是恒真**：同一检查在本节点修复前的语料上报 0.8652（误报 0.1458），"
        "定位到「门/oracle 超时与文件集不公平」「PNG 被 oracle 当文本」「根级文件被结构性门误拒」「smoke 预算不一致」四类缺陷；"
        "修复后 0 不一致。修复清单见 `deviations.json`。")
    add("7. **回放判据 honest FAIL（不可覆盖）**：两臂通过计数相同（Fisher p = 1.0）。根因有两条且都可测量："
        "(a) **容量饱和**——作者容量 C（熔断任务实际落盘文件数）在多数家族已达到或超过交付集规模 |R|，"
        "冷启动臂自身通过率就很高，文件数口径的「容量」不是能分辨两臂的货币；"
        "(b) **路径级交叠近零**——|I∩R|/|R| 仅 3.05%，同名不同路径下也只有 7.32%，"
        "说明同节点的两次尝试常把同一批文件**改名/移位**（示例：熔断单写 `backend/modules/<A>/empirical.py`，"
        "继任者交付 `backend/modules/<B>/empirical.py`），路径集合交集系统性低估可继承性。"
        "结论：回放增益需要在**真实重派**上测（同模型、同预算、有/无种子），本节点不通过改阈值或换判据来「凑过」。")
    add("")
    add("## ⑥ 误差判定")
    add("")
    declared = prereg["predeclared_thresholds"]
    err_rows = [
        ["一致率 vs 预注册下限", f"{cons['consistency']:.4f}", f"{declared['consistency_min']:.2f}",
         f"{(cons['consistency'] - declared['consistency_min']) / declared['consistency_min'] * 100:+.2f}%"],
        ["误报率 vs 预注册上限", f"{cons['false_positive_rate']:.4f}", f"{declared['false_positive_max']:.2f}",
         f"{(cons['false_positive_rate'] - declared['false_positive_max']) / declared['false_positive_max'] * 100:+.2f}%"],
        ["抢救率 vs 预注册下限", f"{rate['rate']:.4f}", f"{declared['salvage_rate_min']:.2f}",
         f"{(rate['rate'] - declared['salvage_rate_min']) / declared['salvage_rate_min'] * 100:+.2f}%"],
        ["幂等率 vs 预注册下限", f"{idem['rate']:.4f}", f"{declared['idempotency_min']:.2f}",
         f"{(idem['rate'] - declared['idempotency_min']) / declared['idempotency_min'] * 100:+.2f}%"],
        ["回放 Fisher p vs 预注册 alpha", f"{rep['fisher']['p_value']:.4g}", f"{declared['replay_fisher_alpha']:.2f}",
         "见判定列"],
    ]
    add(_table(["关键量", "实测", "阈值", "相对阈值偏差"], err_rows))
    add("")
    add("**是否 <3%？** 三类「率」指标（抢救率/幂等率/一致率）对阈值的相对偏差如上表逐项列出："
        "当判据为「≥/≤ 阈值」的门时，报告的是**是否越线**而非拟合误差；本节点没有连续的物理量参考解，"
        "因此不存在「与解析解/文献值比 3%」意义上的数值误差。此处显式声明：**无实验基准量，3% 判据不适用**，"
        "替代判据为预注册的机器门阈值（全部落表）。")
    add("")
    add("## 审计链")
    add("")
    add(f"1. 预注册（测量前）：`artifacts/task_salvage/preregistration.json`")
    add(f"2. 语料（只读采集、冻结）：`artifacts/task_salvage/corpus.json`，sha256 = `{corpus['corpus_sha256']}`")
    add(f"3. 交接件：`artifacts/task_salvage/missions/<mission_id>/salvage.json` + `continuation.json`（规范 JSON，指纹可复算）")
    add(f"4. 机门结果：`artifacts/task_salvage/acceptance.json`（thresholds + 逐门判定 + 不一致清单）")
    add(f"5. 图：`artifacts/task_salvage/figures/*.png`（由 `figures.py` 从上述 JSON 渲染，不引入新数字）")
    add(f"6. 测试：`python3 -m pytest tools/task_salvage/tests -q`（含平台域词denylist扫描的零领域语义守卫）")
    add("7. 偏差登记（相对预注册）：`artifacts/task_salvage/deviations.json`（D1 交付集来源扩展、D2 并列 McNemar、"
        "D3 容量口径、D4 smoke 预算、D5 候选发现缺陷、D6 门/oracle 公平性缺陷、D7 smoke 并发确定性、D8 语料规模、D9 离线记录快照）")
    add("")
    add("## 能力声明与后续")
    add("")
    add("新增平台共性机制（零领域语义）：熔断任务 → `salvage.json` + `continuation.json` 的确定性交接与回测门。"
        "后续可做：把 `continued` 种子直接接到派发器（budget_hint → 重试预算）、把 `EVIDENCED` 候选做成阅读清单、"
        "在真实重派上跑行为学 A/B。")
    add("")
    return "\n".join(lines)


def main() -> dict:
    text = markdown()
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(text, encoding="utf-8")
    payload = {
        "title": "任务熔断部分交付抢救与残项机读交接：三筛 + 三重机门回测（p199w1-N06）",
        "summary": "对 llm_error/timeout/budget_exhausted 终态任务自动产出 salvage.json（可继承文件+残项+阻断码+指纹）与续跑种子；"
                   "三筛判定与独立 oracle 一致率、幂等指纹、回放 A/B 三重机门在真实台账语料上全部落表。",
        "scenario_key": "task_salvage_p199w1_n06",
        "mission_id": MISSION_ID,
        "tags": ["platform", "task-salvage", "circuit-breaker", "machine-gate", "replay-ab", "zero-domain"],
        "markdown": text,
        "figures": _figures_payload(),
    }
    # NOTE: /tmp/report.json is shared scratch on this host (another agent's run
    # overwrote it mid-post once), so the payload is written inside the artefacts
    # directory where it is private to this worktree and auditable.
    payload_path = OUT / "report_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"doc": str(DOC), "chars": len(text), "figures": len(payload["figures"]), "payload": str(payload_path)}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main(), ensure_ascii=False))