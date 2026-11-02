# m1870 — 船舶域判据资产可复现性回归 + 两缺陷重裁（2026-11-02）

**EDIT_WHITELIST**: `docs/, tests/, artifacts/`（本仓 TensorPlatform）

## 1. 案例背景（为什么这是验证欠账，不是新功能）

`tools/ref_probe_8001.py`（m1780 建立）是船舶域**唯一机器可裁判据资产**：它把
`:8001` 的实时回答裁成 PASS/FAIL/WITHHELD，并投影成两份「缺陷判决书」
（SHIP-MAN-001 操纵契约 500 / SHIP-SEA-001 耐波 RAO 伪表）。它的价值完全取决于
**任何人在任何一棵树上重跑都得同一判词**。

本班在干净工作树重跑，得到的却是：

| 现象 | 实测 | 后果 |
|---|---|---|
| 5 个子系统里 3 个 `ERROR` | `probe_crash: FileNotFoundError: artifacts/ref/anchors/{spectra_ittc_issc,propeller_wageningen_b4_70,resistance_kvlcc2_kcs}.json` | **崩溃被当成证据**：门禁分不清「已修好」和「坏掉了」 |
| 锚点缺失根因 | `git ls-files artifacts/ref` 为空；锚点由 `6d9d1883` 引入但被门禁过滤（同 m1780「10 个文件被门禁滤除」记录） | 判据资产不可复现 |
| 契约判据陈旧 | C3 硬编码 `MISSING_PARAM_FIELD="displacement"`，实测线上 turning 的 422 已要求 `Cb/Dp/n/Ar/V0` | 判据与线上契约脱钩 |

渗透测试思路：**判据资产本身也是被测对象**。一个永远 FAIL 的门禁与一个永远
PASS 的门禁等价 —— 都是零信息。

## 2. 怎么做的（方法/流程）

1. **重跑前先留痕**：在工作树内跑 `python3 tools/ref_probe_8001.py`，得到
   `11 records / 5 subsystems`、3 ERROR（崩溃型）。
2. **修复判据资产**：`git checkout 6d9d1883 -- artifacts/ref/anchors`（4 个锚点回位，
   纯参考数据，非源码改动）。重跑得 `37 records / 8 subsystems`，**ERROR 归零**。
3. **升级 500 根因**：从 `:8001` 运行日志取真实 traceback。
4. **加守卫**：`tests/test_gate_asset_reproducibility.py`，把「崩溃≠判决」「锚点在位」
   「不可达不得冒充通过」钉成 12 条机器判据（含 5 条负向控制）。
5. **图**：`artifacts/ref/m1870_gate_repro.png`（左：8 子系统裁据分布；右：锚点相对误差 vs 15% 线）。

## 3. 计算结果（全部程序化注入，零手抄）

### 3.1 门禁可复现性（修复前 → 后）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 记录数 / 子系统数 | 11 / 5 | **37 / 8** |
| ERROR（崩溃型） | 3 | **0** |
| 可裁子系统 | 2 | **7** |
| `defect_self_check` | OK | OK |

### 3.2 两缺陷重裁（2026-11-02 线上态）

| 缺陷号 | 判据 | 判词 | 证据 |
|---|---|---|---|
| SHIP-MAN-001 | C1 turning 200 | **FAIL** | HTTP **500** |
| SHIP-MAN-001 | C2 zigzag 200 | **FAIL** | HTTP **500** |
| SHIP-MAN-001 | C3 缺参 422 指名 | **PASS** | 422（但指名 `Cb/Dp/n/Ar/V0`，非 `displacement`） |
| SHIP-MAN-001 | L1 契约证据级 | **FAIL** | C1/C2 双 500 |
| SHIP-SEA-001 | C4 分辨率 ≥15 点 | **FAIL** | frequencies/heave/pitch 均 **6** 点 |
| SHIP-SEA-001 | C5 船型/频率依赖 | **FAIL** | 换船与换 ω 序列逐点全等 |
| SHIP-SEA-001 | C6 常数表指纹 | **FAIL** | 与 `advanced_theory.py:364-366` 表逐点相等 |
| SHIP-SEA-001 | L2 证据级 | **MOCK** | 指纹命中 |

**结论**：两缺陷**均未修复**（m1780 判决在 2026-11-02 线上仍成立）。

### 3.3 500 根因升级（新证据，m1780 当时只记到「HTTP 500」）

运行日志 `/tmp/ship_8001.log` 给出真实异常链：

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for MMGInput
```

即 `backend/api/maneuvering_mmg.py:270-276` 的
`simulate_turning_test()` 在把 `TurningTestInput` 转成 `MMGInput` 时**校验失败且未捕获**，
被 FastAPI 兜成 500。同时该处 `MMGInput(D=input_data.T, ...)` 把**吃水 T 当成了螺旋桨直径 D**
（同一行 `T=input_data.T` 与 `D=input_data.T` 双赋值），是校验失败的合理嫌疑点。
→ 这条把「不明 500」变成「可定位的单点修复」，交接 ship-platform 仓可直接动手。

### 3.4 其它子系统锚点偏差（同一批裁据，如实列出）

| 子系统 | 量 | 平台值 | 锚点 | 相对误差 |
|---|---|---|---|---|
| resistance/ittc78-advanced | R_T [N] | 10133840 | 1960000 | **417.0%** |
| resistance/calculations-resistance | R_T [N] | 5940310 | 1960000 | **203.1%** |
| resistance/predict | R_T [N] | 3850810 | 1960000 | **96.5%** |
| propulsion/wageningen-b | K_T | 0.336 | 0.4254 | **21.0%** |
| seakeeping/wave-spectrum | m0 | 1.2419 | 1.5648 | **20.6%** |

## 4. 误差判定（是否 <3%）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 门禁可复现性（ERROR 计数） | 0 | 0 | **是**（严格 0） |
| 记录数可加性 | 37 = 各子系统之和 | 0 | **是** |
| SHIP-MAN-001 C1/C2 契约 | 期望 200 | 实测 500 | **否**（缺陷仍在） |
| SHIP-SEA-001 RAO 分辨率 | ≥15 点 | 6 点 | **否**（缺陷仍在） |
| 静水阻力 R_T（三端点） | KVLCC2/KCS 锚点 | 96.5%–417% | **否** |
| 敞水 K_T | Wageningen B4-70 | 21.0% | **否** |
| 波浪谱 m0 | ITTC/ISSC | 20.6% | **否** |

> 声明：表中「否」项是**平台当前缺陷/精度未达标**，不是本班交付物的失败；本班交付物
> （判据资产可复现性）自身误差为 0，验收判据是「ERROR 计数归零 + 37 条裁据可加」。

## 5. 注意点（局限，honest）

1. **只做轻量活**：入场 `load15=122`（>100 纪律线），全程仅 HTTP 探针 + 单元测试，
   未起任何重算力进程；因此**不对阻力/推进数值做根因定位**，只如实登记偏差。
2. **锚点口径未复核**：KVLCC2/KCS 锚点 `1960000 N` 与三个阻力端点相差 2.6 倍，
   其中可能含模型尺度/实船尺度或参考面积口径错配；本班**不宣称**平台阻力精度结论，
   仅指出「同一条船三个端点给出 5.94/10.13/3.85 MN，彼此差 2.6 倍」是内部不自洽的硬证据。
3. **未改 `tools/ref_probe_8001.py` 源码**：C3 的陈旧字段常量只被**登记**（schema drift），
   修复它需要同步 ship-platform 的最终契约，跨仓，留给对应仓单。
4. **锚点恢复用 `git checkout 6d9d1883 --`**：属参考数据回填，非代码回滚；若门禁仍过滤
   `artifacts/ref/anchors/*.json`，下次干净树仍会复发 —— **根因在门禁，不在本修补**。
5. 500 根因（`MMGInput` ValidationError）来自运行日志，属**未捕获异常**级的强证据，
   但本班未读 pydantic 具体字段冲突（预算内未取），故只以「嫌疑点」表述，不做定论。

## 6. 复现

```bash
cd /root/tp-agent/work/m1870
git checkout 6d9d1883 -- artifacts/ref/anchors      # 缺失则先回填
python3 tools/ref_probe_8001.py                     # 37 records / 8 subsystems, ERROR=0
python3 tools/ref_probe_8001.py --verify artifacts/ship_endpoint_verdicts.json
python3 -m pytest tests/test_gate_asset_reproducibility.py -q   # 11 passed, 1 skipped
```

## 7. 给下一班的线索

- **P1**：门禁过滤 `artifacts/ref/anchors/*.json` 的根因（`.gitignore`/CI glob）——否则判据资产每次重播都烂一遍。
- **P2**：ship-platform `simulate_turning_test` 的 `MMGInput` 构造（D/T 双赋值 + 字段校验）单点修复，修好重跑探针即得 L1=PASS。
- **P3**：`advanced_theory.py:364-366` 常数表替换为 1-DOF/Lewis 频率扫描（≥20 点，含 Cb/L 依赖），C4/C5/C6 同步转 PASS、L2→REAL。
- **P4**：阻力三端点内部不自洽（2.6× 散度）值得单开一单做口径对齐，比追绝对精度更高性价比。