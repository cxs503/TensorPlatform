# 参考值语料 v1 + 基准数据一致性门（m1396，verifier 席）

> 本文件说明**做了什么 / 为什么这样做 / 怎么复现 / 局限在哪**。
> 台账数字见自动生成的 [`refs_debt_ledger.md`](refs_debt_ledger.md)，
> 机器工件见 `artifacts/verifier/refs_audit_m1396.json` 与三张 png。

## 1. 动机：平台在拿"未经自洽验证的参考值"给算法判 PASS/FAIL

`/api/v1/calculations/benchmark_validate` 的判决口径是
`error_pct = |calc_ct - ref_ct| / ref_ct`，`ref_ct` 全部来自
`backend/data/benchmark_reference.json`。只要参考记录**自身内部矛盾**
（例如声明的 `experimental_ct` 与 `experimental_rt_n` 在给定湿表面积和航速下
隐含一个 135 kg/m³ 的"水"密度），那么下游任何 PASS/FAIL 都不构成证据——
错的可能是算法，也可能是参考值，甚至两者都错，而判决输出看不出来。

因此本单做的不是"再加一个功能"，而是把**验证欠账**做成机器可判的：
参考值资产化（带溯源 + 容差 + sha256 锚）+ 一致性门（G1–G5）+ 台账。

## 2. 参考值语料（`tools/refs/corpus.json`，17 条）

**数字零手抄**：所有 `value` 由闭式表达式在 `tools/refs/build.py` 里现场算出，
条目内容做 sha256 锚；手改 JSON 里的数字而不重建语料会被
`schema.validate_corpus` 判为 `sha256 anchor mismatch`（已由测试锁定）。

每条条目的模式（`refs.schema` v1）：

| 字段 | 含义 |
|---|---|
| `id / quantity / unit` | 量标识（同 quantity 可多条目，如不同 Re 的 Cf） |
| `value / tolerance{rel,abs}` | 参考值与容差带：`|calc-ref| <= abs + rel·|ref|` |
| `coverage_k` | 容差对应的不确定度覆盖倍数（k=0 表示精确解，k=2 约 95%） |
| `source.type` | `analytic` / `standard` / `literature` / `dataset`，各自强制携带溯源字段 |
| `grade` | `REAL` / `WITHHELD` / `STUB` |
| `sha256` | `canonical_payload` 的摘要（防事后调数） |

v1 覆盖（`analytic` 类全部可独立复算）：ITTC-1957 Cf（Re=1e6/1e8/1e9）、
Blasius 层流 Cf、箱形驳船 KB/BM/GM、wall-sided GZ（φ=10/20/30°）、
势流附加质量（2D 圆 C_A=1、3D 球 0.5、椭圆族 b/a）、深水色散波速、
Kelvin 波半角、∇=Cb·L·B·T 与 Fr=V/√(gL) 定义式；另有 1 条 `dataset` 类
（站内 SUBOFF 湿表面积，`grade=WITHHELD`，锚在来源记录上）。

## 3. 一致性门（`backend/services/benchmark_integrity.py`，可被运行时复用）

| 门 | 判据 | 容差 | 性质 |
|---|---|---|---|
| G1 | `Fr_declared ?= V/√(gL)` | 1% | 定义式，纯算术 |
| G2 | `ρ_implied = Rt/(0.5·Ct·S·V²) ∈ [990,1035]` | 水的物理区间 | **反解密度**，不预设 ρ（避免"选密度凑数"） |
| G3 | `Cb_declared ?= m/(ρ·L·B·T)` | 2% | 几何自洽（先验几何，再谈阻力误差） |
| G4a | 溯源引用长度/字段齐备 | — | 证据分级 |
| G4b | `SHIP_GEOMETRY` 跨字段同值 | ≥3 字段同值 | 复制粘贴污染检测 |
| G5a | 平台 Cf vs ITTC-57（**同 Re** 取证） | 带宽 `1e-4 N + 1e-9·Rf` | 已合并公式对解析基准 |
| G5b | 平台 GZ(20°) vs wall-sided | `5e-7 m`（响应 6 位小数取整） | 同上 |
| G5c | 平台 `S=πBL(0.5+0.25Cb)` vs 数据集 S | 5% | 差异定位 |
| G5d | 平台自报 Fr vs 定义式 | 1e-3 → WARN | 常量口径漂移 |

运行时入口：`GET /api/v1/calculations/benchmark_integrity`（只读，不改任何参考值）。
任何调用方在采信 `benchmark_validate` 的 PASS 前，可先确认 `verdict == "PASS"`。

## 4. 结论（数字见台账；此处只给判读）

- **参考数据集当前 verdict = FAIL**：12 个工况里 8 个存在内部矛盾（DTMB-5415 的
  Fr 列与 Cb/排水量、SUBOFF 的 Ct–Rt 组合），KVLCC2 的 Cb–排水量偏差 4.2%（>2% 门限）。
- **已合并的物理公式本身是对的**：阻力链 ITTC-1957 Cf 与解析基准同 Re 偏差
  ~2e-5（来自响应 4 位小数取整），稳性链 wall-sided GZ 偏差 ~2e-7（6 位小数取整），
  两者均判 INFO/PASS。**错的是参考数据与几何预设，不是公式**——这正是"证据分级"
  要区分的东西。
- **最大的精度隐患不是 3%/15%，而是湿表面积口径**：平台 `S=πBL(0.5+0.25Cb)`
  = 4.36 m²，与同一数据集自带的 S=5.42 m² 差 19.6%；`Rf ∝ S`，误差线性进入阻力。
  该拟合系数无文献出处（`Cr=0.002(1+2Cb)Fn^2.5` 同样无出处），按证据分级应记
  **STUB 系数**，不能作为 REAL 阻力的支柱。
- `SHIP_GEOMETRY['SUBOFF']` 把最大直径 0.508 同时写进 beam/draft/displacement/cb，
  属复制粘贴污染 → 该预设的排水量与 Cb 无物理意义。

## 5. 复现

```bash
python3 -m tools.refs.build     # 重建语料（sha256 锚定）
python3 -m tools.refs.audit     # 审计 + 写工件/图/台账 Markdown
python3 -m pytest tests/test_refs_corpus.py tests/test_benchmark_integrity.py -q
```

## 6. 局限与未做的事（honest）

1. **未修数**：DTMB-5415 的 Fr 列、SUBOFF 的 Rt 列孰对孰错需要原始文献页表
   （Roddy 1990 DTRC-90/012、ITTC V&V Workshop 报告）才能定，本轮无该扫描件，
   按纪律**不猜**、不粉饰，只登记为 FAIL 并给出处置建议。故该项目前仍是
   `WITHHELD`，不得当作 REAL 证据。
2. **未做全尺度/模型尺度换算的裁决**：DTMB-5415 记录疑似把全尺度节数与模型尺度
   长度混在一行（声明 Fr 隐含 L≈181 m）。到底该改航速还是改长度，需要该 workshop
   的模型尺度定义，本单只做"矛盾存在"的机器证明。
3. **G3 对非排水型体不适用**：SUBOFF 这类轴对称体按 L·B·T·Cb 定义 ∇ 意义有限，
   引擎已将其记为 WARN（`cb_geometry` 缺字段）而非 FAIL。
4. **G5 的取证依赖 backend 可导入**：测试里对 `psycopg2` 做了 stub 才 import
   `api.calculations`；若 CI 环境缺 fastapi 等依赖，相关用例会 skip 并给出原因。
5. **语料仅 17 条**：只覆盖"能闭式复算"的量；需要文献表格的量（如 Holtrop 的
   `1+k1`、附体阻力系数表）留待有扫描件/页表时按 `literature` 类型录入，
   现在留空而不是编数。