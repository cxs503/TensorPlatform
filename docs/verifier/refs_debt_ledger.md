# 参考值一致性欠账台账（自动生成，勿手改）

- 任务：m1396　审计器：`tools/refs/audit.py`
- 语料：`tools/refs/corpus.json`，17 条（分级 {'REAL': 16, 'WITHHELD': 1}）
- 受审对象：`backend/data/benchmark_reference.json`（12 个工况）；几何预设 ['DTMB-5415', 'KVLCC2', 'SUBOFF']
- 门限：{"froude_tol_rel": 0.01, "cb_tol_rel": 0.02, "wetted_surface_tol_rel": 0.05, "water_density_range": [990.0, 1035.0], "g": 9.80665}

**总判定：FAIL** — 参考数据/公式存在内部矛盾：下游 PASS/FAIL 判决不可信（见 findings）

计数：FAIL=21　WARN=13　INFO=24

| 门 | FAIL | WARN | INFO |
|---|---|---|---|
| G1 Froude 自洽（Fr=V/√gL） | 4 | 0 | 8 |
| G2 Ct–Rt–ρ 自洽（反解隐含水密度） | 4 | 8 | 0 |
| G3 Cb–排水量–L·B·T 自洽 | 8 | 4 | 0 |
| G4a 溯源引用完整性 | 0 | 0 | 12 |
| G4b 几何预设跨字段污染 | 1 | 0 | 2 |
| G5a 平台 ITTC-57 Cf vs 解析基准（同 Re） | 0 | 0 | 1 |
| G5b 平台 wall-sided GZ vs 解析基准 | 0 | 0 | 1 |
| G5c 平台湿表面积式 vs 基准数据集 S | 4 | 0 | 0 |
| G5d 平台自报 Fr vs 定义式 | 0 | 1 | 0 |

## FAIL 明细（参考记录内部矛盾，下游判决不可信）

| 门 | 案例 | 量 | 声明值 | 计算/反解值 | 相对残差 | 说明 |
|---|---|---|---|---|---|---|
| G1 | DTMB5415-01 | froude_number | 0.061 | 0.34341 | 4.63 | Fr=V/√(gL) 与声明值不符：按 L=5.72 m 应为 0.343，声明 0.061（残差 463.0%）；声明值隐含 L≈181.3 m，说明该条记录把不同尺度的航速与长度写在了一起 |
| G1 | DTMB5415-02 | froude_number | 0.122 | 0.686819 | 4.63 | Fr=V/√(gL) 与声明值不符：按 L=5.72 m 应为 0.687，声明 0.122（残差 463.0%）；声明值隐含 L≈181.3 m，说明该条记录把不同尺度的航速与长度写在了一起 |
| G1 | DTMB5415-03 | froude_number | 0.244 | 1.37377 | 4.63 | Fr=V/√(gL) 与声明值不符：按 L=5.72 m 应为 1.374，声明 0.244（残差 463.0%）；声明值隐含 L≈181.3 m，说明该条记录把不同尺度的航速与长度写在了一起 |
| G1 | DTMB5415-04 | froude_number | 0.366 | 2.06059 | 4.63 | Fr=V/√(gL) 与声明值不符：按 L=5.72 m 应为 2.061，声明 0.366（残差 463.0%）；声明值隐含 L≈181.3 m，说明该条记录把不同尺度的航速与长度写在了一起 |
| G2 | SUBOFF-01 | implied_density | None | 134.87 | — | Rt=12.5 N 与 Ct=0.0038 在 S=5.42 m²、V=3.0 m/s 下隐含 ρ=134.9 kg/m³，超出水的物理区间 (990.0, 1035.0)（0.13× 海水）；两者必有一错，或 Ct 用了别的参考面积口径 |
| G2 | SUBOFF-02 | implied_density | None | 121.08 | — | Rt=21.0 N 与 Ct=0.004 在 S=5.42 m²、V=4.0 m/s 下隐含 ρ=121.1 kg/m³，超出水的物理区间 (990.0, 1035.0)（0.12× 海水）；两者必有一错，或 Ct 用了别的参考面积口径 |
| G2 | SUBOFF-03 | implied_density | None | 110.7 | — | Rt=31.5 N 与 Ct=0.0042 在 S=5.42 m²、V=5.0 m/s 下隐含 ρ=110.7 kg/m³，超出水的物理区间 (990.0, 1035.0)（0.11× 海水）；两者必有一错，或 Ct 用了别的参考面积口径 |
| G2 | SUBOFF-04 | implied_density | None | 104.83 | — | Rt=45.0 N 与 Ct=0.0044 在 S=5.42 m²、V=6.0 m/s 下隐含 ρ=104.8 kg/m³，超出水的物理区间 (990.0, 1035.0)（0.10× 海水）；两者必有一错，或 Ct 用了别的参考面积口径 |
| G3 | DTMB5415-01 | cb | 0.508 | 0.426 | 0.1615 | 声明 Cb=0.508 与排水量 466 kg、L·B·T=1.07 m³ 矛盾：ρ=1025 反解得 Cb=0.4260（淡水下 0.4374），残差 16.1% |
| G3 | DTMB5415-02 | cb | 0.508 | 0.426 | 0.1615 | 声明 Cb=0.508 与排水量 466 kg、L·B·T=1.07 m³ 矛盾：ρ=1025 反解得 Cb=0.4260（淡水下 0.4374），残差 16.1% |
| G3 | DTMB5415-03 | cb | 0.508 | 0.426 | 0.1615 | 声明 Cb=0.508 与排水量 466 kg、L·B·T=1.07 m³ 矛盾：ρ=1025 反解得 Cb=0.4260（淡水下 0.4374），残差 16.1% |
| G3 | DTMB5415-04 | cb | 0.508 | 0.426 | 0.1615 | 声明 Cb=0.508 与排水量 466 kg、L·B·T=1.07 m³ 矛盾：ρ=1025 反解得 Cb=0.4260（淡水下 0.4374），残差 16.1% |
| G3 | KVLCC2-01 | cb | 0.8098 | 0.7758 | 0.04193 | 声明 Cb=0.8098 与排水量 307000000 kg、L·B·T=386048.00 m³ 矛盾：ρ=1025 反解得 Cb=0.7758（淡水下 0.7967），残差 4.2% |
| G3 | KVLCC2-02 | cb | 0.8098 | 0.7758 | 0.04193 | 声明 Cb=0.8098 与排水量 307000000 kg、L·B·T=386048.00 m³ 矛盾：ρ=1025 反解得 Cb=0.7758（淡水下 0.7967），残差 4.2% |
| G3 | KVLCC2-03 | cb | 0.8098 | 0.7758 | 0.04193 | 声明 Cb=0.8098 与排水量 307000000 kg、L·B·T=386048.00 m³ 矛盾：ρ=1025 反解得 Cb=0.7758（淡水下 0.7967），残差 4.2% |
| G3 | KVLCC2-04 | cb | 0.8098 | 0.7758 | 0.04193 | 声明 Cb=0.8098 与排水量 307000000 kg、L·B·T=386048.00 m³ 矛盾：ρ=1025 反解得 Cb=0.7758（淡水下 0.7967），残差 4.2% |
| G4b | SUBOFF | geometry_preset | {'length': 4.356, 'beam': 0.508, 'draft' | None | — | SHIP_GEOMETRY['SUBOFF'] 跨字段同值（复制粘贴污染）：0.508 → ['beam', 'draft', 'displacement', 'cb']；主尺度/排水量/棱形系数不可能同值 |
| G5c | SUBOFF-01 | wetted_surface_area | 5.42 | 4.3588 | 0.1958 | 平台 S=πBL(0.5+0.25Cb)=4.359 m² vs 数据集 S=5.42 m²，偏差 19.6%（Cb 取自 SHIP_GEOMETRY['SUBOFF'].cb（预设，见 G4b）；Rf∝S，误差线性传入阻力） |
| G5c | SUBOFF-02 | wetted_surface_area | 5.42 | 4.3588 | 0.1958 | 平台 S=πBL(0.5+0.25Cb)=4.359 m² vs 数据集 S=5.42 m²，偏差 19.6%（Cb 取自 SHIP_GEOMETRY['SUBOFF'].cb（预设，见 G4b）；Rf∝S，误差线性传入阻力） |
| G5c | SUBOFF-03 | wetted_surface_area | 5.42 | 4.3588 | 0.1958 | 平台 S=πBL(0.5+0.25Cb)=4.359 m² vs 数据集 S=5.42 m²，偏差 19.6%（Cb 取自 SHIP_GEOMETRY['SUBOFF'].cb（预设，见 G4b）；Rf∝S，误差线性传入阻力） |
| G5c | SUBOFF-04 | wetted_surface_area | 5.42 | 4.3588 | 0.1958 | 平台 S=πBL(0.5+0.25Cb)=4.359 m² vs 数据集 S=5.42 m²，偏差 19.6%（Cb 取自 SHIP_GEOMETRY['SUBOFF'].cb（预设，见 G4b）；Rf∝S，误差线性传入阻力） |

## WARN 明细（证据不足，不可升 REAL）

| 门 | 案例 | 量 | 说明 |
|---|---|---|---|
| G2 | DTMB5415-01 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | DTMB5415-02 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | DTMB5415-03 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | DTMB5415-04 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | KVLCC2-01 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | KVLCC2-02 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | KVLCC2-03 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G2 | KVLCC2-04 | resistance_decomposition | 缺 Ct/Rt/V/湿表面积之一，无法校验 Rt 与 Ct 的对应关系 |
| G3 | SUBOFF-01 | cb_geometry | 缺 L/B/T/Cb 之一：几何未验证，不得进入阻力口径 |
| G3 | SUBOFF-02 | cb_geometry | 缺 L/B/T/Cb 之一：几何未验证，不得进入阻力口径 |
| G3 | SUBOFF-03 | cb_geometry | 缺 L/B/T/Cb 之一：几何未验证，不得进入阻力口径 |
| G3 | SUBOFF-04 | cb_geometry | 缺 L/B/T/Cb 之一：几何未验证，不得进入阻力口径 |
| G5d | platform:calc_resistance | froude_number | 平台自报 Fr=0.458926 vs 定义式 0.459005，偏差 1.7e-04——源于 g 取 9.81 而非 9.80665 的常量口径漂移，远小于工程容差但常量来源应统一 |

## 建议处置（按门聚合）

- **G1 Froude 自洽（Fr=V/√gL）**：该记录不可作 Fr 相关判据基准；须统一尺度后重算航速或显式标注 scale
- **G2 Ct–Rt–ρ 自洽（反解隐含水密度）**：修数或显式声明 Ct 的参考面积/密度口径，否则 benchmark 判决无效
- **G2 Ct–Rt–ρ 自洽（反解隐含水密度）**：补湿表面积，否则 Ct 不可复核（判决不可采信）
- **G3 Cb–排水量–L·B·T 自洽**：补齐主尺度，或标注为非排水型体（并按体积系数另行定义）
- **G3 Cb–排水量–L·B·T 自洽**：几何必须先过 Cb/L/B 自洽门再谈阻力误差判据；修数须引母型船资料
- **G4b 几何预设跨字段污染**：逐字段按母型船资料填，否则阻力/排水量口径全链路失真
- **G5c 平台湿表面积式 vs 基准数据集 S**：S 的拟合系数 (0.5+0.25Cb) 无文献出处，且与站内基准不自洽：须以母型船型线或 ITTC 推荐式重锚，或声明 WITHHELD

> 修数纪律：本台账只报矛盾、不猜哪个数对。修参考值必须引母型船资料/文献页表，并在 `tools/refs/corpus.json` 里留下可机器复核的溯源字段；修完重跑 `python3 -m tools.refs.audit` 与本台账。
