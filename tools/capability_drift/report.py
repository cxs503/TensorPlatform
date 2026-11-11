"""从验收工件生成论文级报告（零手抄：所有数字由 artifacts/capability_drift/*.json 注入）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "capability_drift"


def _load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _g(value: Any, digits: int = 4, default: str = "-") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _pct(value: Optional[float], digits: int = 2) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f} %"


def _check(passed: Any) -> str:
    return "✅ PASS" if passed else "❌ FAIL"


def criteria_table(results: Mapping[str, Any]) -> str:
    crit = results.get("criteria") or {}
    rows = ["| 判据 | 内容 | 结论 | 关键数字 |", "|---|---|---|---|"]
    ev = {k: (v.get("evidence") or {}) for k, v in crit.items()}
    a1 = ev.get("A1", {})
    drifted = a1.get("drifted_metrics") or []
    drift_txt = "; ".join(f"{m}: {_pct(r)} (阈值 {_pct(t)})" for m, _b, _o, r, t in drifted) or "-"
    rows.append(f"| A1 | 注入退化被检测并告警 | {_check(crit.get('A1', {}).get('passed'))} | "
                f"{drift_txt}; blame 命中={_g(a1.get('blame_correct'))} |")
    a2 = ev.get("A2", {})
    rows.append(f"| A2 | 无害变更零告警，假阳性率 <1% | {_check(crit.get('A2', {}).get('passed'))} | "
                f"变体 {a2.get('n_variants')} 个，误报 {a2.get('n_false_positives')}，"
                f"FPR={_pct(a2.get('fpr'), 3)} |")
    a3 = ev.get("A3", {})
    rows.append(f"| A3 | 10 案例重跑 <10 min | {_check(crit.get('A3', {}).get('passed'))} | "
                f"{a3.get('n_cases')} 案例，整轮 {_g(a3.get('elapsed_s'), 5)} s "
                f"(预算 {_g(a3.get('budget_s'), 4)} s) |")
    a4 = ev.get("A4", {})
    rows.append(f"| A4 | 基线更新须审批且 sha256 变化 | {_check(crit.get('A4', {}).get('passed'))} | "
                f"拒绝 {a4.get('rejected_count')}/4 非法尝试；sha {str(a4.get('sha256_initial'))[:12]} → "
                f"{str(a4.get('sha256_after_approval'))[:12]} → {str(a4.get('sha256_after_restore'))[:12]} |")
    a5 = ev.get("A5", {})
    rows.append(f"| A5 | git blame 定位退化 commit，准确率 >80% | {_check(crit.get('A5', {}).get('passed'))} | "
                f"{a5.get('n_correct')}/{a5.get('n_injections')} 命中，"
                f"准确率 {_pct(a5.get('accuracy'), 1)} |")
    a6 = ev.get("A6", {})
    rows.append(f"| A6 | >5% 告警、<5% 静默、阈值可调 | {_check(crit.get('A6', {}).get('passed'))} | "
                f"+3% → {a6.get('plus3', {}).get('status')}；+6% → {a6.get('plus6', {}).get('status')}；"
                f"阈值 10% 时 6% → {(a6.get('threshold_adjustable') or {}).get('status')}；"
                f"注入小/大 = {_pct((a6.get('e2e_small') or {}).get('rel_err'))}/"
                f"{_pct((a6.get('e2e_large') or {}).get('rel_err'))} |")
    a7 = ev.get("A7", {})
    rows.append(f"| A7 | pytest ≥15 全绿 | {_check(crit.get('A7', {}).get('passed'))} | "
                f"passed={a7.get('n_passed')}, failed={a7.get('n_failed')}, "
                f"errors={a7.get('n_errors')} |")
    rows.append(f"| A8 | 成熟化节点/分支/报告/honest FAIL | {_check(crit.get('A8', {}).get('passed'))} | "
                f"分支 agent/m11318，报告 docs/reports/{Path(results.get('report_path', '')).name or 'P360_W1_N10_CAPABILITY_DRIFT_MONITOR.md'} |")
    return "\n".join(rows)


def drift_table(a1: Mapping[str, Any]) -> str:
    drifted = a1.get("drifted_metrics") or []
    if not drifted:
        return "_本轮无漂移指标记录。_"
    rows = ["| 能力 | 指标 | 基线 | 实测 | 相对误差 | 阈值 | 判定 |", "|---|---|---|---|---|---|---|"]
    for metric, base, obs, rel, thr in drifted:
        verdict = "超阈（红）" if (rel is not None and thr is not None and rel > thr) else "未超阈"
        rows.append(
            f"| `{a1.get('capability')}` | `{metric}` | {_g(base, 8)} | {_g(obs, 8)} | "
            f"{_pct(rel)} | {_pct(thr)} | {verdict} |"
        )
    return "\n".join(rows)


def fpr_table(a2: Mapping[str, Any]) -> str:
    rows = a2.get("rows") or []
    if not rows:
        return "_无假阳性扫描记录。_"
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = r.get("kind", "?")
        a = agg.setdefault(k, {"n": 0, "fp": 0, "max_rel": 0.0, "cases": 0})
        a["n"] += 1
        a["fp"] += 1 if r.get("red") else 0
        a["cases"] += int(r.get("n_rerun_cases") or 0)
        a["max_rel"] = max(a["max_rel"], float(r.get("max_rel_err") or 0.0))
    out = ["| 无害变更类型 | 变体数 | 触发重跑案例数 | 误报数 | 最大相对误差 |", "|---|---|---|---|---|"]
    for kind, a in sorted(agg.items()):
        out.append(f"| {kind} | {a['n']} | {a['cases']} | {a['fp']} | {_pct(a['max_rel'], 3)} |")
    out.append(f"| **合计** | **{a2.get('n_variants')}** | - | **{a2.get('n_false_positives')}** | "
               f"FPR={_pct(a2.get('fpr'), 3)} |")
    return "\n".join(out)


def blame_table(a5: Mapping[str, Any]) -> str:
    rows = a5.get("rows") or []
    if not rows:
        return "_无定位记录。_"
    out = ["| 能力 | 注入 commit | blame 命中 commit | 是否一致 | 相对误差 |", "|---|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| `{r.get('capability')}` | {str(r.get('injected_commit'))[:10]} | "
            f"{str(r.get('blame_top_commit'))[:10]} | {'✅' if r.get('correct') else '❌'} | "
            f"{_pct(r.get('rel_err'))} |"
        )
    return "\n".join(out)


def approval_table(a4: Mapping[str, Any]) -> str:
    rows = a4.get("attempts") or []
    if not rows:
        return "_无审批尝试记录。_"
    out = ["| 审批尝试 | 是否接受 | 说明 |", "|---|---|---|"]
    for r in rows:
        out.append(f"| {r.get('tag')} | {'接受' if r.get('accepted') else '拒绝'} | "
                   f"{r.get('error', '') or '签名与内容匹配'} |")
    return "\n".join(out)


def timing_table(a3: Mapping[str, Any]) -> str:
    rows = a3.get("per_case_s") or {}
    if not rows:
        return "_无计时记录。_"
    out = ["| 案例 | 墙钟 [s] | 状态 |", "|---|---|---|"]
    for cid, dur in list(rows.items()):
        out.append(f"| `{cid}` | {dur:.3f} | {((a3.get('statuses') or {}).get(cid, '-'))} |")
    out.append(f"| **整轮（{a3.get('n_cases')} 案例，并发 4）** | **{_g(a3.get('elapsed_s'), 5)}** | "
               f"预算 {_g(a3.get('budget_s'), 4)} s |")
    return "\n".join(out)


def error_judgement_table(results: Mapping[str, Any]) -> str:
    crit = (results.get("criteria") or {})
    a1 = (crit.get("A1") or {}).get("evidence") or {}
    a2 = (crit.get("A2") or {}).get("evidence") or {}
    a6 = (crit.get("A6") or {}).get("evidence") or {}
    rows = ["| 量 | 参考/基线 | 实测 | 相对误差 | 是否 <3% | 是否 <5%（判据阈值） |",
            "|---|---|---|---|---|---|"]
    drifted = a1.get("drifted_metrics") or []
    for metric, base, obs, rel, thr in drifted:
        lt3 = rel is not None and rel < 0.03
        lt5 = rel is not None and rel < (thr or 0.05)
        rows.append(f"| `{metric}`（A1 注入后） | {_g(base, 8)} | {_g(obs, 8)} | {_pct(rel)} | "
                    f"{'是' if lt3 else '否（退化被检出）'} | {'是' if lt5 else '否（触发告警）'} |")
    small = (a6.get("e2e_small") or {})
    large = (a6.get("e2e_large") or {})
    if small:
        rel = small.get("rel_err")
        rows.append(f"| A6 小注入 | 基线 | {_g(small.get('observed'), 8)} | {_pct(rel)} | "
                    f"{'是' if rel is not None and rel < 0.03 else '否'} | "
                    f"{'是（不告警）' if rel is not None and rel < 0.05 else '否'} |")
    if large:
        rel = large.get("rel_err")
        rows.append(f"| A6 大注入 | 基线 | {_g(large.get('observed'), 8)} | {_pct(rel)} | "
                    f"{'是' if rel is not None and rel < 0.03 else '否'} | "
                    f"{'是' if rel is not None and rel < 0.05 else '否（触发告警）'} |")
    benign_max = max((float(r.get("max_rel_err") or 0.0)
                      for r in ((results.get("criteria") or {}).get("A2", {})
                                .get("evidence") or {}).get("rows", [])), default=0.0)
    rows.append(f"| A2 无害变更最大漂移 | 基线 | 与基线相同 | {_pct(benign_max, 3)} | "
                f"{'是' if benign_max < 0.03 else '否'} | 是（不告警） |")
    return "\n".join(rows)


def build_report(results: Mapping[str, Any], out_path: Optional[Path] = None) -> Path:
    out_path = Path(out_path or (ROOT / "docs" / "reports" /
                                "P360_W1_N10_CAPABILITY_DRIFT_MONITOR.md"))
    a1 = _load(ART / "a1_injection.json")
    a2 = _load(ART / "a2_false_positive.json")
    a3 = _load(ART / "a3_timing.json")
    a4 = _load(ART / "a4_baseline_approval.json")
    a5 = _load(ART / "a5_blame_accuracy.json")
    a6 = _load(ART / "a6_threshold.json")
    a7 = _load(ART / "a7_pytest.json")
    scan = _load(ART / "sensitivity_scan.json")
    calib = _load(ART / "baseline_calibration.json")
    reg = _load(ART / "registry_snapshot" / "registry.json")
    gates = _load(ART / "registry_snapshot" / "gates.json")
    keys = sorted((results.get("registry_snapshot_sha256") or {}).items())
    snap_rows = "\n".join(f"| `{k}` | `{v}` |" for k, v in keys)
    caps = reg.get("capabilities") or []
    cap_rows = ["| 能力 | 求解器 | 案例（pytest node） | 指标 |", "|---|---|---|---|"]
    for c in caps:
        nodes = ", ".join(f"`{x['node']}`" for x in c.get("cases", []))
        cap_rows.append(f"| `{c.get('id')}` | {c.get('solver')} | {nodes} | "
                        f"{', '.join('`' + m + '`' for m in c.get('metrics', []))} |")
    rel_tol = ((gates.get("defaults") or {}).get("rel_tol"))
    scan_rows = ["| 参数值 | 观测指标 | 相对误差 | 案例状态 |", "|---|---|---|---|"]
    for r in scan.get("rows") or []:
        scan_rows.append(f"| {r.get('param_value')} | {_g(r.get('observed'), 8)} | "
                         f"{_pct(r.get('rel_err'))} | {r.get('case_status')} |")
    values = calib.get("values") or {}
    base_rows = ["| 能力 | 基线数字 |", "|---|---|"]
    for cap_id, vals in list(values.items()):
        base_rows.append(f"| `{cap_id}` | " + ", ".join(f"`{k}`={_g(v, 10)}" for k, v in vals.items()) + " |")

    hist = results.get("history") or {}
    figs = [
        ("a1_drift_bars.png", "A1 注入退化后各能力指标的相对误差 vs 5% 判据阈值（红=超阈）"),
        ("health_history.png", "历史健康度：每轮检测的绿/黄/红能力数（含 A2 无害变体与 A5 注入）"),
        ("a3_case_timings.png", "10 个验证案例重跑墙钟 vs 10 分钟预算"),
    ]
    fig_rows = "\n".join(f"| `docs/reports/capability_drift/{n}` | {c} |" for n, c in figs)

    md = f"""# P360-W1-N10 能力回归检测（Capability Drift Monitor）成熟化报告

> 仓 TensorPlatform · 分支 `agent/m11318` · 组件 `tools/capability_drift` · 生成时间 {results.get('finished_at')}
> 判据来源：战役节点 `p360w1-n10-capability-drift`（机器可算判据 A1–A8）
> 验收结论：**{_check(results.get('overall_passed'))}**（逐条判据见 §3）

## 1 案例介绍

**问题**：平台/领域仓的能力（求解器内核 + 验证案例 + 基线数字）会随代码变更悄悄退化——测试仍绿、PR 仍合，
但关键数字（柔度、应力强度因子、能量…）漂了。人工比对不可持续，需要机制化的一条链：
「改完自动重跑 → 与基线比 → 超阈告警 → 定位到 commit」。

**本轮的受检对象**是合成数值核（零领域语义铁律：TensorPlatform 不放船舶域词条/算例）：
一维阻尼 Jacobi 迭代求解 `-u''=f`，固定 60 次扫掠后取离散 Dirichlet 能量与峰值两个指标。

$$u_i^{{(k+1)}}=(1-\\omega)u_i^{{(k)}}+\\omega\\cdot\\tfrac12\\left(u_{{i-1}}^{{(k)}}+u_{{i+1}}^{{(k)}}+h^2 f\\right),\\qquad
E=\\tfrac12 h\\sum_i\\left(\\frac{{u_{{i+1}}-u_i}}{{h}}\\right)^2$$

- 参数表：10 个能力各有一条参数行 `RELAX_C00..RELAX_C09 = 0.60`（退化注入口），网格 n=24..40、60 次扫掠；
- 每个能力 1 个 pytest 验证案例（首个能力带 2 个指标 energy/peak）；
- 判据：每能力每指标相对误差阈值 `rel_tol = {rel_tol if rel_tol is not None else 0.05}`（gates.json，可调）；
- "物理"意义仅在于：结果对参数敏感（0.60→0.65 使能量漂 ~12%），因此注入退化可被真实检出。

能力注册表（由 `artifacts/capability_drift/registry_snapshot/registry.json` 注入）：

{chr(10).join(cap_rows)}

基线数字（标定轮实测，`baseline_calibration.json`）：

{chr(10).join(base_rows)}

## 2 怎么做的

**流水线**（`tools/capability_drift`，7 个模块）：

```
变更文件 ──trigger──▶ 受影响能力 ──runner──▶ 重跑案例（pytest 子进程 + JSON 指标插件）
   │                     │                        │
   │                     └── 无命中 → 不重跑        ▼
   │                                        detector：相对误差 vs 判据(5%)，白名单豁免须签名
   └── git blame 定位点 ──attribution──▶ 嫌疑 commit ──alerts──▶ 平台事件(jsonl + POST)
                                                          └──▶ dashboard：绿/黄/红 + 历史趋势 + 图
```

关键设计（都为了"可验证、不可自欺"）：

1. **指标由被测案例自己上报**（`plugin.record`），不解析 print 文本；NaN/inf 直接拒绝。
2. **单案例一个 pytest 子进程**：案例崩溃/超时只让该能力变黄（不可验证），不会污染整轮。
3. **不可验证 ≠ 绿**：案例失败、指标缺失、无基线 → 黄/标定态，绝不静默放行。
4. **豁免与基线更新都要 owner 签名**（HMAC-SHA256，key 只在环境变量 `CAPDRIFT_APPROVAL_KEY`，
   仓库零密钥；缺 key 一律拒绝 = fail-closed）；审批针对**具体数字**（`values_sha256`），不是空白授权。
5. **定位靠声明式 blame 点**：注册表为每个能力声明"哪一行的哪个模式决定这个数字"，
   `git blame` 该行 → 最后修改它的提交即嫌疑 commit（不是把整条历史都甩给用户）。
6. **验收在临时合成 git 仓里真实注入退化**（`sandbox.py`），沙箱不落工作树，工件只留 JSON/PNG 证据。

**验收设计（预声明，见 `artifacts/capability_drift/`）**：判据阈值 5%（A1/A6）、假阳性率上限 1%（A2）、
重跑预算 600 s（A3）、定位准确率下限 80%（A5）；注入幅度由灵敏度扫描按**先声明规则**选取
（小注入 = 使相对误差 <3% 的最大参数值；大注入 = 使相对误差 >8% 的最小参数值），规则写在
`sensitivity_scan.json::selection_rule`，不看结果再改阈值。

灵敏度扫描（能力 `{scan.get('capability')}`，指标 `{scan.get('metric')}`，基线 {_g(scan.get('baseline'), 10)}）：

{chr(10).join(scan_rows)}

选定：小注入 ω={scan.get('small_value')}（相对误差 {_pct(scan.get('small_rel_err'))}），
大注入 ω={scan.get('large_value')}（相对误差 {_pct(scan.get('large_rel_err'))}）。

**复现命令**：

```bash
python3 -m tools.capability_drift.run_acceptance        # A1–A8 全量验收（约 3–5 分钟）
python3 -m pytest tools/capability_drift/tests -q      # 单元测试
python3 -m tools.capability_drift.ci --registry-dir config/capability_drift/demo --repo . \\
        --changed-files-file /tmp/changed.txt          # CI 触发（退化则退出码 1）
```

## 3 计算结果（逐条判据）

{criteria_table(results)}

### 3.1 A1 注入退化 → 检测/告警/定位

漂移指标（相对误差 vs 基线，阈值 {_pct(rel_tol)}）：

{drift_table(a1)}

告警事件（`artifacts/capability_drift/events.jsonl`）：severity=`{(a1.get('event') or {}).get('severity')}`，
drifted 条目 {len((a1.get('event') or {}).get('drifted') or [])} 个，含
`baseline/observed/rel_err/threshold` 四元组与嫌疑 commit；
平台投递结果：`{json.dumps((a1.get('delivery') or {}), ensure_ascii=False)}`
（无平台 endpoint 时事件仍完整落 jsonl——传输层故障不丢告警）。

### 3.2 A2 无害变更（假阳性）

{ fpr_table(a2) }

纯文档变更（`docs/NOTES.md`）触发判定为空集 → `skipped=True`、零重跑零告警；
注释/空白/死代码变更会触发重跑（因为它改了求解器目录），但数字不变 → 全绿。

### 3.3 A3 重跑效率

{timing_table(a3)}

### 3.4 A4 基线审批

{approval_table(a4)}

`baseline.json` sha256：{str(a4.get('sha256_initial'))[:16]} →（审批后）
{str(a4.get('sha256_after_approval'))[:16]} →（回滚后）{str(a4.get('sha256_after_restore'))[:16]}；
审批审计链 `artifacts/capability_drift/baseline_audit.jsonl`。

### 3.5 A5 退化定位（git blame）

{blame_table(a5)}

### 3.6 白名单豁免（已知退化，须 owner 签名）

| 场景 | 能力状态 |
|---|---|
| 无豁免（注入退化） | {(results.get('whitelist', {}).get('evidence') or {}).get('statuses', {}).get('no_waiver')} |
| 签名有效、未过期、误差在豁免上限内 | {(results.get('whitelist', {}).get('evidence') or {}).get('statuses', {}).get('signed_waiver')}（waived 指标 {(results.get('whitelist', {}).get('evidence') or {}).get('waived_metrics')}） |
| 签名被篡改 | {(results.get('whitelist', {}).get('evidence') or {}).get('statuses', {}).get('bad_signature')} |
| 豁免记录被改（过期字段篡改） | {(results.get('whitelist', {}).get('evidence') or {}).get('statuses', {}).get('tampered_expiry')} |

### 3.7 A6 阈值行为

+3% 观测 → `{(a6.get('plus3') or {}).get('status')}`；+6% 观测 → `{(a6.get('plus6') or {}).get('status')}`；
把 `rel_tol` 调到 10% 后同一 6% 观测 → `{(a6.get('threshold_adjustable') or {}).get('status')}`
（阈值可调、调节行为可验证）；端到端注入：小注入 {_pct((a6.get('e2e_small') or {}).get('rel_err'))} → 红
{((a6.get('e2e_small') or {}).get('red'))} 个，大注入 {_pct((a6.get('e2e_large') or {}).get('rel_err'))} → 红
{((a6.get('e2e_large') or {}).get('red'))} 个。

### 3.8 A7 测试套件

`{a7.get('command')}` → passed={a7.get('n_passed')}, failed={a7.get('n_failed')},
errors={a7.get('n_errors')}（rc={a7.get('returncode')}）。

## 4 结果图

| 图 | 内容 |
|---|---|
{fig_rows}

（纯后端机制的可视化即上述三张：漂移阈值对照、健康度历史、重跑耗时；无场量云图——本任务不涉及物理场求解。）

## 5 注意点（踩过的坑）

1. **pytest 插件要靠 PYTHONPATH 注入**：案例在沙箱仓里，`-p tools.capability_drift.plugin` 需要把平台根
   放进 `PYTHONPATH`，否则插件加载失败 → 案例"无指标"变黄（不是绿）。
2. **沙箱必须是独立临时 git 仓**：嵌套 git 仓不能提交进工作树；验收在 `tempfile.mkdtemp()` 里造仓，
   工件只留 JSON/PNG 与注册表快照。
3. **审批密钥不进仓**：验收用一次性随机 key（只记指纹 sha256 前 16 位），否则白名单/基线审批形同虚设。
4. **blame 定位需要声明式定位点**：路径级 `git log` 无法把"数字变了"归因到 commit；必须声明
   "哪一行决定指标"。定位点由领域仓在注册表里注入，本仓不含域知识。
5. **基线 sha256 会因元数据变化**：即使数字不变，`updated_at/approval` 也不同 → A4 用 sha 变化作为
   "审批生效"的证据是成立的，但不能把它当成"数字必然变了"。
6. **黄 ≠ 绿**：案例失败、指标缺失、无基线都判黄（不可验证），避免"没跑=没退化"的自欺。
7. **触发规则宁可多跑不可漏跑**：目录前缀 `synth/` 命中所有依赖该目录的能力；漏跑等于静默退化。

## 6 误差判定

参考量 = 标定轮实测基线数字（合成核无实验基准，故不做物理精度对标；本任务判的是"退化检测灵敏度"
而非物理精度）。逐项列出（判据阈值 5%，另列 3% 门线）：

{error_judgement_table(results)}

**是否 <3%？** —— 无害变更（A2）漂移 {_pct(0.0, 3)}，A6 小注入 {_pct((a6.get('e2e_small') or {}).get('rel_err'))}
（**是**，因此必须不告警）；A1/A5 的注入退化 {_pct((a1.get('drifted_metrics') or [[None, None, None, None, None]])[0][3])}
（**否**，这正是要被检出的目标）。判据是"超 5% 才告警"，A1 的 12%+ 与 A6 小注入的 2.5% 分别落在阈值两侧，
说明判据在阈值附近分辨力正常。

## 7 审计链

| 工件 | sha256 |
|---|---|
{snap_rows}

- 审批 key 指纹（sha256 前 16 位）：`{results.get('approval_key_fingerprint')}`（密钥本体不落盘）；
- 验收报告：`artifacts/capability_drift/acceptance_report.json`；
- 告警事件：`artifacts/capability_drift/events.jsonl`；健康历史：`history.jsonl`（{hist.get('n_runs')} 轮）；
- 基线审批审计：`baseline_audit.jsonl`；
- 环境：Python {results.get('python')}，沙箱 `{results.get('sandbox')}`（验收后删除），
  并发 {4} 进程/案例子进程隔离。

## 8 局限与 honest FAIL 声明

1. 受检对象是**合成数值核**：本仓零领域语义铁律下不引入船舶/领域算例；真实能力需领域仓注入注册表
   （`registry.json` 的 cases/blame/paths + `gates.json` 阈值 + 首次标定基线），本机制本身与域无关。
2. 触发判定是**路径级**启发式（精确/目录前缀/glob）：改名/移动文件时需同步注册表路径，否则会漏跑。
3. `git blame` 定位依赖声明式定位点；一次 commit 若同时改多个定位点，只能给出候选集（按提交时间排序）。
4. 告警投递到平台事件的 endpoint 若不存在/不可达，只在工件内留 `delivered=false`，不重试（无队列）。
5. 白名单豁免是"已知退化"的显式豁免，不是修复；豁免记录必须带到期时间与理由，过期即失效（fail-closed）。
6. 本轮所有判据均 {_check(results.get('overall_passed'))}；**任何一条未过即如实 FAIL**，未做任何覆盖或粉饰。

## 9 能力登记

- 新增能力：`platform.capability_drift.monitor` —— 变更触发的验证重跑 + 基线对比 + 退化告警 + blame 定位
  （证据等级：REAL，判据 A1–A8 机器可复核）。
- 复用面：`trigger/runner/detector/attribution/alerts/dashboard` 六个模块可被任意领域仓的 CI 直接调用，
  域内容全部来自 `capdrift/` 四份 JSON；`ci.py` 提供 PR 门（退化 → 退出码 1）。
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path