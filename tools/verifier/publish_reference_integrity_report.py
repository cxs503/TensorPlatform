#!/usr/bin/env python3
"""Publish the mission-1526 progress report (numbers read from the artifact)."""
from __future__ import annotations

import base64
import json
import os
import sys

import httpx

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8001/api/v1").rstrip("/")
ART = "artifacts/verifier_reference_integrity"
FIG = os.path.join(ART, "figures", "reference_integrity.png")

summary = json.load(open(os.path.join(ART, "figures", "summary.json"), encoding="utf-8"))
audit = json.load(open(os.path.join(ART, "reference_integrity_audit.json"), encoding="utf-8"))

mig_rows = "\n".join(
    f"| {m['title']} | {m['old']['1+k']:.5f} | {m['new']['1+k']:.5f} | "
    f"{(m['new']['1+k']-m['old']['1+k'])/m['old']['1+k']*100:+.1f}% | {m['old_version']}→{m['new_version']} |"
    for m in summary["migrated"])
mut_rows = "\n".join(
    f"| {r['candidate']} | {r['expected']} | {r['verdict']} | {r['pass']}/{r['fail']} |"
    for r in summary["mutation"]["rows"])
crit_rows = "\n".join(
    f"| {c['criterion']} | {('PASS' if c['passed'] else 'FAIL')} | {c['detail'][:96]} |"
    for c in audit["after"]["criteria"] if c["criterion"].startswith("C1")
    or c["criterion"].startswith("C2") or c["criterion"].startswith("C3")
    or c["criterion"].startswith("C4"))

md = f"""## 1. 案例介绍

**对象**：平台独立复核仪表 `tools/verifier/`（hold-out 题库 + 判决引擎 + 信用账本）。
平台口径明确规定「自洽测试不计入」复核通过率，因为参考值若由被测代码同源产生，复核即循环论证。

**审计发现的缺陷**：题库 15 题中 12 题（ITTC-1957 / Blasius / Prandtl-Schlichting）标准值确由已发表闭式给出；
但 3 题 Holtrop-Mennen 形体系数题的标准值由生成器私有表达式合成，而 pilot 候选实现的正是同一表达式 —— 循环验证。
该私有式为

```
1+k = 0.93 + 0.1 * 0.487118 * Cb^(-0.9249) * (L/B)^0.15 * (B/T)^0.2
```

对照 Holtrop & Mennen (1982, ISP 29(335) 166-170) 原文，它缺少 run 长度项 `(L/L_R)`、体积项 `(L^3/∇)`、
棱形项 `(1-Cp)` 与艉形系数 `c14`，并把 `(B/L)^1.06806` 写成符号相反的 `(L/B)^0.15`。函数形式不同，因此既不具名义精度，也不具名义趋势。

**算例矩阵（3 型船）**

| 船型 | C_B | L [m] | B [m] | T [m] | C_m | lcb/L |
|---|---|---|---|---|---|---|
| Series 60 | 0.60 | 100 | 15.0 | 6.0 | 0.980 | +0.010 |
| Bulk carrier | 0.70 | 150 | 22.0 | 8.5 | 0.985 | +0.010 |
| Tanker | 0.80 | 200 | 32.0 | 11.0 | 0.990 | +0.010 |

`C_m`/`lcb`/`C_stern` 在原回归中是输入量而非回归量，本轮已显式写入题目规格（声明的工程代表值）。

## 2. 怎么做的

1. **独立参考实现** `tools/verifier/reference/holtrop_mennen_1982.py`：按原文逐项转写
   `1+k = 0.93 + 0.487118·c14·(B/L)^1.06806·(T/L)^0.46106·(L/L_R)^0.121563·(L³/∇)^0.36486·(1−Cp)^(−0.604247)`，
   `L_R = L(1−Cp+0.06Cp·lcb/(4Cp−1))`，`Cp = Cb/Cm`，`c14 = 1+0.011·C_stern`；
   另给**第二条代数独立装配路径**（对数空间求和），两路互验。
2. **判据模块** `tools/verifier/reference/integrity.py`：C1 表达式指纹、C2 物理单调性（1+k 必须随 C_B 上升）、
   C3 量级窗口 [1.10, 1.50]、C4 趋势锚（单参数相关式，相对门限 0.50）、C5 独立重算（相对门限 1e-9）。
3. **迁移工具** `tools/verifier/reverify_question_bank.py`：审计 → 原位改写不可重算的题（保留 id/created_at，version+1，重算 commit_hash）→ 复审，输出前后对照工件。
4. **故障注入** `tools/verifier/tests/test_reference_integrity.py`：用变异候选证明仪表能抓住 MOCK，而不是自证清白。
5. 同步修正 e2e pilot 候选，避免测试自身继续使用退役表达式。

## 3. 计算结果

**参考值迁移（3 题，build 自工件 `reference_integrity_audit.json`）**

| 题 | 1+k（旧，循环） | 1+k（新，发表式） | 相对变化 | version |
|---|---|---|---|---|
{mig_rows}

**迁移后形体系数判据（C1–C4）**

| 判据 | 判决 | 证据 |
|---|---|---|
{crit_rows}

12 题闭式题独立重算最大相对偏差 **{summary['re_derived_max_rel_dev']:.1e}**（门限 1e-9），即闭式题的标准值确属可复现。

**故障注入矩阵（仪表判别力）**

| 候选 | 期望 | 实测判决 | 通过/失败题数 |
|---|---|---|---|
{mut_rows}

检出率 **{summary['mutation']['detection_rate']*100:.0f}%**，特异度 **{summary['mutation']['specificity']*100:.0f}%**。

## 4. 结果图

三面板：(a) 1+k 随 C_B 的变化 —— 发表式单调上升、退役式单调下降，趋势符号相反；
(b) 15 题银行值 vs 独立重算的相对偏差，(c) 故障注入判决矩阵。

## 5. 注意点与局限

* **无独立数值表锚定**：离线环境不能引用 Holtrop 论文试验表值，故形体系数的*精度*对文献只做量级/趋势锚定；
  C4 的 band 按判别力标定（发表式 31–34%、退役式 78–94%、门限 50%），不按精度标定。文献表值锚定标 **WITHHELD**。
* C2/C3/C4 只对同族船（固定 L/B、B/T 带）成立；跨族比较需扩展矩阵。
* 未改动 verifier 的比较语义（相对偏差 + 逐题 tolerance）。比较层已知弱点（expected=0 时退化为绝对偏差、
  bool 与数值混比、缺少 key 直接 inf 但无显式诊断）留待后续单。
* 迁移是原位改写，history 通过 version 字段与本次工件留痕，不回滚旧值。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 12 题闭式参考值 | 发表闭式独立重算 | ≤ {summary['re_derived_max_rel_dev']:.1e} | 是 |
| 3 题形体系数参考值 | Holtrop-Mennen 1982 发表式 | 对发表式为精确重算；对趋势锚 ≤34.3% | 对发表式：是；对实验：**WITHHELD** |
| 退役表达式 vs 发表式 | — | 差 0.5%–31%（且趋势反向） | 否（故退役） |
| 仪表检出率 | 故障注入矩阵 8 候选 | {summary['mutation']['detection_rate']*100:.0f}% | 是 |

**是否 <3%**：闭式量是（≤1e-17，机器精确）；形体系数对**实验**的 <3% 本轮**不判定**，
原因是离线无可引用的文献数值表，按规则标 WITHHELD 而非含糊通过。诚实结论：本轮清偿的是
「参考值来源是否独立」这一更上游的欠账，不是「形体系数物理精度」。

## 7. 工件指针

* commit `7467ceb`（branch `agent/m1526`）
* 审计工件 `artifacts/verifier_reference_integrity/reference_integrity_audit.json`
* 摘要 + 图 `artifacts/verifier_reference_integrity/figures/`
* 证据页 `docs/verifier/REFERENCE_INTEGRITY.md`
* 测试 `python3 -m pytest tools/verifier/tests/ -q` → **34 passed**
"""

payload = {
    "title": "验证仪表自身计量：Holtrop-Mennen 参考值循环验证欠账清偿（题库 3 题退役私有合成式）",
    "summary": (
        "审计发现题库 3 题 Holtrop-Mennen 形体系数的标准值由生成器私有表达式合成，"
        "而 pilot 候选实现同一表达式 —— 复核是循环的。新增独立参考实现（原文逐项转写 + 双路装配互验）、"
        "5 项机器可判判据（表达式指纹/物理单调性/量级窗口/趋势锚/独立重算）、题库迁移工具与故障注入测试。"
        "迁移后 1+k 由 1.0547/1.0393/1.0276 修正为 1.16456/1.19845/1.29510（趋势符号纠正），"
        "15 题全部可独立重算；仪表对 8 个变异候选检出率 100%，0.2% 微偏仍通过。34 项测试全绿。"
        "形体系数对实验的精度标 WITHHELD（离线无文献数值表）。"
    ),
    "scenario_key": "verification",
    "mission_id": 1526,
    "tags": ["verifier", "reference-integrity", "holtrop-mennen", "fault-injection",
             "verification-debt", "circular-verification", "m1526"],
    "markdown": md,
    "figures": [{
        "name": "reference_integrity.png",
        "caption": "(a) 1+k vs C_B：发表式单调上升、退役私有式单调下降，趋势符号相反；"
                   "(b) 15 题银行值 vs 独立重算相对偏差（门限 1e-9）；(c) 故障注入判决矩阵。",
        "png_base64": base64.b64encode(open(FIG, "rb").read()).decode(),
    }],
}


def main() -> int:
    with httpx.Client(timeout=120.0) as c:
        r = c.post(f"{BASE}/auth/login", json={
            "username": os.environ["TENSOR_AGENT_USER"],
            "password": os.environ["TENSOR_AGENT_PASS"],
        })
        r.raise_for_status()
        tok = r.json().get("access_token") or r.json().get("token")
        r = c.post(f"{BASE}/tensor/reports", json=payload,
                   headers={"Authorization": f"Bearer {tok}"})
        print(r.status_code, r.text[:600])
        return 0 if r.status_code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())