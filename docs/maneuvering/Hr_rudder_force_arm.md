# Hr（舵力臂）物理意义与使用指南

## 定义

**Hr**（Rudder Force Arm / 舵力臂）是 MMG 操纵性数学模型中的关键几何参数，
定义为**舵压力中心至船舶重心的纵向距离**，单位：米（m）。

## 正负号约定

在 MMG 标准船体坐标系中（原点取在船舶重心，x 轴指向船首，y 轴指向右舷）：

- **Hr > 0**：舵压力中心在重心**之后**（aft of CG）
- **Hr < 0**：舵压力中心在重心**之前**（forward of CG）—— 非常规布置

对于常规船型，舵位于船尾，因此 **Hr 恒为正值**。

## 物理意义

Hr 决定了舵力产生的**转艏力矩臂长**：

```
N_R = -x_R · F_N · cos(δ)
```

其中：
- `N_R` 为舵产生的转艏力矩（N·m）
- `x_R` 为舵的纵向位置（船体坐标系，x_R = -Hr）
- `F_N` 为舵法向力（N）
- `δ` 为舵角（rad）

**Hr 越大** → 力臂越长 → 相同舵力产生的转艏力矩越大 → 回转直径越小、航向稳定性越好。

## 典型取值

| 船型 | Hr/Lpp 范围 | 说明 |
|------|-------------|------|
| 油轮（VLCC） | 0.45 ~ 0.50 | 舵位于船尾，距重心约半船长 |
| 集装箱船 | 0.47 ~ 0.52 | 船型瘦长，舵位置相对靠后 |
| 散货船 | 0.45 ~ 0.50 | 与油轮类似 |
| 客船/渡轮 | 0.40 ~ 0.48 | 上层建筑重，重心靠后 |

**经验公式**：`Hr ≈ (0.45 ~ 0.55) × Lpp`

## 与相关参数的关系

### 舵面积 Ar

舵面积与舵力臂共同决定舵力大小：
```
F_N = 0.5 · ρ · Ar · f_α · U_R² · sin(α_R)
```

其中展弦比 `Λ = Hr² / Ar` 影响升力梯度 `f_α`。

### 舵位置 x_R

在船体坐标系中：
```
x_R = -Hr  （舵在重心后）
```

MMG 模型中常用无量纲形式 `x_R' = x_R / L = -Hr / L`。

## 在 API 中的使用

### 端点

```
POST /calculations/maneuvering/mmg
```

### 请求示例

```json
{
  "length": 320.0,
  "beam": 58.0,
  "draft": 20.8,
  "cb": 0.8098,
  "displacement": 320000.0,
  "Hr": 150.0,
  "Ar": 112.5,
  "rudder_angle_deg": 35.0,
  "D_prop": 8.5,
  "n_rps": 1.2,
  "speed_ms": 7.717,
  "water_density": 1025.0
}
```

### 校验规则

- `Hr` 为**必填字段**（`gt=0`）
- 缺失 `Hr` 时返回 **HTTP 422**，错误信息包含 `"Hr"`
- `Hr` 必须为正数（`Hr > 0`）

## 参考

- Yasukawa, H. & Yoshimura, Y. (2015). *Introduction of MMG standard method for ship maneuvering predictions*. Journal of Marine Science and Technology, 20(1), 37-52.
- IMO MSC.137(76) - Standards for Ship Maneuverability
- SIMMAN 2008/2014 Workshop on Verification of CFD for Ship Manoeuvring
