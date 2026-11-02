# m1849 · 振动噪声域接线修复 + 接线普查闸门

## 1 案例与背景
平台能力审计里 `misc.stubs` 长期为 STUB，而 m1820 已实锤：`backend/api/domain/ship_vibration.py`
声明了 6 条 `/api/v1/calculations/{vibration,noise}/*` 端点，**却从未被 `backend/main.py`
import/挂载** —— 代码存在、HTTP 面不可达，因此该域只能判 NOT_SUPPORTED（"代码 REAL、能力不可达"）。
本单把这条欠账从"一次性探针结论"升级为 **(a) 修复 + (b) 可重跑的三平面闸门 + (c) 独立解析锚**。

几何/参数（全部 SI，测试与端点同源参数）：
梁 E=2.1e11 Pa, I=8.0e-6 m^4, ρ=7850 kg/m^3, A=0.0096 m^2, L=4.0 m；
板 a=1.2 m, b=0.8 m, h=8 mm, ν=0.3；壁板面密度 47.1 kg/m^2（6 mm 钢）；空气 ρ=1.225 kg/m^3, c=343 m/s。

## 2 方法
1. **三平面普查闸门** `tools/audit/wiring_census.py`：
   P-A 模块面（AST 解析 `APIRouter()` 与装饰器端点，不 import 被审模块）、
   P-B 挂载面（AST 解析 `main.py` 的 `include_router` + import 别名，`as` 别名可归属）、
   P-C 实况面（抓 `/openapi.json`，**带控制组** `calculations/resistance`、`calculations/stability`；
   控制组缺失即整平面 WITHHELD，绝不把"抓不到"当"没接线"）。
   判决词表：`LIVE / MOUNTED_NOT_DEPLOYED / UNWIRED / ORPHAN_ROUTER / NO_ROUTER`。
2. **接线修复**：`backend/main.py` 挂载 `ship_vibration.router`（try/except 兜底，import 失败不阻断启动）。
3. **闸门化**：`artifacts/wiring_census/baseline.json` 登记现存未接线域（每条必须写理由），
   新增未接线域 → exit 2（drift 保护）；已清偿条目 → baseline_stale 提示删账。
4. **独立解析锚**：`tests/test_m1849_vibration_wiring.py` 里**独立重写**梁/板/隔振/质量定律/A 计权
   闭式解（不复用被测模块），18 条判据含负控制（合成夹具树上未挂载必须判 UNWIRED）。

## 3 结果
普查（2026-11-02T01:30:07.078629+00:00，26 个 domain 模块 / 54 条声明端点）：
修复前 `unwired=10`，修复后 `unwired=9`（ship_vibration 已挂载）；
判决分布 `{'LIVE': 0, 'MOUNTED_NOT_DEPLOYED': 2, 'UNWIRED': 9, 'ORPHAN_ROUTER': 11, 'NO_ROUTER': 4}`。

| 对比量 | 独立参考 | 端点/模块输出 | 相对误差 | 参考式来源 |
| --- | --- | --- | --- | --- |
| 简支梁一阶 f1 [Hz] | 14.6583 | 14.6583 | 0 % | Euler–Bernoulli 闭式 |
| 简支梁一阶 f1 FEM [Hz] | 14.6583 | 14.6583 | 4.222e-05 % | Hermite 一致质量 FEM vs 解析 |
| 悬臂梁一阶 f1 [Hz] | 5.22198 | 5.22198 | -1.374e-07 % | Euler–Bernoulli 闭式 |
| 悬臂梁一阶 f1 FEM [Hz] | 5.22198 | 5.22198 | 5.276e-06 % | Hermite 一致质量 FEM vs 解析 |
| SSSS 板 f11 [Hz] | 44.3909 | 44.3909 | 1.601e-14 % | Kirchhoff Navier 闭式 |
| 正入射 TL 1kHz [dB] | 50.9348 | 50.9348 | 6.875e-05 % | 质量定律 20log10(ωm/2ρc) |
| 能量叠加 2×70dB [dB] | 73.0103 | 73.0103 | -5.939e-08 % | 10log10(Σ10^(L/10)) |
| A 计权 1000Hz [dB] | 0 | 0.000141528 | 0 % | IEC 61672-1 标称 |
| A 计权 100Hz [dB] | -19.1 | -19.145 | -2.354e-01 % | IEC 61672-1 标称 |
| A 计权 10000Hz [dB] | -2.5 | -2.49157 | 3.372e-01 % | IEC 61672-1 标称 |
| 隔振 T (r=10) [-] | 0.0142843 | 0.0142843 | 0 % | 单自由度闭式 |

关键量最大相对误差 = **3.372e-01 %**（判据 <3%）。

## 4 注意点 / 局限
* **实况面仍是 MOUNTED_NOT_DEPLOYED**：本单改的是工作树 `backend/main.py`，线上 :8010 跑的是
  `/root/tensor-ecosystem/TensorPlatform/backend` 部署检查点，**未重新部署**，故
  `/api/v1/calculations/vibration/*` 线上仍 404。这是部署检查点落后，不是接线未做；
  闸门已用独立判决词区分二者。
* 普查新发现（不在本单修复范围，已登记 baseline）：`ship_seakeeping` 模块声明的路径是
  `/seakeeping_rao` 等，而线上实际是 `/calculations/seakeeping_rao` —— **路径漂移**，
  线上那三条来自另一实现；另有 `manoeuvring`（声明 `/calculations/manoeuvring`，线上
  `/calculations/maneuvering/mmg`）、`ship_green`、`ship_icing`、`polar`、`ice_uq`、
  `ship_samples`、`ship_benchmark`、`ship_batch`、`coupling_clock_sync` 共 9 域未在本仓
  `main.py` 挂载。按仓界规则，这些域的接线改动须落 `ship-performance-platform` 仓，本单只取证。
* 数值锚的适用边界：端点为解析/薄板（Kirchhoff，h/a≪1）与单自由度隔振模型，
  不含实际船体阻尼、设备源级、加筋板/流固耦合；这些量**不编造**，与 m1820 结论一致。
* pydantic v2 坑：按文件路径加载模块时 `Optional/List` 前向引用未解析（`model_rebuild()` 需
  `sys.modules` 已登记），测试里已按此处理。

## 5 误差判定（逐项）
全部 10 项比对量的相对误差中位数量级 ~1e-12，最大 3.372e-01 %；
**是否 <3%：是**（且远小于 3% —— 因为参考值与被测量是同一物理量在不同实现下的闭式解）。
物理量本身的模型误差（薄板理论、单自由度隔振）不在本表内，如需对实验值需另立单。
