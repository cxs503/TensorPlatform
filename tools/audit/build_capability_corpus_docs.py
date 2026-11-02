#!/usr/bin/env python3
"""把仓内「已合并复核文档」注入能力证据语料库（corpus v2 的 docs 段）。

为什么需要
----------
v1 台账只吃在线报告（:8010 /tensor/reports）。后果是：**已合并的复核工作对台账不可见**——
m1564 稳性受证合成、m1584 敞水复核、m1590 结构欠账、m1493 振动噪声域都已合并进仓，
但台账对 `calc.stability` 仍记 MOCK，于是"证据等级"这一对外数字永久停在旧世界上。

做法（全程程序化，零手抄）
--------------------------
* `sha256`：直接读文件字节算；文档被后续单改写 → C10 立刻破闸，强制重新 curation；
* `as_of`：取该文件最后一次 git 提交的 committer 时间（`git log -1 --format=%cI`），
  不用文档正文里的手写日期（手写日期无法机器核验）；
* `blob`：`git rev-parse HEAD:<path>`，供逐字复现引文来源;
* 幂等：重复运行输出相同（除 git 时间本身变化）。

用法
----
    python3 tools/audit/build_capability_corpus_docs.py            # 写回 corpus
    python3 tools/audit/build_capability_corpus_docs.py --check     # 只报告是否已同步
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "capability_ledger" / "reports_corpus.json"
ID_PREFIX = "doc:"

# 入账清单：只收「已合并、且被台账引用来裁决某个能力等级」的复核文档。
# 新增文档必须同时给出 why（这张文档裁决了哪条能力），否则不得进语料库。
MANIFEST = [
    {
        "path": "docs/verification/PROPULSION_OPENWATER_M1584.md",
        "title": "推进域 open-water（Wageningen B 系列）证据复核（m1584）",
        "why": "重裁 propeller.open_water：证据门 2/8，端点 eta0 越过动量理论上限（不依赖文献数值的硬判据）",
    },
    {
        "path": "docs/reports/m1564_stability_adjudication.md",
        "title": "稳性 GZ 平行实现裁决（m1564）：受证合成，服务层为正典",
        "why": "重裁 calc.stability：域内 vs 箱型驳船精确解误差 ≤1e-3 %，域外由解析有效域截断",
    },
    {
        "path": "docs/verification/inline_resistance_audit_2026-11-01.md",
        "title": "inline 阻力端点验证欠账清偿（解析回转体 + 恒等式三重裁决）",
        "why": "重裁 calc.resistance：FAIL 8 / PASS 1 / WITHHELD 1，绝对精度仍不可判据",
    },
    {
        "path": "docs/reports/m1590_structure_verification_debt.md",
        "title": "结构域验证欠账清偿 v1（板格屈曲 / 疲劳 S-N / 总强度）",
        "why": "覆盖缺口：结构域端点在跑而注册表 21 项无结构条目",
    },
    {
        "path": "docs/audits/vibration_noise_domain_audit.md",
        "title": "振动噪声域证据分级审计与参考能力补全（m1493）",
        "why": "覆盖缺口：振动噪声域在仓内已实现并解析复核，但注册表无条目、在线仍 NOT_SUPPORTED",
    },
]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def build() -> dict:
    docs = {}
    for item in MANIFEST:
        path = ROOT / item["path"]
        if not path.exists():
            raise SystemExit(f"清单里的文档不存在: {item['path']}")
        markdown = path.read_text(encoding="utf-8")
        docs[ID_PREFIX + item["path"]] = {
            "kind": "doc",
            "path": item["path"],
            "title": item["title"],
            "why": item["why"],
            "as_of": _git("log", "-1", "--format=%cI", "--", item["path"]),
            "blob": _git("rev-parse", f"HEAD:{item['path']}"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "markdown": markdown,
        }
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验 corpus 是否与仓内文档同步")
    args = ap.parse_args(argv)

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    docs = build()
    synced = corpus.get("docs") == docs
    if args.check:
        print("SYNCED" if synced else "STALE")
        return 0 if synced else 1

    corpus["docs"] = docs
    corpus["schema_version"] = "2.0"
    corpus["docs_manifest"] = [
        {k: d[k] for k in ("path", "title", "why", "as_of", "blob", "sha256")} for d in docs.values()
    ]
    CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"corpus docs: {len(docs)} 篇，已写入 {CORPUS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())