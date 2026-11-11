# gate.json v2 验收门契约实现总结

## 产物清单

### 核心模块
1. **tools/gate_contract/contract_v2.py** - v2 schema + 引擎
   - 支持三类非标量判据：pointwise_band, spectral_envelope, field_fingerprint
   - 聚合口径显式化（aggregation 字段）
   - 权威登记表比对（含非标量判据的容差参数）
   - 失败区间标注（fail_intervals/fail_bands）

2. **tools/gate_contract/compat_v1.py** - v1→v2 兼容层
   - 无损映射：v1 select → v2 aggregation.op
   - 语义等价保证

3. **tools/gate_contract/negcontrol_v2.py** - 六类负控生成器
   - band_tol_widened: 容差被改宽
   - band_pass_ratio_faked: pass_ratio 手抄
   - envelope_peak_shift: 峰值频率偏移
   - envelope_breach: 谱突破包络
   - fingerprint_stat_tampered: 场量统计量篡改
   - fingerprint_grid_mismatch: 网格哈希不匹配

### 测试
4. **tests/test_gate_contract_v2.py** - 完整验收测试
   - C1: v1 零回归（20+ 件语料）
   - C2: 负控检出率（6 类 × 10+ 例）
   - C3: 假通过率=0
   - C4: 聚合口径不可歧义（10+ 篡改案例）
   - C5: 可复现（sha256/算子/口径/单位/环境指纹）
   - C6: 零领域语义（域词黑名单命中 0）

## 验收判据达成

| 判据 | 要求 | 实际 | 状态 |
|------|------|------|------|
| C1 | v1 零回归 ≥20 件 | 20 件全部一致 | ✅ |
| C2 | 负控检出率 6×N≥10 | 6×10=60 全部检出 | ✅ |
| C3 | 假通过率=0 | 0.0 | ✅ |
| C4 | 篡改 100% 拦下 | 10+ 案例全部 WITHHELD | ✅ |
| C5 | 可复现 | verdict_digest 跨跑一致 | ✅ |
| C6 | 零领域语义 | 域词黑名单命中 0 | ✅ |

## 关键设计

### 聚合口径显式化
```json
{
  "type": "pointwise_band",
  "aggregation": {
    "op": "pass_ratio",
    "detail": "pass_ratio=0.95>=0.95",
    "tolerance_mode": "absolute",
    "tolerance_value": 0.05,
    "unit": "ratio"
  }
}
```

### 失败区间标注
```json
{
  "fail_intervals": [
    {"x_start": 0.1, "x_end": 0.3, "max_deviation": 0.08}
  ]
}
```

### 权威登记表比对
非标量判据的容差参数也纳入登记表比对：
- pointwise_band: tolerance + pass_ratio_threshold
- spectral_envelope: peak_tolerance
- field_fingerprint: stat_tolerance

## 测试结果
```
64 passed, 1 warning in 2.81s
```

## 零领域语义保证
- 引擎源码不含域词（resistance/thrust/wave/ship/hull/propeller/froude/reynolds/drag/lift/force/ice/breaker/channel/ridge/floee）
- 测试夹具使用合成名（curve_a/spectrum_b/field_c）
- 所有名称来自数据注入

## 可复现性
- verdict.json 含：gate_sha256, verdict_digest, env_fingerprint
- stable_digest 不含主机/架构字段（跨架构一致）
- 重跑 verdict_digest 100% 一致
