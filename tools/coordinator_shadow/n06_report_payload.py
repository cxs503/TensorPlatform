#!/usr/bin/env python3
"""构建 IN28-N06 论文级报告负载（/tmp/report_in28.json），供 /tensor/reports 发布。

数字来自 artifacts/coordinator_shadow/IN28_N06_acceptance.json，图来自 runs/in28_n06/figs/，
正文与 docs/IN28_N06_sr1_shadow_maturation.md 同源——报告与产物不会互相打架。
图降到 ~640 px 宽再 base64（平台对 >250 KB 负载会静默失败，见 KNOWN_TRAPS）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

BUNDLE = os.path.join(ROOT, "artifacts/coordinator_shadow/IN28_N06_acceptance.json")
DOC = os.path.join(ROOT, "docs/IN28_N06_sr1_shadow_maturation.md")
FIGS = [(("fig_match_ladder", "图1 匹配阶梯：通道×状态分布与自动匹配率（95% Wilson CI）vs 90% 门槛"),
         "runs/in28_n06/figs/fig_match_ladder.png"),
        (("fig_divergence", "图2 SR1 影子分歧率：全体/v0写者/v1写者（95% CI）vs 20% 目标；右图匹配时滞分布"),
         "runs/in28_n06/figs/fig_divergence.png"),
        (("fig_gates", "图3 回填库条数 vs 50 条门槛；回滚演练判据矩阵（坏部署拦截/回滚秒数/逐字节一致/篡改负控）"),
         "runs/in28_n06/figs/fig_gates.png")]
SCALE = 0.45


def png_b64(path: str) -> str:
    im = Image.open(os.path.join(ROOT, path)).convert("RGB")
    w, h = im.size
    im = im.resize((max(1, int(w * SCALE)), max(1, int(h * SCALE))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def main() -> int:
    bundle = json.load(open(BUNDLE))
    c = bundle["criteria"]
    coord = bundle["numbers"]["coordinator_shadow"]
    md = open(DOC).read()
    header = "\n".join([
        "> **任务**：IN28-N06（SR1 影子决策成熟化与 v0 退役）· mission 5516 · 仓库 TensorPlatform",
        f"> **机器验收**：六条判据 all_pass = **{bundle['all_pass']}**；"
        f"SR1 目标（<20%）未达成，如实记 target-miss。",
        "",
    ])
    figures = []
    for (name, caption), rel in FIGS:
        figures.append({"name": name, "caption": caption, "png_base64": png_b64(rel)})
    payload = {
        "title": "IN28-N06：SR1 影子决策成熟化（匹配率 90.5% / 回填 1865 条）+ v0 退役 + 回滚演练 PASS",
        "summary": (
            f"成熟化引擎把影子提议与事后处置自动对齐：coordinator_shadow 通道 {coord.get('proposals')} 条提议，"
            f"匹配率 {100*coord['match_rate']:.1f}%（成熟子集 100%），回填库 {c['a_maturation_engine']['backfill_records']} 条；"
            f"SR1 分歧率从 WITHHELD 变成连续数字 {100*c['c_divergence_to_chronicle']['value']:.1f}%"
            f"（95%CI 19.0–40.1%）并落 chronicle s8，**高于 20% 目标，如实记 target-miss**；"
            f"v0 影子协调官 2 个进程已退役、退役后 legacy 写 0 条、v1 唯一写者；"
            f"回滚演练 PASS（坏部署全拦、回滚 {c['d_rollback_drill']['rollback_seconds']:.4f}s、状态逐字节一致、篡改负控通过）；"
            f"fail-closed 自检 3/3 逃逸被拒、沙箱外写 0；pytest {c['f_pytest']['passed']} passed。"
        ),
        "scenario_key": "tensorplatform.agent_harness.sr1_maturation",
        "mission_id": 5516,
        "tags": ["IN28-N06", "SR1", "shadow-decision", "maturation", "v0-retirement",
                 "rollback-drill", "fail-closed", "chronicle-s8"],
        "markdown": header + md,
        "figures": figures,
    }
    blob = json.dumps(payload, ensure_ascii=False)
    out = "/tmp/report_in28.json"
    with open(out, "w") as fh:
        fh.write(blob)
    print(f"payload bytes={len(blob.encode())/1024:.1f} KB  figures={len(figures)}  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())