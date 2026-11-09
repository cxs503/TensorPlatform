# repro_audit —— 历史验收声明的重跑复证工具（P70-N08）

只读、幂等、可被后续波次复用的复证流水线。唯一写库路径是 `run_audit.py --persist`（写
`tensor_guide_tracker`），其余步骤只产出 `artifacts/` 下的工件。

## 四个脚本

| 脚本 | 作用 | 是否有副作用 |
|---|---|---|
| `scan_select.py` | 从 `campaign_nodes`(done) 分层抽样「带可执行工件」节点，并锚定工件 sha256 | 只读 DB / git，写 `artifacts/repro_audit/sample.json` |
| `probe_runner.py` + `launch.py` + `shims.py` | 集群侧探针：干净检出内两轮重放（pytest + 工件脚本），逐项比对 | 只写检出内的 `_repro/out/` |
| `run_audit.py` | 编排：干净检出 → sha256 复核 → tar 直达集群 → 判决 → 归因 → 汇总 → （可选）KPI 落库 | `--persist` 写库；其余只写工件 |
| `make_report.py` + `splice_report.py` | 图 + 程序化注入的数字表 + 报告缝合 | 只写 `artifacts/`、`docs/repro_audit/` |

## 标准一次跑

```bash
# 1) 抽样（约 1.5 min，扫 ~450 个 done 节点）
python3 tools/repro_audit/scan_select.py --n 36

# 2) 主跑：断言通道（每节点 ≤600 s，集群直跑，不压 99）
python3 tools/repro_audit/run_audit.py                 # → artifacts/repro_audit.json + nodes/m*.json

# 3) 数值通道：证据驱动重放（谁真的写出了提交里的 JSON 就比谁）
IDS=$(python3 -c "import json;d=json.load(open('artifacts/repro_audit/sample.json'));print(','.join(str(n['mission_id']) for n in d['nodes'] if n['spec']['numeric']))")
python3 tools/repro_audit/run_audit.py --only $IDS --auto-outputs \
        --out /tmp/num.json --node-dir artifacts/repro_audit/nodes_num
python3 tools/repro_audit/run_audit.py --merge-dir artifacts/repro_audit/nodes_num   # 合并数值面

# 4) 出图出表 + 缝合报告 + KPI 落库
python3 tools/repro_audit/make_report.py --audit artifacts/repro_audit.json
python3 tools/repro_audit/splice_report.py
python3 tools/repro_audit/run_audit.py --persist-from artifacts/repro_audit.json
```

## 判据（改这些常数等于改判据，务必同步文档）

`canonical.py`: `REL_TOL=1e-6`（observed）、`ABS_TOL=1e-9`（diagnostic）、`NEAR_ZERO=1e-8`、
`ULP_GATE=65536` 与 `QUANT_DIGITS=12` 对齐 `docs/REPRODUCIBILITY_CROSS_ISA.md`。
KPI：`repro_rate = pass / (pass + drift)`，**blocked 不进分母**，落库前用同一 JSON 重算，
要求 `|KPI − 重算| ≤ 1e-9`。

## 三个已知坑（都是实测踩出来的）

1. **「没写出」≠「复现」**：脚本可能根本没碰该工件（静态映射猜错）。判据必须落在
   「本轮确实写出」（哨兵 mtime）而不是「hash 变了」——逐位复现时 hash 不变，
   用 hash 判会把最理想的 pass 判成「没写」。
2. **导入面替身**：集群无 matplotlib/fastapi/pydantic，`shims.py` 只替换导入面（绘图 no-op、
   `APIRouter/BaseModel` 空壳），不动数值代码；替身清单写进每个节点工件，代价是 API 层行为不可复证。
3. **机器通道只有 ~8 KB 尾部**：探针 stdout 必须紧凑序列化 + 明细截断，否则 JSON 被截断
   会被误判 blocked。

## 复用建议

* 新战役验收后，把 `scan_select.py --n <N>` 指向新节点即可复算 repro_rate；
* 想让某节点从「断言通道」升级到「数值通道」，只需在仓库里补一个**入库的** `make_artifacts.py`
  （写 JSON），本工具会自动发现并重放。