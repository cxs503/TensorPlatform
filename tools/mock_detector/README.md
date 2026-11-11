# tools/mock_detector — 结构证据探测器（确定性 × 单调性 × 幂等性）

把「这个端点返回的数字是不是真算出来的」从**领域判断**降级为**结构判断**：
三条与领域无关的性质足以证伪绝大多数伪实现，且不需要任何参考值、解析解或领域先验。

| 探针 | 断言 | 被证伪的造假方式 |
|---|---|---|
| 确定性 determinism | 同一请求两次 → 数值向量 SHA-256 相同 | `random()`、未播种噪声、并发竞态 |
| 幂等性 idempotency | 同一请求重复，且中间插入一个不同请求后仍一致（A→B→A） | 隐藏状态、计数器、缓存串味 |
| 单调性 monotonicity | 扫单个数值输入时至少一个数值输出随之移动，且单调或平滑 | 硬编码常数（不动）、查表分档（几档跳变） |

## 与 `tools/capability_audit` 的分工（重要）

| | `tools/capability_audit` | `tools/mock_detector`（本模块） |
|---|---|---|
| 判据来源 | 物理先验：解析解对拍、基准值、单调方向由物理锁定 | 无：只断言结构性质 |
| 前置条件 | 每个能力手写 `CapabilitySpec`（载荷 + 期望方向） | 端点自身 OpenAPI schema 自动合成载荷 |
| 覆盖面 | 6 个能力 | 任意带 JSON body schema 的 POST 端点 |
| 能得出的结论 | 精度/误差、单调方向是否物理正确 | 是否硬编码/随机/有状态/分档 |
| 不能得出的结论 | — | **精度**：结构 REAL ≠ 数值准确 |

因此本模块的等级带 `level_scope="STRUCTURAL"` 与 `accuracy_verified=False`，
报告与 `criteria.json` 都显式声明「不作精度替代」。二者是互补而非重复。

## 用法

```bash
# 0) 合成夹具自检（构造已知答案的伪/真端点，判据 C1）
python3 -m tools.mock_detector.cli fixtures --out artifacts/mock_detector

# 1) 扫描（默认排除 simulate/train/upload/stream/delete 等异步作业端点）
python3 -m tools.mock_detector.cli scan --base-url http://127.0.0.1:8002 \
        --out artifacts/mock_detector --max-targets 120 --concurrency 10

# 2) 复跑抽检（判据 C4：重扫子样本，等级必须一致）
python3 -m tools.mock_detector.cli repeat --scan artifacts/mock_detector/scan.json --n 8

# 3) 与能力台账对账（只写 overlay / 派生副本，规范台账不动）
python3 -m tools.mock_detector.cli ledger --scan artifacts/mock_detector/scan.json \
        --out artifacts/mock_detector --apply-to artifacts/mock_detector/capability_ledger_derived.json

# 4) 报告（4 张图 + criteria.json + Markdown，数字全部程序化注入）
python3 -m tools.mock_detector.cli report --out artifacts/mock_detector
```

显式目标（领域仓自带载荷，零领域语义照样成立）：

```json
{"targets": [{"cap_id": "x.y", "method": "POST", "path": "/api/v1/x/y",
              "base_payload": {"p": 1.0, "q": 2.0}, "sweep_fields": ["p"]}]}
```

`scan --targets targets.json` 即用配置注入的载荷，探测器代码里依旧没有任何领域词条。

## 等级语义（有序规则表，首个命中定级，其余同真谓词作为佐证一并记录）

| 规则 | 等级 | 含义 |
|---|---|---|
| `ROUTE_ABSENT` | NOT_SUPPORTED | 404/405/410/501 |
| `UNREACHABLE` | WITHHELD | 连接失败/超时/非 JSON/5xx —— **无证据，不是通过** |
| `INPUT_REJECTED` | WITHHELD | 400/422：载荷造不出来，什么都没测到 |
| `NO_NUMERIC_OUTPUT` | STUB | 2xx 但一个数值场都没有 |
| `NONDETERMINISTIC` | MOCK | 同请求两次数值向量不一致 |
| `NON_IDEMPOTENT` | MOCK | 重复同一请求答案变化 |
| `ORDER_DEPENDENT_STATE` | MOCK | 中间插入别的请求后答案变化（隐藏状态） |
| `INPUT_INVARIANT` | MOCK | ≥2 条输入轴上没有任何输出移动 = 硬编码 |
| `QUANTIZED_OUTPUT` | MOCK | ≥8 次采样只有 ≤2 个不同输出向量 = 几档冻结桶 |
| `NO_SWEEPABLE_INPUT` | WITHHELD | 没有可扫的数值输入，响应形状不可测 |
| `STRUCTURAL_PASS` | REAL | 确定 + 幂等 + 顺序无关 + 对数值输入有响应 |

信息性旗标（不影响定级）：`VOLATILE_METADATA`（整个 payload 的 hash 变了但数值向量稳定，
说明只是 request-id/时间戳在动）、`PARTIALLY_INVARIANT`、`NON_MONOTONE_RESPONSE`
（物理上合法的驼峰）、`STEPPED_RESPONSE`（像分段/查表）、`SINGLE_AXIS_ONLY`、`AXES_REJECTED`。

## 设计约定

1. **探针只测量，不定级**：等级永远来自 `classify.py` 的有序规则表，输入只有记录下来的数字，
   所以任何判定都能从 `scan.json` 重新导出并逐条争辩。
2. **空比较不算通过**：`compare_vectors` 在无可比字段时返回 `n_compared=0`，定级按
   `WITHHELD`；这在早期版本是最大的假通过通道。
3. **数值指纹而非整包指纹**：确定性比较用扁平化数值向量的 SHA-256，避免时间戳/请求号
   把真实现判成随机；整包 hash 变化但数值稳定 → 只记 `VOLATILE_METADATA` 旗标。
4. **禁词表是数据不是代码**：`domain_guard.json` 存放领域禁词，`report.domain_leak_scan`
   只扫 `*.py`（否则禁词表会命中自己的定义，判据恒红）。
5. **台账只 overlay 不改写**：规范台账被 `tools.audit.ledger_paths` 的 sha256 契约钉住，
   自动改写会破坏契约门，也会抹掉「当时自述」与「后来审计」的区别。

## 已知局限

* 结构 REAL 只证明「数字由输入算出来」，不证明对；精度必须另测。
* 需要领域知识才能构造合法载荷的端点 → `WITHHELD`，本模块不猜参数。
* 异步作业端点默认排除（job launcher 的「同请求两次」无意义）。
* 单调性方向与物理无关：本模块只看「动没动、是否单调/平滑」，方向正确性归
  `tools/capability_audit` 的 monotonic 三刀。

## 产物

```
artifacts/mock_detector/
├── scan.json                 # 每端点探针原始数字 + 判定 + 规则表
├── targets.json              # 载荷（复跑用）
├── fixture_selftest.json     # 合成夹具自检（判据 C1）
├── repeat.json               # 复跑一致性（判据 C4）
├── ledger_overlay.json       # 台账对账 overlay（不发写入规范台账）
├── capability_ledger_derived.json  # 派生副本（mock_flag / reconciled_level_proposed）
├── domain_leak_scan.json     # 禁词扫描（判据 C6）
├── criteria.json             # 预注册判据 + 实测数字 + 产物路径
├── report.md                 # 论文级报告
└── figures/*.png             # 4 张图
```