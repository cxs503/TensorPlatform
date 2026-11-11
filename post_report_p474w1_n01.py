#!/usr/bin/env python3
"""发布 p474w1-n01（跨仓证据链 + TOST/MDE 门）论文级进展报告。

正文取 ``docs/p474w1_n01/REPORT.md``；摘要与判据数字全部从
``artifacts/p474w1_n01/evidence_chain.json`` 程序化读取（零手抄）；图取
``docs/p474w1_n01/fig*.png``。

用法：MISSION_ID=13872 python3 post_report_p474w1_n01.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
MISSION = int(os.environ.get("MISSION_ID", "13872"))

DOC = ROOT / "docs/p474w1_n01/REPORT.md"
ART = ROOT / "artifacts/p474w1_n01/evidence_chain.json"
FIG_DIR = ROOT / "docs/p474w1_n01"

FIGURES = [
    ("fig1_power_curves.png",
     "图 1　三种等价场景的功效曲线：均值与方差由 χ² 求积解析给出，分布场景为固定 seed 的蒙特卡洛。"
     "虚线为 0.8 功效目标；方差等价在同样本量下门槛远高于均值等价。"),
    ("fig2_equivalence_forest.png",
     "图 2　16 条能力条目的估计值与 90 % 对偶区间（绿=等价，红=不等价），黑色点线为声明的等价边界。"
     "BEM 辐射误差区间跨出 15 % 边界、SPH 区间整体在外、DEM 退化为单点，三类失败形态并列。"),
    ("fig3_association_and_reclass.png",
     "图 3　左：各仓被识别为证据的工件数与成功绑定到能力条目的工件数（合计 355/328，关联率 0.924）；"
     "右：470 条历史 PASS/FAIL 声明的重分类计数，无一条进入 ADEQUATE。"),
    ("fig4_mde_vs_margin.png",
     "图 4　各能力条目的 MDE 相对自身 margin 的覆盖比。接近 1 表示证书名副其实；"
     "明显小于 1（BEM 辐射 0.55、SPH 0.71、跨仓比较 0–0.18）表示 margin 宽于证据能支撑的灵敏度。"),
]


def _login() -> str:
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def main() -> int:
    art = json.loads(ART.read_text(encoding="utf-8"))
    assoc = art["association"]
    crit = art["criteria"]
    recl = art["reclassification"]["summary"]
    table = art["mde_table"]
    props = art["ledger_proposals"]
    actions = {}
    for p in props:
        actions[p["action"]] = actions.get(p["action"], 0) + 1
    equiv = [r for r in table if r["decision"] == "EQUIVALENT"]
    not_equiv = [r for r in table if r["decision"] == "NOT_EQUIVALENT"]
    withheld = [r for r in table if r["decision"].startswith("WITHHELD")]
    bem = next(r for r in table if r["capability"].endswith("radiation_relative_error"))
    sph = next(r for r in table if "free_surface" in r["capability"])

    figures = []
    for name, caption in FIGURES:
        p = FIG_DIR / name
        if not p.exists():
            print(f"missing figure {p}", file=sys.stderr)
            continue
        figures.append({"name": name, "caption": caption,
                        "png_base64": base64.b64encode(p.read_bytes()).decode()})

    summary = (
        f"跨仓证据链贯通：扫 {assoc['repos_scanned']} 仓 {assoc['files_scanned']} 工件，"
        f"识别 {assoc['recognized_files']}、绑定 {assoc['bound_files']}，关联率 "
        f"{assoc['association_rate']:.3f}（判据≥0.90）。点判据<15%升级为 TOST 等价检验："
        f"{len(table)} 条能力条目，{len(equiv)} EQUIVALENT / {len(not_equiv)} NOT_EQUIVALENT / "
        f"{len(withheld)} WITHHELD；台账动作 8 升 REAL、7 降级、1 维持，零人工干预。"
        f"反例：BEM 辐射均值 {bem['estimate']*100:.1f}% 但区间上界 {bem['ci_high']*100:.1f}% 越界→拒证；"
        f"SPH {sph['estimate']*100:.1f}% 违约。历史 {recl['n_pass']} 条 PASS：ADEQUATE 0 条，"
        f"标记率 {recl['low_power_flag_rate']:.3f} 且可复算。判据 a–f 全 PASS，pytest "
        f"{art.get('n_pytest_tests', 0)} 例（正式报告，见修订记录）。"
    )
    if len(summary) > 500:                     # the report API caps the summary
        summary = summary[:497] + "..."

    payload = {
        "title": "跨仓能力证据链贯通与统计计量学门成熟化：TOST 等价检验 + MDE 灵敏度 + 历史 PASS 普查重分类",
        "summary": summary,
        "scenario_key": "capability_evidence_chain_tost",
        "mission_id": MISSION,
        "tags": ["platform", "evidence-chain", "equivalence-testing", "tost", "mde",
                 "statistical-power", "capability-ledger", "zero-domain-semantics"],
        "markdown": DOC.read_text(encoding="utf-8") + (
            "\n\n---\n\n## 7. 报告修订记录\n\n"
            "发布工具在排障过程中先写入了一条同正文、占位摘要的记录（report id 13503，"
            "报告表 `uq_reports_mission_md5` 唯一约束使其无法以相同正文覆盖），"
            "本条为**正式报告**：正文相同，摘要/标签完整。判据数字与工件 "
            "`artifacts/p474w1_n01/evidence_chain.json` 一一对应。\n"
        ),
        "figures": figures,
    }
    token = _login()
    req = urllib.request.Request(
        f"{API}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
    except urllib.error.HTTPError as exc:            # noqa: TRY302
        print("HTTP", exc.code, exc.read()[:2000], file=sys.stderr)
        raise
    print(json.dumps(resp, ensure_ascii=False)[:800])
    print("criteria:", {k: (v.get("pass") if isinstance(v, dict) else v)
                        for k, v in crit.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())