# m5299 · 能力审计自动化 + 防造假 Merkle 锚链（索引页）

> 论文级正文与逐条证据：`artifacts/capability_audit/report.md`
> 机器产物：`artifacts/capability_audit/{audit_results.json, criteria.json, anchor.json, verification.json}`
> 历史锚记录（链）：`anchor_0001.json`（index 1）、`anchor_0002.json`（index 2）

## 一句话

把“能力是 REAL 还是 MOCK”从人工自述变成**可执行证伪**（五族探针）+ **不可篡改的审计工件**
（Merkle 根 + Ed25519 签名 + git commit 时间锚），CLI 四子命令可复跑。

## 产物

| 路径 | 说明 |
|---|---|
| `tools/capability_audit/cli.py` | `audit` / `report` / `anchor` / `verify` |
| `tools/capability_audit/probes.py` | 不变性 / 敏感性 / 解析 / 单调 / 基准 五族探针引擎 |
| `tools/capability_audit/adapters.py` | 6 个能力 × 探针声明式注册表 |
| `tools/capability_audit/oracle.py` | 独立转写的闭式解（ITTC-1957、Fr/Re/P_E、壁面式 KB/BM/GM、作动盘、色散） |
| `tools/capability_audit/benchmark.py` | 复用 `backend/data/benchmark_reference.json`，遵守其 integrity_gate 策略 |
| `tools/capability_audit/merkle.py` · `ed25519.py` | RFC 6962 Merkle（含存在性证明）· 纯 Python Ed25519（RFC 8032） |
| `tools/capability_audit/anchor.py` | 锚链构建与 8 项独立复核 |
| `tools/capability_audit/tests/test_capability_audit.py` | 40 个 pytest 用例 |
| `artifacts/capability_audit/` | 审计结果、6 张图、报告、锚记录、复核记录 |

## 关键数字（2026-11-07 实测，:8001）

* 6 个能力 / **65** 个探针（50 通过、7 失败、8 按语料裁决 WITHHELD）。
* 覆盖：输入扰动不变性 6 个能力（判据 ≥5）、解析解对拍 6 个（≥3）、单调性三刀 6 个（≥3）、
  基准对拍 4 个可判行（SUBOFF；DTMB5415/KVLCC2 八行由语料自身裁决 WITHHELD）。
* 5 个能力机制判 REAL；`calc.maneuvering`（`/maneuvering/turning`）判**硬编码代理**：
  advance=3.5L、transfer=2.0L、tactical/steady=4.5L/3.5L，对舵角（35°→15°）与航速（12→6 kn）
  零响应 —— 因果敏感性与单调性两族同时击穿。
* 精度另判：`calc.resistance` 的潜体支路对 SUBOFF 语料的 C_T 误差
  +13.8% / +22.0% / +28.4% / +33.6%（Fr=0.459/0.612/0.765/0.918），仅 1/4 落在 15% 内 ⇒
  **机制 REAL、精度 FAIL**，两者分开报告，不用“判 MOCK”掩盖真实算法在子域上的精度缺陷。
* Merkle 根 `ac6f64d50edb5179001d30db2ebf7401c44f2ccb1e5ae9fa78d7b43abbba8284`
  （6 个工件，RFC 6962 域分隔，叶子只哈希 `{path,sha256,bytes}` 投影），
  Ed25519 公钥指纹 `7c5dc68a13ca3d1c`；外部时间锚 = 提交 `8ce3069f07c4`，`verify` 确认
  6/6 工件与该提交逐字节一致；链 index 3 → anchor_0002 → anchor_0001。

## 复核

```bash
python3 -m tools.capability_audit.cli verify --out artifacts/capability_audit --require-commit-message
```

八项检查：Ed25519 签名、工件摘要、Merkle 根重算、存在性证明、git 提交内容与时间、
锚链前驱、提交信息中的根引用、派生验收计数与锚定证据的一致性。
把工件集复制到仓外仍可复现根（叶子不含任何仓库相对路径），有专门用例覆盖。

## 局限（详见报告第 6 节）

1. 能力注册表仍未 HTTP 暴露，自述等级取自仓内快照；未注册端点按 UNREGISTERED 标注。
2. KB/BM 对拍使用端点自报的 `Cw`，属“闭式形式 + 自洽”的弱对拍。
3. 基准对拍仅覆盖语料允许的 DERIVED 行；SUBOFF 潜体支路的几何以平台自身型线体积为锚
   （L=4.356 m 时 ∇=0.699195 m³，端点拒绝其它取值），属已知建模差异。
4. 锚链保证工件未被改写、签名者可追踪，**不**保证审计员诚实——oracle 每条闭式解注明出处并有单测。
5. 锚定集合限于机器证据（audit_results.json + figures/*.png）；报告与 criteria.json 为派生视图，
   由 verify 的 `criteria_consistency` 反查一致性。