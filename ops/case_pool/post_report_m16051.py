#!/usr/bin/env python3
"""把 pool-expand-r1 报告发到 TensorPlatform /tensor/reports（六要素，图 base64 内嵌）。"""
import base64
import json
import os
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
API = os.environ["TENSOR_API_BASE"].rstrip("/")
USER, PASS = os.environ["TENSOR_AGENT_USER"], os.environ["TENSOR_AGENT_PASS"]
ART = os.path.join(ROOT, "artifacts", "m16051")


def j(n):
    return json.load(open(os.path.join(ART, n), encoding="utf-8"))


v, ref, ded = j("verify_report.json"), j("ref_verify_final.json"), j("pool_expand_r1_dedup.json")
md = open(os.path.join(ROOT, "docs", "case_pool", "POOL_EXPAND_R1.md"), encoding="utf-8").read()
ledger = open(os.path.join(ROOT, "ops", "case_pool", "REF_LEDGER_r1.md"), encoding="utf-8").read()
md = md + "\n\n---\n\n" + ledger

figs = []
for name, cap in [("pool_composition_before_after.png",
                   "图1 tensor_case_pool 六仓组成：扩容前(浅色)/扩容后(实色)，pending 新增 42 例，可供给队列 0→42"),
                  ("ref_verdict_and_tol.png",
                   "图2 左：42 例引文 Crossref 核验等级分布（28 本体 DOI 命中/7 次级命中/5 专著书评佐证/2 前 DOI 专著）；右：tol_policy 上界分布")]:
    p = os.path.join(ROOT, "docs", "case_pool", "figs", name)
    figs.append({"name": name, "caption": cap,
                 "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

payload = {
    "title": "算例池供给线修复 r1：tensor_case_pool +42 例（六仓轮转，引文 DOI 级核验）",
    "summary": "断供根因=19 例 pending 全部被锚占用；本批以六仓各 7 例共 42 例重建供给，"
               "可供给 pending 0→%d；42 例引文经 Crossref 程序化核验（28 本体 DOI/7 次级/5 专著佐证/2 教科书），"
               "四道去重闸 0 硬冲突，锚点对拍 0 精确覆盖故全部留空。" % v["pending_supplyable_no_anchor"],
    "scenario_key": "case_pool_expand",
    "mission_id": 16051,
    "tags": ["case-pool", "FEAT-CASEPOOL-100", "supply-line", "crossref-verified", "six-repo-rotation"],
    "markdown": md,
    "figures": figs,
}
body = json.dumps(payload).encode()
req = urllib.request.Request(API + "/auth/login",
                            data=json.dumps({"username": USER, "password": PASS}).encode(),
                            headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req, timeout=60).read())["access_token"]
req2 = urllib.request.Request(API + "/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": "Bearer " + token})
with urllib.request.urlopen(req2, timeout=120) as r:
    print(r.status, r.read().decode()[:400])
