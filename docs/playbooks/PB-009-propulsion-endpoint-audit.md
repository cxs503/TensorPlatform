# PB-009 推进/螺旋桨端点「自报方法名」不可信：三判据秒判伪回归

**症状**：`/propeller-design/wageningen-b` 报 `method="Wageningen B系列回归公式"`，
但 η0 与 P/D、Z、Ae/Ao 无关；`/calculations/propulsion` 返回 η_O=1.575、η_D=1.733
（>1）；`/propulsion/predict` 对 320 m VLCC 给桨径 11.65 m、推力 2.9 kN。

**根因**：端点用闭式手写替代系列回归，并加 `min()/max()` 截顶掩盖越界：
`K_T=max(0.36P/D−0.24J+0.2(Ae/Ao−0.7)+z(Z),0.05)`、`K_Q=K_T/10`、`η0=min(1.5915J,0.85)`。
常数比 K_Q≡K_T/10 使 η 与几何无关；0.05 下限抹掉零推力点 J0。

**检查顺序（不读源码也能判，全部机器可断言）**
1. 定义恒等式：`η_O == J·K_T/(2πK_Q)` 用同一响应的三列自比 —— 截顶会当场自相矛盾（J=1.0 时复算 1.5915 vs 报 0.85）。
2. 形状性质：`η_O(J=0)≡0`；K_T 必须在有限 J0 归零且 **J0 随 P/D 单调增**；`J→J0` 时 `K_T/K_Q→0`；η0 必须随 P/D 变。
3. 物理上界 + 跨域锚：`η_O < 2/(1+√(1+8K_T/(πJ²)))`；`T=R_T/(1−t)` 用 ITTC-78 干摩擦+相关津贴自算 R_T（不抄端点）；两船（VLCC vs 120 m 近海船）探硬编码。

**治愈**：换真 Oosterveld & van Oossanen (1975) B 系列回归（含 Z、Ae/Ao、P/D 项），禁截顶/禁 K_T 下限，返回 J0 与 η0 峰值。

**勿重蹈**：`method`/`series` 字段不是证据；门禁必须同时能放行合法合成曲线（否则是恒假门）。

判例来源：mission m1498（报告 #1085），代码 `tools/verify/propulsion_audit.py`、`tests/test_propulsion_audit.py`。
