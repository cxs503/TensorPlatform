# 船舶域高危缺陷登记（交接件）

- **任务**：m1780 —— 把规划师第 3 班实测的两处船舶域高危缺陷转成机器可裁、持续可跑的判据资产
- **被测平台**：`http://127.0.0.1:8001`（uvicorn 单 worker，源码 `/srv/tensor/ship-workspace/ship-performance-platform/backend`，**不在本工作树、不在派单白名单**）
- **判据工具**：`tools/ref_probe_8001.py`（在既有探针上增补，未另建第二套）
- **判决工件**：`artifacts/ship_endpoint_verdicts.json`（当前实测）、`artifacts/ship_endpoint_verdicts_prefix_baseline.json`（**修复前冻结证据，只增不改**）
- **消费方**：`tests/test_ship_endpoint_defect_verdicts.py`（16 例）、`tests/test_ref_anchors.py`（探针逻辑回归，27 例）
- **实测时间**：2026-11-01T18:02Z

---

## 1. 判决矩阵（端点 × 判据 × 现状 verdict × 修复后期望）

| 缺陷号 | 端点 | 判据 | 判据内容 | 现状 verdict | 实测证据 | 修复后期望 |
|---|---|---|---|---|---|---|
| SHIP-MAN-001 | `POST /api/v1/maneuvering-mmg/turning` | C1 | 合法 KVLCC2 级输入必须 200 | **FAIL** | HTTP **500**（`Internal Server Error`，6–8 ms） | PASS |
| SHIP-MAN-001 | `POST /api/v1/maneuvering-mmg/zigzag` | C2 | 合法输入（含 `heading_angle`）必须 200 | **FAIL** | HTTP **500** | PASS |
| SHIP-MAN-001 | `POST /api/v1/maneuvering-mmg/turning` | C3 | 缺参必须 422 且 `detail.loc` 指名缺项 | **PASS** | HTTP 422，`loc=["body","displacement"]` | PASS（不得回归） |
| SHIP-MAN-001 | 上述两端点 | L1 | 契约整体证据级 | **FAIL** | C1/C2 双 500 | PASS |
| SHIP-SEA-001 | `POST /api/v1/advanced/bem` | C4 | `frequencies`/`rao` 长度 ≥ 15 | **FAIL** | heave/pitch/frequencies 均 **6** 点 | PASS |
| SHIP-SEA-001 | `POST /api/v1/advanced/bem` | C5 | RAO 随 Cb/L/ω 变化：逐点差 > 5% 且（相关性 < 0.99 或共振峰移位 > 5%） | **FAIL** | L320/Cb0.82 与 L230/Cb0.651 两条船 + ω=0.3/1.3：序列**逐点全等**，相关性 1.0，峰不移位 | PASS |
| SHIP-SEA-001 | `POST /api/v1/advanced/bem` | C6 | 不得等于 6 点常数表指纹 | **FAIL** | 与 `advanced_theory.py:364-366` 表**逐点相等**（heave/pitch 双命中） | PASS |
| SHIP-SEA-001 | `POST /api/v1/advanced/bem` | L2 | RAO 证据级 | **MOCK** | 指纹命中 + 6 点 + 与船型/航速/频率无关 | **REAL** |

证据分级：`REAL` 真实解算 · `MOCK` 硬编码/伪表 · `STUB` 空壳 · `WITHHELD` 不可达（**绝不记 PASS**）· `PASS/FAIL` 单条判据布尔结论。

---

## 2. SHIP-MAN-001：操纵域输入契约断裂致 500

**现象**（可复现）

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8001/api/v1/maneuvering-mmg/turning \
  -H 'Content-Type: application/json' \
  -d '{"L":320,"B":58,"T":20.8,"displacement":320000,"Cb":0.82,"Dp":9.86,"n":80,"Ar":120,"V0":7.7,"rudder_angle":35}'
# 500
```

`zigzag` 需额外 `heading_angle`，补齐后同样 500。

**根因定位（文件:行）**

- `/srv/tensor/ship-workspace/ship-performance-platform/backend/api/maneuvering_mmg.py:270`
  `simulate_turning_test()` 构造 `MMGInput(...)` 时**未传 `Hr`**；
- `.../backend/api/maneuvering_mmg.py:324`
  `simulate_zigzag_test()` 同一缺陷；
- `MMGInput` 的必填字段集：`.../maneuvering_mmg.py:15-41`（`D`、`KG`、`xG`、**`Hr`**）；
- 端点输入模型 `TurningTestInput` (`:42-53`)、`ZigzagTestInput` (`:55-67`) 只声明 `L B T displacement Cb Dp n Ar V0 rudder_angle [heading_angle]` —— **字段集已漂移**；
- 栈顶（`/tmp/ship8001.log`）：`api_turning` → `simulate_turning_test` → `pydantic ValidationError: 1 validation error for MMGInput / Hr`。

**判据**：C1/C2（合法输入 200）、C3（缺参 422 且指名）、L1（聚合）。

**修复方向（本工作树无写权限，仅登记）**：给 `MMGInput` 的 `D/KG/xG/Hr` 加默认值，或在 `TurningTestInput/ZigzagTestInput` 增补 `D/KG/xG/Hr` 并在两处构造时透传；修复后 C1/C2 应转 PASS。

**精度影响**：模型跑不起来 → 回转战术直径/进距/K-T 指数**无任何精度可谈**，精度判决为 `WITHHELD`（不是 0 误差，也不是「不适用」）。

---

## 3. SHIP-SEA-001：耐波域 BEM/RAO 硬编码伪表

**根因定位**：`/srv/tensor/ship-workspace/ship-performance-platform/backend/api/advanced_theory.py:364-366`

```python
frequencies = [0.3, 0.5, 0.7, 0.9, 1.1, 1.3]
rao_heave   = [0.8, 1.2, 1.5, 1.3, 1.0, 0.8]
rao_pitch   = [0.5, 0.8, 1.2, 1.0, 0.7, 0.5]
```

同函数 `calculate_bem()` 的附加质量/阻尼/恢复力虽由 L/B/T/ω 解析式给出，但 `rao` 分支完全绕开这些量，`BEMInput.wave_frequency`（`advanced_theory.py:59`）被接收后**从未使用**。因此：

- 换船（L320/Cb0.82 ↔ L230/Cb0.651）：RAO 逐点全等（实测相关性 **1.0**，逐点差 **0%**）；
- 换频率参数（`wave_frequency` 0.3 ↔ 1.3）：RAO 逐点全等；
- 序列长度恒为 **6**，与 `advanced_theory.py:364-366` 表逐点相等 → 指纹命中。

**判据**：C4（分辨率 ≥15 点）、C5（船型/频率依赖性）、C6（常数表指纹）、L2（证据级 → MOCK）。

**修复方向（仅登记）**：用辐射/绕射解（或至少 1-DOF/Lewis 剖面 + 频率扫描 ≥20 点、含 Cb/L 依赖的附加质量与阻尼）替换常数表；修复后 L2 应转 REAL，且 C4/C5/C6 全 PASS。

**精度影响**：无可用 RAO 基准（无模型试验/势流解算参考），本班**不宣称**任何 RAO 精度；判决只基于「物理不变性 + 指纹」，属结构判据，非精度判据。

---

## 4. 判据实现要点（`tools/ref_probe_8001.py`）

| 机制 | 说明 |
|---|---|
| 单一探针 | 在既有 `PROBES` 元组追加 `("maneuvering-contract", ...)`、`("advanced-bem-rao", ...)`，复用同一 HTTP 传输与记录 schema，无第二套实现 |
| 证据即判词 | `reevaluate_defect_record()` 从每条记录自带 evidence 重算 verdict；`verify_defect_document()` 逐条比对，篡改判词/证据必被拒（pytest 负向 6 例） |
| 指纹检测 | `constant_table_fingerprint(series, ref)`：长度 6 且逐点差 ≤1e-9 即命中；20 点物理曲线因长度不符必然未命中（双向测试） |
| 不可达纪律 | `_http_verdict(status=None) → WITHHELD`；文档级校验禁止 `platform_reachable=False` 时出现 PASS/REAL |
| 一键回归 | `python3 tools/ref_probe_8001.py` 同时产出锚点探针工件与缺陷判决工件；`--verify <json>` 单独复裁，退出码非零即漂移 |
| 修复前冻结 | `--freeze-baseline --defect-out artifacts/ship_endpoint_verdicts_prefix_baseline.json`，写 `frozen=true` 与 `frozen_note` |

**复现命令**

```bash
cd /srv/tensor/agent/work/m1780
python3 tools/ref_probe_8001.py                     # 跑全部探针（含两个新子系统）
python3 tools/ref_probe_8001.py --verify artifacts/ship_endpoint_verdicts.json
python3 -m pytest tests/test_ship_endpoint_defect_verdicts.py tests/test_ref_anchors.py -q
```

预期：`maneuvering-mmg/contract → C1/C2 FAIL, C3 PASS, L1 FAIL`，`advanced-bem/rao → C4/C5/C6 FAIL, L2 MOCK`，`defect_self_check=OK`；pytest 43 passed（16 + 27）。

---

## 5. 派单归属与处置建议（需 owner 裁决）

- 两个缺陷的**根因仓库**是 `ship-platform`（`backend/api/maneuvering_mmg.py`、`backend/api/advanced_theory.py`），该仓**不在派单白名单**，本班 `EDIT_WHITELIST` 仅 `tools/ tests/ artifacts/ docs/`；
- 因此本班交付的是**判据资产 + 交接登记**：缺陷可被机器持续复现与裁决，但**修复本身无派单归属**；
- 建议 owner 二选一：(a) 把 `ship-platform` 后端纳入派单白名单（最小修复见 §2/§3 的修复方向）；(b) 授权在本工作树内做代理修复（需扩白名单，且平台部署需重启 :8001）；
- 修复合入后无需改判据：重跑探针即得 `L1=PASS`、`L2=REAL`；`prefix_baseline` 保留 500/MOCK 的历史判决，构成回归对照，不会被覆盖。

---

## 6. 局限（honest）

1. **无 RAO 精度基准**：平台无模型试验/势流参考解，C4–C6 只证明「不是硬编码常数表」，不证明 RAO 数值正确；六要素报告中的误差项如实记为 `WITHHELD`。
2. **契约判据只判连通性**：C1/C2 只要求 200 可解析，不校验 K/T、战术直径量级（无 MMG 基准算例随附）；修复后需补精度判据（KVLCC2 试验 TD/Lpp≈3.0 量级可作下一班判据起点）。
3. **被测平台源码在工作树外**：`file:line` 引用的是运行态源码（`/proc/<pid>/cwd` 指向的目录），仓库若换分支需重定位。
4. **C5 判据是结构判据**：对「形状几乎相同但整体缩放」的伪响应，若同时满足逐点差 >5% 且相关性 <0.99/峰移位 >5% 才判 PASS，属必要非充分条件。
5. 平台不可达时探针输出 `WITHHELD`，pytest 相应断言 skip —— 不把不可达当通过。