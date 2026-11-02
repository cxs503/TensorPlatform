# 验证欠账：结构域板格屈曲（structure / plate buckling）

**席位** tp-impl-8（platform 席，自找活）· **任务** m1852 · **仓** TensorPlatform · 分支 `agent/m1852`
**被测件** 船舶总体性能平台 `POST /api/v1/fea/plate-buckling`（平台源码 `backend/api/fea.py::PlateBucklingCalculator`）

## 为什么是这一项

`docs/verification_debt/` 此前覆盖阻力（wigley_michell）、推进（propeller_endpoint）、
耐波（seakeeping_fk / following_sea / prod_gap）、操纵（maneuvering_linear_benchmark）、
稳性（stability_pontoon）与跨端点闭环（chain_gate），**结构域完全空白**——而结构是
船舶总体性能八大块之一，且 `/fea/plate-buckling` 是平台“结构”门面接口。

## 结论（机器裁决，详见 REPORT.md）

| 判据 | 内容 | 实测 | 结果 |
|---|---|---|---|
| A1 | 简支单轴压缩屈曲系数 vs 精确 von Mises/Timoshenko k(α) | 最坏 **-36.00%**（α=0.5），6/10 个 α 越 3% 线 | **FAIL** |
| A2 | 简支纯剪系数 vs Timoshenko 5.34+4/α² | +0.00% | PASS |
| A3 | `critical_load` = σ·b·t（米制→kN） | **-100%**（低 6 个数量级） | **FAIL** |
| A4 | 报告的 `half_waves` 是否为所用屈曲模式 | 系数与 α 无关（常数 4.0），模式未参与 | **FAIL** |
| A5 | 固支常数 k=7.0 vs 长板渐近 6.97 | +0.43% | PASS（仅渐近一致） |
| A6 | `free`/未知边界 token 是否被拒 | 静默落到固支分支（不报错），2 点越界 | **FAIL** |

根因：`k_compression = 4.0` 硬编码（`backend/api/fea.py`），`half_waves = int(a/b+0.5)`
算出但从未参与系数；`P_cr = reported_stress*b*t/1000` 把米制几何当毫米制。
综合评级 **REAL(标度) + MOCK(压缩系数) → WITHHELD**。

## 工件

| 文件 | 作用 |
|---|---|
| `refs_plate.py` | 独立参考解：闭式 `k_ss_exact`（Timoshenko eq. 9-6）+ 正弦-Galerkin 数值互校 `k_ss_ritz`（两路一致到 1e-15）；Timoshenko 纯剪系数 + 8×8 Galerkin 独立复算（α=1 差 0.12%）；σ↔k 与 kN 换算 |
| `audit_plate_buckling.py` | 审计器：10 个 aspect ratio × 压缩/纯剪，加标度律、临界载荷量纲、边界 token、兄弟端点（`/fea/stiffened-panel`、`/structure/plate`）检查；程序化生成 `REPORT.md` 与三图 |
| `numbers.json` | 全部原始数字（报告表内数字由此读回，零手抄） |
| `figs/fig1_k_vs_alpha.png` | k(α)：精确曲线 + ±3% 带 + 平台点 |
| `figs/fig2_error.png` | 平台 k 的带符号相对误差（负=不安全） |
| `figs/fig3_critical_load.png` | critical_load 与应得值（对数轴） |
| `REPORT.md` | 论文级报告（六要素） |
| `tests/ship/test_structure_plate_buckling.py` | 30 passed / 8 xfailed（缺陷闸门；修复后转 XPASS） |

## 复现

```bash
python3 docs/verification_debt/structure_plate_buckling/audit_plate_buckling.py   # 需 :8001 在线
python3 -m pytest tests/ship/test_structure_plate_buckling.py -q                   # 离线部分恒绿
```

## 留痕

* 平台报告 **#1611**（`scenario_key=ship_structure_plate_buckling_verification`）
* 提交 `55f85638`（工件）· `f726ac93`（派单指针）
* 修复派单 **mission 1894**（`runner=native`, `repo=ship-performance-platform`）
* 局限：只审弹性屈曲；固支分支仅与长板渐近 6.97 比对（α<1.4 需 aspect-ratio 表，未引入）；
  尝试过的固支 Rayleigh-Ritz 参考解在 α>4 收敛性未确立，已弃用且不参与任何判定。