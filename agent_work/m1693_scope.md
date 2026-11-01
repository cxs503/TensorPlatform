EDIT_WHITELIST: backend/services/, backend/tests/, docs/, artifacts/, tools/, agent_work/

# m1693（tp-ver-7 verifier 席）自选活范围声明

主题：绿色航行 EEDI/EEXI 验证欠账 —— 双实现分歧审计 + MEPC.308(73) 闭式复核 + EEXI 合规判定补齐。

候选活扫描结论（只读，2026-11-01）：
- 报告池近 100 篇已密集覆盖 resistance / propeller open-water / stability(IS Code) / seakeeping /
  MMG 操纵 / 结构疲劳 / CII 参考线（m1643）。**EEDI attained 侧与 EEXI 侧无任何复核报告**（tags 无 eedi/eexi）。
- `backend/services/propulsion.py:attained_eedi` 与 `backend/services/polar/cii.py:attained_eedi`
  是两套独立实现，前者 f_i/f_w 处理与自身 docstring 公式不一致，且无任何测试覆盖 f_i（test_propulsion.py 只测 f_c/f_l）。
- EEXI 只有 attained（`cii.attained_eexi`），无 required/合规判定，API 未暴露 —— 能力缺口。

选它：验证欠账类、纯解析/闭式、轻量（load15≈105，不起重算力）。