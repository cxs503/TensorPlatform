# 基准参考数据运行时闭环：失效值下线 + 首原理闸门 + 消费端契约修复

mission 1577 · platform 席 · 2026-11-01

## 问题
`backend/data/benchmark_reference.json` v1.0 被四张分支用作「误差 ≤15%」的判据，但该文件本身从未
被首原理校验；且唯一消费端 `POST /api/v1/calculations/benchmark_validate` 的链路是死的：

| 缺陷 | 证据 | 后果 |
|---|---|---|
| SUBOFF `experimental_rt_n`=12.5/21.0/31.5/45.0 N 与同行 `experimental_ct` 互斥 | 反解湿面积 0.73 m² vs 行内 5.42 m²（差 7.4×） | 任何 Rt 比对都基于错误参考值 |
| DTMB-5415 四行 `froude_number` 隐含船长 181.2 m，`geometry.length_m`=5.72 m | V/√(g·5.72) → Fn 0.343/0.687/1.374/2.060（后两者物理不可能） | 整族不可用于精度声明 |
| 无 `rho`/介质溯源 | 95.0 N 恰为海水 1025 复算值（+2.68%） | 纯恒等式闸门抓不到张冠李戴 |
| `_call_resistance_api` 走 HTTP :8002，payload 键 `speed` ≠ 契约 `speed_ms`，响应无 `ct` | 实测 `calc_ct` 恒 0.0 → error 100% | 端点恒 FAIL，验证证据为零 |

## 修法
1. `tools/validation/canonicalize_benchmark_reference.py` —— 确定性重建 v3.0：SUBOFF 四行由
   `Rt=Ct·½ρSV²`（ρ=998.2，淡水，ITTC 表）复算，状态 `DERIVED`；无验证湿面积的 8 行 `rt=null` +
   `WITHHELD`（不编数字）；v1.0 旧值全部保留在 `legacy_values`（可审计，不删除）。`--check` 判文件是否漂移。
2. `backend/services/benchmark_integrity.py` —— 六条首原理规则：R1 恒等式(1%)、R1b 缺湿面积禁止给数、
   R2 介质/密度/出处、R3 Froude(2%)、R5 `Ct ≥ Cf_ITTC57(Re)`、R6 状态与数值一致。硬规则违约即 `500`，
   软发现（已 WITHHELD 的冲突列）只登记、不进精度声明。
3. `backend/api/domain/ship_benchmark.py` —— 加载即过闸；阻力核改为进程内调用 `compute_resistance`
   （消除 8002 端口与 schema 漂移），按行几何与验证湿面积传参，summary 输出闸门裁决与 WITHHELD 清单。
4. `backend/api/calculations.py` —— 抽出纯函数 `compute_resistance`，响应新增 `wetted_surface_m2` / `ct`，
   并提供 `wetted_surface_m2` 入参（让对标测阻力模型而非几何近似）。

## 判决
v1.0 → **36 处违约 / 12 案例**（R1 恒等式、R2 介质、R6 状态）；v3.0 → **0 违约**，4 DERIVED + 8 WITHHELD。
`Ct` 对湿面积严格不变（最大相对差 2.2e-16）⇒ 以 Ct 为唯一判据的闸门**结构上看不见几何错误**，必须同时报 Rt。

阻力量精度（诚实）：SUBOFF Ct 误差 9.41% / 2.17% / 11.83% / **32.08%**（3/4 过 15% 门，Fr=0.918 失败即
方法适用域之外）；Rt 误差随湿面积来源剧烈变化（预设式 27.16/21.34/10.08/6.20%，验证 S 9.43/2.19/11.81/32.05%）。

## 复现
```
PYTHONPATH=backend python3 tools/validation/canonicalize_benchmark_reference.py --check
PYTHONPATH=backend python3 tools/validation/benchmark_reference_audit.py
PYTHONPATH=backend python3 -m pytest backend/tests/test_benchmark_integrity.py --noconftest -q   # 16 passed
```
产物：`artifacts/benchmark_reference_audit/audit.json` + 三张图。
