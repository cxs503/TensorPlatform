# cross_arch_gate — 跨架构基准复现门（平台共性机制，零领域语义）

一句话：**同一个基准代码，在两套 ISA / 两台机器上跑出的每一份输出，逐字段判四类差异，
给 PASS/FAIL，并产出可审计的判据 JSON。** 测量按架构分开做，判官只有一套。

```
tools/cross_arch_gate/
├── record.py       测量层：在「被测架构」上执行，产出不可变记录（%a 精确十六进制 + 溯源 + digest）
├── classify.py     判决层：单字段四类标签 BIT_IDENTICAL / WITHIN_TOLERANCE / OVER_TOLERANCE / NAN
├── gate.py         裁决层：compare(ref, cand, policy) 纯函数 -> 逐字段行 + 统计 + PASS/FAIL
├── selftest.py     自检层：变异矩阵（检出率 / 误报率 / NaN 检出率）
├── audit.py        审计层：零领域语义扫描（平台词表 = 配置，被测体 = 门包）
├── criteria.py     判决汇总：六项机器判据 -> criteria.json（每项带 value/threshold/source）
├── pipeline.py     编排：建臂 -> 逐对判决 -> 变异 -> 审计 -> 判据 -> 图
├── numerics.py     %a 编解码 / ULP 距离 / 可移植 IEEE-754 binary64 oracle（精确有理数 + 一次舍入）
├── fixtures.py     合成夹具（整数 / 直线程序 / 归约 / 超越 / 抵消），零领域语义
└── default_policy.json  容差策略（可 --policy 覆盖，域数据不许进代码）
```

## 1. 三条命令

```bash
# ① 在「被测架构」上采一份记录（唯一必须在目标机器上跑的一步）
python3 -m tools.cross_arch_gate.cli run --arm loong64-n315 \
        --out artifacts/cross_arch_gate/external/loong64-n315.json

# ② 任意机器上裁决（判官是纯函数，x86 也能判 loong64 的记录）
python3 -m tools.cross_arch_gate.cli compare --ref ref.json --cand cand.json --out pair.json

# ③ 全流程（建 6 个受控臂 + 变异自检 + 领域审计 + 判据 + 图 + 报告）
python3 -m tools.cross_arch_gate.cli pipeline --outdir artifacts/cross_arch_gate \
        --external-arm loong64-n315=<path> --report-md docs/reports/<name>.md
```

## 2. 接一个真实基准（不用改门）

```python
from tools.cross_arch_gate import record
fields = {name: (record.FP, value) for name, value in my_benchmark().items()}   # 或 record.INT
record.save_record(record.record_from_fields("loong64-<job>", fields), out_path)
```

输出是命名数字的基准都能一行接入；门不关心这些数字的物理含义。

## 3. 容差带怎么定（有理由，不是调出来的）

| 旋钮 | 默认 | 为什么必须有 |
|---|---|---|
| `fp64_rel_tol` | 1e-10 | 判「合法」跨实现差异（结合序、libm 路线、FMA 收缩）主判据 |
| `fp64_ulp_cap` | 65536 | 与相对容差**并联**使用：单独相对容差看不见近零伪一致，单独 ULP 会把抵消型诊断量判死 |
| `diag_abs_tol` | 1e-14 | 声明诊断量（O(1) 项抵消到 ~1e-17）只能用绝对容差，字段名在报告里全程披露 |
| `int_strict` | true | 整数没有舍入自由度：不等即缺陷 |

## 4. 踩过的坑（实测，别再踩）

1. **对精确 0.0 做相对扰动等于没做**：`0.0 * (1+1e-9) == 0.0`，变异自检会显示"漏检"。
   零值字段必须用**绝对**注入（变异族 `zero_abs_shift`）。
2. **相对度量在抵消型字段上无意义**：O(1e6) 项抵到 1e-17，相对差是 O(1)。这类字段必须
   声明为 `fp64_diag` 并给出绝对容差，否则门会常态化假红。
3. **`nan != nan` 会把「两边都是 NaN」误判为不一致**：要按 bit pattern 比较，并区分
   `both_nan_same_bits` / `both_nan_diff_payload` / `single_sided_nan`。
4. **`+0.0` 与 `-0.0` ULP 距离为 0 但 bit 不同**：判决必须先比 bit 再比 ULP，否则符号零的
   差异会被容差吞掉。
5. **`constraint`：`fma` 不是 `mul`+`add`**：把 FMA 收缩当成"违反 IEEE"会造出假红；它是
   **另一种运算**（一次舍入），只能进容差带。
6. **`uint64` 与 `int64` 的溢出语义差异**是整数域逐位一致的边界：换位宽就击穿判据。

## 5. 成熟化状态（诚实标注）

- loongarch64 控制面 + 集群节点：记录层已跑通（本机 `python3 -m ... run`）。
- x86_64：本单执行环境无 x86 主机、无交叉编译器/qemu/容器，**实测臂缺席**；
  `.github/workflows/cross-arch-gate-x86-arm.yml` 已在 x86_64 runner 上产出
  `x86_64-ci` 记录并与仓内提交的 loong64 参考记录对判——机制就位，首次 CI 运行后即补上实测。
- `+ - * / sqrt` 的跨 ISA 一致性由 IEEE-754 强制，门用可移植 oracle 臂逐位验证；
  libm 族与 FMA 收缩族只进容差带。

## 6. 判据（criteria.json，机器可算）

`c1` 整数逐位一致率 = 1.0 · `c2` 双精度容差带内占比 ≥ 0.95 · `c3` 超容差检出率 = 1.0
且误报率 = 0.0 · `c4` NaN 检出率 = 1.0 · `c5` 端到端时延 ≤ 300 s · `c6` 零领域语义命中 = 0。
每项都带来源工件路径；`all_pass` 是它们的与。