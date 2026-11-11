# API 契约测试夹具（OpenAPI × 实现自动比对）— 方法、证据与复用边界

**标题/规则**：任何发布 OpenAPI 的 HTTP 服务，其「规格 ↔ 实现」一致性必须由
**可复算的契约夹具**持续验证，且覆盖率只统计**实际执行**（收到响应）的端点；
零执行证据的判据只能报 `WITHHELD`，不得报 PASS。

- **上下文/适用范围**：适用于提供 OpenAPI 3.0/3.1 文档的同步 HTTP/JSON 服务；
  可覆盖 schema、状态码、错误信封、数值恒等式、破坏性变更五类契约面。
  **不适用**：异步回调/WebSocket/流式响应；需要跨请求会话状态的端点；规格未声明
  业务约束（如物理自洽门）时，夹具只能把「规格合法但业务拒绝」记为
  `SPEC_UNDERSPECIFIED` 发现，不能判为夹具失败。
- **证据/出处**：TensorPlatform `tools/api_contract`（m14740/p380w2-n03，分支
  `agent/m14740`）；实测对象 = ship-performance-platform 后端（OpenAPI 3.1，
  496 路径 / 513 操作）。原始证据：`runs/m14740/live/contract_report.json`、
  `runs/m14740/asgi/contract_report.json`、`runs/m14740/findings/defects.json`、
  `runs/m14740/checkpoints/cases_live.jsonl`（逐例 JSONL 台账）。
- **机制**（为什么这样做）：
  1. **执行覆盖**取代**生成覆盖**：`HttpTarget` 在连接失败时先探 `/health`、等到
     服务重启完成后重试，并把每例即时写 JSONL 检查点，`resume` 续跑——上一版
     881 例中 804 例因后端看门狗重启记 `UNREACHABLE`，"100% 覆盖"名不副实。
  2. **双执行面**：在线 HTTP 与进程内 ASGI（`app.openapi()` + Starlette
     `TestClient`）各跑一遍；差异本身即「部署 ≠ 仓库」的证据（本次：0 漂移）。
  3. **数值判据数据化**：响应数值不靠人工抄，靠 `references.json` 里的表达式恒等式
     （ITTC-57 摩擦线、Re/Fr 定义、Rt=ct·½ρSV²、Pe=Rt·V、KM=KB+BM、GM=KM-KG、
     BM=I/∇），容差 `rel<3% / abs<1e-6`，并对已发布小数位加 `quantization` 容差。
  4. **MOCK 检测**：敏感性探针扫同一端点的一个输入，若被观察的数值叶**完全不随
     输入变化**，标 `MOCK_SUSPECT`（本次抓到耐波 RAO 极值恒为 2.0/3.5/1.25）。
  5. **变更门**：`diff` 给 10 类变更码、5 类必检（新增必填/字段删除/状态码变更/
     错误格式变更/精度降低），并用 `mutate_spec` 注入 5 类突变证明检出率。
- **推荐流程**：
  1. 采集规格快照 → 2. 配置注入（`spec/target/overrides/matrix/references/
  sensitivity`）→ 3. `cli run`（线上；必要时 `resume`）→ 4. ASGI 交叉复核 →
  5. `cli mutations` 验证检出能力 → 6. 对每条违规用独立脚本复现（`triage_defects.py`）
  → 7. 判据落 JSON + 图 → 8. CI 用 `cli diff --fail-on-breaking` 做合并门。
- **反例/局限**：
  * 全量 957 例顺序执行约 8 分钟；对被审计服务有实际负载，生产环境应降并发/限流。
  * 规格欠声明（物理自洽门、燃料枚举、跨字段互斥）会让「规格合法」请求收到 422；
    这不是夹具误判，须在报告中单列 `SPEC_UNDERSPECIFIED`，并建议后端把约束写进
    OpenAPI（`minimum/maximum/enum`）。
  * 响应 schema 未声明 `example`/`multipleOf` 时，`spec_declared_precision` 通道
    为空，精度只能由注入恒等式承担——不得因此宣称「精度已验证」。
  * 敏感性探针只判「是否随输入变化」，不判物理正确；恒值未必错（例如排水量与
    航速无关），所以被观察字段必须由数据显式指定。
- **回归保护**：`tests/api_contract/test_contract_harness.py`（21 例）覆盖降级三态、
  生成覆盖、schema/状态码判定、表达式白名单、5 类突变检出、ASGI 目标、全流程
  判据 all_pass、零执行 → `WITHHELD`，以及**领域泄漏守卫**（包内出现
  URL/回环地址/应用路径字面量即失败）。
- **评审状态**：待评审（本报告自证：现场跑数 + 独立复现 26/26）。**最后验证日期**：
  2026-11-11。

## 本次现场发现（夹具抓到的真实缺陷，均由独立脚本复现）

| 类别 | 数量 | 说明 |
|---|---|---|
| 无效请求未被拒（非 4xx） | 5 | 4 例 500（`assets/search`、`datasets/{id}`、`digital-twin/predict`×2）、1 例 200（`data-standard/{code}` 接受错误类型 `name`） |
| 2xx 响应违反自身 schema | 1 | `fatigue-analysis/analyze` 返回 `corrected_stress=null`/`damage_ratio=null`，规格声明为 number |
| 错误信封漂移 | 10（抽样） | 手写 `HTTPException(422, detail="…")` 返回字符串，规格声明 `detail: array`（FastAPI HTTPValidationError） |
| 规格合法输入触发 5xx | 10（抽样）+ 全量见统计 | 规格未声明的跨字段/枚举约束未被后端压成 4xx |
| 数值诊断失败 | 3 | 推力系数 KT 报 118.12 = 定义值 0.1181 ×1000（量纲）；thrust 627.6 kN vs 由阻力反算的必需推力 98.8 kN（×6.35）；`effective_power_pe=0` vs 阻力有效功率 303.1 kW |
| MOCK 嫌疑 | 2 端点 | 耐波 RAO 极值不随周期/浪向变化（恒 2.0/3.5/1.25），振幅 = Hs/2 × 常数 |

> 诚实声明：判据 (b) 因此判 **FAIL**（2xx schema 125/126 = 99.21%；无效请求拒绝
> 439/444 = 98.87%）。FAIL 归因于被审计服务（ship-performance-platform），
> 夹具自身的判定能力由 pytest 正/负控与 5/5 突变检出证明；不粉饰、不豁免。