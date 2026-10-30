# PB-004 海洋 z-up 数据进 y-up 场景（模型侧躺 90°）

**症状**：加载官方/参数化船体后整船侧躺；或 A/B 渲染证明图两张「都是立的」。

**根因**：船舶数据链全线海洋系 z-up（SectionProfile z≤0 为水下、position=(x, 半宽y, 垂向z)、
测量标签 ΔY半宽 ΔZ垂向）；THREE 默认 y-up（无 camera.up 覆盖 + GridHelper XZ + 俯视从 +y）。

**治愈**：场景统一 z-up——camera.up=(0,0,1)、grid.rotation.x=π/2（水面 XY, z=0）、四视图/初始取景对准
包围盒、官方网格平移落座（中心归原点、龙骨贴 z=0）+ 截面叠加 group.position 同步偏置并在 create/clear 复位。

**勿重蹈**：matplotlib 3D 默认 z-up——拿它模拟 y-up 渲染必须换轴 (X,Z,Y)，否则 A/B 证明无效。
数值判据范例：平移后中心 (0,0,·)、keel minZ=0.000、相机俯角 28°、屏幕竖直=垂向 19.5m（侧躺时=半宽 20.5m）。
