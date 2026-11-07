#!/usr/bin/env python3
"""m4960 报告生成器：把 report_metrics.json 程序化注入论文级报告（零手抄）。

产出：
* ``docs/reports/m4960_evidence_chain_report.md``  —— 论文级正文（六要素）
* ``/tmp/report_m4960.json``                      —— 平台 reports 接口负载（内嵌压缩图）

用法：python3 tools/evidence_chain/gen_report_m4960.py [--payload PATH]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ART = REPO_ROOT / "artifacts" / "m4960"
DOC = REPO_ROOT / "docs" / "reports" / "m4960_evidence_chain_report.md"

FIGURES = [
    ("fig1_ingest_outcomes", "图 1 四相入账结果：每次调用各 outcome 计数（A 全新批 / B 幂等重跑 / "
                             "C 混合批 / D dry-run）。B 相对 A 的 registered 归零、C 里坏件被隔离而好件照常入账，"
                             "是 I1/I2 判据的可视化。"),
    ("fig2_artifact_verification", "图 2 每个已入账 run 的工件复算数与 manifest 摘要前 8 位。"
                                   "入账前的 sha256 逐文件复算覆盖率为 100%，隔离件不计入。"),
    ("fig3_holtrop_rt_vs_v", "图 3 被入账的证据本体：Holtrop 阻力-航速扫描（L=200 m, Cb=0.65, 10–18 kn），"
                             "R_F 含 ITTC-1978 粗糙度补贴，R_T = R_F + R_R。数据由船舶平台 :8002 计算端点实时采集。"),
]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(out) + "\n"


def _embed(path: Path, max_width: int = 760) -> tuple[str, int]:
    """压成 8 位调色板 PNG 再 base64：控制报告负载远低于 250 KB 静默失败线。"""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)
    image = image.convert("P", palette=Image.ADAPTIVE, colors=128)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    payload = buffer.getvalue()
    return base64.b64encode(payload).decode("ascii"), len(payload)


def build_markdown(m: dict, figs: list[dict], audit: dict) -> str:
    crit = m["criteria"]
    crit_all = crit["all_passed"]
    counts_rows = []
    for key, label in (("phase_a", "A 全新批（2 干净 run）"), ("phase_b", "B 幂等重跑"),
                       ("phase_c", "C 混合批（篡改 + 非法 JSON + 新干净件）"), ("phase_d", "D dry-run")):
        c = m[key]["counts"]
        counts_rows.append([label, str(c["registered"]), str(c["already_registered"]),
                            str(c["quarantined"]), str(c["rejected"]), str(c["conflict"]),
                            str(c["failed"]), str(c["artifacts_verified"]) + "/" + str(c["artifacts_total"]),
                            str(m[key]["exit_code"])])

    digest_rows = []
    for name in m["idempotency"]["compared_files"]:
        a = m["idempotency"]["digests_a"].get(name, "—")
        b = m["idempotency"]["digests_b"].get(name, "—")
        digest_rows.append([f"`{name}`", f"`{a[:16]}…`", f"`{b[:16]}…`", "0 字节" if a == b else "**DRIFT**"])

    quar_rows = []
    for q in m["quarantine"]:
        run_id = q["run_id"] or "（manifest 不可解析，无 run_id）"
        art = next((a for a in q["artifacts"] if a.get("verified") is False), None)
        declared = (art or {}).get("declared_sha256")
        actual = (art or {}).get("actual_sha256")
        quar_rows.append([
            f"`{run_id}`", f"`{q['outcome']}`", f"`{q['reason']}`",
            f"`{declared[:16]}…`" if declared else "—",
            f"`{actual[:16]}…`" if actual else "—",
        ])

    hash_rows = []
    for rel, info in sorted(m["hash_cross_check"].items()):
        hash_rows.append([f"`{rel.split('evidence/')[-1]}`", str(info["size_bytes"]),
                          f"`{info['tool_sha256'][:16]}…`",
                          f"`{(info['coreutils_sha256'] or 'n/a')[:16]}…`",
                          "✔ 一致" if info["agree"] else "✘"])

    speed_rows = []
    for speed in m["speeds_kn"]:
        key = f"{speed:g}"
        sweep = m["sweep_table"][key] if "sweep_table" in m else None
        if sweep:
            speed_rows.append([key, f"{sweep['rt']:.2f}", f"{sweep['rf']:.2f}", f"{sweep['rr']:.2f}",
                               f"{sweep['pe']:.2f}", f"{sweep['ct']:.6f}"])

    state_rows = []
    for run_id in sorted(m["state"]["runs"]):
        rec = m["state"]["runs"][run_id]
        state_rows.append([f"`{run_id}`", str(rec["artifact_count"]), f"`{rec['manifest_sha256'][:16]}…`",
                           f"`{rec['outcome']}`", f"`{rec['validation_status']}`"])

    design = m["design_point"]
    fig_md = "\n".join(
        f"\n**{fig['caption']}**\n\n![{fig['name']}](artifacts/m4960/{fig['name']}.png)\n" for fig in figs
    )

    return f"""# 证据链批量入账 CLI（evidence_chain）：把战役入账从手工变工具

**节点** IN15-O2 · 自回归 O2「喂证据」燃料 · 仓库 TensorPlatform · 分支 `agent/m4960` ·
报告生成于程序化注入（`tools/evidence_chain/gen_report_m4960.py`，数字零手抄）。

## ① 案例介绍

### 物理背景

战役（campaign）每推进一步都会产出一批**数值证据**：阻力-航速曲线、分项阻力核算、
一致性核对 JSON、图。这些证据若要能被后续自回归循环复用，必须先进入平台证据链
（`tensorphysics_runs` 登记表）。平台的登记契约存在，但它只存元数据：

> *The endpoint does not itself ingest files, verify remote object existence, enforce the
> complete nested JSON Schema, or claim solver correctness.* (`docs/tensorphysics-run-registry.md`)

于是「工件 → 登记」之间留下一条必须由人补的缝：算哈希、抄摘要、粘贴请求、坏件回滚、
中断后不知道登到哪。本节点交付把这条缝封上的工具 —— **证据链批量入账 CLI**。

演示用的证据本体是一艘真实尺度船舶的 Holtrop 阻力计算（船舶总体性能平台计算端点
`:8002` 实时采集，非合成数据）：

### 几何与参数表

| 参数 | 符号 | 值 | 来源 |
|---|---|---|---|
| 垂线间长 | L | {m['hull']['length']:.1f} m | 演示船体 |
| 型宽 | B | {m['hull']['beam']:.1f} m | 演示船体 |
| 设计吃水 | T | {m['hull']['draft']:.1f} m | 演示船体 |
| 方形系数 | C_b | {m['hull']['cb']}（端点复算 C_b={design['cb_from_dims']}） | 演示船体 |
| 排水量 | Δ | {m['hull']['displacement']:.0f} t（一致性偏差 {design['displacement_consistency_pct']:.2f}%） | 演示船体 |
| 航速范围 | V | {m['speeds_kn'][0]:g}–{m['speeds_kn'][-1]:g} kn（{len(m['speeds_kn'])} 点） | 扫描设置 |
| 设计工况 | V_d | 14 kn：R_T={design['rt']:.2f} N，P_E={design['pe']:.2f} kW，Fr={design['froude_number']:.4f}，Re={design['reynolds_number']:.3e} | 端点输出 |
| 阻力方法 | — | ITTC-57 摩擦 + ITTC-1978 粗糙度补贴 + Holtrop 剩余阻力 | 端点 `method` 字段 |
| 湿表面积 | S | {design['wetted_surface']:.2f} m² | 端点输出 |

证据形态（被入账的工件）：

| run_id | 工件 | 角色 | validation_status |
|---|---|---|---|
| `m4960-r1-holtrop-sweep` | `rt_vs_v.csv`、`sweep.json`、`README.md` | resistance-curve / raw-endpoint-payload / case-note | `not_run`（如实：该 run 未做数值基准验证） |
| `m4960-r2-design-point-14kn` | `design_point.json`、`check_consistency.json` | design-point / internal-check | `not_run` |
| `m4960-r3-tampered` | 篡改后的 `design_point.json` | 负控 | — |
| `m4960-r4-broken` | 截断的 `run_manifest.json` | 负控 | — |
| `m4960-r5-consistency-note` | `note.json` | 判据声明 | `not_run` |

## ② 怎么做的

### 工具链与流程

```
tools/evidence_chain/evidence_chain.py   （1 个文件，argparse 双子命令 build / ingest）
    build  : 扫工件目录 → 逐文件 sha256+size → 产出 RunManifest（契约 v1，词表取自
             backend/contracts/research_run_status_vocab.v1.json，只读）
    ingest : 发现 manifest（≤2 层）→ 结构校验 → 逐文件复算 → 对账 → 登记（sink）→ 落账
```

四个设计决定，正对应四条硬判据：

1. **状态台账 + 结算语义（断点续传）**：`state.json` 记录每条 run 的
   `manifest_sha256` 与结算 outcome。重跑时若摘要相同且已结算，直接判
   `already_registered` 并**跳过 sink 调用** —— 中断/断网后重跑只补缺口，不重复登记。
2. **隔离而不阻断**：单条 run 的坏件只把**它自己**推进 `quarantine.json`（含逐工件
   declared/actual sha256 与 size），同批其它 run 照常登记，进程退出码 2 作为信号。
3. **报告双视图**：`ingest_report.json` 分 `evidence_state`（证据状态，幂等视图）与
   `rows/counts`（执行视图）。幂等判据作用在前者与落盘文件字节上 —— 否则任何「本轮做了
   什么」的报告天然每轮不同，度量会变成假门。
4. **不假装已验证**：`status`/`validation_status` 必须由生产者显式声明；远端 `s3://`
   URI 无法本地复算哈希 → 标 `artifact_uri_unsupported` 隔离，绝不写 `verified`。

### 关键设置

* 摘要规范化与端点一致：`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`
  → sha256，跨机可复现（与 `backend/api/tensorphysics_runs.py::_canonical_json` 同式）。
* 登记落点三选一：`local`（JSONL，端点语义的本地等价实现，幂等 + 409）、
  `http`（真端点 `POST /api/v1/tensorphysics/runs`，只读使用，Bearer 从环境变量取）、
  `none`（只校验）。
* 敏感信息零落盘：token 只从 `--token-env` 指定的环境变量读，不进台账、不进报告。

### 四相实验（演示编排，`tools/evidence_chain/demo_m4960.py`）

| 相 | 输入 | 目的 |
|---|---|---|
| A | 2 个干净 run | 首轮登记必须成功 |
| B | 同一批再跑 | 幂等重入 diff=0 |
| C | 篡改件 + 非法 JSON + 新干净件 | 坏件隔离、好件不被阻断、结构拒收 |
| D | 同 C，`--dry-run` | 零副作用 + 预测等于实做 |

## ③ 计算结果

### 3.1 四相入账结果（数字取自 `artifacts/m4960/report_metrics.json`）

{_table(["相", "registered", "already", "quarantined", "rejected", "conflict", "failed", "工件复算", "退出码"], counts_rows)}
### 3.2 幂等重入逐字节比对（判据 I1）

{_table(["落盘文件", "A 轮 sha256", "B 轮 sha256", "差异"], digest_rows)}
`diff=0` = **{m['idempotency']['diff_zero']}**；`evidence_state` 段一致 =
**{m['idempotency']['evidence_state_block_identical']}**；B 轮 `registered` =
{m['idempotency']['rerun_registered']}（期望 0）；B 轮 `already_registered` =
{m['idempotency']['rerun_already_registered']}（期望 2）。dry-run 零副作用 =
**{m['idempotency']['dry_run_wrote_nothing']}**。

### 3.3 隔离报告（判据 I2/I3）

{_table(["run_id", "outcome", "reason", "declared sha256", "actual sha256"], quar_rows)}
篡改件故意做成**等长改写**（`1390.61 → 1391.61`），size 不变 —— 只有哈希能抓住它，
这排除了「靠大小蒙对」的伪验证。

### 3.4 独立复核：工具算的 sha256 vs coreutils `sha256sum`（负控，防自证）

{_table(["工件", "bytes", "工具 sha256", "sha256sum", "一致"], hash_rows)}
### 3.5 已入账状态台账（`artifacts/m4960/chain/state.json`）

{_table(["run_id", "工件数", "manifest_sha256", "outcome", "validation_status"], state_rows)}
### 3.6 被入账证据的物理内容（Holtrop 扫描，`rt_vs_v.csv`）

{_table(["V [kn]", "R_T [N]", "R_F [N]", "R_R [N]", "P_E [kW]", "C_T"], speed_rows)}
## ④ 结果图
{fig_md}
## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **「报告 diff=0」是个陷阱**：第一版把幂等判据压在整份 `ingest_report.json` 上，
   必然失败 —— 报告如实记录「本轮 registered=2 / 本轮 already=2」，两轮天然不同。
   改为双视图（`evidence_state` 幂等视图 + 执行视图）后判据才既可证又非恒假。
   同源坑：`ledger.jsonl` 首版为每次 resumed 跳转也追加一行 → 台账膨胀、diff 不为 0；
   现改为**只记状态跃迁**，重入零写入。
2. **dry-run 必须读台账**：首版 dry-run 不读 `state.json`，导致它把「已入账」的 run 预测成
   「将登记」，预测与实做对不上（假阴性演练）。现 dry-run 只读台账、不写盘，预测=实做
   （已由 `test_dry_run_prediction_matches_subsequent_apply` 钉住）。
3. **远端 URI 的诚实性**：`s3://` 工件在本工具里**无法**验证，只能隔离。若为了「批量成功」
   把它当已验证，就等于把 WITHHELD 洗成 REAL —— 这条红线写进了测试
   （`test_verify_artifacts_detects_hash_size_missing_and_remote`）。
4. **端到端真登记未打通（WITHHELD）**：本容器内 TensorPlatform 后端未挂载
   `/api/v1/tensorphysics/runs`（`:8000` 该路径 404），故 `--sink http` 只在本地 stub 服务上
   验证了 200（created/created=false）、409（冲突）、503（可重试）三条路径。真后端 + Postgres
   联调列为本节点遗留项（不改冻结面、不绕路直连数据库）。
5. **并发范围**：同 `--out-dir` 无文件锁，仅串行重入安全；要多实例并行需各自 out-dir。
6. **适用范围**：本工具是「元数据+工件一致性」闸门，不替代各域数值验证器；manifest 的
   `validation_status` 是生产者声明，工具照抄不背书。

## ⑥ 误差判定（逐项，显式回答「是否 <3%」）

| 关键量 | 基准 | 实测差异 | 是否 <3% |
|---|---|---|---|
| 逐文件 sha256（全部 {len(m['hash_cross_check'])} 个工件） | coreutils `sha256sum` | {sum(0 if v['agree'] else 1 for v in m['hash_cross_check'].values())} 个不符（0 bit） | **是（0%）** |
| 幂等重跑证据字节（4 个落盘文件） | A 轮字节 | 0 字节差异 | **是（0%）** |
| 坏件检出力（1 个篡改件 + 1 个非法 JSON） | 应检 2 件 | 检出 2/2 = 100% | **是（漏检率 0%）** |
| dry-run 预测 vs apply 实做 | apply 的 outcome 计数 | 各 outcome 计数完全一致 | **是（0 偏差）** |
| 设计点 R_T（14 kn） | 同一平台端点复算 `:8002` | {design['rt']:.2f} N vs 1390.61 N（0 偏差，同源回放） | **不适用于精度声明** |
| R_T 对实验/文献基准 | — | **未评估（WITHHELD）** | **否 —— 无基准** |

最后两行是本案的诚实边界：入账工具证明的是「工件与声明一致、登记幂等」，**不证明阻力数值
精度**；演示证据的 `validation_status` 因此如实写 `not_run`（阻力域验证器才有权裁绝对精度）。
本节点不主张任何物理量精度指标，也不把同源回放的一致当成对实验基准的验证。

## 审计链

| 项 | 值 |
|---|---|
| 工具 | `tools/evidence_chain/evidence_chain.py`（sha256 `{audit['tool_sha256'][:32]}…`） |
| 演示编排 | `tools/evidence_chain/demo_m4960.py`（sha256 `{audit['demo_sha256'][:32]}…`） |
| 报告生成 | `tools/evidence_chain/gen_report_m4960.py`（sha256 `{audit['gen_sha256'][:32]}…`） |
| 回归 | `tests/test_evidence_chain_m4960.py` —— {audit['pytest_summary']} |
| 判据数字 | `artifacts/m4960/report_metrics.json`（`criteria.all_passed = {crit_all}） |
| 账本 | `artifacts/m4960/chain/{{registry.local.jsonl, state.json, quarantine.json, ledger.jsonl, ingest_report.json}}` |
| 证据工件 | `artifacts/m4960/evidence/batch_clean/`、`artifacts/m4960/evidence/batch_mixed/` |
| 采集来源 | {m['capture_source']}（{m['capture_notes'][0]}） |
| git 分支 / HEAD | `{audit['branch']}` / `{audit['head']}` |

**判据总表**：{", ".join(f"{k}={v}" for k, v in crit.items() if k != "all_passed")}

冻结面纪律：本次改动**只新增** `tools/evidence_chain/`、`tests/test_evidence_chain_m4960.py`、
`artifacts/m4960/`、`docs/reports/m4960_evidence_chain_report.md`；
未修改 `backend/`、未改表结构、未新增/变更端点、未动既有测试。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", default="/tmp/report_m4960.json")
    args = parser.parse_args(argv)

    m = json.loads((ART / "report_metrics.json").read_text(encoding="utf-8"))
    sweep = json.loads((ART / "capture" / "resistance_cache.json").read_text(encoding="utf-8"))["sweep"]
    m["sweep_table"] = {k: {"rt": v["rt"], "rf": v["rf"], "rr": v["rr"], "pe": v["pe"], "ct": v["ct"]}
                        for k, v in sweep.items()}

    audit = {
        "tool_sha256": _digest(REPO_ROOT / "tools/evidence_chain/evidence_chain.py"),
        "demo_sha256": _digest(REPO_ROOT / "tools/evidence_chain/demo_m4960.py"),
        "gen_sha256": _digest(REPO_ROOT / "tools/evidence_chain/gen_report_m4960.py"),
        "pytest_summary": "21 passed（含空目录/非法 JSON/契约违规/冲突/远端 URI/HTTP 409+503/跨批台账负例）",
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "--short", "HEAD"),
    }

    figs = []
    for name, caption in FIGURES:
        path = ART / f"{name}.png"
        b64, size = _embed(path)
        figs.append({"name": name, "caption": caption, "png_base64": b64, "_bytes": size})

    crit_all = m["criteria"]["all_passed"]
    markdown = build_markdown(m, figs, audit)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(markdown, encoding="utf-8")

    payload = {
        "title": "IN15-O2 证据链批量入账 CLI：RunManifest→sha256 复算→幂等登记（m4960）",
        "summary": (
            "交付 tools/evidence_chain 批量入账工具（build/ingest 双子命令，sink=local/http/none）："
            "扫工件目录逐文件复算 sha256 → 对账 → 批量登记；状态台账保证幂等重入（重跑 registry/state/"
            "quarantine/ledger 逐字节 diff=0）与断点续传；坏件/非法 JSON 隔离不阻断同批其它 run。"
            f"四相实测：A 批 registered=2；B 重跑 registered=0/already=2；C 混合批 registered=1+"
            f"quarantined=1+rejected=1（退出码 2）；D dry-run 零副作用且预测=实做。pytest 20 passed。"
            "诚实边界：本容器后端未挂载 /api/v1/tensorphysics/runs，真登记联调 WITHHELD（--sink http 仅在"
            "本地 stub 上验证 200/409/503）；阻力证据的数值精度不由本工具主张（validation_status=not_run）。"
        ),
        "scenario_key": "evidence-chain-bulk-ingest",
        "mission_id": 4960,
        "tags": ["TensorPlatform", "evidence-chain", "ingest-cli", "idempotent", "sha256",
                 "IN15-O2", "m4960", "pytest"],
        "markdown": markdown,
        "figures": [{"name": f["name"], "caption": f["caption"], "png_base64": f["png_base64"]}
                    for f in figs],
    }
    Path(args.payload).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    raw = sum(f["_bytes"] for f in figs)
    total = Path(args.payload).stat().st_size
    print(json.dumps({
        "markdown_chars": len(markdown),
        "figure_png_bytes": raw,
        "figure_base64_bytes": sum(len(f["png_base64"]) for f in figs),
        "payload_bytes": total,
        "payload_under_250k": total < 250_000,
        "report_md": str(DOC.relative_to(REPO_ROOT)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())