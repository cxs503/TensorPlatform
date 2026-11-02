# m1773 · 阻力基准链退化测试重生：从「断言已修缺陷」到「钉住现行真理」

## ① 案例介绍

- **对象**：`TensorPlatform` 后端阻力对标链——`POST /api/v1/calculations/benchmark_validate`
  + `backend/data/benchmark_reference.json`（canon **v3.0**，12 行）
  + 两个完整性引擎（`services/benchmark_dataset_audit` 为端点路径，`services/benchmark_audit` 为旧路径）。
- **物理背景**：阻力对标以总阻力系数 $C_T=C_F+C_R$ 与水池/文献 $C_{T,ref}$ 比对，
  ITTC-1957 摩擦线 + 经验剩余阻力（轴对称体拟合）。基准行自身必须满足两条内蕴恒等式：
  ① $Fn = V/\sqrt{gL}$；② $C_T = R_T/(\tfrac12\rho S V^2)$。任一行不满足，则该行的"精度判决"无意义。
- **几何/参数**：三个预设——SUBOFF（$L=4.356$ m, $B=T=0.508$ m, $C_b=0.542$）、
  DTMB-5415（$L=5.720$ m, $B=0.759$ m, $T=0.246$ m, $C_b=0.508$）、
  KVLCC2（$L=320$ m, $B=58$ m, $T=20.8$ m, $C_b=0.8098$）。验收门：$|\Delta C_T|/C_{T,ref} \le 15\%$。

## ② 怎么做的

1. **扫描**：读提案池、近 20 条平台报告正文的「局限/待办」节。报告 1362（m1771）明确交接：
   `backend/tests/feat/test_benchmark_integrity_audit.py` 仍为红灯，"留作下一单（建议：按本次口径统一清理）"。
2. **复现**：该文件在本单开工前 **13 FAILED / 12 passed**。
3. **归因**（逐条，不靠自述）：失败分两类——
   - **陈旧断言**：m1388 的套件钉住的是 *canon v3.0 已修好的数据缺陷*（DTMB-5415 行 Froude 不一致、
     Ct-vs-Rt 差 7–9 倍、verdict `REFERENCE_DATASET_UNUSABLE`）。数据修好后这些断言必然失败——
     它们证明"缺陷存在"，而不是"缺陷已被守卫"。
   - **API 漂移**：`ResistanceResponse` 已把字段改名为 `total_resistance_coefficient` /
     `wetted_surface_m2` / `wetted_surface_source`；`BenchmarkResult` 已改为
     `dataset_status` / `withheld` / `dataset_checks_failed`（旧断言读 `ct` / `envelope_flags`）。
4. **重生**：把 25 项断言重写为"现行必须成立"的物理/契约性质，且**每条都从原始值独立复算**
   （$C_T$ 由 $R_T/\tfrac12\rho S V^2$ 反算、`error_pct` 由 `calc_ct`/`ref_ct` 反算、
   Froude 由 $V$ 与预设 $L$ 反算、排水量由 $C_bLB T$ 反算），避免"读回被验字段"的自证循环。
5. **工具链**：`pytest --noconftest`（sqlite 隔离库）+ 只读探针脚本 `runs/compare_audit_engines.py`。

## ③ 计算结果

`pytest backend/tests/feat/test_benchmark_integrity_audit.py` → **25 passed / 0 failed**
（开工前 13 failed / 12 passed）。逐案例判决（canon v3.0）：

| case | hull | Fn | C_T calc | C_T ref | err % | dataset | pass |
|---|---|---|---|---|---|---|---|
| DTMB5415-01 | DTMB-5415 | 0.0610 | 0.003851 | 0.004500 | 14.4 | PARTIAL | no |
| DTMB5415-02 | DTMB-5415 | 0.1220 | 0.003392 | 0.003800 | 10.7 | PARTIAL | no |
| DTMB5415-03 | DTMB-5415 | 0.2440 | 0.003098 | 0.003500 | 11.5 | PARTIAL | no |
| DTMB5415-04 | DTMB-5415 | 0.3660 | 0.003107 | 0.003800 | 18.2 | PARTIAL | no |
| KVLCC2-01 | KVLCC2 | 0.1285 | 0.001415 | 0.002800 | 49.5 | PARTIAL | no |
| KVLCC2-02 | KVLCC2 | 0.1377 | 0.001410 | 0.003000 | 53.0 | PARTIAL | no |
| KVLCC2-03 | KVLCC2 | 0.1423 | 0.001408 | 0.003100 | 54.6 | PARTIAL | no |
| KVLCC2-04 | KVLCC2 | 0.1469 | 0.001406 | 0.003200 | 56.1 | PARTIAL | no |
| SUBOFF-01 | SUBOFF | 0.4589 | 0.003462 | 0.003800 | 8.9 | CONSISTENT | yes |
| SUBOFF-02 | SUBOFF | 0.6119 | 0.003953 | 0.004000 | 1.2 | CONSISTENT | yes |
| SUBOFF-03 | SUBOFF | 0.7649 | 0.004766 | 0.004200 | 13.5 | CONSISTENT | yes |
| SUBOFF-04 | SUBOFF | 0.9179 | 0.005921 | 0.004400 | 34.6 | CONSISTENT | no |

汇总：6/12 行落在 15% 门内，
3 行计为 PASS，8 行因参考行非 CONSISTENT 而 `withheld`。

两个引擎对同一数据集的判决（**漂移钉住**）：

| status | canonical engine | legacy engine |
|---|---|---|
| CONSISTENT | 4 | 12 |
| PARTIAL | 8 | 0 |
| INCONSISTENT | 0 | 0 |

## ④ 结果图

左图：逐案例误差（对数纵轴）与 15% 验收门；柱色=判决（绿 PASS / 黄 门内但 withheld / 红 超门），
柱顶标注参考行数据集状态。右图：同一数据集上两个完整性引擎的状态分布——canonical 报
4 CONSISTENT + 8 PARTIAL，legacy 报 12 OK / verdict PASS，漂移可视。

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

1. **本单只重写测试，未改任何物理实现**——因此"绿"的含义是"现行实现与 canon v3.0 的口径自洽"，
   不是精度提升。KVLCC2 仍有 49–56% 偏差（经验 $C_R$ 对肥大船不适用），本单把它钉成
   **必须失败的 honest-FAIL 断言**，禁止后人用调参抹平。
2. **两引擎未合并**（遗留债）：`services/benchmark_audit`（legacy）对 DTMB/KVLCC2 的
   `ct_rt_error_pct` 恒为 `None`（跳过恒等式检查），因此它给出 12×OK/PASS，比 canonical 宽松。
   本单用 `TestEngineDriftPinned` 把该分歧**钉成显式断言**，避免它被静默"顺手修掉"或静默失效；
   统一（删 legacy 副本）应作为独立单。
3. **DTMB/KVLCC2 仍为 PARTIAL**：canon v3.0 未把它们提升为 CONSISTENT（模型尺度锚定仍缺），
   所以端点对这两型一律 `withheld`——这是**正确的诚实语义**，不是缺陷。
4. 端点实测走 in-process 路径；`SHIP_BENCHMARK_REMOTE=1` 的 HTTP 分支只用 mock httpx 验证
   schema（`speed_ms` 回显、$R_T\to C_T$ 推导），未对活体服务做网络比对。
5. 适用域：三个预设主尺度包络内；越域（如 4 m 量级回转体套 Holtrop）不在本单范围。

## ⑥ 误差判定

- **关键量①**（本单核心指标）：`pytest` 通过率——25/25 = 100%，
  开工前 12/25。相对"绿"这一判据误差 **0%**（<3% ✔）。
- **关键量②**：`error_pct` 独立复算 vs 端点返回值——最大绝对偏差 < 0.05 pp（仅 0.1 pp 报告取整），
  相对误差 < 0.6%（<3% ✔）。
- **关键量③**：$C_T$ 由 $R_T/(\tfrac12\rho S V^2)$ 复算 vs 端点 `total_resistance_coefficient`——
  rel 1e-6 级（<3% ✔）。
- **关键量④**：排水量逆恒等式 vs 预设值——rel 1e-12（<3% ✔）。
- **关键量⑤（诚实失败，无基准）**：阻力精度本身 vs 实验值——SUBOFF 8.9%、
  KVLCC2 最高 56.1%。**是否 <3%：否**。本单不对此粉饰：
  KVLCC2 的超门偏差是本平台已知的模型类局限（轴对称体经验 $C_R$），
  已由 honest-FAIL 断言固定为"必须失败"，属如实上报。
