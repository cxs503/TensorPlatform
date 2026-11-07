# domains/ice_fracture_contact —— 冰层断裂与接触（领域包）

本目录是 **领域目录**：物理内容（冰的接触/粘结/断裂模型、文献基准、算例、角色知识包）
全部在这里，通过配置与工件注入；`tools/abm/` 只提供与领域无关的通用机制（五角色席位、
七环过门、预测盲、六维对照、验收聚合、哈希链留痕），**不含任何域词条**（TensorPlatform 铁律）。

## 目录

| 文件 | 作用 |
|---|---|
| `dem_bpm.py` | 二维粘结颗粒离散元：接触（线性弹簧+黏性阻尼+tanh 滑移库仑摩擦）、平行粘结（法/切/弯，梁类比）、断裂准则、近邻表、稳定步长估计、Love–Weber 应力均匀化 |
| `benchmarks.py` | 经典算例：单键刚度、仿射格弹性解析校验、单轴压缩、直接拉伸、巴西劈裂、三点弯、Weibull 尺寸效应、弹性/强度标定 |
| `control_arm.py` | 对照组（既有冰区集团军常规模式的**重建**基线：一次性实现/无标定/无盲预测/无独立复算） |
| `run_abm.py` | 实验组全流程驱动器：五角色×七环 + 预测盲 + 六维 + 三腿闭合 → `artifacts/IN59-P2/` |
| `make_report.py` | 出图 + 报告载荷 + 发布到平台报告面（六要素） |
| `tests/test_ice_dem.py` | 实现腿验证：单键、格弹性、动量/能量守恒、稳定步长边界、Weibull 统计、直接拉伸方向 |

## 关键物理结论（详见 `docs/IN59-P2_ice_fracture_contact_abm.md`）

- 理论↔实现：中心力三角格 E 相对解析式 (2/√3)k/t 偏 -3.5%；ν 一致性（σ_yy/σ_xx）达 5e-7；
  单键刚度到机器精度（3e-11）。
- 实验腿：ν=0.301 vs 格理论 0.329（8.4%）；直接拉伸 σt=0.81 MPa（文献 0.7–1.5）；
  **UCS=2.26 MPa 低于文献区间 3–7 MPa（honest FAIL）**；三尺寸未见 Weibull 尺寸效应（m 不可辨识）。
- 稳定性：Δt 安全系数 0.2 会因切向/转动模态自反馈发散，0.1 稳定 —— 已写入步长估计与测试。

## 复现

```bash
python3 domains/ice_fracture_contact/run_abm.py                 # ~70 s，落 artifacts/IN59-P2/
python3 domains/ice_fracture_contact/make_report.py --publish    # 出图 + 发布报告
pytest tests/abm domains/ice_fracture_contact/tests -q           # 19 passed
```

## 已知局限

- 二维切片（t=0.05 m）不能表达海冰板三维弯曲与碎块尺寸分布；
- 质量缩放 β=3600 放大惯性，巴西劈裂/无粘结对照的峰值受惯性污染（已在报告中披露，不作强度判据）；
- 断键后摩擦为 tanh 滑移正则，无静摩擦累积，残余强度偏保守。