# m811 正例三门体检（脚本渲染）

> 数据源：`outputs/e2e1_b1/m811_positive_check.json`（`python3 -m src.sample_db.inspect ... --api` 产出，
> mission 终态取自平台 `GET /tensor/missions/811`）。

**结论：NOT_ADMISSIBLE —— 三门中 1 门通过（mission）；未通过/不可评估：manifest, checksum → 不可准入**

| 门 | 状态 | 说明 |
|---|---|---|
| manifest | fail | no RunManifest found (looked for run_manifest.json/manifest.json in the run directory and an embedded 'manifest' key); unverified products must not enter the sample library |
| mission | pass | mission 811 terminal state succeeded (api) |
| checksum | not_evaluable | no manifest ⇒ 没有任何声明哈希可用作复算基准（不是「哈希对得上」） |

run 目录文件指纹（sha256 实测）：

| 文件 | size_bytes | sha256 |
|---|---|---|
| RESULTS.md | 9087 | `af28e39b6b55fe1ab3bdd74d0544a188ea7ee3c1b5bf06623e0a982a5c35e578` |
| profiles.csv | 73625 | `de430af3886fea48ab266c7d26bc0e3996ecbbabce1924fd47534d8b10920c64` |
| summary.json | 44721 | `9dce9b038370c0de22cf986745dc82ced947aafe698d2990a7d4c007ebb7fa53` |

缺失项：

- 门1 RunManifest 缺失：run 目录下无 run_manifest.json / manifest.json / RunManifest.json，产物内也无 run_manifest / manifest / RunManifest 键
- 门3 关键文件哈希无法复算：manifest 缺失 → 没有声明哈希可作复算基准

补齐路径：

- 补写 RunManifest（contracts/tensorphysics-run-manifest-v1.schema.json）：run 目录下 run_manifest.json 或产物内嵌 manifest 键，至少含 schema_version/run_id/status/validation_status/outputs[]（每个 output 需 uri + media_type + checksum_sha256(64hex) + size_bytes）

局限：`run_dir_inventory` 只列 run 目录顶层前 12 个文件（体检读数，不是全量清单）；
本体检是只读模式（不写准入流水），生产入库必须走 `src.sample_db.ingest` 并留痕。
