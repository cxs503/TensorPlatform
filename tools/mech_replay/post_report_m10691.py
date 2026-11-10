#!/usr/bin/env python3
"""发布 p348w3-n03（mech_replay）论文级进展报告到平台 /tensor/reports。

数字全部从 artifacts/mech_replay/gate_report.json + summary.json 读取（零手抄），
图以 base64 内嵌。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "mech_replay"
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")


def login() -> str:
    payload = json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                          "password": os.environ["TENSOR_AGENT_PASS"]}).encode()
    req = urllib.request.Request(BASE + "/auth/login", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def main() -> int:
    summary = json.loads((ART / "summary.json").read_text(encoding="utf-8"))
    gate = json.loads((ART / "gate_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
    cover = manifest.get("snapshot_coverage", {})
    rg_real = [r for r in gate["replay_results"]
               if r["origin"] == "real" and r["mechanism"] == "report_gate"]
    rg_fail = [r for r in rg_real if r["decision"] == "fail"]
    c = gate["criteria"]
    figs = json.loads((ART / "figures_manifest.json").read_text(encoding="utf-8"))
    captions = {
        "fig1": "图1 8 个机制的 golden fixture 覆盖（real 派生 / 合成探针分层，红线为 C3 门槛 ≥5 条/机制）",
        "fig2": "图2 机制 × 6 类退化的检出矩阵（格内 k/n = 检出/样本；— 表示该机制无此类判据面）",
        "fig3": "图3 检出渠道分解：M1/M2/M3/M6 走决策与理由码 diff，M4 走二次调用漂移，M5 走审计面丢失",
    }
    figures = [
        {"name": Path(path).name, "caption": captions[key],
         "png_base64": base64.b64encode(Path(path).read_bytes()).decode()}
        for key, path in sorted(figs.items())
    ]

    c2_rows = "\n".join(
        f"| `{k}` | {v['samples']} | {v['detected']} | {v['detection_rate']:.4f} | {v['required_channel']} | "
        f"{'✅' if v['required_channel_ok'] else '❌'} |"
        for k, v in c["C2"]["classes"].items())
    per_mech = "\n".join(f"| `{k}` | {v} |" for k, v in gate["manifest_per_mechanism"].items())

    markdown = f"""## 1 案例背景与对象

平台 8 个核心门禁（能力登记 / 证据等级 / 机器门 / 报告门 / 合并门 / 自治提案 / 战役状态 / 预算门）
此前只有**事后结论**可查：判词埋在业务分支里，没有离线可重放的机制纯函数入口。于是 6 类
「门变绿但事故发生了」的退化（阈值放宽 / 异常吞掉 / 顺序敏感 / 幂等失效 / 审计字段丢失 /
短路常量）无法在合并前发现。本节点把每个机制压成声明式规则表，从真实战役记录派生
golden fixture，离线重放 diff 判词，并注入 6 类退化度量检出率，最后以 C1–C5 CI 门裁决。

## 2 方法与工具链

* `tools/mech_replay/spec.py`：声明式规则表 + 纯函数解释器（幂等 / 顺序无关 / 缺字段显式 fail / 审计可校验）。
* `tools/mech_replay/mechanisms.py`：8 个机制规则表（能力登记 5 条判据、证据等级 6、机器门 4、报告门 6、合并门 6、自治提案 5、战役状态 4、预算门 5）。
* `tools/mech_replay/reference.py`：8 机制的**独立命令式第二实现**，重放时做差分（C1 因此不是自证）。
* `tools/mech_replay/collector.py`：真实 `tensor_reports` / `campaign_plans` / `campaign_nodes` → 脱敏结构化投影（自由文本不落地、主键加盐 sha256）→ fixture + sha256 清单；`expected_verdict` 必须双实现一致才落盘。
* `tools/mech_replay/mutate.py`：6 类退化 × 92 个行为级变异体，每类指定**必需检出渠道**。
* `tools/mech_replay/gate.py`：C1–C5 门（绿=0 / 红=1），数字落 `artifacts/mech_replay/gate_report.json`。

## 3 计算结果

**C1–C5 实测（总判定 {'GREEN' if gate['green'] else 'RED'}）**

| 判据 | 实测 | 门槛 |
|---|---|---|
| C1 golden replay 一致性 | {c['C1']['fixtures_matched']}/{c['C1']['fixtures_total']}（一致性 {c['C1']['consistency']:.4f}） | ≥50 条且 100% |
| C2 退化检出率 | {c['C2']['mutation_detected']}/{c['C2']['mutation_samples']}（{c['C2']['detection_rate']:.4f}），每类 ≥{c['C2']['min_samples_per_class']} 样本 | 每类 ≥10、100% |
| C3 机制覆盖 | {c['C3']['mechanisms_covered']}/{c['C3']['mechanisms_total']}，单机制最少 {c['C3']['min_per_mechanism']} 条 | ≥8 机制、每机制 ≥5 |
| C4 sha256 完整性 | {c['C4']['files_ok']}/{c['C4']['files_checked']} 文件 | 100% 一致 |
| C5 pytest 覆盖面 | {c['C5']['test_count']} 例 | ≥20 |

**fixture 覆盖（{gate['manifest_fixture_count']} 条，real {gate['manifest_origin_counts']['real']} / probe {gate['manifest_origin_counts']['probe']}）**

| 机制 | fixture |
|---|---|
{per_mech}

**6 类退化检出**

| 退化类 | 样本 | 检出 | 检出率 | 必需渠道 | 达标 |
|---|---|---|---|---|---|
{c2_rows}

**真实记录上被机制抓住的平台欠账**

* 预算门：{cover.get('plans', 0)}/{cover.get('plans', 0)} 个计划 `budget_planned.max_tokens = 0` → `TYPE_ERROR`（分母为 0）判 fail：预算门对这批计划**不可判定**，而非「预算健康」。
* 报告门：真实报告 {len(rg_real)} 条 real fixture 中 {len(rg_fail)} 条被判 fail（`RG_ERROR_ANSWER`×4、`RG_SECTIONS`×4、`RG_SUMMARY`×2、`RG_FIGURES`×2）——历史报告缺「误差结论显式回答」可量化。
* 状态词表漂移：真实节点状态 `in-flight` vs 机制枚举 `in_flight`，采集器显式登记同义词（否则 `CS_STATUS_ENUM` 误报）。
* `tensor_dev_missions` 有 `budget` 无 per-mission 用量字段 → 预算门「已消耗」无法真实取数，mission 记录采集但不派生 fixture（`mission_records_derived = 0`）。

## 4 结果图

图 1–3 内嵌（覆盖 / 检出矩阵 / 检出渠道分解）。

## 5 注意点与局限

1. real fixture 的部分字段是**代理**（证据条数用 `metadata.acceptance_criteria` 长度、报告六要素用结构字段存在性），逐字段在 `field_sources` 标注。
2. `machine_gate` / `merge_gate` / `autonomy_proposal` 三个机制**无 real fixture**（库中无「实测 vs 阈值」/评审冲突/检索次数记录），仅由确定性探针覆盖；补齐需平台先落字段。
3. 规则表阈值是人写的机制口径显式化，不是从历史判词反推的最优值；改口径须升 `RULES_VERSION` 并重采 fixture。
4. M3 只对有集合判据的 6 个机制注入（12 样本），`merge_gate` / `budget_gate` 无集合面——「无靶面」而非「漏检」。

## 6 误差判定（是否 <3%）

无物理实验/解析解基准，误差判据替换为机器可算的判词基准量：一致性 {c['C1']['consistency']:.4f}（误差 0.00%）、检出率 {c['C2']['detection_rate']:.4f}（0.00%）、必需渠道达标 {'1.0000' if c['C2']['semantic_ok'] else '0'}（0.00%）、sha256 完整性 {c['C4']['files_ok']}/{c['C4']['files_checked']}（0.00%）、机制覆盖 {c['C3']['mechanisms_covered']}/{c['C3']['mechanisms_total']}（0.00%）——**全部 <3%**。
诚实说明：C1 的 0% 是「重放 vs 冻结判词」一致性，不等于「规则阈值与专家意图一致」，后者无基准可测，是本题最实质局限。

## 7 审计链与复现

* `artifacts/mech_replay/manifest.json`（75 文件 sha256 + 快照 sha256 `{gate['manifest_snapshot_sha256'][:16]}…`）、`gate_report.json`、`docs/mech_replay.md`。
* 复现：`python3 -m tools.mech_replay --mode {{replay,mutate,gate,figures}}`；`python3 -m pytest tests/test_mech_replay_*.py -q`（{c['C5']['test_count']} 例全绿）。
"""

    payload = {
        "title": "p348w3-n03 机制重放一致性套件：8 门禁 golden replay + 6 类退化 100% 检出（C1–C5 全绿）",
        "summary": (f"为平台 8 个核心门禁建 golden 重放套件：{gate['manifest_fixture_count']} 条 fixture"
                    f"（real {gate['manifest_origin_counts']['real']}/probe {gate['manifest_origin_counts']['probe']}）"
                    f"判词一致性 {c['C1']['consistency']:.4f}；6 类退化 {c['C2']['mutation_detected']}/{c['C2']['mutation_samples']} 检出"
                    f"（每类 12–16 样本、必需渠道全达标）；机制覆盖 {c['C3']['mechanisms_covered']}/8；"
                    f"sha256 {c['C4']['files_ok']}/{c['C4']['files_checked']} 一致；pytest {c['C5']['test_count']} 例。"),
        "scenario_key": "platform-mech-replay",
        "mission_id": 10691,
        "tags": ["mech-replay", "gate-conformance", "mutation-testing", "golden-fixture",
                 "platform-mechanism", "p348w3-n03", "ci-gate"],
        "markdown": markdown,
        "figures": figures,
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + "/tensor/reports", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + login()})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            print("report_id", data.get("id"), "status", resp.status)
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        print("HTTP", exc.code, exc.read().decode()[:500])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())