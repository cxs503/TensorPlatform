# E2E-1 修复 · 门缺陷 B1 · 判决表（脚本渲染，数字零手抄）

> 渲染器：`tests/e2e1_b1/render_docs.py`；数据源：`outputs/e2e1_b1/negative_evidence.json`、
> `outputs/e2e1_b1/m811_positive_check.json`。本文件不复述过程，只落判据。

## 缺陷一句话

样本库（samples 表）入库路径 `tools/sample_db/backfill.py::backfill_file()` 只做字段抽取 + INSERT，
不读 RunManifest、不复算哈希、不看来源 mission 终态 —— m810 实测（`B1_sample_db_unverified_ingest`）
一个伪造 `verdict="PASS"` 且 τ=0.4（ν<0 非物理）的裸 JSON 入库成功并判决 FAIL。

## 三门（修复后）

| 门 | 判据 | 拒绝码 |
|---|---|---|
| 1 manifest | RunManifest 存在 + `tensorphysics-run-manifest-v1` 契约通过 + `validation_status=passed` + outputs 非空 | `E_MANIFEST_MISSING` / `E_MANIFEST_NOT_OBJECT` / `E_MANIFEST_UNREADABLE` / `E_CONTRACT` / `E_UNVERIFIED` / `E_NO_OUTPUTS` |
| 2 mission | 来源 mission 终态 `succeeded`（`approval.finished_status` 若在也须 `succeeded`；解析不出 → fail-closed） | `E_MISSION_STATE` / `E_MISSION_UNRESOLVED` / `E_MISSION_ID_MISSING` |
| 3 checksum | `manifest.outputs[]` 的 sha256 复算命中 + `size_bytes` 一致 + `uri` 不越出 run 目录 | `E_CHECKSUM_MISMATCH` / `E_CHECKSUM_FORMAT` / `E_SIZE_MISMATCH` / `E_ARTIFACT_MISSING` / `E_ARTIFACT_ESCAPE` |

## 汇总（机器产）

| 指标 | 值 |
|---|---|
| 用例数 | 12 |
| 坏产物（应拒） | 11 → 实拒 11 |
| 正对照（应准入） | 1 → 实准入 1 |
| 修复前（legacy 裸入口）放行的坏产物数 | 11 |
| 审计留痕条数 / 判定次数 | 12 / 12 |
| 总判 | **PASS** |

## 用例逐条（实际 = 门输出的结构化拒绝码）

| 用例 | 攻击面 | 门 | 预期 | 修复前(legacy) | 修复后(gated) 实际码 | 判定 |
|---|---|---|---|---|---|---|
| `C0_control_valid` | 合法 run 产物（有效 manifest + mission 811 succeeded + sha256 命中） | 三门 | ADMITTED | ADMITTED | ADMITTED | **PASS** |
| `B1_repro_m810_unverified_ingest` | m810 实测载荷原文：伪造 verdict=PASS、τ=0.4(ν<0)、无 RunManifest、无哈希 | manifest | REJECTED | ADMITTED | E_MANIFEST_MISSING | **PASS** |
| `B2_manifest_not_object` | run_manifest.json 是数组而非对象 | manifest | REJECTED | ADMITTED | E_MANIFEST_NOT_OBJECT | **PASS** |
| `B3_contract_missing_checksum` | manifest.outputs[0] 缺 checksum_sha256（契约硬约束） | manifest | REJECTED | ADMITTED | E_CONTRACT | **PASS** |
| `B4_checksum_mismatch` | manifest 声明哈希后等长篡改产物（St 0.1642→0.1900，字节数不变） | checksum | REJECTED | ADMITTED | E_CHECKSUM_MISMATCH | **PASS** |
| `B5_unverified_manifest` | manifest 合法但 validation_status='not_run'（未验证 run） | manifest | REJECTED | ADMITTED | E_UNVERIFIED | **PASS** |
| `B6_mission_failed` | 三门前两门合法，但来源 mission 811 终态 failed | mission | REJECTED | ADMITTED | E_MISSION_STATE | **PASS** |
| `B7_mission_unknown` | 来源 mission 不在状态源中（无法确认 succeeded → fail-closed） | mission | REJECTED | ADMITTED | E_MISSION_UNRESOLVED | **PASS** |
| `B8_artifact_escape` | manifest uri 用 ../ 指向 run 目录外文件（哈希/尺寸都对得上） | checksum | REJECTED | ADMITTED | E_ARTIFACT_ESCAPE | **PASS** |
| `B9_size_mismatch` | sha256 命中但 size_bytes 声明 +7（被截断/拼接过的产物） | checksum | REJECTED | ADMITTED | E_SIZE_MISMATCH | **PASS** |
| `B10_artifact_missing_file` | manifest 声明了未落盘的场量文件 field_dump.npz | checksum | REJECTED | ADMITTED | E_ARTIFACT_MISSING | **PASS** |
| `B11_no_outputs` | manifest.outputs=[]（无可复算的产物 → 哈希门形同虚设） | manifest | REJECTED | ADMITTED | E_NO_OUTPUTS | **PASS** |

判据（可失败）：任一坏产物被准入（samples 表出现其行）= FAIL；拒绝无留痕 = FAIL。
本表所在运行中两项判据均未触发（见上表 `修复前(legacy)` 列即为缺陷活体证据）。

## 图

![gate matrix](../../outputs/e2e1_b1/fig/gate_matrix.png)

![red to green](../../outputs/e2e1_b1/fig/red_to_green.png)

## m811 正例体检（Re=1000 硬门达成的那一单）

- 结论：**NOT_ADMISSIBLE** —— 三门中 1 门通过（mission）；未通过/不可评估：manifest, checksum → 不可准入
- 体检对象：`/srv/tensor/agent/work/m811/outputs/lid_driven_cavity/summary.json`（run 目录 `/srv/tensor/agent/work/m811/outputs/lid_driven_cavity`）
- 门位视图：`{"checksum": "not_evaluable", "manifest": "fail", "mission": "pass"}`

缺什么：

- 门1 RunManifest 缺失：run 目录下无 run_manifest.json / manifest.json / RunManifest.json，产物内也无 run_manifest / manifest / RunManifest 键
- 门3 关键文件哈希无法复算：manifest 缺失 → 没有声明哈希可作复算基准

怎么补齐：

- 补写 RunManifest（contracts/tensorphysics-run-manifest-v1.schema.json）：run 目录下 run_manifest.json 或产物内嵌 manifest 键，至少含 schema_version/run_id/status/validation_status/outputs[]（每个 output 需 uri + media_type + checksum_sha256(64hex) + size_bytes）

> 注意：事后补写 manifest 只能锁定字节（sha256/size），**不能**追认 `validation_status=passed`。
> m811 的 `validation_status` 必须由当时那次硬门运行的验证器产出证据支撑，否则补写即等于自我声明，
> 属「哈希门可过、验证门不可伪造」的边界 —— 该用例保留为 NOT_ADMISSIBLE，不做事后补录。
