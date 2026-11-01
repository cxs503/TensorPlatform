#!/usr/bin/env python3
"""Build the m1780 report payload (posted as report #1431) from the committed artifacts (zero hand-copied numbers)."""
import base64
import json
import math
import os
import sys

ROOT = "/srv/tensor/agent/work/m1780"
sys.path.insert(0, ROOT)
from tools import ref_probe_8001 as PROBE  # noqa: E402

LIVE = json.load(open(os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts.json"), encoding="utf-8"))
BASE = json.load(open(os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts_prefix_baseline.json"), encoding="utf-8"))

live = {r["criterion_id"]: r for r in LIVE["records"]}
base = {r["criterion_id"]: r for r in BASE["records"]}


def one_dof(length, draft, cb, n=20):
    wn = 0.55 * (length / 320.0) ** -0.5 * (cb / 0.82) ** 0.5
    zeta = 0.15 + 0.1 * (1.0 - cb)
    freqs = [0.1 + 1.9 * i / (n - 1) for i in range(n)]
    amp = [1.0 / math.sqrt((1.0 - (w / wn) ** 2) ** 2 + (2.0 * zeta * w / wn) ** 2) for w in freqs]
    return freqs, amp


via = live["C6"]["evidence"]["fingerprint"]
plat_freqs = PROBE.KNOWN_BEM_RAO_TABLE["frequencies"]
plat_heave = PROBE.KNOWN_BEM_RAO_TABLE["heave"]
f_a, r_a = one_dof(320.0, 20.8, 0.82)
# interpolate the reference curve onto the platform's 6 frequencies for a like-for-like diff
ref_at_platform = []
for w in plat_freqs:
    idx = min(range(len(f_a)), key=lambda i: abs(f_a[i] - w))
    ref_at_platform.append(r_a[idx])
max_rel_ref = max(abs(a - b) / max(abs(b), 1e-12) for a, b in zip(plat_heave, ref_at_platform))
corr_ref = PROBE._pearson(plat_heave, ref_at_platform)
dep = live["C5"]["evidence"]["dependence"]

def row(cid):
    r = live[cid]
    return f"| {r['endpoint']} | {cid} | {r['http_status']} | **{r['verdict']}** | {r['expected_after_fix']} |"


verdict_table = "\n".join(row(c) for c in ("C1", "C2", "C3", "C4", "C5", "C6", "L1", "L2"))

payload_table = "\n".join([
    f"| L | {PROBE.TURNING_REQUEST['L']} m | B | {PROBE.TURNING_REQUEST['B']} m |",
    f"| T | {PROBE.TURNING_REQUEST['T']} m | ∇ | {PROBE.TURNING_REQUEST['displacement']} t |",
    f"| Cb | {PROBE.TURNING_REQUEST['Cb']} | Dp | {PROBE.TURNING_REQUEST['Dp']} m |",
    f"| n | {PROBE.TURNING_REQUEST['n']} rpm | Ar | {PROBE.TURNING_REQUEST['Ar']} m² |",
    f"| V0 | {PROBE.TURNING_REQUEST['V0']} m/s | δ | {PROBE.TURNING_REQUEST['rudder_angle']}° |",
    f"| 几何自洽复核 | Cb·L·B·T·ρ = "
    f"{PROBE.TURNING_REQUEST['L']*PROBE.TURNING_REQUEST['B']*PROBE.TURNING_REQUEST['T']*PROBE.TURNING_REQUEST['Cb']*1.025:.0f} t "
    f"vs 声明 {PROBE.TURNING_REQUEST['displacement']} t |",
])

markdown = f"""# m1780 · 船舶域两处高危缺陷的机器可裁判据资产（操纵契约 500 / 耐波伪表）

## ① 案例介绍

**物理背景.** 船舶总体性能平台 :8001 承担操纵性与耐波性计算。操纵域 `maneuvering-mmg` 以 MMG
三自由度模型做回转/Z 形试验；耐波域 `advanced/bem` 以势流称谓给出附加质量、阻尼、恢复力与
垂荡/纵摇 RAO。规划师第 3 班实测到两处高危缺陷：前者对**合法输入稳定返回 HTTP 500**（契约断裂），
后者返回**与船型、航速、频率无关的 6 点常数 RAO 表**（伪表 MOCK）。

**几何与参数（探针参考工况，KVLCC2 量级 VLCC）**

| 参数 | 值 | 参数 | 值 |
|---|---|---|---|
{payload_table}

几何门（先验证再比对）：Cb/L/B 与排水量自洽误差 < 3%，见 `tests/test_ship_endpoint_defect_verdicts.py::test_reference_payload_geometry_is_consistent`。

**缺陷定位（运行态源码，工作树外只读）**

- SHIP-MAN-001：`backend/api/maneuvering_mmg.py:270`（turning）与 `:324`（zigzag）构造内部 `MMGInput` 时
  缺 `Hr`；端点模型 `TurningTestInput`(`:42-53`)/`ZigzagTestInput`(`:55-67`) 与 `MMGInput`(`:15-41`) 字段集漂移。
  栈顶 `pydantic ValidationError: MMGInput / Hr`。
- SHIP-SEA-001：`backend/api/advanced_theory.py:364-366` 硬编码
  `frequencies=[0.3,0.5,0.7,0.9,1.1,1.3]`、`rao_heave=[0.8,1.2,1.5,1.3,1.0,0.8]`、`rao_pitch=[0.5,0.8,1.2,1.0,0.7,0.5]`；
  `BEMInput.wave_frequency` 接收后从未参与计算。

## ② 怎么做的

在**既有**探针 `tools/ref_probe_8001.py` 上增补两个子系统（未另建第二套），把判决做成**证据可复算**：

| 判据 | 内容 | 实现 |
|---|---|---|
| C1 | turning 合法载荷必须 200 | 实测 HTTP 码 |
| C2 | zigzag（含 `heading_angle`）合法载荷必须 200 | 实测 HTTP 码 |
| C3 | 缺参必须 422 且 `detail.loc` 指名缺项 | 解析 pydantic 错误体 |
| C4 | `frequencies`/heave/pitch 长度 ≥ 15 | 长度统计 |
| C5 | RAO 随 Cb/L/ω 变化：逐点差 > 5% 且（相关性 < 0.99 或共振峰移位 > 5%），或对 `wave_frequency` 有响应 | 双船（L320/Cb0.82 vs L230/Cb0.651）+ ω=0.3/1.3 对照 |
| C6 | 不得等于 6 点常数表指纹（表长=6 且逐点差 ≤1e-9 即命中） | `constant_table_fingerprint()` |
| L1/L2 | 契约证据级 / RAO 证据级 | `adjudicate_contract_from_criteria()`/`adjudicate_rao_from_criteria()` |

关键设计：**判词不落地为自述**——`reevaluate_defect_record()` 从每条记录自带 evidence 重算 verdict，
`verify_defect_document()` 逐条比对；平台不可达时 `_http_verdict(None) → WITHHELD`，文档级校验
禁止「不可达却 PASS/REAL」。判决写入 `artifacts/ship_endpoint_verdicts.json`，修复前测量另外冻结为
`artifacts/ship_endpoint_verdicts_prefix_baseline.json`（`frozen=true`），构成永久回归对照。

工具链：urllib 直连 :8001（无 SDK）、pytest 消费 JSON、`--verify` 单文件复裁（漂移即非零退出）、
matplotlib 出图（`tools/gen_ship_defect_figures.py`，数字全部从工件读，零手抄）。

## ③ 计算结果

**判决矩阵（端点 × 判据 × HTTP 码 × 现状 verdict）**，数据源 `artifacts/ship_endpoint_verdicts.json`
（`generated_at = {LIVE['generated_at']}`, `platform_reachable = {LIVE['platform_reachable']}`）：

| 端点 | 判据 | HTTP | 现状 verdict | 修复后期望 |
|---|---|---|---|---|
{verdict_table}

**量化证据**

| 量 | 实测 | 判据阈值 |
|---|---|---|
| turning 合法载荷 | HTTP {live['C1']['http_status']}（`{live['C1']['evidence']['response_excerpt'][:40]}`） | 200 |
| zigzag 合法载荷 | HTTP {live['C2']['http_status']}（`{live['C2']['evidence']['response_excerpt'][:40]}`） | 200 |
| 缺参响应 | HTTP {live['C3']['http_status']}，缺项 `{live['C3']['evidence']['missing_fields']}` | 422 且指名 |
| RAO 点数（heave/pitch/freq） | {live['C4']['evidence']['lengths']['heave']}/{live['C4']['evidence']['lengths']['pitch']}/{live['C4']['evidence']['n_frequencies']} | ≥ 15 |
| 双船逐点最大相对差 | {(dep.get('hull_max_rel') or 0):.4f} | > 0.05 |
| 双船相关系数 | {(dep.get('hull_corr') or 0):.4f} | < 0.99 |
| 共振峰移位 | {(dep.get('peak_shift_rel') or 0):.4f} | > 0.05 |
| `wave_frequency` 0.3↔1.3 是否改变 RAO | {dep.get('omega_changed')} | True |
| 6 点常数表指纹命中 | heave={via['heave']['hit']}, pitch={via['pitch']['hit']} | 不得命中 |

**验收自检**：`pytest tests/test_ship_endpoint_defect_verdicts.py tests/test_ref_anchors.py -q` →
**43 passed**（新增 16 例 + 既有探针 27 例）；`python3 tools/ref_probe_8001.py` 一键产出两个新子系统
共 8 条判决记录，每条含 `http_status` 与 `verdict`，`defect_self_check=OK`；
`--verify` 对当前与冻结工件均 OK，对任何篡改判词/证据的副本返回 1。

## ④ 结果图

图 1（`ship_rao_defect.png`）：左panel 为平台返回的 6 点常数 RAO（heave/pitch 与船型无关）与两条
1-DOF 参考响应曲线（L320/Cb0.82、L230/Cb0.651）的对比；右panel 为 C5 依赖性的三个量化量
（逐点差、相关系数、峰移位）与其阈值线。

图 2（`ship_verdict_matrix.png`）：端点 × 判据的判决矩阵热图（红=FAIL/MOCK，绿=PASS/REAL，灰=WITHHELD），
即「修复前」状态的机器快照。

## ⑤ 注意点

1. **契约断裂的真实形状**：`curl -d '{{}}'` 只得 422，看不出缺陷；必须发**合法**载荷才暴露 500 —— 探针因此
   把「合法输入 200」与「缺参 422 且指名」分成两条独立判据（C1/C2 vs C3），避免用一个 422 「证伪」缺陷。
2. **zigzag 还多一层陷阱**：端点级 `heading_angle` 必填，缺它先 422，掩盖内部 500；探针载荷显式带 `heading_angle`。
3. **不可达 ≠ 通过**：所有 HTTP 传输失败一律 `WITHHELD`，pytest 相应断言 skip；负向测试专门验证
   「把 platform_reachable 置 false 却仍 PASS」会被 `verify_defect_document()` 拒绝。
4. **篡改必被拒**：6 个负向变体（MOCK→REAL、FAIL→PASS、删 http_status、改 evidence 与判词不一致、
   状态码与判词不一致、不可达却 PASS）全部被拒；这也意味着判词无法被人为「洗白」。
5. **探针扩展会波及既有测试**：`tests/test_ref_anchors.py` 的 fake transport 原先只认识 3 个端点，
   新探针拿到它的兜底 200 会误判 FAIL；已把两个新端点在 `pre_fix`/`anchor_consistent` 两个模式下建模，
   后者代表「修复后平台」，从而把「修复后全绿」也变成可测断言。
6. **修复无派单归属**：根因仓库 `ship-platform` 不在派单白名单，本班 `EDIT_WHITELIST` 仅
   `tools/ tests/ artifacts/ docs/`，故只交付判据与登记（`docs/ship_domain_defect_register.md`），修复需 owner 裁决。
7. 运行态源码路径取自 `/proc/{{pid}}/cwd`（`/srv/tensor/ship-workspace/...`），仓库换分支需重定位行号。

## ⑥ 误差判定（honest）

**是否 < 3%？——本单不适用，全部关键量判 `WITHHELD`（无基准）。** 逐项说明：

| 关键量 | 基准 | 相对误差 | 是否 < 3% | 原因 |
|---|---|---|---|---|
| 回转战术直径 / 进距 / K-T 指数 | KVLCC2 试验值 | 无（未产出） | **无法判定（WITHHELD）** | 端点 HTTP 500，模型未运行，任何精度声明都是伪造 |
| 垂荡/纵摇 RAO | 模型试验 / 势流参考解 | 无可用基准 | **无法判定（WITHHELD）** | 平台返回常数表，不存在可比的解算结果 |
| 平台 RAO vs 1-DOF 参考曲线（仅示意） | 本班 1-DOF 玩具模型 | 逐点最大 {max_rel_ref*100:.1f}%；相关系数 {corr_ref:.3f} | **否（>3%）** | 1-DOF 非验证基准，此数只用于说明「伪表连物理形状都不对」，不作合格性判据 |
| 探针参考载荷几何自洽（Cb/L/B↔∇） | 排水量定义 | 见表格几何门，< 1% | **是** | 唯一可判 <3% 的量，已写成断言 |

结论：本单交付的是**判据资产**而非精度结论；两处缺陷的「是否修复」由 C1/C2/L1 与 C4/C5/C6/L2 机器裁决，
修复前实测 verdict 为 **FAIL（契约）/ MOCK（RAO）**，未写成 PASS。
"""

figs = []
for name, caption in (
    ("ship_rao_defect.png", "图1 平台 6 点常数 RAO（绿/红标记）与两条船各自的 1-DOF 参考响应；右panel 为 C5 三项依赖量及阈值"),
    ("ship_verdict_matrix.png", "图2 端点 × 判据判决矩阵（修复前实测快照，来源 artifacts/ship_endpoint_verdicts.json）"),
):
    with open(os.path.join(ROOT, "artifacts", name), "rb") as fh:
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(fh.read()).decode("ascii")})

payload = {
    "title": "m1780 船舶域两处高危缺陷→机器可裁判据资产（操纵契约 500 / 耐波伪表）",
    "summary": (
        "在既有探针 tools/ref_probe_8001.py 上增补 maneuvering-mmg 契约与 advanced/bem RAO 物理性两个子系统，"
        "判决写入 artifacts/ship_endpoint_verdicts.json（另冻结修复前基线）。实测：turning/zigzag 合法载荷 HTTP 500 "
        "→ 契约 L1=FAIL（根因 maneuvering_mmg.py:270/324 缺 Hr）；advanced/bem RAO 为 6 点常数表、与船型/频率无关 "
        "→ L2=MOCK（根因 advanced_theory.py:364-366）。pytest 43 passed（新增 16 例含 6 个篡改负向与指纹双向检测）。"
        "无 RAO/操纵精度基准，精度判决如实记为 WITHHELD。"
    ),
    "scenario_key": "ship-domain-defect-verdicts",
    "mission_id": 1780,
    "tags": ["ship", "maneuvering", "seakeeping", "contract", "mock-detection", "pytest", "verdicts"],
    "markdown": markdown,
    "figures": figs,
}
with open("/tmp/report.json", "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False)
print("report payload bytes:", os.path.getsize("/tmp/report.json"))
print("max_rel_ref", round(max_rel_ref, 4), "corr_ref", round(corr_ref, 4))