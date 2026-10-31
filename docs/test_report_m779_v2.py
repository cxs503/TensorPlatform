#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m845 返工报告的验收断言（D1–D7 逐条可机检）。

运行： python3 docs/test_report_m779_v2.py
前置： python3 docs/audit_seed_plan4.py && python3 docs/figures_m845.py && python3 docs/report_builder_m845.py
"""
import json
import os

WORK = "/srv/tensor/agent/work/m845"
AUDIT = os.path.join(WORK, "artifacts/audit_report_m845.json")
MD = os.path.join(WORK, "docs/report_m779_v2.md")
PAYLOAD = "/tmp/report_m845.json"

a = json.load(open(AUDIT, encoding="utf-8"))
md = open(MD, encoding="utf-8").read()
pay = json.load(open(PAYLOAD, encoding="utf-8"))
g = a["graph"]
acc = a["proposal_accounting"]

# --- 数据层：真值必须是 SQL ARRAY 实际值
deps = g["deps_by_ext"]
assert deps["W4-N07"] == ["W4-N01", "W4-N04"], deps["W4-N07"]
assert deps["W4-N06"] == ["W4-N02", "W4-N03"], deps["W4-N06"]
assert {n["ext_id"]: n["milestone_id"] for n in a["nodes"]}["W4-N07"] == 16
# --- D3：最长链 3 节点 2 步
assert g["longest_chain_len_nodes"] == 3 and g["longest_chain_len_steps"] == 2, g["longest_chain"]
assert all(x["pass"] for x in g["gates"]) and len(g["gates"]) == 10
# --- D4：账目闭合
assert acc["total"] == 16 and acc["identifiable_refs"] == 11 and len(acc["unseeded"]) == 5
assert acc["total"] - acc["identifiable_refs"] == len(acc["unseeded"])
# --- SQL↔DB
assert all(c["match"] for c in g["sql_consistency"]) and len(g["sql_consistency"]) == 8

# --- D1：独立审计节存在且含四类审计
for key in ("## ⑦ 独立审计节", "7.2 数据来源可信度审计", "7.3 聚合去重逻辑审计",
            "7.4 SQL 与报告一致性审计", "7.5 图结构事实审计", "7.6 缺陷闭环表"):
    assert key in md, key
for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
    assert d in md, d
# --- D2：修正后的依赖写进报告；旧的错误断言不得出现在「事实」位置
assert "W4-N07.dependencies = {W4-N01, W4-N04}" in md
assert "W4-N06.dependencies = {W4-N02, W4-N03}" in md
assert "W4-N07 属于 M15" in md          # 仅作为被纠正的原始错误出现在 7.5 审计表
assert md.count("W4-N07 属于 M15") == 1
# --- D3 文字
assert "3 节点 / 2 步" in md or "= 3 节点 2 步" in md
# --- D5：图件豁免
assert "图件豁免声明（D5）" in md and "不包含场量云图" in md
# --- D6：误差表含显式回答
assert "是否 <3%" in md and "相对误差 0.00%" in md and "N/A" in md
# --- D7：结论被限定，旧的「100% 无误差」撤回
assert "SQL 插入 7 项机器门校验全部通过" in md
assert "100% 通过、无误差" in md and "撤回" in md
assert "所有机器门校验项 100% 通过，无误差。\n" not in md
# --- 新增审计发现：映射错配入报告
assert "A-1.1" in md and "冰激振动频率锁定" in md

# --- payload
assert pay["mission_id"] == 845, pay["mission_id"]
assert isinstance(pay.get("markdown"), str) and len(pay["markdown"]) > 5000
assert len(pay["figures"]) == 2 and all(f.get("png_base64") for f in pay["figures"])
assert pay["markdown"].strip() == md.strip(), "payload 与 docs 报告必须逐字一致"

print("ALL ASSERTIONS PASSED: 报告 %d 字，图 %d 张，机器门 %d/%d，账目 %d=%d+%d"
      % (len(md), len(pay["figures"]), sum(1 for x in g["gates"] if x["pass"]),
         len(g["gates"]), acc["total"], acc["identifiable_refs"], len(acc["unseeded"])))