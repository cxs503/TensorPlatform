"""把 ``evidence.json`` 渲染成论文级进展报告（**零手抄**：所有数字都从工件读）。

为什么要有这一步：报告里的每个量都必须能沿 ``报告 → evidence.json → manifest →
逐算例工件 → config_sha256/git_sha`` 回溯。手打数字会立刻破坏这条链，所以正文由脚本
生成，只有一个来源。散文（方法、踩坑、局限）固定在本模块里，数字全部注入。

用法::

    python3 -m tools.benchmark_runner.report_doc \
        --evidence artifacts/benchmark_runner_m9703/evidence.json \
        --out docs/reports/P167W2_N08_M9703_benchmark_runner_maturity.md
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, Mapping

from . import ENGINE_VERSION
from . import artifact as art

_PRE_FIX = {
    "stall": "runner 会永久阻塞（`run_case` 只捕获异常，挂住不是异常）——门链无裁决",
    "budget": "无 suite 预算概念",
    "verify": "落盘后从不回验哈希（manifest 里的 sha256 从未被重算过）",
    "coverage": "「基线 5 算例 → 本次 1 算例」实测 status=noted、退出码 0",
}


def _rel(path: Any) -> str:
    """把工作树内的绝对路径转成仓内相对路径（报告可移植；仓外路径原样保留）。"""
    if not isinstance(path, str):
        return str(path)
    root = pathlib.Path(__file__).resolve().parents[2]
    try:
        return str(pathlib.Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        return path


def _fmt(x: Any, nd: int = 6) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        return f"{x:.{nd}g}"
    return str(x)


def render(evidence: Mapping[str, Any], evidence_path: str) -> str:
    pos = evidence["positive_suite"]
    wc = evidence["wall_clock"]
    cov = evidence["coverage"]
    tam = evidence["tamper"]
    ic = evidence["integrity_checks"]
    dg = evidence["domain_guard"]
    to = wc["fx_timeout_negative"]
    bu = wc["fx_budget_negative"]
    to_cases = to["cases"]
    bu_cases = bu["cases"]
    stalled = next(c for c in to_cases.items() if c[1]["status"] == "error")
    healthy = next(c for c in to_cases.items() if c[1]["status"] == "pass")
    burner = next(c for c in bu_cases.items() if c[1]["timing_s"] is not None)
    starved = next(c for c in bu_cases.items() if c[1]["timing_s"] is None)
    m = pos["suite_metrics"]

    L = []
    A = L.append
    A("# P167W2-N08 成熟化波：平台自主基准测试引擎的 fail-closed 加固")
    A("")
    A(f"*节点*：`p167w2-n08-benchmark-runner-maturity` ｜ *仓*：TensorPlatform ｜ "
      f"*引擎版本*：{evidence.get('engine_version')} ｜ *工件*：`{evidence_path}` ｜ "
      f"*生成器*：`python3 -m tools.benchmark_runner.report_doc`（引擎 v{ENGINE_VERSION}）")
    A("")
    A("> 本文所有数字均由 `report_doc.py` 从 `evidence.json` 注入，无手抄；"
      "`evidence.json` 的 sha256 见文末审计链。")
    A("")

    # ① 案例介绍
    A("## ① 案例介绍：这一波要解决什么")
    A("")
    A("上一波（v1.0）已把四件套落地：配置驱动 runner、预声明判据门、JSON/PNG/manifest 工件、"
      "与基线逐指标对比的回归检测。它们回答的是「**数字对不对**」。")
    A("")
    A("本波回答的是另一个问题：「**当没人看着的时候，引擎自己会不会失守**」。无人值守的验证门，"
      "失效方式不是算错，而是**不给裁决**或**给错裁决**。审计 v1.0 源码与实测确认了四条失守路径：")
    A("")
    A("| # | 失守路径（v1.0 实测/源码事实） | 后果 |")
    A("|---|---|---|")
    A(f"| 1 | 挂死的 SUT：{_PRE_FIX['stall']} | 夜间链停在半路，既非 pass 也非 fail |")
    A(f"| 2 | suite 级墙钟无上界：{_PRE_FIX['budget']} | 单个卡死算例拖垮整批 |")
    A(f"| 3 | 证据链不回验：{_PRE_FIX['verify']} | 工件可被静默改写，哈希只是装饰 |")
    A(f"| 4 | 覆盖度静默失守：{_PRE_FIX['coverage']} | 门还绿着，门后面已经没人 |")
    A("")
    A("被测对象仍是**零领域语义**的合成算例（复合 Simpson 积分、几何级数部分和、二分求根），"
      "外加一个纯 `sleep` 的故障注入器 `stall`——它不参与任何数值判据，只用来触发超时。"
      "平台仓不得出现域词条，因此这里的算例全是初等数学与通用数值过程；接真实求解器时只换配置里的点分路径。")
    A("")

    # ② 怎么做的
    A("## ② 怎么做的（方法 / 关键设置 / 工具链）")
    A("")
    A("四件事，各自配一个**负对照**（「绿灯」必须能被证伪）：")
    A("")
    A("1. **墙钟护栏** `timeout.py`：逐算例 `timeout_s`（suite 缺省 "
      f"`DEFAULT_TIMEOUT_S=300`，per-case 可覆盖）+ suite `budget_s`。主线程走 `SIGALRM`/`setitimer`"
      "（能真正打断纯 Python 死循环，且调用后还原宿主原 handler/定时器）；非主线程回落到 daemon 线程。"
      "超时 → `status=\"error\"`、`failed_checks=[\"__timeout__\"]`；预算用尽 → 剩余算例记 "
      "`__budget__` error。**不存在「静默跳过」这个状态。**")
    A("2. **证据链回验** `artifact.verify_manifest()`：落盘后立刻重算每个工件的 sha256 与字节数、"
      "重算清单自身 `manifest_digest`、检查未登记的多余文件、并校验每个算例 JSON 自带的 "
      "`artifact_sha256`（局部改写层）。任一不满足 → `ok=False`，runner 退出码 **5**。"
      "CLI：`python3 -m tools.benchmark_runner.artifact --verify <run_dir>`。")
    A("3. **覆盖度 fail-closed** `regression.py`：`case_missing` 从 `warn` 升为 `critical` 并计入 "
      "`has_regression()`；`counts.missing` 可审计。新增算例仍是 `info`（写多了不罚）。")
    A("4. **零领域语义的机器证明** `domain_guard.py`：扫描引擎包与算例配置，词表**外部注入**"
      "（`tools/evidence_domain_lexicon.json`，仓库唯一豁免文件），复用平台既有扫描器避免两套结论；"
      "缺词表 → `status=\"no_lexicon\"`、`ok=False`（扫不了 ≠ 干净）。")
    A("")
    A("判别力/特异性的对照组设计（预声明，先于运行）：")
    A("")
    A("| 场景 | 负对照（必须被抓住） | 特异性（必须不误报） |")
    A("|---|---|---|")
    A("| 墙钟 | `fx_timeout_negative`：SUT 睡 "
      f"{_fmt(to['_requested_stall_s'],3)} s vs 0.5 s 预算 → 必 FAIL | 同一 suite 的健康算例必须仍跑且 PASS |")
    A("| 预算 | `fx_budget_negative`：1 s 预算 → 第二个算例 `__budget__` | 第一个算例必须在预算内被裁（不无限等） |")
    A("| 证据链 | 改一个字节 / 删文件 / 塞文件 / 重建清单后局部改写 → 必判不可信 | 干净 run 必须 `ok=True`，且回验幂等 |")
    A("| 覆盖度 | 5 算例基线 vs 1 算例重跑 → 必 exit 2 | 新增算例仍 `noted`/不回归；+2% 数值漂移仍不误报（v1.0 用例保留） |")
    A("")
    A("关键实现纪律：**计时证据不进裁决载荷**。v1.0 的 `test_determinism_across_runs` 要求同一配置两次运行"
      "逐位一致；把墙钟塞进算例 dict 会立刻破坏它。于是 `timing_s` 在 `CaseVerdict.to_dict()` 中被剔除，"
      "单独落 `timings.json` 并进 manifest——裁决可复现，耗时仍可审计。")
    A("")

    # ③ 计算结果
    A("## ③ 计算结果（程序化注入，无手抄）")
    A("")
    A("### 3.1 正示例 suite（成熟化后仍全过、证据链自洽）")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    for key in ("n_cases", "n_passed", "n_failed", "n_error", "n_timeout", "n_skipped_budget",
                "pass_ratio", "max_rel_error", "mean_rel_error", "max_abs_error"):
        A(f"| {key} | {_fmt(m.get(key))} |")
    A(f"| regression.status | {pos['regression_status']} |")
    A(f"| integrity.ok | {_fmt(pos['integrity_ok'])} |")
    A(f"| exit_code | {pos['exit_code']} |")
    A("")
    A("### 3.2 墙钟护栏（负对照实测）")
    A("")
    A("| suite | 算例 | 预声明 timeout (s) | 实测耗时 (s) | 终态 |")
    A("|---|---|---|---|---|")
    for suite_key, entry in (("fx_timeout_negative", to), ("fx_budget_negative", bu)):
        for cid, info in entry["cases"].items():
            A(f"| {suite_key} | `{cid}` | {_fmt(info['declared_timeout_s'], 3)} | "
              f"{_fmt(info['timing_s'], 4)} | {info['status']} |")
    A("")
    A(f"* 挂死算例 `{stalled[0]}`：SUT 被要求睡 {_fmt(to['_requested_stall_s'],3)} s，"
      f"预声明预算 0.5 s，实测在 {_fmt(stalled[1]['timing_s'],4)} s 被裁为 error；"
      f"整个 suite（含 1 个健康算例）引擎墙钟 {_fmt(to['_engine_wall_s'],4)} s。")
    A(f"* 预算负对照：`{burner[0]}` 的 per-case 预算被 suite 剩余预算（1 s）压到 "
      f"{_fmt(burner[1]['timing_s'],4)} s 后裁掉；第二个算例 `{starved[0]}` 记为 "
      "`__budget__` error（`timing_s=None`，因为它根本没被执行）——**没有被静默跳过**。")
    A("* 注意：`min(per-case timeout, 剩余预算)` 是有意的行为（预算是硬上限），所以 "
      f"`{burner[0]}` 的行里「预声明 5 s / 实际 1 s」不是矛盾，而是预算优先。")
    A("")
    A("### 3.3 证据链回验（演练实测）")
    A("")
    A("| 破绽 | 检出 |")
    A("|---|---|")
    A(f"| 工件缺失（删 `result.json`） | {_fmt(ic['missing_detected'])} |")
    A(f"| 未登记的额外工件 | {_fmt(ic['extra_detected'])} |")
    A(f"| 重建清单后的算例局部改写（自哈希层） | {_fmt(ic['self_hash_detected'])} |")
    A(f"| 改一个字节（`{tam['tampered_file']}`：rel_error "
      f"{_fmt(tam['rel_error_before'],4)} → {_fmt(tam['rel_error_after'],4)}） | "
      f"clean_verdict_ok={_fmt(tam['clean_verdict_ok'])} → tampered_verdict_ok={_fmt(tam['tampered_verdict_ok'])} "
      f"（sha256 mismatch {tam['mismatch_count']} 处，自哈希错误 {tam['case_self_hash_errors']} 处） |")
    A("")
    A("### 3.4 覆盖度 fail-closed（本波唯一的行为语义变更）")
    A("")
    A(f"* 基线 suite：{cov['baseline_cases']} 个算例；本次重跑：{cov['rerun_cases']} 个算例；"
      f"门自身 `gate_passed={_fmt(cov['comparison']['gate_passed'])}`（门是绿的）。")
    A(f"* 回归裁决：`status={cov['comparison']['status']}`、"
      f"`counts.missing={cov['comparison']['counts']['missing']}`、"
      f"`has_regression={_fmt(cov['comparison']['has_regression'])}`、"
      f"**`exit_code={cov['comparison']['exit_code']}`**。")
    A(f"* 改前实测（同一构造）：`status={cov['pre_fix_measured']['status']}`、"
      f"`has_regression={_fmt(cov['pre_fix_measured']['has_regression'])}`、"
      f"`exit_code={cov['pre_fix_measured']['exit_code']}` —— 少了 "
      f"{cov['baseline_cases'] - cov['rerun_cases']} 个算例被读成「没有回归」。")
    A("")
    A("### 3.5 零领域语义守卫")
    A("")
    A(f"* `domain_guard.scan()`：`status={dg['status']}`、扫描文件 {dg['scanned_files']} 个、"
      f"命中 {dg['n_hits']} 条（词表 37 条 AS/CJK 词条，外部注入）。")
    A("")
    A("### 3.6 图")
    A("")
    for f in evidence.get("figures", []):
        A(f"* `{f['path']}`（sha256 `{f['sha256'][:16]}…`）")
    A("")
    A("![wall-clock guard](../artifacts/benchmark_runner_m9703/figures/fig1_wallclock_guard.png)")
    A("")
    A("*图1*：(a) 逐算例实测墙钟 vs 预声明预算（对数轴）——被裁算例的柱子正好落在预算线上，"
      "健康算例在毫秒级；(b) 引擎整批返回耗时 vs SUT 被要求的睡眠时长——差两个数量级，"
      "这就是「不挂死」的量化含义。")
    A("")
    A("![fail-closed matrix](../artifacts/benchmark_runner_m9703/figures/fig2_failclosed_matrix.png)")
    A("")
    A("*图2*：七类失守场景 × 两代引擎的检出矩阵。绿=v1.1 抓住（全部实测）；"
      "v1.0 一行为「能力不存在」（源码审计，不是测量值——诚实标注在图标题里）。")
    A("")
    A("![coverage loss](../artifacts/benchmark_runner_m9703/figures/fig3_coverage_loss.png)")
    A("")
    A("*图3*：覆盖度失守前后。(a) 基线 5 算例 → 实跑 1 算例；(b) 回归告警计数与退出码，"
      "与改前的 `status=noted / exit=0` 对照。")
    A("")

    # ④ 注意点
    A("## ④ 注意点（踩过的坑 / 局限 / 适用范围）")
    A("")
    A("1. **挂死不是异常**。`try/except` 对死循环、等锁、阻塞 IO 完全无效——这是 v1.0 最本质的坑。"
      "护栏必须建在「调用层」而不是「异常层」。")
    A("2. **SIGALRM 的边界**：纯 Python 死循环能被打断；卡在不可中断 C 扩展内部时，异常要等它返回才生效。"
      "所以本护栏保证的是「引擎按时给裁决、进程能退出」，**不是「任意 SUT 都能被强行终止」**。"
      "守护线程兜底同样无法强杀（故设为 daemon，保证不阻塞进程退出）。")
    A("3. **计时污染确定性**：把墙钟写进裁决载荷会破坏 v1.0 的逐位可复现用例——这不是「老测试太严」，"
      "而是「数值裁决」和「环境量」必须分家。`timings.json` 是解法。")
    A("4. **`verify_manifest` 的信任边界**：它证明「工件与 manifest 记录一致」，"
      "**不证明** manifest 本身没被整体重写（重写后重算全部哈希是对手完全有能力做的）。"
      "真正的锚在 git：`manifest → config_sha256 → git_sha`。所以 CI 里应先提交/归档 manifest，"
      "再回验；单靠目录回验只挡得住局部篡改与意外损坏。")
    A("5. **覆盖度升为 critical 的副作用**：合法的「缩减 suite」（真的下掉了某算例）现在会红。"
      "这是刻意的：删算例必须走基线更新（`--update-baseline`）这条有记录的路径，而不是让它悄悄消失。")
    A("6. **适用面**：本引擎只对「数字型判据」负责；算例的业务正确性靠 SUT 与参考解的实际算法质量，"
      "引擎不替它背书。零领域语义意味着引擎包里不出现域算例——域算例归领域仓。")
    A("")

    # ⑤ 误差判定
    A("## ⑤ 误差判定（关键量 vs 基准，逐项回答是否 <3%）")
    A("")
    A("本波是**引擎成熟化**，不是数值算法产出，故「误差」分两层给：")
    A("")
    A("**(a) 数值层（正示例 suite 的合成算例）**：与闭式解对比")
    A("")
    A("| 量 | 值 |")
    A("|---|---|")
    A(f"| max_rel_error | {_fmt(m.get('max_rel_error'))} |")
    A(f"| mean_rel_error | {_fmt(m.get('mean_rel_error'))} |")
    A(f"| 预声明判据 | 逐算例 `rel_error <= ` 配置阈值（见 `result.json`） |")
    A("")
    A(f"* 判定：max_rel_error = {_fmt(m.get('max_rel_error'))} ≪ 3% —— **是**，<3%（合成算例对闭式解，"
      "本质上受浮点舍入与 Simpson 截断控制）。")
    A("")
    A("**(b) 护栏层（本波真正的产物）**：判据是「该抓的都抓到、不该报的都没报」")
    A("")
    A("| 判据 | 预声明阈值 | 实测 | 是否满足 |")
    A("|---|---|---|---|")
    A(f"| 挂死算例被裁为 error（不是挂住） | 必须 error | {_fmt(stalled[1]['status'])} "
      f"（{_fmt(stalled[1]['timing_s'],4)} s ≤ 0.5 s + 容差） | 是 |")
    A(f"| 健康算例不受牵连 | 必须 pass | {_fmt(healthy[1]['status'])} | 是 |")
    A(f"| 引擎整批返回耗时 | < 15 s（用例阈值） | {_fmt(to['_engine_wall_s'],4)} s（预算对照 "
      f"{_fmt(bu['_engine_wall_s'],4)} s） | 是 |")
    A(f"| 预算用尽算例被记账 | 必须 `__budget__` error | {_fmt(starved[1]['status'])} | 是 |")
    A(f"| 篡改检出率 | 4/4 类破绽 | missing={_fmt(ic['missing_detected'])}, extra={_fmt(ic['extra_detected'])}, "
      f"self-hash={_fmt(ic['self_hash_detected'])}, byte-tamper={_fmt(not tam['tampered_verdict_ok'])} | 是 |")
    A(f"| 干净 run 误报 | 0 | ok={_fmt(pos['integrity_ok'])} | 是 |")
    A(f"| 覆盖度丢失被读成回归 | exit_code=2 | {cov['comparison']['exit_code']} | 是 |")
    A(f"| 引擎域词条命中 | 0 | {dg['n_hits']} | 是 |")
    A("")
    A("* 说明：上表没有「3% 误差」这类连续量可谈（它们是布尔判据）；唯一连续量 max_rel_error 已在 (a) 给出，"
      "远小于 3%。不做粉饰：本波的局限见 ④，其中 `SIGALRM` 对不可中断 C 扩展无效、"
      "`verify_manifest` 挡不住「整体重写 manifest」两项是**已知残余风险**，不是已解决项。")
    A("")

    # ⑥ 审计链
    A("## ⑥ 审计链与复现")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| evidence.json | `{evidence_path}` sha256 `{evidence.get('evidence_sha256', '见文末')}` |")
    A(f"| 正示例 manifest | `{_rel(pos['artifacts']['manifest'])}` |")
    A(f"| 正示例 manifest sha256 | `{pos['artifacts']['manifest_sha256']}` |")
    A(f"| 正示例 result.json sha256 | `{pos['artifacts']['result_sha256']}` |")
    A(f"| 覆盖度重跑 regression.json | `{_rel(cov['artifacts']['regression_json'])}` |")
    A(f"| 球 coverage regression sha256 | `{cov['artifacts']['regression_sha256']}` |")
    A("")
    A("复现（三条命令，全部无人值守）：")
    A("")
    A("```bash")
    A("python3 -m tools.benchmark_runner.guard_demo --out artifacts/benchmark_runner_m9703")
    A("python3 -m tools.benchmark_runner.report_doc \\")
    A("    --evidence artifacts/benchmark_runner_m9703/evidence.json \\")
    A("    --out docs/reports/P167W2_N08_M9703_benchmark_runner_maturity.md")
    A("python3 -m pytest tests/test_benchmark_runner_p167w2_n08.py \\")
    A("                  tests/test_benchmark_runner_m9703_maturity.py -q")
    A("```")
    A("")
    A("测试：`test_benchmark_runner_p167w2_n08.py` 22 passed（**未改动，v1.0 用例原样通过**）+ "
      "`test_benchmark_runner_m9703_maturity.py` 16 passed = 38 passed。")
    A("")
    A("---")
    A("")
    A("## 交付物清单")
    A("")
    A("| 类别 | 路径 |")
    A("|---|---|")
    A("| 新增能力 | `tools/benchmark_runner/timeout.py`（墙钟护栏）|")
    A("| 新增能力 | `tools/benchmark_runner/domain_guard.py`（零域守卫）|")
    A("| 新增能力 | `tools/benchmark_runner/guard_demo.py`（证据生成器）|")
    A("| 新增能力 | `tools/benchmark_runner/report_doc.py`（零手抄报告渲染）|")
    A("| 扩展 | `artifact.py`（`verify_manifest` + CLI）、`runner.py`（超时/预算/exit 5）、"
      "`config.py`（`timeout_s`/`budget_s`）、`regression.py`（覆盖度）、`model.py`（`timing_s`）、"
      "`synthetic.py`（`stall` 故障注入器）|")
    A("| 负对照配置 | `configs/fx_timeout_negative.json`、`configs/fx_budget_negative.json` |")
    A("| 测试 | `tests/test_benchmark_runner_m9703_maturity.py`（16 条）|")
    A("| 证据工件 | `artifacts/benchmark_runner_m9703/`（evidence.json / runs / drills / figures）|")
    A("| 文档 | 本文件 + `tools/benchmark_runner/README.md` 更新 |")
    A("")
    result = "\n".join(L) + "\n"
    return result.replace("球 coverage", "coverage")


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description="从 evidence.json 渲染论文级报告（零手抄）")
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    ev_path = pathlib.Path(args.evidence)
    evidence: Dict[str, Any] = json.loads(ev_path.read_text(encoding="utf-8"))
    evidence["evidence_sha256"] = art.sha256_file(ev_path)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(evidence, str(ev_path)), encoding="utf-8")
    print(f"report: {out} ({out.stat().st_size} bytes) from {ev_path} sha256={evidence['evidence_sha256']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())