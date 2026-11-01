# 报告证据闸门 v2（GATE-2）— 交付说明

**单号** mission 1499（tp-infra-1 infrastructure 席自找活）· 报告 id **1083** · 分支 `agent/m1499`

## 1. 为什么改

`POST /api/v1/tensor/reports/qa/check` 背后的 `_check_report_quality`（v1）只做**关键词存在性**
匹配：正文里出现「物理」「方法」等词即判定该要素合格，**完全不检查** ③ 结果数字表、
④ 图件是否真存在、⑥「是否 <3%」的误差判定。后果：

1. 一段词表文本（`物理 几何 方法 验证 图 局限`）即可满分通过 → **假门禁**；
2. 真正的欠账（报告缺误差判定、图裂）反而无告警；
3. 旧测试 `tests/test_report_gate_standalone.py` **拷贝**了闸门函数再测拷贝，闸门漂移不可见。

## 2. 改了什么

| 文件 | 变更 |
|---|---|
| `backend/utils/report_gate.py` | **新增**。纯函数证据闸门（无 DB / 无 web 依赖，可离线测）。8 个 check：物理背景、方法、对标、结果数字表、图件、图件取证、局限、误差判定 `<3%`。含反刷分 `keyword_only_*`。 |
| `backend/api/tensor_reports.py` | `_check_report_quality` 改为薄委托 + 磁盘图件探针（fail-closed，取不到真 PNG 即不通过）。**字段名与 `qa_status` 落库语义不变**；schema 未改。 |
| `backend/tests/feat/test_report_gate_v2.py` | **新增** 12 例，直接 import 生产模块（不拷贝）。 |
| `artifacts/m1499/report_gate_audit.py` | 审计脚本：用 `git show HEAD:` 原文 `exec` 出 v1 做逐字对照 + 6 例变异体 + 实盘 20 篇测量。 |
| `artifacts/m1499/numbers.json` / `fig1_gate_compare.png` / `fig2_issue_hist.png` | 测量数字与图件（报告 1083 的数字全部由脚本注入，无手抄）。 |

## 3. 关键数字

- 变异集（6 例关键词/半成品刷分）：v1 通过 **83%** → v2 通过 **0%**。
- 实盘最近 20 篇已发布报告：v1 通过 17/20（85%）→ v2 通过 7/20（35%）。
- v2 逐项不通过率：方法 30%、图件 25%、图件取证 25%、局限 20%、物理背景 5%；
  结果数字表、对标、误差判定 0%（说明实盘报告在"数字/对标"上确实齐备）。
- 单测：`tests/feat/test_report_gate_v2.py` 12 passed（`--noconftest`）。

## 4. 判据边界（重要）

- 闸门只回答「**证据在不在**」，不回答「**数对不对**」——数值正确性必须由解析解/文献基准
  承担。不要把闸门当精度证明用。
- v2 显著更严（85%→35%），因此**本轮把 v2 定为 advisory**：`qa/check` 仍只写 `qa_status`，
  由质检席按 issue 清单裁决；不建议立即当发布硬门（会造成产线误停）。
- 图件取证失败 5 篇（报告 1081/1082/1063/1072/1074）源于服务与工作树不共享上传卷，
  属**取证环境限制**，已在报告 1083 中显式列出待人工确认。

## 5. 后续欠账

1. 数字溯源：要防「刻意伪造数字表」，必须把关键量做成 M-G1 schema 的程序化注入
   （报告只允许引用工件字段），闸门再校验字段存在性——本单未做。
2. `tests/test_report_gate_standalone.py`（v1 拷贝式测试）建议改写为历史行为回归或删除。
3. 图件取证需在服务侧路径下执行（或让 `FIGURE_DIR` 对审计脚本可见），否则只能记 fail-closed。