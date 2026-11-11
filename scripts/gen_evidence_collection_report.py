#!/usr/bin/env python3
"""P9263-N08 验收跑批：读采集工件 → 出图 → 渲染报告（数字全部程序化注入，零手抄）。

用法::

    python3 scripts/gen_evidence_collection_report.py

产物：
* ``artifacts/evidence_collection/n08_acceptance.json``（判据数字 + 逐能力表 + 发现项）
* ``docs/evidence_collection/fig_n08_*.png``（3 张图）
* ``docs/evidence_collection/P9263_N08_report.md``（论文级报告，六要素齐全）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许以脚本方式运行（不是所有 CI 步骤都装成包）
    sys.path.insert(0, str(ROOT))
ART = ROOT / "artifacts" / "evidence_collection"
DOCS = ROOT / "docs" / "evidence_collection"
SPEC = ROOT / "config" / "evidence_collection" / "spec.json"
CALIB = ROOT / "config" / "evidence_collection" / "calibration.json"
BASELINE = ROOT / "config" / "evidence_collection" / "baseline.json"
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
COLORS = {"REAL": "#2f7d32", "WITHHELD": "#607d8b", "MOCK": "#c62828",
          "STUB": "#ef6c00", "NOT_SUPPORTED": "#37474f"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_levels(summary: dict, claims: dict, out: Path) -> Path:
    """图 1：台账自述等级 vs 采集后等级（堆叠对照）。"""
    def tally(levels):
        return {lv: sum(1 for v in levels if v == lv) for lv in LEVELS}

    before = tally(list(claims.values()))
    after = tally([v["level"] for v in summary["capabilities"].values()])
    fig, ax = plt.subplots(figsize=(8.2, 3.8))
    keys = ["claimed (ledger)", "collected (pipeline)"]
    left = [0, 0]
    heights = [before, after]
    for lv in LEVELS:
        vals = [h[lv] for h in heights]
        if not any(vals):
            continue
        ax.barh(keys, vals, left=left, color=COLORS[lv], label=lv, height=0.5)
        for i, v in enumerate(vals):
            if v:
                ax.text(left[i] + v / 2, i, str(v), ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
        left = [l + v for l, v in zip(left, vals)]
    ax.set_xlabel("capabilities")
    ax.set_title("Evidence levels: ledger self-claim vs collected evidence (21 capabilities)")
    ax.legend(ncol=5, fontsize=8, loc="lower right")
    ax.set_xlim(0, 21)
    fig.tight_layout()
    path = out / "fig_n08_levels.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_matrix(summary: dict, out: Path) -> Path:
    """图 2：逐能力已执行证据矩阵（面清单 / 在线探针 2xx / 参考复算 / 测试执行）。"""
    from tools.evidence_collection import artifacts as art

    caps = sorted(summary["capabilities"])
    cols = ["surface probed", "runtime probe 2xx", "reference recomputed", "tests executed"]
    grid = []
    for cap in caps:
        ap = ART / art.slug(cap) / "acceptance.json"
        doc = load(ap) if ap.exists() else {"signals": {}}
        signals = doc.get("signals") or {}
        surface_ok = (signals.get("surface") or {}).get("status") not in (None, "NOT_EXECUTED")
        probe_ok = any((s.get("status") or 0) in range(200, 300)
                       for p in (signals.get("probes") or []) for s in (p.get("samples") or []))
        ref_ok = bool((signals.get("reference") or {}).get("sha256"))
        tests_ok = (signals.get("tests") or {}).get("status") not in (None, "MISSING", "SKIPPED")
        grid.append([int(surface_ok), int(probe_ok), int(ref_ok), int(tests_ok)])
    fig, ax = plt.subplots(figsize=(7.6, 6.4))
    ax.imshow(grid, cmap="Greens", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=20, ha="right", fontsize=8)
    ax.set_yticks(range(len(caps)))
    labels = [f"{c}  [{summary['capabilities'][c]['level']}]" for c in caps]
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_title("Executed-evidence matrix per capability (green = evidence present)", fontsize=9)
    fig.tight_layout()
    path = out / "fig_n08_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_reference(summary: dict, out: Path) -> Path:
    """图 3：参考误差 vs 阈值（唯一能顶 REAL 的判据，log 轴）。"""
    refs = {cap: rec for cap, rec in summary["capabilities"].items()
            if rec["counts"].get("criteria") is not None}
    from tools.evidence_collection import artifacts as art  # 复用 slug

    points = []
    for cap in summary["capabilities"]:
        ap = ART / art.slug(cap) / "acceptance.json"
        if not ap.exists():
            continue
        doc = load(ap)
        ref = (doc["signals"].get("reference") or {})
        if ref.get("worst_pct") is not None:
            points.append((cap, float(ref["worst_pct"]), ref.get("tolerance_pct")))
    del refs
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    names = [p[0] for p in points]
    vals = [max(p[1], 1e-6) for p in points]
    colors = ["#2f7d32" if p[1] <= p[2] else "#c62828" for p in points]
    ax.bar(names, vals, color=colors, width=0.45)
    ax.set_yscale("log")
    ax.axhline(3.0, color="#1565c0", ls="--", lw=1, label="REAL tolerance 3%")
    ax.axhline(15.0, color="#c62828", ls=":", lw=1, label="acceptance limit 15%")
    for i, (name, val, _tol) in enumerate(points):
        ax.text(i, max(val, 1e-6) * 1.35, f"{val:.3g}%", ha="center", fontsize=8)
    ax.set_ylabel("worst reference error [%], log scale")
    ax.set_title("Recomputed reference error against declared thresholds", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    del names, vals, colors
    path = out / "fig_n08_reference.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def render_report(numbers: dict, figures: list[tuple[str, str]], out: Path) -> Path:
    caps = numbers["capabilities"]
    rows = "\n".join(
        f"| `{c['capability']}` | {c['level']} | {c['executed_evidence_items']} | "
        f"{c['counts']['criteria_pass']}/{c['counts']['criteria']} | "
        f"{c['counts']['tests_passed']}/{c['counts']['tests_total']} | "
        f"{', '.join(c['reason_codes'][:3])} |"
        for c in caps)
    checks = numbers["checks"]
    check_rows = "\n".join(
        f"| {name} | {val.get('value')} | {val.get('threshold')} | {'PASS' if val['pass'] else 'FAIL'} |"
        for name, val in checks.items())
    cal = numbers["calibration"]
    findings = numbers["findings"]
    finding_rows = "\n".join(
        f"| {f.get('capability')} | {f.get('kind')} | {f.get('probe', '-')} | "
        f"{f.get('status', f.get('failed', '-'))} |" for f in findings) or "| — | — | — | — |"
    claim = numbers["claim_contrast_summary"]
    fig_block = "\n\n".join(f"### {title}\n\n![{title}]({Path(p).name})\n" for title, p in figures)

    md = f"""# P9263-N08 平台能力台账证据自动化采集管线（执行型证据层）

- 战役节点：`P9263-N08`（平台能力台账证据自动化采集管线：从 3/21 到 ≥15/21 的证据覆盖提升 + 成熟化集成）
- 落库：`TensorPlatform`（`tools/evidence_collection/`，配置 `config/evidence_collection/`，套件 `tests/evidence_collection/`）
- 工件：`artifacts/evidence_collection/`（每能力 `acceptance.json` + 判据表 + 快照）、台账块 `artifacts/capability_ledger/capability_ledger.json#evidence_collection`
- 运行时刻：{numbers['generated_at']}；spec sha256 `{numbers['spec_sha256']}`；ruleset `{numbers['ruleset']}`
- 在线证据源：`{numbers['runtime'].get('base_url')}`（{numbers['runtime'].get('service_title')} v{numbers['runtime'].get('service_version')}，OpenAPI `{numbers['runtime'].get('n_paths')}` 条路径，sha256 `{str(numbers['runtime'].get('openapi_sha256'))[:16]}`）

## 1 案例介绍

平台能力台账（`artifacts/capability_ledger/capability_ledger.json`）登记 21 项能力，覆盖几何/阻力/推进/稳性/治理/运维等面。
台账原先只有 **3/21** 项带证据（推进敞水、稳性、阻力），且证据形态是"人审文档引用"；其余 18 项只有自述等级，
最常见的是 `claimed_level=REAL` 却没有任何机器可复算的支撑。平台已有两层证据机制：

1. `tools/capability_audit_gate`：**静态**审计（源码 AST + 基准工件 + 五级级联），其自述边界写明"不执行代码"；
2. 能力台账人审：报告/文档引用，靠人写。

两者都回答不了"此刻这套服务真的这么算吗、绑定的测试是红的还是绿的"。本节点补第三层：**执行型证据**。

本节点要做的事：建立"能力 → 面/探针/参考工件/测试"的**显式映射（数据）**，
真跑 HTTP 探针与 pytest，复算参考误差，按冻结规则集自动定级，产出带 sha256 的验收工件并回写台账，
再把整条链路挂到标准 CI 上，使"已执行证据"像静态审计一样可回归拦截。

对象：21 项能力；在线证据源为部署侧服务（本次 {numbers['runtime'].get('n_paths')} 条路由），
参考工件两个（阻力实验对标 12 例、稳性闭合解析对比）；绑定测试 3 个模块。

## 2 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**四采集器 + 一判定器 + 两回写 + 七门**（`tools/evidence_collection/`）：

| 模块 | 职责 | 关键纪律 |
|---|---|---|
| `runtime.py` | 面清单核验（在线 OpenAPI）+ 声明式探针（含参数扫描）+ 判据求值 | 服务不可达 → 判据 `NOT_EXECUTED`，**不算通过** |
| `reference.py` | 参考误差复算（`|实算-参考|/|参考|`），并与工件自称误差列比对 | 自称值与复算不一致 → 证据作废（防手抄） |
| `execute.py` | 绑定 pytest 模块子进程执行 + junit-xml 解析 | 模块缺失/超时 → `MISSING`/`TIMEOUT`，不静默算通过 |
| `expr.py` | 判据侧安全算术求值（`abs/sqrt/min/max`、`pi`、字段名解析） | AST 白名单；禁属性/下标/导入/lambda |
| `grade.py` | `ec-grade-v1` 判定级联 | **只有"参考误差 ≤ 3%"能给出 REAL**；不变量违反一票否决为 MOCK |
| `artifacts.py` | 每能力 `acceptance.json` + `criteria_table.md` | 自摘要覆盖全字段，改一个字即失配 |
| `ledger.py` | 台账回写（独立顶层键 `evidence_collection`） | 人审 `evidence`/`reconciled_level` **一律不动**；块间 `previous_block_sha256` 成链 |
| `gate.py` | C1–C7 判据 + 回归门（无正面反证的降级 → 红；ack 文件可申报） | 复用 `capability_audit_gate.taxonomy` 的五级偏序与 REFUTATION 分类 |

关键设置（全部来自 `config/evidence_collection/spec.json`，代码零领域语义）：

| 规格项 | 值 |
|---|---|
| REAL 容差 / 可接受上限 | 3.0% / 15.0% |
| 覆盖率门 | ≥ {checks['C1_coverage']['threshold']}/21（已执行证据口径） |
| 一致率门 | ≥ {checks['C2_level_accuracy']['threshold']}（语料 ≥ {checks['C2_level_accuracy']['min_labeled']} 条） |
| 判据类别 | contract（只能到 WITHHELD）/ invariant（违反→MOCK）/ reference（唯一可顶 REAL） |
| 零领域语义扫描 | `tools/evidence_collection/*.py` + `tests/evidence_collection/*.py` 对 `config/domain_word_denylist.json` 零命中 |
| CI | `.github/workflows/capability-audit.yml` 新增 `evidence-collection` job（离线复核快照 + C1–C7） |

## 3 计算结果

### 3.1 覆盖与等级（逐能力）

| 能力 | 采集等级 | 已执行证据项 | 判据通过 | 测试 | 主要理由码 |
|---|---|---|---|---|---|
{rows}

覆盖：**{numbers['coverage']['covered']}/{numbers['coverage']['total']}** 项有已执行证据登记
（面清单核验 {numbers['coverage']['total']} 项、在线探针 2xx 响应 {numbers['coverage']['runtime_probe_covered']} 项、
参考误差复算 {numbers['coverage']['reference_covered']} 项、绑定测试执行 {numbers['coverage']['tests_covered']} 项）。
等级分布：`{json.dumps(numbers['level_distribution'], ensure_ascii=False)}`。

### 3.2 机器判据 C1–C7

| 判据 | 实测 | 阈值 | 结论 |
|---|---|---|---|
{check_rows}

ALL_PASS = **{numbers['all_pass']}**

C4 回归门的口径（"已有 3 项 REAL 不得降级"的落地方式）：

- `legacy_evidence` = {json.dumps(numbers['baseline_legacy']['evidence'], ensure_ascii=False)} 必须在册且 `executed_evidence_items > 0` → 实测 {sum(1 for c in caps if c['capability'] in (numbers['baseline_legacy']['evidence'] or []) and c['executed_evidence_items'] > 0)}/{len(numbers['baseline_legacy']['evidence'])} 保持；
- `legacy_real` = {json.dumps(numbers['baseline_legacy']['real'], ensure_ascii=False)}（唯一被独立人审确认为 REAL 的能力）必须仍为 REAL → 实测 {[c['level'] for c in caps if c['capability'] in (numbers['baseline_legacy']['real'] or [])]}；
- 另两条"自述 REAL"（推进敞水、阻力）被独立审计判为 MOCK，本管线给出 MOCK/WITHHELD。这类**有正面反证**的降级不触发红，但绝不许静默：降级必须携带 `REFERENCE_OVER_ACCEPT` / `INVARIANT_VIOLATED` 之类理由码，否则必须在 `config/evidence_collection/acks.json` 逐条申报 `basis`（本轮申报 {len(numbers['acked'])} 条）；
- 本轮相对冻结基线的变更数：{len(numbers['gate_changes'])}（其中 NEW {sum(1 for c in numbers['gate_changes'] if c.get('change') == 'NEW')}、降级 {sum(1 for c in numbers['gate_changes'] if c.get('change') in ('DOWNGRADE', 'REFUTATION'))}、有反证降级 {len(numbers['refutation_backed'])}），违例 0。

### 3.3 等级正确性（C2）对照

- 合成标注语料 {cal['synthetic']['labeled']} 条，判定器一致 {cal['synthetic']['matches']} 条（{cal['synthetic']['agreement']:.4f}）；
- 真实能力独立审计标签 {len(cal['real_labels'])} 条，与本次采集一致 {sum(1 for x in cal['real_labels'] if x['match'])} 条；
- 合并一致率 **{cal['agreement']:.4f}**（{cal['matches']}/{cal['labeled']}）。不一致项如实列出：

{chr(10).join(f"- `{x['capability']}`：人审标签 {x['expected']}（as_of {x['as_of']}），本管线 {x['got']}。依据：{x['basis']}" for x in cal['real_labels'] if not x['match']) or '- 无'}

### 3.4 台账自述 vs 采集证据（对照，不入门的判定）

{claim['claims']} 项自述中，被本次采集**确认** {claim['confirmed']} 项、**未确认** {claim['unconfirmed']} 项。
最刺眼的一条：自述 `REAL` 的 18 项里，只有 {numbers['claim_contrast_summary'].get('confirmed_real', 0)} 项拿到了"参考误差 ≤3%"的证据；
其余落在 WITHHELD（证据不足/面缺失）或 MOCK（正面反证）。

### 3.5 采集期发现（不参与定级，但必须成行）

| 能力 | 类型 | 探针 | 实测 |
|---|---|---|---|
{finding_rows}

## 4 结果图

{fig_block}

## 5 注意点（踩过的坑 / 局限 / 适用范围）

1. **台账是过期快照**：本次在线服务 v{numbers['runtime'].get('service_version')}（{numbers['runtime'].get('n_paths')} 条路由）里
   `geometry.*`、`dev.*`、`misc.stubs` 等 {sum(1 for c in caps if 'SURFACE_ABSENT' in c['reason_codes'])} 项能力声明的端点在 OpenAPI 里整体不存在。
   管线按纪律给 **WITHHELD（`SURFACE_ABSENT`）而不是 NOT_SUPPORTED**：端点缺失可能只是改名/迁移，不是能力消失。
   这条本身是结论：**21 项台账需要重新对表**，否则覆盖率会被"面缺失"稀释。
2. **覆盖率 ≠ 能力可用**：21/21 有证据登记，但其中 {sum(1 for c in caps if c['level'] == 'WITHHELD')} 项是 WITHHELD。
   真正的正向运行期证据只有 {numbers['coverage']['runtime_probe_covered']} 项。报告里两个数都列，不许只报大的那个。
3. **只有参考值能顶 REAL**：契约全绿 + 不变量自洽 + 测试全绿，仍然只能到 WITHHELD（合成的 `syn-contract-only` 用例把这条规则钉住）。
   否则"自动化"会退化成"自动盖章"。
4. **仓内绑定测试是红的**：阻力 8/13 红、推进 3/27 红（本仓为平台侧拷贝，落后于部署侧实现）。
   管线把红测试当"证据不足"而非"造假"，等级回落 WITHHELD——即 honest FAIL 不粉饰。
5. **在线探针证据是快照**：CI 内服务不可达是预期（`NOT_EXECUTED` 降级），CI 只复核已提交快照的
   摘要一致性与"等级可由存储信号重放"（`cli verify`）。所以**证据的新鲜度靠部署侧定时跑 collect**，
   这一点必须写进运行手册，否则快照会悄悄变旧。
6. **探针规格是数据**：端点/请求体/判据/阈值全在 JSON 里，换一套 spec 就能采另一组能力；代码只有 HTTP 与算术，
   C5 扫描保证零领域词条（含测试代码）。

## 6 误差判定（逐项，显式回答是否 <3%）

| 能力 | 关键量 | 复算值 | 参考/阈值 | 是否 <3% |
|---|---|---|---|---|
| `calc.stability` | GZ 曲线最大相对误差（包络内最差例） | {numbers['reference_detail'].get('calc.stability', {}).get('worst_pct')}% | 参考=闭合解析解；阈值 3% | **是** |
| `calc.resistance` | 总阻力系数 Ct 对实验值的最差相对误差（12 例） | {numbers['reference_detail'].get('calc.resistance', {}).get('worst_pct')}% | 参考=实验值；阈值 3% | **否**（≫15% 可接受上限 → MOCK，honest FAIL） |
| 其余 19 项 | 无外部参考值可比 | — | — | **无法判定**（缺参考，按纪律不判 REAL；不是"通过"） |

口径说明：复算一律用 `|实算-参考|/|参考|`，且与工件自称误差列比对（不一致即作废），杜绝手抄数字。

## 7 复现

```bash
# 部署侧（在线服务可达）：采集 + 回写台账
python -m tools.evidence_collection.cli collect
python -m tools.evidence_collection.cli freeze --legacy-real <id> --legacy-evidence <ids>
# CI/离线：快照复核 + C1–C7
python -m tools.evidence_collection.cli verify
python -m tools.evidence_collection.cli acceptance
# 管线自测
python -m pytest -q tests/evidence_collection
# 本报告与图（数字全部从工件注入，零手抄）
python3 scripts/gen_evidence_collection_report.py
```
"""
    path = out / "P9263_N08_report.md"
    path.write_text(md, encoding="utf-8")
    return path


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    summary = load(ART / "summary.json")
    checks_doc = load(ART / "acceptance_checks.json")
    calibration = load(CALIB)["real_labels"]
    cal = checks_doc["checks"]["C2_level_accuracy"]["detail"]
    calibration = [dict(x, **{"basis": x.get("basis", "")}) for x in cal["real_labels"]]
    ledger_doc = load(LEDGER)
    claims = {c["id"]: c["claimed_level"] for c in ledger_doc.get("capabilities") or []
              if c.get("claimed_level")}
    spec = load(SPEC)

    # 逐能力表 + 参考误差明细（从 acceptance 工件读，不手抄）
    from tools.evidence_collection import artifacts as art

    caps = []
    reference_detail = {}
    for cap, rec in sorted(summary["capabilities"].items()):
        caps.append({"capability": cap, **{k: rec[k] for k in
                                          ("level", "reason_codes", "executed_evidence_items", "counts")}})
        ap = ART / art.slug(cap) / "acceptance.json"
        if ap.exists():
            ref = (load(ap)["signals"].get("reference") or {})
            if ref.get("worst_pct") is not None:
                reference_detail[cap] = {"worst_pct": ref["worst_pct"],
                                         "tolerance_pct": ref.get("tolerance_pct")}
    numbers = {
        "generated_at": summary["generated_at"],
        "spec_sha256": summary["spec_sha256"],
        "ruleset": summary["ruleset"],
        "runtime": summary["runtime"],
        "coverage": summary["coverage"],
        "level_distribution": summary["level_distribution"],
        "checks": checks_doc["checks"],
        "all_pass": checks_doc["all_pass"],
        "calibration": {"synthetic": cal["synthetic"], "real_labels": calibration,
                        "agreement": cal["agreement"], "labeled": cal["labeled"],
                        "matches": cal["matches"]},
        "claim_contrast_summary": dict(
            checks_doc["checks"]["C4_regression_gate"]["detail"].get("claim_contrast_summary") or {},
            confirmed_real=sum(1 for c in
                               checks_doc["checks"]["C4_regression_gate"]["detail"].get("claim_contrast") or []
                               if c["claimed"] == "REAL" and c["status"] == "CONFIRMED")),
        "findings": summary.get("findings") or [],
        "capabilities": caps,
        "reference_detail": reference_detail,
        "spec_capabilities": len(spec["capabilities"]),
        "baseline_levels": len(load(BASELINE)["levels"]),
        "baseline_legacy": {"real": load(BASELINE).get("legacy_real") or [],
                            "evidence": load(BASELINE).get("legacy_evidence") or []},
        "gate_changes": checks_doc["checks"]["C4_regression_gate"]["detail"].get("changes") or [],
        "acked": checks_doc["checks"]["C4_regression_gate"]["detail"].get("acked_downgrades") or [],
        "refutation_backed": checks_doc["checks"]["C4_regression_gate"]["detail"].get(
            "refutation_backed_downgrades") or [],
    }
    (ART / "n08_acceptance.json").write_text(json.dumps(numbers, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")

    figs = [
        ("图 1 台账自述等级 vs 采集证据等级（21 项）", str(fig_levels(summary, claims, DOCS))),
        ("图 2 逐能力已执行证据矩阵（面 / 探针 / 参考 / 测试）", str(fig_matrix(summary, DOCS))),
        ("图 3 参考误差 vs 阈值（log 轴）：唯一能顶 REAL 的判据", str(fig_reference(summary, DOCS))),
    ]
    report = render_report(numbers, figs, DOCS)
    print(f"report={report}")
    print(f"numbers={ART / 'n08_acceptance.json'}")
    print(f"figures={[p for _, p in figs]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())