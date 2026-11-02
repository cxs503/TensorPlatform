# Handler 契约漂移静态判据（SHIP-MAN-001 类缺陷的通用化）

**任务** m1865（自找活·models 席·验证欠账类）｜**仓** TensorPlatform｜**产物** `tools/audit/handler_contract_drift.py`、`tests/test_handler_contract_drift.py`、`artifacts/audit/handler_contract_drift_8001.json`、`artifacts/audit/fig_handler_contract_drift_m1865.png`

## ① 案例介绍

被测平台 `http://127.0.0.1:8001`（船舶总体性能平台）。缺陷族 SHIP-MAN-001：端点声明的输入模型与 handler 内部构造的核模型字段集漂移，导致 pydantic `ValidationError` → HTTP 500。本单把「果然 500」的逐点手测，升级为**静态可判**：构造表达式 vs 目标模型必填集合。

## ② 怎么做的

1. AST 抽模型必填字段（无默认值注解字段）与 handler 体内 `Model(...)` 构造；
2. 缺失必填/未知关键字 → 漂移记录；
3. 判据自证：合成缺陷必须命中、合成健康必须静默；
4. 与部署 OpenAPI 的 `required` 集合交叉核对（源码漂移 vs 部署漂移分离）；
5. 按 OpenAPI schema 自动生成 payload 实测状态码（独立证据链）。

## ③ 计算结果

| 项 | 值 |
|---|---|
| 扫描源码文件数 | 104 |
| 识别 pydantic 模型数 | 335 |
| 识别写端点（POST/PUT/PATCH）数 | 231 |
| 漂移记录数 | 4 |
| 判据自证 | 通过 (合成缺陷命中 ['Hr']，合成健康静默 True) |

### 3.1 漂移清单（静态判据）

| 端点 | handler:行 → 构造:行 | 输入模型 → 核模型 | 缺失必填 | 未知关键字 | 实测状态码 |
|---|---|---|---|---|---|
| `POST /static` | fem.py:478 → 265 | FEMInput → MeshInput | height | thickness | 422 |
| `POST /turning` | maneuvering_mmg.py:409 → 270 | TurningTestInput → MMGInput | Hr | — | 500 |
| `POST /zigzag` | maneuvering_mmg.py:419 → 324 | ZigzagTestInput → MMGInput | Hr | — | 500 |
| `POST /design` | propulsion.py:78 → 81 | PropulsionRequest → PropellerDesignInput | delivered_power, rpm, ship_speed, thrust_deduction, wake_fraction | B, Cb, Dp, L, T, displacement, n, speed | 500 |

### 3.2 部署 schema 与源码模型必填集合的差异

| 端点 | 部署 schema | 部署 required | 源码输入模型 | 源码 required | 差异 |
|---|---|---|---|---|---|
| `POST /static` | FEMInput | E, boundary_type, length, load_type, load_value, nu, rho, thickness, width, yield_stress | FEMInput | E, boundary_type, length, load_type, load_value, nu, rho, thickness, width, yield_stress | 一致 |
| `POST /turning` | TurningTestInput | Ar, B, Cb, Dp, L, T, V0, displacement, n, rudder_angle | TurningTestInput | Ar, B, Cb, Dp, L, T, V0, displacement, n, rudder_angle | 一致 |
| `POST /zigzag` | ZigzagTestInput | Ar, B, Cb, Dp, L, T, V0, displacement, heading_angle, n, rudder_angle | ZigzagTestInput | Ar, B, Cb, Dp, L, T, V0, displacement, heading_angle, n, rudder_angle | 一致 |
| `POST /design` | PropulsionRequest | B, Cb, L, T, displacement, speed | PropulsionRequest | B, Cb, L, T, displacement, speed | 一致 |

## ④ 结果图

![handler contract drift](../../artifacts/audit/fig_handler_contract_drift_m1865.png)

## ⑤ 注意点

- 静态判据只覆盖「handler 直接构造其它 pydantic 模型」这一族；通过 dict 透传再 `**kwargs` 展开的写法不可见（记为局限）。
- 实测状态码依赖 payload 量级提示表（`MAGNITUDE_HINT`），非物理标定；其作用仅是触发/不触发 500，不产生精度结论。
- 修复后同一判据应转静默，`tests/test_handler_contract_drift.py` 会锁住该期望。

## ⑥ 误差判定

关键量 = 缺陷是否被静态判据命中；判据自证结果为上表「判据自证」行。静态预测与实测 500 的一致性见 3.1 表最后一列（同一行内自洽）。本单不产生物理量误差（缺陷=端点不可用，精度判决为 WITHHELD，非 0 误差）。

