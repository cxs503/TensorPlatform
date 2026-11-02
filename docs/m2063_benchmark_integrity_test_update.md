# m2063 基准数据集完整性测试更新报告

## 背景

`tests/test_ship_benchmark_integrity.py` 是平台基准验证的回归测试，覆盖三处历史缺陷的修复：
1. 基准验证端点跨服务 HTTP 调用契约不匹配（calc_ct 恒为 0）
2. 几何预置与数据集单位不一致（DTMB-5415 排水量 466.3「t」实为 kg）
3. 参考数据集从未机器校验

## 问题发现

运行测试发现 `test_dtmb5415_block_rejected_for_scale_mixing` 失败：
- 测试期望 DTMB-5415 的 fn_identity 检查 FAIL（因尺度混用）
- 实际结果为 PASS

## 根因分析

基准参考数据集 `backend/data/benchmark_reference.json` 已被修正：

### DTMB-5415 修正
- **旧数据**：speed_ms=2.572（实船服务航速）配 L=5.72 m（模型尺度），Fn 偏差 >80%
- **新数据**：speed_ms 改为由 Fr 与 L 反算的条件值（0.4569 m/s @ Fr=0.061），自洽性恢复
- fn_identity 检查现通过（rel_dev < 2%）

### SUBOFF 修正
- **旧数据**：缺 wetted_surface_m2 字段，Rt=12.5 N @3 m/s 反算 Ct=5e-4 < Cf=2.9e-3（低于摩擦下界）
- **新数据**：补充 wetted_surface_m2=5.42，Rt 列修正为物理合理值（92.52-428.49 N）
- rt_friction_floor 检查现通过

### KVLCC2 修正
- **旧数据**：cb_identity 有警告（排水量与 Cb·L·B·T 偏差 >2%）
- **新数据**：排水量修正，cb_identity 通过（rel_dev=0.0）

## 测试更新

更新 `tests/test_ship_benchmark_integrity.py` 以反映修正后的数据状态：

1. **test_dtmb5415_fn_identity_passes_after_correction**：验证 fn_identity 通过
2. **test_dtmb5415_ct_admitted_with_cb_warning**：验证 DTMB-5415 准入 CT 通道（cb_identity 仍有警告）
3. **test_suboff_rt_friction_floor_passes_after_correction**：验证 rt_friction_floor 通过
4. **test_kvlcc2_admitted_with_block_coefficient_warning**：更新为 cb_identity 通过
5. **test_admission_counts**：全 12 算例准入 CT/RT 通道（无 critical 失败）

## 验证结果

```bash
$ python3 -m pytest tests/test_ship_benchmark_integrity.py -v
======================== 12 passed, 1 warning in 0.17s =========================

$ python3 -m pytest tests/ship/ tests/test_ship_verification.py tests/test_ship_calc_verification.py tests/test_ship_benchmark_integrity.py -v
======================== 85 passed, 1 warning in 0.35s =========================
```

## 结论

数据集修正后，平台基准验证的完整性裁定逻辑正确：
- 全 12 算例（DTMB-5415 × 4 + KVLCC2 × 4 + SUBOFF × 4）准入 CT/RT 通道
- SUBOFF 的 ct_rt_identity 有警告（Ct 与 Rt 不同源），但不阻断准入
- DTMB-5415 的 cb_identity 有警告（排水量派生值与记录值差 16%），但不阻断准入

测试已更新以反映当前数据状态，所有 85 项船舶相关测试通过。

## 给下次自找活的建议

1. **验证欠账类任务优先级最高**：本单展示了数据集修正后测试未同步更新的问题，这类"验证欠账"容易发现且修复价值高
2. **关注 WITHHELD 状态**：数据集仍有部分字段标记为 WITHHELD（如 DTMB-5415 的 Rt 列），可考虑补充实验数据或文献值
3. **船-桨配合验证**：propeller.py 的 BEMT 模块已有解析锚（致动盘上界），但缺少与 Wageningen B 系列图谱的系统对比
4. **耐波性辐射阻尼**：seakeeping.py 的 EVIDENCE['radiation_damping']=WITHHELD，实现 2D 自由液面 Green 函数可提升耐波性判据覆盖
