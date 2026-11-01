# 事故复盘：船舶平台端点漂移（:8002 vs :8001）+ 无守护宕机

- 日期：2026-11-01 · 席位 tp-infra-1 · mission 1412
- 结论：两处基础设施缺陷，均已修复并留下机器判据。

## 1 症状
能力审计（`audit_capabilities` / `call_ship_api`）返回“8001/8002 均不可达”，21 项能力只能落到 WITHHELD。

## 2 根因（两条独立失效叠加）
| # | 缺陷 | 位置 | 证据 |
|---|------|------|------|
| R1 | 代理层写死 `:8002`，真实服务在 `:8001` | `backend/services/ship_proxy.py`、`backend/api/domain/ship_batch.py`、`backend/api/domain/ship_benchmark.py`、`tools/optimizer/toy_optimize.py` | `ss -ltnp` 无 8002 监听；`:8001/health` 返回 healthy |
| R2 | `:8001` 宕机数小时无人拉起 | `/srv/tensor/agent/autoheal_state.json` 守护名单中无 ship-api | 重启同一代码即 healthy，非代码缺陷 |

R1+R2 叠加 = “双重静默失效”：没人能区分“端口错”和“服务死”。

## 3 修复
1. 新增 `backend/services/ship_api_endpoint.py`（单一事实源）：`ship_api_origin / ship_api_base / ship_api_url / probe`；
   环境变量 `SHIP_API_BASE`（兼容旧别名 `SHIP_PLATFORM_URL`）优先，默认 `http://127.0.0.1:8001`；
   origin / 含 `/api/v1` / 带尾斜杠 / 带空格 四种写法统一收敛。
2. 四条调用链全部改由该模块解析，`ShipProxy` 改为实例化时解析（测试可注入）。
3. 新增 `tools/ops/ship_api_watchdog.py`：一次探测输出 JSON，退出码 0/1，支持 `--restart-cmd` 自动拉起；
   可作为 cron / fleet_governor 的 ship-api 健康源（补 R2 缺口）。
4. 回归测试 `backend/tests/test_ship_api_endpoint.py`：解析/优先级/URL 拼接/四条链一致性/源码禁写死 8002/探针不抛异常/在线健康探测/守护脚本可执行。

## 4 复核结果（KVLCC2 @15.5 kn，机器判据）
| 判据 | 结果 |
|------|------|
| 代理 vs 直连相对误差 | 0.0% （PASS，代理只转发不改数） |
| 平台内部 `rf+rr=rt` | 0.000168% （PASS） |
| 平台 Cf vs ITTC-57（同 Re=0.00139543） | 0.0305% （PASS） |
| 湿表面积 vs Mumford 估算 | 2.172% （PASS） |
| 隐含形状因子 1+k | 1.3146 （PASS，肥大型船 1.20~1.35） |
| Cb / L/B / B/T / 排水量自洽 | 全部 PASS |

量纲注意：平台返回的 `rf/rr/rt` 单位是 **kN**，`rf` 是含形状因子的粘性阻力 (1+k)Rf，不是平板摩擦阻力——直接拿 Rf 比会误判为 1000 倍量纲错。

## 5 局限
- 未对 KVLCC2 总阻力做文献全尺度基准比对（本机无该算例的试验值金标准），故 `Ct` 绝对精度标 WITHHELD，不计 PASS。
- 守护脚本未接入 crontab/fleet_governor（涉及平台侧调度改动，留待 owner 决定）。
