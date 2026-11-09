# waLBerla 盘点单 · 发布记录（诚实留痕）

- **权威报告**：`tensor_reports` **id 9925**（mission 8551，scenario_key=oss-inventory，含 [OSS-INV] + 13 × [OSS-SOL] + 3 图），
  正文与 `docs/oss_inventory/REPORT_walberla.md` **逐字一致**。
- **探测性残留行（本单产生，无 API 可清理，如实登记）**：
  | id | 来源 | 状态 |
  |---|---|---|
  | 9921 | 为定位 422 校验错误而做的**最小 payload 探测**（title=`tttt…`，markdown 为当时尚未修正的双前缀版本） | 作废，不得引用 |
  | 9923 | 同上，title 长度探测（40 字符） | 作废，不得引用 |
  | 9924 | 同上，title 长度探测（120 字符） | 作废，不得引用 |
- **422 根因（已定位并修正）**：`summary` 字段上限 **500 字符**；首版 summary 超限。修正后 summary 369 字符，发布成功。
- **清理尝试（均失败，已记录）**：`DELETE /tensor/reports/{id}` → 405；`PATCH` → 405；`POST /tensor/reports/{id}/retract` → 404；
  `POST /tensor/reports/{id}/status` → 404。**平台当前不提供报告撤回/删除能力** —— 建议后续在平台侧补一个 `retracted` 状态位
  （否则任何探测都会永久污染 mission 的报告列表，且守护线取"最新一条"时可能读到探测行）。
- **自检**：`walberla_pointer_check.txt`（143 条显式 + 35 条简写指针回读源码，0 失败）。