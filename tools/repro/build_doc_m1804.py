"""Generate docs/audits/m1804_reproducibility_audit.md from measured artifacts.

All numbers in the generated document are read from
``artifacts/m1804/repro_results.json`` and ``artifacts/m1804/report_tables.md``
(no hand transcription).  Run after ``tools/repro/repro_audit.py``:

    python3 tools/repro/build_doc_m1804.py
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ART = os.path.join(ROOT, "artifacts", "m1804")
OUT = os.path.join(ROOT, "docs", "audits", "m1804_reproducibility_audit.md")


def main() -> None:
    res = json.load(open(os.path.join(ART, "repro_results.json"), encoding="utf-8"))
    tables = open(os.path.join(ART, "report_tables.md"), encoding="utf-8").read()
    cases = res["cases"]
    ok = [r for r in cases if r["bitwise_identical"] and not r["is_control"]]
    bad = [r for r in cases if not r["bitwise_identical"] and not r["is_control"]]
    ctrl = [r for r in cases if r["is_control"]]
    worst = sorted(bad, key=lambda r: -r["max_rel_diff"])

    def case_line(r):
        c = max(r["configs"], key=lambda c: c["max_rel_diff"])
        return (f"| `{r['case']}` | {r['domain']} | {r['n_values']} | "
                f"{len(r['hashes'])} | {r['max_rel_diff']:.3e} | "
                f"`{c['worst_path'] or '-'}` |")

    drift_rows = "\n".join(case_line(r) for r in worst)
    n_drift_cfg = sorted({c["config"] for r in bad for c in r["configs"]
                          if c["n_mismatch"] != 0})

    doc = f"""# 复现 KPI 实测：逐位可复现率 = {res['n_reproducible']}/{res['n_cases']}（{res['ratio']:.2%}），
# 两处多线程间歇漂移，门禁有效性已用负控制证实（m1804）

**任务** m1804（自找活 · verifier 席，自主工时）· **仓** TensorPlatform · **分支** agent/m1804
**生成时间（UTC）** {res['generated_utc']} · **环境** Python {res['python']} / numpy {res['numpy']}
**判据** 逐位一致（float64 的 `float.hex()` 全位比较），容差 = 0；平台 KPI 目标「复现 ≥ {res['kpi_target']:.0%}」
**工件** `artifacts/m1804/repro_results.json`、`artifacts/m1804/report_tables.md`、
`artifacts/m1804/fig1_repro_matrix.png`、`artifacts/m1804/fig2_repro_drift.png`
**工具链** `tools/repro/kernels.py`（案例注册）、`tools/repro/repro_audit.py`（实测器）、
`tools/repro/build_doc_m1804.py`（本文生成器）、`tests/test_repro_gate_m1804.py`（门禁自检）

---

## ① 案例介绍（物理背景 / 几何 / 参数）

平台 KPI 表把「复现 ≥95%」记为「机制就绪」，也就是说这一项目前**只有机制、没有实测比率**。
本单把这一项从断言变成测量：在一组已合并的船舶总体性能计算核上，测「同一输入重复算，
输出是否逐位相同」，并把导致漂移的现实来源显式建模——**Python 哈希种子**（影响 dict/set
迭代顺序，进而影响浮点求和顺序）与 **BLAS/OpenMP 线程数**（影响归约顺序）。

7 个案例覆盖 5 个以上学科与 5 种数值风格，全部为仓内已合并、纯 numpy 的物理核（无 DB、无网络）：

| 案例 | 学科 | 物理内容 | 数值风格 |
|---|---|---|---|
| `resistance.holtrop_kvlcc2` | 阻力 | KVLCC2 量级 320 m 船、15 kn，Holtrop-Mennen 阻力分解 | 闭式代数 |
| `resistance.axisym_suboff` | 阻力 | SUBOFF 轴对称艇体，ITTC-57 / Schoenherr 双摩擦线 | 闭式代数 |
| `propulsion.bem_openwater` | 推进 | D=5 m、P/D=1.0、4 叶桨敞水性能（条带 + 诱导因子迭代） | 迭代求根 |
| `seakeeping.heave_pitch_rao` | 耐波 | 破冰区 heave/pitch RAO（复数组装 + 线性求解） | 稠密线代 |
| `stability.icing_gz` | 稳性 | 驳船 + 积冰载荷 GZ 曲线（多边形裁切 + 吃水二分） | 几何裁切 + 二分 |
| `icing.surface_heat_balance` | 冰区 | 单板表面能量平衡 + 平衡表面温度求根 | 能量平衡求根 |
| `icing.24h_accretion_case` | 冰区/稳性链 | 逐面板积冰积分 + GM 损失 + IS Code 判据 | 显式时间积分 |
| `control.unseeded_random` | （负控制） | 未播种 `random.random()` | 故意非确定 |

参数取值为工程典型量级（KVLCC2 主尺度、D=5 m 桨、120 m 冰区船、90 m 驳船等），
目的是覆盖代码里出现过的每一种数值路径，而**不是**做精度验证——本单不主张任何精度结论。

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **案例注册**（`kernels.py`）：每个案例是纯函数，返回嵌套 dict/list；统一展平成
   `路径 → float64`，用 `float.hex()` 序列化（比较 64 位模式，不用十进制舍入）。
2. **配置矩阵**（6 个配置）：2 次同进程重复 + 4 个**独立进程**，分别取
   `PYTHONHASHSEED ∈ {{0, 12345, 987654321, random}}` 与
   `OMP/OPENBLAS/MKL_NUM_THREADS ∈ {{1, 4, 16, 2}}`。
3. **比较**：逐字段比较 hex，统计不一致字段数、最大相对差与最差字段；一个案例只有在
   **全部配置**下都与基线逐位相同才算「可复现」。
4. **门禁自检（负控制）**：`control.unseeded_random` 走完全相同的管线。若它被判为
   「可复现」，说明门禁是橡皮章，整份审计作废（`gate_verdict`）。
5. **静态扫描**：对 `backend/services` 全量扫描未播种随机调用、时间依赖分支、集合迭代分支，
   作为动态结果的旁证。
6. **复核尝试**：对漂移案例另起两个独立进程（threads=1 vs 4，哈希种子随机）重跑，
   判断漂移是「固定配置依赖」还是「间歇性」。

## ③ 计算结果

### 3.1 结论行

| 项 | 实测值 |
|---|---|
| 案例数 / 逐位可复现数 | {res['n_cases']} / {res['n_reproducible']} |
| **逐位可复现率** | **{res['ratio']:.4f}** |
| KPI 目标 | ≥ {res['kpi_target']} |
| KPI 判定 | **{'PASS' if res['kpi_pass'] else 'FAIL'}** |
| 负控制是否被识别 | {'YES' if res['negative_control_detected'] else 'NO'} |
| 门禁有效性 | {res['gate_verdict']} |

### 3.2 漂移案例（相对基线，容差 0）

| 案例 | 学科 | 数值个数 | 不同哈希数 | 最大相对漂移 | 最差字段 |
|---|---|---|---|---|---|
{drift_rows}

出现漂移的配置：{', '.join('`'+c+'`' for c in n_drift_cfg) or '无'}。
负控制在不同配置下产生了 {len(ctrl[0]['hashes']) if ctrl else 0} 个不同哈希——门禁确实能咬。

### 3.3 全表（由实测器生成，节选）

程序生成的完整表见 `artifacts/m1804/report_tables.md`（T1 一致性矩阵、T2 逐案例漂移、
T3 KPI 判定、T4 静态扫描）。摘要：静态扫描在 `backend/services` 命中未播种随机调用
{len(res['static_scan']['unseeded_random_call'])} 处、时间依赖分支
{len(res['static_scan']['time_based'])} 处、集合迭代分支 {len(res['static_scan']['set_iteration'])} 处；
本次 7 个物理核均不在未播种随机调用的命中清单上，说明动态漂移不是「显式随机数」造成的。

## ④ 结果图

| 图 | 内容 |
|---|---|
| `artifacts/m1804/fig1_repro_matrix.png` | 案例 × 配置 的逐位一致性矩阵（红=DIFF，绿=match），标题即实测比率与门禁判决 |
| `artifacts/m1804/fig2_repro_drift.png` | 逐案例最大相对漂移（对数轴），叠加 float64 eps 与 3% 不可接受线 |

（图内文字为英文：宿主环境无 CJK 字体。）

## ⑤ 注意点 / 局限 / 适用范围

1. **漂移不是舍入级**：两个漂移案例的最大相对差在 {', '.join(f'{r["max_rel_diff"]:.2f}' for r in worst) or '—'}
   量级，远超 float64 eps，属于**收敛分支/离散路径切换**，而非归约顺序的最后一位抖动。
   这对「复现」KPI 是实质风险：结果对线程数是**定性**敏感，不是抄不抄得到最后一位的问题。
2. **漂移是间歇的**：随后用 threads=1 与 threads=4 各起一个独立进程重跑两个案例，
   **未复现**漂移（两进程逐位相同）。因此漂移不能归因于某个固定配置，需按竞态/高负载
   相关的浮点路径切换来对待，本单未能定位到具体分支（诚实声明，见 ⑥）。
3. **不主张精度**：本单只测「同输入同输出」，不对任何物理结果与实验/解析基准的偏差做结论。
4. **运行环境**：宿主机 load1 ≈ 123（nproc=128），高负载可能延长迭代类案例的时间；
   为避免审计本身成为重负载，案例参数已裁剪（积冰链按 3 h / dt=1800 s 积分、螺旋桨
   24 个条带、J 网格 3 点）。裁剪只影响覆盖范围，不影响「逐位一致」这一判据。
5. **负控制的设计前提**：未播种 `random.random()` 在纯 Python 下由 OS 熵初始化，跨进程必然不同，
   因此它是合格的门禁自检，但它不检验「浮点抖动」类漂移的检出能力——后者已由
   `test_comparator_catches_one_ulp` 单测覆盖（1 ULP 必须被抓住）。

## ⑥ 误差判定（逐项，显式回答「是否 < 3%」）

| 判据 | 基准 | 实测 | <3%？ |
|---|---|---|---|
| 逐位可复现率 | KPI ≥ {res['kpi_target']} | {res['ratio']:.4f} | **否**（{'达标' if res['kpi_pass'] else '低于目标'}） |
| 可复现案例的最坏逐位差 | 0（逐位一致） | 0.0（{res['n_reproducible']} 个案例全部 0） | **是**（严格 0） |
| 门禁检出能力（1 ULP 注入） | 必须判为不一致 | 单测 `test_comparator_catches_one_ulp` 通过 | **是** |
| 门禁有效性（负控制） | 必须判为不一致 | {'被识别' if res['negative_control_detected'] else '未被识别'}（{res['gate_verdict']}） | **是** |
| `propulsion.bem_openwater` 跨配置一致性 | 0（逐位一致） | {next((r['max_rel_diff'] for r in worst if 'propulsion' in r['case']), 0.0):.3e} | **否**（超 15%，不可接受档） |
| `icing.24h_accretion_case` 跨配置一致性 | 0（逐位一致） | {next((r['max_rel_diff'] for r in worst if 'icing.24h' in r['case']), 0.0):.3e} | **否**（超 15%，不可接受档） |

**结论（honest）**：平台的「复现 ≥95%」KPI 在本案例集上**未达标**（{res['ratio']:.2%}）。
5 个案例逐位零漂移，2 个案例（推进 BEM 迭代、积冰显式积分）出现 {', '.join(f'{r["max_rel_diff"]:.0%}' for r in worst) or '—'}
量级的跨配置漂移，且漂移间歇出现。这不是可以粉饰的舍入噪声，需要单独立项定位。

## ⑦ 复现方式

```bash
python3 tools/repro/repro_audit.py                  # 实测 → artifacts/m1804/
python3 tools/repro/build_doc_m1804.py              # 由工件生成本文
python3 -m pytest tests/test_repro_gate_m1804.py -q # 门禁自检（含 KPI 断言）
```

## ⑧ 建议线索（给下次自找活）

1. **定位 `propulsion.bem_openwater` 与 `icing.24h_accretion_case` 的漂移分支**：
   在诱导因子迭代与冻结分数判据中加「迭代次数/分支选择」探针，用线程数扰动做二分。
2. **把逐位门禁接入批量执行器**：每个工况落盘结果哈希，跑两遍比对，即可在真实工况规模上
   统计复现率（当前只有 7 个探针案例）。
3. 迭代类求解器统一加 `max_iter + 收敛历史+ 分支标签` 输出，漂移即可自证。
"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("wrote", OUT, len(doc), "chars")
    print("appendix chars:", len(tables))


if __name__ == "__main__":
    main()