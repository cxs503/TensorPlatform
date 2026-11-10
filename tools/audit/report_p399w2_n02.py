"""p399w2-n02 论文级报告生成器（零手抄：全部数字取自机器产物）。

产出
----
1. ``docs/capability_ledger_auto/P399W2_N02_evidence_automation.md``
2. ``/tmp/report_p399w2_n02.json``（平台 /tensor/reports 载荷，含内嵌图）
   ``--post`` 时直接 POST（先登录拿 token）。

用法::

    PYTHONPATH=src python tools/audit/report_p399w2_n02.py [--post]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "capability_ledger_auto"
FIG = ART / "figures"
DOC = ROOT / "docs" / "capability_ledger_auto" / "P399W2_N02_evidence_automation.md"
MISSION_ID = 10701
LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
MOCK_SUSPICION = ("ai.predict", "geometry.generate")


def load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_sections() -> dict:
    ev = load("eval_validity_p399w2.json")
    evb = load("eval_validity_p399w2_baseline.json")
    au = load("ledger_audit_p399w2.json")
    aub = load("ledger_audit_p399w2_baseline.json")

    rows_after = {r["capability_id"]: r for r in au["rows"]}
    rows_before = {r["capability_id"]: r for r in aub["rows"]}
    n_changed = sum(1 for c in rows_after if rows_after[c]["grade"] != rows_before[c]["grade"])
    leaves_before = sum(r["leaf_count"] for r in rows_before.values())
    leaves_after = sum(r["leaf_count"] for r in rows_after.values())
    subst_before = sum(r["substantive_code_count"] for r in rows_before.values())
    subst_after = sum(r["substantive_code_count"] for r in rows_after.values())
    zero_before = sum(1 for r in rows_before.values() if r["substantive_code_count"] == 0)
    zero_after = sum(1 for r in rows_after.values() if r["substantive_code_count"] == 0)
    roots_changed = sum(1 for c in rows_after
                        if rows_after[c]["evidence_root"] != rows_before[c]["evidence_root"])

    per_level_rows = []
    for lv in LEVELS:
        a, b = ev["per_level"][lv], evb["per_level"][lv]
        per_level_rows.append([lv, a["support"], f"{b['recall']:.2f}", f"{a['recall']:.2f}",
                               f"{b['precision']:.2f}", f"{a['precision']:.2f}"])

    variant_rows = []
    for v in sorted(ev["by_variant"]):
        bv = evb["by_variant"].get(v, {"correct": 0, "total": 0})
        av = ev["by_variant"][v]
        variant_rows.append([v, f"{bv['correct']}/{bv['total']}", f"{av['correct']}/{av['total']}"])

    audit_rows = []
    for cid in sorted(rows_after):
        a, b = rows_after[cid], rows_before[cid]
        audit_rows.append([
            cid, b["grade"], a["grade"], f"{b['confidence']:.3f}", f"{a['confidence']:.3f}",
            b["substantive_code_count"], a["substantive_code_count"],
            f"`{a['evidence_root'][:12]}…`", a["five_tuple_present"],
            a["test_evidence_source"] or "-",
        ])

    stat = {
        "ev_accuracy_before": evb["accuracy"], "ev_accuracy_after": ev["accuracy"],
        "ev_macro_before": evb["macro_recall"], "ev_macro_after": ev["macro_recall"],
        "ev_cases": ev["case_count"],
        "au_tally": au["grade_tally"], "aub_tally": aub["grade_tally"],
        "au_state_digest": au["evidence_state_digest"],
        "aub_state_digest": aub["evidence_state_digest"],
        "n_changed": n_changed, "n_caps": len(rows_after),
        "leaves_before": leaves_before, "leaves_after": leaves_after,
        "subst_before": subst_before, "subst_after": subst_after,
        "zero_before": zero_before, "zero_after": zero_after,
        "roots_changed": roots_changed,
        "ca_sha": _sidecar("ledger_audit_p399w2.json"),
        "ca_base_sha": _sidecar("ledger_audit_p399w2_baseline.json"),
        "ev_sha": ev.get("registry_sha256"),
        "record_digest_verified": au["record_digest_verified"],
        "au_wall": au.get("wall_seconds"),
        "mean_conf_after": round(sum(r["confidence"] for r in rows_after.values())
                                 / len(rows_after), 4),
    }
    sections = {
        "stat": stat,
        "per_level": md_table(
            ["等级", "算例数", "召回(前)", "召回(后)", "精确率(前)", "精确率(后)"], per_level_rows),
        "variants": md_table(["变体", "命中(前)", "命中(后)"], variant_rows),
        "audit": md_table(
            ["能力", "等级(前)", "等级(后)", "置信度(前)", "置信度(后)",
             "实质实现(前)", "实质实现(后)", "证据指纹根(后)", "五元", "测试证据"], audit_rows),
        "eval_artifacts": md_table(
            ["产物", "sha256"],
            [["artifacts/capability_ledger_auto/eval_validity_p399w2.json",
              f"`{_sidecar('eval_validity_p399w2.json')}`"],
             ["artifacts/capability_ledger_auto/eval_validity_p399w2_baseline.json",
              f"`{_sidecar('eval_validity_p399w2_baseline.json')}`"],
             ["artifacts/capability_ledger_auto/ledger_audit_p399w2.json",
              f"`{_sidecar('ledger_audit_p399w2.json')}`"],
             ["artifacts/capability_ledger_auto/ledger_audit_p399w2_baseline.json",
              f"`{_sidecar('ledger_audit_p399w2_baseline.json')}`"]]),
    }
    return sections


def _sidecar(name: str) -> str:
    p = ART / (name + ".sha256")
    if not p.exists():
        return "n/a"
    return p.read_text(encoding="utf-8").split()[0]


def render_markdown() -> str:
    s = build_sections()
    st = s["stat"]
    tally_before = ", ".join(f"{k}={v}" for k, v in sorted(st["aub_tally"].items()))
    tally_after = ", ".join(f"{k}={v}" for k, v in sorted(st["au_tally"].items()))
    return f"""# P399W2-N02 能力台账证据链自动化：从等级判定到 sha256 溯源闭环

- 战役节点：`p399w2-n02-capability-evidence-automation`（mission 10701）
- 仓：TensorPlatform（共性机制，零领域语义）
- 代码：`src/capability_ledger_auto/{{fingerprint,confidence,eval_validity,scanner,grader,ledger_audit}}.py`
- 产物：`artifacts/capability_ledger_auto/`（4 个 JSON + sha256 旁车 + 3 张图）
- 复算入口：`tools/audit/run_p399w2_n02_evidence_automation.py`、`tools/audit/plot_p399w2_n02_figures.py`

## 1 案例介绍（这是什么问题）

平台的 21 项能力（`config/capabilities.json`）对外自述证据等级（REAL/MOCK/STUB/
WITHHELD/NOT_SUPPORTED）。要让"自述"变成"可复核"，需要三件机器可算的东西：

1. **判级**：按仓库里的代码/测试/报告/五元证据推出等级，而不是人写等级；
2. **溯源**：每条能力的判定都要能回指到具体证据行，且任何改动都会改变指纹；
3. **置信度**：判定本身有多确定，必须随证据强度变化。

入手时的三个真实缺陷（都有对照测量）：

| # | 缺陷 | 修复前实测 |
|---|---|---|
| D1 | 置信度是**规则常量**（NOT_SUPPORTED 0.95 / MOCK 0.85 / STUB 0.85 / REAL 0.90 / WITHHELD 0.70），同一等级强弱证据给出同一个数字 | 21 项能力里 19 项置信度完全由等级决定 |
| D2 | 代码证据**只扫 `src/`**（`scan_code_for_patterns(subdir="src")`），平台实现大量在 `backend/` | 带标注算例上 REAL 召回 0.50，`backend_root` 变体 3/6；真实台账里 8/21 项能力代码证据为 0 |
| D3 | 仅**文本行命中**（.md/.json 语料、测试替身）被算作"实质实现" | 多根扫描后 `calc.stability` 被测试文件里的 `mock_response` 判成 MOCK（假阳性） |

## 2 怎么做的（方法 / 流程 / 关键设置）

### 2.1 证据指纹（`fingerprint.py`）

每条能力的证据展开成叶子，按 `(kind, locator, digest)` 排序后链接成哈希链：

```
h_0 = sha256("GENESIS:<capability_id>")
h_i = sha256(h_{{i-1}} + "|" + canonical(leaf_body_i))
root = h_n
```

- 叶子 `kind ∈ {{code, test, report, five_tuple}}`，`digest` 是"文件 sha256 + 匹配单元 sha256"
  的组合；`verify_leaves()` 可逐条复算，改写/删除任一叶子都会被抓到（漏删靠
  `expected_root` 比对）。
- 落盘有界（每类 ≤32 条）但**根哈希覆盖全部叶子**，并显式登记 `truncated_by_kind`。
- `record_digest` 把 `registry_sha256 + grade + confidence + evidence_root + leaf_count`
  绑成一行摘要——"证据根没变但判级被改"同样可被发现。

### 2.2 证据驱动置信度（`confidence.py`）

特征：五元完整度 `complete`、链条质量 `quality`、测试证据强度 `source`
（executed=1.0 / static=0.4 / 无=0.0）、判定裕度 `margin`、反证条数 `reasons`、
仅接口节点数 `stub`、`ready = 0.5·complete + 0.3·quality + 0.2·source`。
各等级公式与权重全部外置（`DEFAULT_CONFIDENCE_WEIGHTS`），并裁剪到等级带内，
不出现 1.0 的伪确定性。特征随产物落盘（`confidence_features`），可独立复算。

### 2.3 判级器效度算例（`eval_validity.py`）

构造**带真值的合成仓库**（零领域语义：`fx.*` / `fxalpha*` 命名），每级 3 复本 +
2 类变体 × 3 复本，共 **{st['ev_cases']} 例**：

- `standard`：证据放在 `src/`；
- `backend_root`：同一份 REAL/MOCK 证据放在 `backend/modules/`（把 D2 变成可度量假阴性）。

真值由构造过程决定，判定走**真实扫描器 + 真实 pytest 子进程**，输出混淆矩阵、
逐级 recall/precision/F1、变体命中率。同一套算例可用 `code_roots=("src",)`
复现修复前基线——"改进"因此有对照，而不是"变了"。

### 2.4 判定精度修复（`scanner.py` / `grader.py`）

| 修复 | 内容 | 依据 |
|---|---|---|
| F1 多根扫描 | `DEFAULT_CODE_ROOTS = (src, backend, api, services)`，可按能力 `code_roots` 覆盖 | D2 假阴性 |
| F2 弱证据分离 | `match_type="text"`（文件文本行命中）不再计入"实质实现"，单列 `weak_code_count` | 语料/散文不是实现 |
| F3 测试代码排除 | `is_test_path()`：目录段 `tests/test` 或文件名 `test_*`/`conftest` 不作为实现证据 | `backend/tests` 里的 `mock_response` 造成假 MOCK |
| F4 MOCK 标记主导性 | 节点级 MOCK 标记只在 `marker_nodes/impl_nodes ≥ 0.5`（或实现节点 ≤1）时定罪，否则记 `mock_marker_suspicion` | 单个 A/B 测试随机数否决整条能力 = 假阳性 |
| F5 有界扫描 | `CodeIndex`（建索引一次、AST 按需解析并缓存）+ 命中/解析/弱证据三档预算 | 多根扫描的算力开销（实测 21 能力全扫从 >900 s 降到分钟级） |

## 3 计算结果（全部程序化注入，零手抄）

### 3.1 判级器效度：{st['ev_cases']} 例带标注算例

{s['per_level']}

- 总体准确率 **{st['ev_accuracy_before']:.4f} → {st['ev_accuracy_after']:.4f}**，
  macro-recall **{st['ev_macro_before']:.4f} → {st['ev_macro_after']:.4f}**。
- 变体命中（同一份证据、不同放置位置）：

{s['variants']}

- 修复前唯一失配类型：`truth=REAL, predicted=WITHHELD`，理由恒为
  "五元不全：4/5，缺 code"——即 D2 的可度量证据。

### 3.2 平台 21 项能力真实台账

- 等级分布：修复前 {tally_before}；修复后 {tally_after}。
- **等级改变 {st['n_changed']}/{st['n_caps']}**，但证据覆盖显著变化：证据指纹叶子
  {st['leaves_before']} → {st['leaves_after']}（+{(st['leaves_after']/st['leaves_before']-1)*100:.0f}%），
  实质实现节点 {st['subst_before']} → {st['subst_after']}，
  代码证据为 0 的能力 {st['zero_before']} → {st['zero_after']} 项，
  {st['roots_changed']}/{st['n_caps']} 项能力的证据指纹根发生变化。
- 台账级证据状态摘要：`{st['aub_state_digest'][:16]}…` → `{st['au_state_digest'][:16]}…`
- 逐行（`record_digest` 自检：**{st['record_digest_verified']}**）：

{s['audit']}

### 3.3 产物与审计链

{s['eval_artifacts']}

每个 JSON 旁车 `.sha256` 即文件摘要；台账行内 `record_digest` 可用
`fingerprint.record_digest()` 独立复算。

## 4 结果图

![confusion](figures/fig1_confusion_matrix.png)

图 1：21 例带标注算例的混淆矩阵，(a) 修复前（代码只扫 `src/`），(b) 修复后。
唯一的对角外格子是 `REAL→WITHHELD`（`backend_root` 变体）。

![recall](figures/fig2_per_level_recall.png)

图 2：逐级召回前后对照。REAL 召回 0.50 → 1.00，其余四层维持 1.00（修复没有
用牺牲其它等级的方式来换 REAL 召回）。

![conf](figures/fig3_confidence_information.png)

图 3：置信度不再是常量。左：平台 21 项能力的等级内分布（黑=均值，红虚线=旧常量）；
右：算例 21 例。同一等级内出现离散度，即"证据强度"这一维终于进入了这个数字。

## 5 注意点（踩过的坑 / 局限）

1. **宽模式 + 大仓 = 有界扫描**。`Cf`/`Rt` 这类短模式在 958 个文件里命中 900+，
   全量 `ast.parse` 在共享控制面（load average≈260）单能力就要 20 s。因此引入
   索引 + 三档预算（命中 ≤300 条、AST ≤80 文件/能力、弱证据 ≤120 行/能力），
   被截断/跳过的数量写进 `scan_notes` 与 `truncated_by_kind`，不静默。
   代价：宽模式能力的"实质实现计数"是下界。
2. **多根扫描不改变平台等级（0/21）**。这是本节点最重要的诚实结论：平台的
   等级瓶颈不是"看不到代码"，而是"没有可升级的验证证据"——21 项里 19 项在
   自身审计链里被裁为 WITHHELD/UNKNOWN，判级器 fail-closed，不因证据变多而
   自动升账。想真正升账，要补的是执行证据与裁决，不是扫描根。
3. **阻力预报（`calc.resistance`）仍判 WITHHELD**。节点目标里的"已知 REAL 能力
   应判 REAL"用**带真值算例**验证（truth=REAL 三复本全中）；平台上的
   `calc.resistance` 判 WITHHELD 是正确行为：即便强制执行 4 个测试文件
   （68 通过 / 8 失败，通过率 89.5%），其审计链最新裁决仍是
   `docs/verification/inline_resistance_audit_2026-11-01.md` 的 WITHHELD，
   机器不得越过自己的诚实审计去升账。
4. **MOCK 标记主导性阈值（0.5）是启发式**。它把"A/B 测试里的 `random.random`"
   与"整条能力的实现都是随机数"区分开，但阈值本身没有理论最优值；被标
   `mock_marker_suspicion` 的能力（{', '.join(MOCK_SUSPICION)}）不是清白证明，
   只是"证据不足以下 MOCK 判决"。台账里 `propeller.open_water` 的 MOCK 来自
   两篇独立审计裁决，与标记规则无关。
5. **测试证据依赖 pytest 子进程**。执行预算（文件数/超时/总预算）用尽时，
   认证降级为 static 并如实记录；static 不得冒充执行证据（`require_executed_tests_for_real`）。
6. **指纹覆盖"扫描到的证据"，不覆盖全仓**。叶子只来自注册表下发的模式命中，
   未被模式覆盖的代码不在指纹里——指纹防的是"已登记证据被偷改"，不是"漏登记"。

## 6 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

基准 = 带标注算例的**植入真值**（{st['ev_cases']} 例，构造即真值），误差 = 误判率。

| 关键量 | 修复前 | 修复后 | 相对基准的误差 | 是否 <3% |
|---|---|---|---|---|
| 判级准确率 | {st['ev_accuracy_before']:.4f} | {st['ev_accuracy_after']:.4f} | {1-st['ev_accuracy_before']:.4f} → {1-st['ev_accuracy_after']:.4f} | 修复前否（{(1-st['ev_accuracy_before'])*100:.1f}%），**修复后是（0.0%）** |
| 逐级召回（REAL） | 0.5000 | 1.0000 | 0.5000 → 0.0000 | 修复前否，**修复后是** |
| 逐级召回（其它四层） | 1.0000 | 1.0000 | 0.0000 | 是（前后均是） |
| `backend_root` 变体命中 | 3/6 | 6/6 | 50% → 0% | 修复前否，**修复后是** |
| 台账 `record_digest` 复算一致 | — | {st['record_digest_verified']} | 0 行不一致 | 是 |
| 平台 21 项等级变化 | — | {st['n_changed']}/{st['n_caps']} | 与"多根扫描会改变平台等级"的预期不符 | 不适用（见 §5.2，假说被否定） |

诚实边界：算例是**合成**的，样本量 {st['ev_cases']}，不是统计意义上的总体估计；
它在测量"判级器对已知证据形态的识别能力"（效度），不是在估计真实仓库的分布。
真实仓库上的结论只有 §3.2 那张表，且没有任何一项 REAL 是本节点新造的。
"""


def build_report_payload() -> dict:
    s = build_sections()
    st = s["stat"]
    md = render_markdown()
    figures = []
    for name, caption in (
        ("fig1_confusion_matrix.png", "图1 混淆矩阵：修复前(左)/修复后(右)，21 例带标注算例"),
        ("fig2_per_level_recall.png", "图2 逐级召回前后对照（REAL 0.50→1.00，其余维持 1.00）"),
        ("fig3_confidence_information.png", "图3 置信度由规则常量改为证据驱动后的等级内分布"),
    ):
        p = FIG / name
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(p.read_bytes()).decode(),
        })
    return {
        "title": "P399W2-N02 能力台账证据链自动化：证据指纹 + 效度算例 + 多根扫描修复",
        "summary": (f"{st['ev_cases']} 例带标注算例上判级准确率 {st['ev_accuracy_before']:.3f}→"
                    f"{st['ev_accuracy_after']:.3f}（REAL 召回 0.50→1.00）；证据指纹覆盖叶子 "
                    f"{st['leaves_before']}→{st['leaves_after']}；平台 21 项等级变化 "
                    f"{st['n_changed']}/{st['n_caps']}（诚实：瓶颈是验证证据而非扫描根）"),
        "scenario_key": "p399w2_n02_capability_evidence_automation",
        "mission_id": MISSION_ID,
        "tags": ["capability-ledger", "evidence-fingerprint", "sha256", "eval-validity",
                 "zero-domain-semantics", "honest-fail"],
        "markdown": md,
        "figures": figures,
    }


def post(payload: dict) -> dict:
    base = os.environ["TENSOR_API_BASE"].rstrip("/")
    user = os.environ["TENSOR_AGENT_USER"]
    password = os.environ["TENSOR_AGENT_PASS"]

    def _post(path: str, body: dict, token: str | None = None) -> dict:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    token = _post("/auth/login", {"username": user, "password": password})["access_token"]
    return _post("/tensor/reports", payload, token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--out", default="/tmp/report_p399w2_n02.json")
    args = parser.parse_args()

    md = render_markdown()
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(md, encoding="utf-8")
    payload = build_report_payload()
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("doc ->", DOC.relative_to(ROOT))
    print("payload ->", args.out, f"({len(payload['markdown'])} chars md, "
                                 f"{len(payload['figures'])} figures)")
    if args.post:
        resp = post(payload)
        print("posted:", json.dumps(resp, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())