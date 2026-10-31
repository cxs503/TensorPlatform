# E2E-1 修复 · 门缺陷 B1：样本库准入未验证谱系

> 判决表（数字由脚本渲染）：[`VERDICTS.md`](VERDICTS.md) · m811 正例体检：[`m811_positive_check.md`](m811_positive_check.md)
> 证据：`outputs/e2e1_b1/negative_evidence.json`、`outputs/e2e1_b1/admission_audit.jsonl`、
> `outputs/e2e1_b1/m811_positive_check.json`、`outputs/e2e1_b1/fig/*.png`

## 1. 缺陷（m810 实测，本单复现确认）

样本库（`samples` 表）入库路径 `tools/sample_db/backfill.py::backfill_file()` 只做
「JSON 字段抽取 + INSERT」：不读 RunManifest、不复算工件哈希、不看 `validation_status`、
不看来源 mission 终态。m810 的 B 类负测试 `B1_sample_db_unverified_ingest` 用一个
**伪造 `verdict="PASS"` 且 τ=0.4（ν<0，非物理）的裸 JSON** 直接入库 1 行，判决 FAIL。

本单复现（同一构造法，`tests/e2e1_b1/run_negative_suite.py`，修复前分支 legacy 列）：
12 个用例里 **11 个坏产物全部被旧入口放行**（`legacy_admitted_bad_products=11`）。
样本库是优化库 / 代理模型的**直接训练数据源**，这条路径等于「未经任何验证的数字进入训练集」。

## 2. 修复：三门准入（全过才准入）

实现于 `src/sample_db/`（`admission.py` 判定、`ingest.py` 入库、`inspect.py` 体检）：

| 门 | 判据 | 拒绝码 |
|---|---|---|
| 1 manifest | RunManifest 存在（`run_manifest.json` / `manifest.json` / 产物内嵌 `manifest` 键）+ `tensorphysics-run-manifest-v1` 契约通过 + `validation_status == 'passed'` + `outputs` 非空 | `E_MANIFEST_MISSING` `E_MANIFEST_NOT_OBJECT` `E_MANIFEST_UNREADABLE` `E_CONTRACT` `E_UNVERIFIED` `E_NO_OUTPUTS` |
| 2 mission | 来源 mission 终态 `succeeded`（`approval.evidence.finished_status` 若存在也须 `succeeded`；解析不出 → **fail-closed** `E_MISSION_UNRESOLVED`，绝不当作 succeeded） | `E_MISSION_STATE` `E_MISSION_UNRESOLVED` `E_MISSION_ID_MISSING` |
| 3 checksum | `manifest.outputs[]` 的 `checksum_sha256` 复算命中 + `size_bytes` 一致 + `uri` 不越出 run 目录 | `E_CHECKSUM_MISMATCH` `E_CHECKSUM_FORMAT` `E_SIZE_MISMATCH` `E_ARTIFACT_MISSING` `E_ARTIFACT_ESCAPE` |

设计与红线：

- **判定与抽取分离**：抽取/建表仍复用 `tools/sample_db/backfill.py`（单一事实源，未改），
  `src/sample_db/ingest.py::ingest_artifact()` 在三门通过后才调用 `backfill_file()`；
  拒绝时不 INSERT 任何一行。
- **拒绝必留痕**：每次判定（准入或拒绝）写**恰好一条** JSONL 记录，含 `ts`、`code`、
  `artifact` + `artifact_sha256`、`mission_id`、`gates{manifest,mission,checksum}`。
  「拒绝无留痕」在测试里直接判 FAIL（`test_rejections_all_leave_audit_trail` 校验行数 == 判定次数）。
- **fail-closed**：无 mission 状态源时默认 `NullMissionResolver` 一律拒；mission 状态
  `running` 这类非终态也拒（"not even terminal"），不是「非 failed 即放行」。
- 契约校验用 `jsonschema` Draft2020（仓库内 schema 文件），缺依赖时退化为内置硬约束检查。

用法：

```bash
# 入库（三门全过才写 samples 表；失败 exit 3 + 结构化错误）
python3 -m src.sample_db.ingest <artifact.json> --db /tmp/samples.sqlite \
    --run-dir <run_dir> --api --audit outputs/sample_db_admission_audit.jsonl
# 只体检（不写准入流水）
python3 -m src.sample_db.inspect <artifact.json> --run-dir <run_dir> --mission-id 811 --api
```

## 3. 回归（先红后绿）

```bash
python3 -m pytest tests/test_e2e1_b1_sample_admission.py -q   # 19 passed, 1 xfailed
python3 tests/e2e1_b1/run_negative_suite.py                   # 证据 JSON + 审计 JSONL
python3 tests/e2e1_b1/render_docs.py                          # 判决表 + 图
```

- 红：`test_legacy_ungated_backfill_still_admits` 以 `xfail(strict=True)` 钉住工具侧裸入口，
  现在必然红；接线后 XPASS → strict 报错，强制同步台账。
- 绿：11 个 B 类负用例全部 REJECTED（码与构造攻击面一一对应），1 个正对照 ADMITTED 且样本行字段完整。
- **判据可失败**：任一坏产物进 samples 表 → `test_bad_products_are_rejected_with_structured_error`
  的 `row_count(db) == 0` 断言失败；拒绝无留痕 → 审计行数断言失败。

## 4. m811 正例结论（不粉饰）

`python3 -m src.sample_db.inspect /srv/tensor/agent/work/m811/outputs/lid_driven_cavity/summary.json --api`
（mission 终态取自平台 `GET /tensor/missions/811`）：

| 门 | 状态 | 事实 |
|---|---|---|
| 1 manifest | **fail** `E_MANIFEST_MISSING` | run 目录只有 `RESULTS.md`/`profiles.csv`/`summary.json`，无 `run_manifest.json`，产物内也无 `manifest` 键 |
| 2 mission | **pass** | mission 811 `status=succeeded`、`approval.evidence.finished_status=succeeded`（API 实测） |
| 3 checksum | **not_evaluable** | 没有声明哈希 ⇒ 无复算基准（≠「哈希对得上」） |

→ **m811 产物当前不可准入**：缺 RunManifest（连带缺声明哈希与 size）。
补齐方式：在 manifest 里登记 `summary.json`/`profiles.csv` 的 sha256+size，并把当时硬门
验证器的结论写进 `validation`/`validation_status`。**注意边界**：事后补写只能锁定字节，
不能追认 `validation_status=passed` —— 那必须由当时那次运行的验证器证据支撑，否则等于自我声明。

## 5. 残留缺口（白名单外，未做）

`tools/sample_db/backfill.py` 不在本单 `EDIT_WHITELIST`（`src/, tests/, docs/, outputs/`），
因此旧的裸入口仍存在。收口方式（一行级改动，需白名单扩展）：

```python
# tools/sample_db/backfill.py
from src.sample_db.admission import admit_sample, AdmissionRejected, DefaultResolver  # 见下

def backfill_file(conn, filepath, *, mission_resolver=None, audit_log=DEFAULT_AUDIT_LOG):
    decision = admit_sample(filepath, mission_resolver=mission_resolver, audit_log=audit_log)
    if not decision.admitted:                      # 拒绝：结构化错误 + 已留痕
        raise AdmissionRejected(decision)
    ...  # 原有抽取 + INSERT 逻辑不变
```

（生产调用方需显式传 `ApiMissionResolver()`；不传则 `NullMissionResolver` fail-closed，安全但会拒一切。）

在接线完成前，**任何批量回填都应走 `python3 -m src.sample_db.ingest`**，不要直接调
`backfill.py`。缺口由 `test_legacy_ungated_backfill_still_admits`（strict xfail）看守。

## 6. 适用范围与局限

- 三门只保证「谱系完整 + 字节未被动过 + 来源 mission 成功」，**不保证物理正确性**：
  验证判据本身的强度仍是上游责任（见 m810 D1 求解入口物理域门）。
- 哈希门只能证明 manifest 与产物一致；若 manifest 与产物由同一次错误运行一起产出，
  哈希照样命中 —— 这就是为什么门 1 必须同时要求 `validation_status=passed`。
- mission 状态是**平台档案事实**（调度用），若平台记录本身失真，门 2 无法自证；本单只做
  「解析不出即拒」的 fail-closed。
- 审计 JSONL 是 append-only 应用层约定，无签名/防篡改（建议后续纳入 WORM 存储或哈希链）。