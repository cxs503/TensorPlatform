# 盲预测校准账本与 run-manifest 回填器 —— 机器可裁审计表

> 本文件由 `python3 -m tools.blind_calibration audit` 程序化生成，所有数字直接取自 `artifacts/blind_calibration/*.json`（零手抄）。

## 1. 判据总表

| 判据 | 阈值 | 实测 | 结果 |
|---|---|---|---|
| (a) 可靠性曲线最大偏差 | < 0.1 | 0.055 | PASS |
| (b) Brier < 随机基线 | < 0.25 | 0.0210 | PASS |
| (c) Murphy 恒等式 | 残差 < 1e-10 | 0.000000000000 | PASS |
| 盲性（commit 早于 observe） | 违规=0 | 0 违规 / 140 项 | PASS |
| (d) E1–E5 槽位完整率 | = 100% | 100.0% (50/50) | PASS |
| (e) 工件 SHA256 锚定 | 全部匹配 | 52/52 | PASS |

## 2. 计分对照（内部盲程序 vs 外部预测源）

| 数据集 | n | 占用箱/箱数 | Brier(逐样本) | Brier(分箱) | max\|p−f\| | REL | RES | UNC | 分解残差 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| sealed blind program | 140 | 3/10 | 0.0210 | 0.0205 | 0.055 | 0.0026 | 0.1096 | 0.1275 | -0.000000000000 | PASS |
| external prior holdout | 694 | 4/10 | 0.3873 | 0.3960 | 0.662 | 0.1820 | 0.0336 | 0.2476 | 0.000000000000 | FAIL |

## 3. 逐箱可靠性（判据 a 的展开）

**sealed blind program**

| 箱 | 区间 | n | 平均概率 p̄ | 实际频率 f | p̄−f |
|---|---|---|---|---|---|
| 0 | [0.00,0.10) | 18 | 0.020 | 0.000 | 0.020 |
| 1 | [0.10,0.20) | 0 | — | — | — |
| 2 | [0.20,0.30) | 0 | — | — | — |
| 3 | [0.30,0.40) | 0 | — | — | — |
| 4 | [0.40,0.50) | 0 | — | — | — |
| 5 | [0.50,0.60) | 0 | — | — | — |
| 6 | [0.60,0.70) | 0 | — | — | — |
| 7 | [0.70,0.80) | 0 | — | — | — |
| 8 | [0.80,0.90) | 18 | 0.880 | 0.833 | 0.047 |
| 9 | [0.90,1.00) | 104 | 0.945 | 1.000 | -0.055 |

**external prior holdout**

| 箱 | 区间 | n | 平均概率 p̄ | 实际频率 f | p̄−f |
|---|---|---|---|---|---|
| 0 | [0.00,0.10) | 0 | — | — | — |
| 1 | [0.10,0.20) | 0 | — | — | — |
| 2 | [0.20,0.30) | 0 | — | — | — |
| 3 | [0.30,0.40) | 0 | — | — | — |
| 4 | [0.40,0.50) | 0 | — | — | — |
| 5 | [0.50,0.60) | 0 | — | — | — |
| 6 | [0.60,0.70) | 1 | 0.662 | 0.000 | 0.662 |
| 7 | [0.70,0.80) | 113 | 0.790 | 0.195 | 0.596 |
| 8 | [0.80,0.90) | 468 | 0.851 | 0.425 | 0.426 |
| 9 | [0.90,1.00) | 112 | 0.912 | 0.821 | 0.091 |

## 4. 按问句类型拆解（盲程序）

| 问句类型 | n | 平均声明概率 | 实际命中率 | Brier | max\|p−f\| |
|---|---|---|---|---|---|
| file_anchor | 52 | 0.970 | 1.000 | 0.0009 | 0.030 |
| json_parse | 18 | 0.900 | 1.000 | 0.0100 | 0.100 |
| json_top_is_dict | 18 | 0.880 | 0.833 | 0.1411 | 0.047 |
| utf8_text | 52 | 0.615 | 0.654 | 0.0033 | 0.070 |

## 5. 回填审计表（逐节点 E1–E5）

| 节点 | 等级 | 工件 | 锚定 | E1 | E2 | E3 | E4 | E5 |
|---|---|---|---|---|---|---|---|---|
| m597 | E1 | 4 | 4 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m642 | E1 | 5 | 5 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1386 | E1 | 7 | 7 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1388 | E1 | 7 | 7 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1564 | E1 | 17 | 17 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1882 | E1 | 3 | 3 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1892 | E1 | 1 | 1 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1393 | E1 | 5 | 5 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1444 | E1 | 2 | 2 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1479 | E1 | 1 | 1 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |

## 6. 溯源

- 计分账本（盲程序）：`artifacts/blind_calibration/calibration_ledger.jsonl`，链完整=True，链摘要=`698315c4b23401696732d22026535ed37ed5fa6fead78a8f3f75c579bafb650d`，数据集摘要=`793ff6755e36710ca1b3144a47e8240ad3913dc8ef0544669c4361ce689dd628`
- 计分账本（外部源）：`artifacts/blind_calibration/external_ledger.jsonl`，链完整=True，链摘要=`5e5d31ef86b41e767e01d877b558b66a08688c1c87897546c155e19bcbf905a4`
- 外部源：`pg` / 变体 `platt_prior` / 样本 694 / 数据集摘要 `4d147ce28a51cf324b9b6e1813f805e05ca67462e01dd875a8d3be5870e6351e`
- 盲程序：承诺时刻 `2026-11-10T21:40:00Z`，声明时 git 干净=True，程序摘要=`e97045601648ea2b9cd2fb6b73f3bcd820ebdc03de897545aae8316c440abde4`

