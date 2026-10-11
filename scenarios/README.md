# 开放底座确定性行业场景包

本目录用于基础环境阶段的离线功能验证。生成数据均为合成测试数据，不访问数据库、消息队列、对象存储或生产设备，不得作为工艺决策、节能收益或模型精度证明。

## 统一约定

- `manifest.schema.json`：场景清单的机器可读规范。
- `<scenario>/manifest.json`：字段、单位、范围、角色、固定种子和适用限制。
- `<scenario>/generator.py`：满足基本工艺约束的确定性生成逻辑。
- `<scenario>/data_card.md`：数据卡和使用边界。
- 三种模式：`baseline` 为正常工况，`drift` 为渐进分布变化，`fault` 为已知异常注入。
- 每个场景至少包含一个 `ground_truth` 字段，可直接验证漂移或异常检测结果。
- 默认只在内存生成清单定义的小样本（96—240 行）；CLI 只有传入 `--output` 才会写文件，且最多 10,000 行。输出仅允许位于 `/tmp` 或 `/srv/tensor/operator-b/foundation-validation-runs` 等审核过的验证根目录，拒绝生产上传路径。

## 使用

在仓库根目录运行：

```bash
python3 -m scenarios sintering_endpoint_temperature --mode drift --rows 48
python3 -m scenarios photovoltaic_power --mode fault --rows 48 --output /tmp/foundation-pv-fault.csv
python3 -m unittest discover -s scenarios/tests -v
```

相同的场景 ID、模式、种子和行数会得到相同内容指纹。需要覆盖默认种子时使用 `--seed`；这不会修改场景清单。

## 首批场景

| 场景 | 目标 | 已知异常 |
|---|---|---|
| `sintering_endpoint_temperature` | 生产过程终点温度 | 热工偏移、风箱泄漏 |
| `sintering_feo` | FeO 含量 | 原燃料漂移、工艺参数优化失配 |
| `dust_collection_efficiency` | 除尘风机比能耗 | 阻力增长、滤袋堵塞 |
| `plant_load_forecast` | 全厂电力负荷 | 需求结构漂移、负荷尖峰 |
| `photovoltaic_power` | 光伏有功功率 | 季节漂移、逆变器降额 |
| `rotating_equipment_fault` | 旋转设备剩余寿命 | 渐进退化、轴承故障 |

## 晋级用途

场景包可作为 Demo → Beta 的证据之一，用来验证字段契约、数据治理、训练、部署、漂移、告警和清理闭环。它不能替代真实现场的数据分布、长稳、模型效果、节能率及安全联锁验收。
