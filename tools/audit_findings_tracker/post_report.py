#!/usr/bin/env python3
"""发布 mission 16224（AUDIT-FINDINGS-TRACKER）平台进展报告。

六要素全部程序化拼装：证据表数字读自 ``artifacts/audit_findings_tracker/evidence_table.json``
（由 ``make_evidence`` 从结构化输出生成），图读自 PNG 并 base64 内嵌 —— 零手抄。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "audit_findings_tracker"
FIG = ART / "fig_audit_findings_tracker.png"
EVIDENCE = ART / "evidence_table.json"

TITLE = "m16224 AUDIT-FINDINGS-TRACKER v1：交差审计五类发现结构化+自动拦截（31 例全绿）"
SUMMARY = ("新增 Platform 共性机制 tools/audit_findings_tracker：异构审计报告 → 五类发现"
           "（fake_pass/hand_copy/label_mismatch/undefined_criteria/other）结构化台账；未识别条目"
           "降级 WITHHELD 并令退出码 1，声明计数漂移与标签冲突派生 label_mismatch 拦截；"
           "两份异构样本各自五类计数一致（差值 0），31 例 pytest 全绿，domain-scan 命中 0。")

SECTIONS = """
## ① 案例介绍

对象：交差审计（15311/15312 双审）产出的「发现」条目 —— 天然的异构文本，长期靠人抄进
台账。本单不涉及任何物理场量：**案例 = 审计发现的分类与拦截**，刻意使用合成夹具
（`node-alpha … node-delta`，且不含任何学科词条），因为机制本身必须零领域语义。

参数表（判据，来自任务书）：

| 参数 | 取值 |
|---|---|
| 五类发现闭词表 | fake_pass / hand_copy / label_mismatch / undefined_criteria / other |
| 退出码 | 0=PASS（已结构化）；1=FAIL（解析失败或发现未识别） |
| schema 注入 | `tools/audit_findings_tracker/audit_findings.v1.schema.json` |
| 测试 | pytest ≥10 例，含五类逐一反例 |

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **新独立包**（不改旧工具）：`core.py`（定位→归类→派生→判定）+ `cli.py` + `__main__.py`。
2. **容器容错定位**：按 schema 的 `finding_list_keys` 递归找发现列表；支持根列表、
   深层嵌套、显式空列表；无已知键时用「≥半数元素带分类字段/形如发现编号」的启发式，
   布局方式写进 `layout.mode` 供审计。
3. **归类不冒充强证据**：优先结构化分类字段（category/type/kind/code/tags…），缺字段才退
   文本字段并标 `match_scope="text"`；多命中记 `ambiguous=true` 与 `detail.matched`。
4. **两条派生拦截**：报告自述 `counts` ≠ 实测 → 派生 `label_mismatch`
   （`DECLARED_COUNT_MISMATCH`，防手抄汇总数）；声明类别与其它分类字段矛盾 → 派生
   `label_mismatch`（`LABEL_CONFLICT`，防标签错位）。派生条目**不覆盖**原始发现类别。
5. **缺就写缺**：无发现结构 → `NO_FINDINGS_STRUCTURE`；非对象/非字符串条目 →
   `UNSUPPORTED_ENTRY`；无法归类 → `UNRECOGNIZED_CATEGORY`；三者均降级 WITHHELD 且总判 FAIL。
6. **可溯源/确定性**：输出带报告与 schema 的 sha256；无时钟无随机，同输入逐字节同。
7. 工具链：Python 3.11 + pytest + matplotlib + `tools/evidence_domain_scan.py`（零领域语义自检）。

## ③ 计算结果（程序化注入，数字来自 evidence_table.json）

{TABLE}

## ④ 结果图

图 A：四份样本 × 五类发现计数（含派生条目）；图 B：recognized / WITHHELD 分布与
PASS/FAIL 判定（图上每个数字都由 `core.analyze_report` 现算）。

## ⑤ 注意点 / 局限

- 本工具**不替审计下结论**：只做归类+计数+拦截；一条 fake_pass 是否成立仍由证据链裁决。
- 文本兜底是弱证据（`match_scope="text"`），词表误命中风险由 `ambiguous`/`detail` 暴露，不隐藏。
- 计数不一致只报不改：派生 label_mismatch 是给上游的账，原报告不回写。
- 别名表是平台审计词汇，不含学科词条（domain-scan 命中 0）；阈值/类别全在 schema 工件里。
- 本次未接入派发/验收流水线（下一步）；CLI 契约已固定，接入属纯编排工作。

## ⑥ 误差判定（显式回答「是否 <3%」）

本机制输出计数类判据，**无连续量实验/解析基准**，「<3%」不适用于物理精度；能给的
可复算误差面是**分类一致性**：两份布局完全不同的报告（`$.findings` vs
`$.report.data.issues`，dict/string 混排）对同一批合成发现给出**逐类计数完全相同**
（每类 1 条，差值 0 = 0%），且四份样本的构造预期计数与实测逐项相等（表 ③）。
失败样本按设计 FAIL（withheld=1 → rc=1），非误判。
"""


def build_markdown() -> str:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    table = evidence["markdown_table"]
    return f"# {TITLE}\n\n{SUMMARY}\n\n{SECTIONS.replace('{TABLE}', table)}\n"


def _login(api_base: str) -> str:
    body = json.dumps({"username": os.environ.get("TENSOR_AGENT_USER", "eco_admin"),
                       "password": os.environ.get("TENSOR_AGENT_PASS", "")}).encode()
    req = urllib.request.Request(f"{api_base}/auth/login", data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    payload = {
        "title": TITLE,
        "summary": SUMMARY,
        "scenario_key": "platform-assurance",
        "mission_id": 16224,
        "tags": ["audit-findings", "anti-fake-pass", "evidence-governance", "m16224",
                 "platform-mechanism"],
        "markdown": build_markdown(),
        "figures": [{
            "name": "fig_audit_findings_tracker.png",
            "caption": "A. 四份合成审计样本的五类发现计数（含派生 label_mismatch）；"
                       "B. recognized/WITHHELD 分布与 PASS/FAIL 退出码。",
            "png_base64": base64.b64encode(FIG.read_bytes()).decode("ascii"),
        }],
    }
    out = Path("/tmp/report_m16224.json")
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    token = _login(api_base)
    req = urllib.request.Request(f"{api_base}/tensor/reports",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        result = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"post failed: {exc}\n")
        if hasattr(exc, "read"):
            sys.stderr.write(exc.read().decode(errors="replace") + "\n")
        return 1
    print(json.dumps(result, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())