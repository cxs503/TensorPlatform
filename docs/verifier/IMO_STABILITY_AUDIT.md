# 完整稳性 / IMO 衡准 独立复核（verification-debt 偿付单 tp-ver-3）

> 席位：verifier（tp-ver-3）｜仓：TensorPlatform｜日期：2026-11-01
> 判定：**FAIL**——被复核能力（`calc.stability`，平台审计口径 REAL）在解析基准下
> 关键量误差 10%–188%，且两个并存端点在**同一输入**上互相矛盾达 114%。
> 本文所有数字均由 `tools/verifier/audit_stability_imo.py` 程序化产出，
> 工件：`artifacts/verifier/stability_imo_audit/{audit.json,audit.md,api_captures/}`。

## 1. 为什么复核这一项

`audit_capabilities` 把 `calc.stability` 标为 **REAL**，但平台台账
（`artifacts/credit_ledger.jsonl`）里只有 1 条裁决，域为 `resistance`；
`docs/verifier/DESIGN.md` §7 自陈"更多学科域的题库"未建。即：稳性能力**从未**
经过独立参考复核，属于典型验证欠账。

复核需要一个"平台与参考值不共源"的基准。唯一在任意横倾角都能写出**精确解析解**
的船型是矩形浮箱（box barge / pontoon，Biran《Ship Hydrostatics and Stability》
§5.4 "the box-shaped vessel"）：吃水线切割矩形剖面后仍是多边形，浸湿面积与形心由
鞋带公式精确给出。平台入参 `length/beam/draft/depth/displacement/cb/kg`
对 Cb=1.0 的浮箱完全可表达，因此这是**公平且精确**的判据。

## 2. 参考链（三层，均不依赖平台代码）

1. **剖面精确积分**（`tools/verifier/box_barge_reference.py`）：对给定 φ，二分求
   水线偏移 c 使浸湿面积 = 常数排水量，多边形裁剪 + 鞋带公式得形心，
   `KN = y_B cosφ + z_B sinφ`，`GZ = KN − KG sinφ`；面积用 0.1° 步长 Simpson 积分。
2. **墙舷闭式解**（`stability_reference.py`）：`GZ = sinφ (GM + ½ BM tan²φ)`，
   其原函数 `−GM cosφ + ½BM(secφ + cosφ)` 给出任意角区间的**精确**面积。
   在甲板边缘入水角以下，方法 1 与方法 2 必须逐位一致（测试 `rel=1e-9` 已证），
   这是参考链自洽性的硬证据。
3. **IMO IS Code 2008**（Res. MSC.267(85) Part A Ch.2 §2.2.1）阈值表逐条誊录。

## 3. 算例

| 算例 | 主尺度 | KG | 解析基准 |
|---|---|---|---|
| barge-B20-T8-D12-KG7 | 100×20×8 m, D=12 | 7.0 | KB=4.000, BM=4.167, **GM=1.167**, 甲板边缘入水角 21.8° |
| barge-B30-T10-D16-KG8 | 120×30×10 m, D=16 | 8.0 | 详见 audit.json |

## 4. 结果（算例 1，误差 = |平台−解析|/解析）

| 量 | 解析 | 端点A `/calculations/stability` | 误差A | 端点B `/stability/imo-check` | 误差B |
|---|---|---|---|---|---|
| KB [m] | 4.000 | 5.333 | **+33.3%** | 4.400 | +10.0% |
| BM [m] | 4.167 | 4.167 | 0.0% | 4.150 | −0.4% |
| GM [m] | 1.167 | 2.500 | **+114.3%** | 1.550 | **+32.9%** |
| GZ_max [m] | 0.7495 | 2.160 | **+188.2%** | 0.586 | **−21.8%** |
| area 0–30° [m·rad] | 0.1896 | 0.3702 | **+95.3%** | 0.1876 | −1.1% |
| area 30–40° [m·rad] | 0.1299 | 0.3196 | **+146.1%** | 0.0991 | **−23.7%** |
| area 0–40° [m·rad] | 0.3194 | 0.6898 | **+116.0%** | 0.2867 | +10.2% |

全库共 **17 项**超 15% 不可接受线（见 `audit.json.summary.failures`）。

## 5. 缺陷清单（按严重度）

- **D1（安全性 / 非保守）衡准阈值错**：端点 A 用 `area_0_40 ≥ 0.055`，
  IS Code 2008 §2.2.1.2 要求 **0.090 m·rad**（宽松 1.64 倍）。
  而同一服务的 `stability_imo.py` 用的正是 0.090 —— 同一平台两个答案。
- **D2（口径替换）**：端点 A 以 `GZmax ≥ 0.20 m` 代替 §2.2.1.4 的
  **GZ@30° ≥ 0.20 m**。峰值晚出的船（GZ@30°=0.15、峰值 0.30@60°）会被误判通过。
  测试 `test_criterion_uses_gz_at_30_not_gz_max` 固化此反例。
- **D3（漏检条目）**：端点 A 完全没有 §2.2.1.1（area 0–30° ≥ 0.055）。
- **D4（伪条目 + 构造性自满足）**：两端口均输出 "vanishing angle" 衡准
  （A ≥55°、B ≥60°，彼此不一致），而 IS Code 2008 §2.2 无此条；
  且 A/B 的 GZ 模型都带 `max(0, GZ)` 钳位或 `(60−φ)/20` 线性收口，
  使消失角**恒等于 60°**——衡准因此永远成立，指标失去信息量。
- **D5（物理模型错）**：端点 A 的大倾角项写成 `0.5·BM·sin²φ`，
  墙舷精确解为 `0.5·BM·tan²φ`（差 cos²φ 倍的 BM 项），且 φ>40° 施加
  经验收口，导致 GZ 系统性偏大（本算例 +188%）。
  端点 B 的 `KN=(BM sinφcosφ + KB sinφ)(1+0.001φ)` 同样非水静力学量，
  导致 GZ 在 35° 之后人为衰减、92% 的曲线被钳为 0。
- **D6（KB 公式与自身 BM 假设不自洽）**：A 用 `KB=T(1−Cb/3)`（细瘦船型近似），
  但 BM 用 `CWP=0.75+0.25Cb`（Cb=1 时 CWP=1，即满矩形水线面）。
  对 Cb=1 的浮箱，KB 精确值 4.0 m，公式给 5.333 m（+33%）。
- **D7（端点冗余）**：同一物理问题两个端点、两套阈值、两套 GZ 模型，
  同输入 GM 相差 114%（A 2.500 vs B 1.550）——平台对自身答案不确定。

## 6. 为什么本单不直接改代码

被复核实现位于运行时仓 `/srv/tensor/ship-workspace/`，
不在本单仓库白名单（TensorPlatform）内。按纪律"只改工作树内文件"，
本单交付**证据 + 判据 + held-out 考题 + 修复取向**，修复单另行派发
（runner=native，EDIT_WHITELIST 指向该运行时仓 `backend/api/`）。

修复取向（已按最小改动写清）：
1. A 的 0–40° 阈值 0.055 → **0.090**；补 0–30° 条目；`GZmax≥0.2` → `GZ@30°≥0.2`；
2. 大倾角项 `sin²φ` → `tan²φ`，删除 φ>40° 的经验收口（入水角应由 `D,T,B` 几何算出）；
3. 删除无出处的 vanished-angle 衡准，或注明来自 CCS 等规则集并据实计算；
4. 两端口收敛为单一实现，B 为参考、A 改为调用 B。

## 7. 我们自己的局限（如实）

- 解析基准只在 Cb=1.0 的浮箱上成立；对常规船型（Cb≈0.6–0.85）无闭式解，
  需静水力曲线/横剖面数值积分作参考，本单未做，已列为后续单。
- 墙舷闭式解仅在甲板边缘入水角以下有效；本单的比较因此分两段报告
  （≤21.8° 用闭式解，>21.8° 用剖面精确积分）。
- 端点 A 不返回 GZ 曲线，其 GZ 曲线只能由标量（GZ_max/面积）间接约束；
  图 1 中 A 仅以峰值散点呈现，不做曲线逐点比对。
- 报告未对 `displacement` 参数做敏感性分析（A/B 的 GZ 均不随排水量变化，
  这本身也是一处可疑点，未列入上面缺陷表，因缺少参考判据）。

## 8. 复现

```bash
python -m pytest tools/verifier/tests/ -q                      # 28 passed
python -m tools.verifier.audit_stability_imo \
    --out artifacts/verifier/stability_imo_audit               # 在线复核（会重抓 api_captures）
python -m tools.verifier.audit_stability_imo --offline \
    --out artifacts/verifier/stability_imo_audit               # 离线重放
python -m tools.verifier.generate_stability_questions          # 题库增补 stability 域（幂等）
```

图：`docs/verifier/figures/fig1_gz_curve.png`（GZ 曲线 vs 精确解）、
`fig2_error_bars.png`（误差条形图 + 15%/3% 参考线）、
`fig3_criteria.png`（实现阈值 vs IS Code 阈值）。