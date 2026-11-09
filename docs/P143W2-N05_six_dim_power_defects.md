# P143W2-N05 · 六维统计功效门（`tools/abm/six_dim_power.py`）缺陷修复登记

任务 m8646 · 分支 `agent/m8646` · 基线 main `acf12eab`

## 结论速览

| # | 缺陷 | 修复前（可复算） | 修复后 |
|---|---|---|---|
| D1 | MDE(n) 曲线塌陷：`_estimate_power` 造数据 `treat = ctrl + d·σ`，配对差方差为 0 → Cohen's d = inf | n=6/20/100 **全报 0.01**（与 n 无关，偏差两个数量级） | 1.5058 / 0.6541 / 0.2507，与解析 MDE（1.3374 / 0.6544 / 0.2826）误差 12.6% / 0.04% / 11.3% |
| D2a | 回溯功效模拟噪声写成 `σ_diff×0.1` → 模拟 d 被放大 10 倍 | 功效 1.0 vs 独立重抽真值 0.53（高估 0.47） | 0.595 vs 0.53（误差 0.065） |
| D2b | 子种子来自内建 `hash(dim)`，受 `PYTHONHASHSEED` 随机化 | 三进程三组不同值（`[0.17,0.205]` / `[0.135,0.16]` / `[0.17,0.19]`） | 三进程严格一致（`[0.07,0.045]`） |
| D3 | `aa_check` 只落库，不参与判决 | A/A 校准失控（fpr=0.5）时判决照旧 `pass` | 校准失控 → `fail` + "p 值不可信，判决作废" |
| D4 | 零显著一律判 `fail`（把"没本事看出"当成"没有"） | n=6 无效应 → `fail` | → `inconclusive`（`n < n_required=34`） |
| D5 | 置换检验每轮重建列表 + 两趟遍历 | 37.0 ms / 1000 perm | 5.5 ms（**6.73×**） |

另：`mde_curve` 现在每点附带解析对照 `mde_analytic`、不可达时返回 `mde=None` + `note`（n=5
时符号翻转检验的 p 值下限 ~0.06，目标功效不可达——显式声明，不伪造点）；
门判决新增 `calibration_ok` / `n_required_for_min_effect` / `sample_underpowered` /
`mde_analytic_at_n` 四个可审计字段。

## 已知未修 / 语义变更

- `fwer_controlled` 在 Holm 校正下恒真，属已知弱字段，本轮未动（牵动验收链路）。
- 零显著 + 欠功效的判决由 `fail` 改为 `inconclusive`：**下游把 `fail` 当"能力缺失"记账的
  历史判决需按新语义复核**。旧测试断言已同步更新，并新增两条对照测试
  （n=60 充足 → `fail`；n=8 不足 → `inconclusive`）。
- 退化情形（配对差零方差）的显式分支是**防御性改动**：实测与基线数值一致
  （都触到 `1/(n_perm+1)` 下限），**不计入缺陷修复**。

## 复现

```bash
# 红/绿基线（同一探针，基线模块从 git 动态取出）
git show acf12eab:tools/abm/six_dim_power.py > /tmp/orig_sdp.py
python runs/m8646/six_dim_power_defect_probe.py --module-path /tmp/orig_sdp.py   # 红
python runs/m8646/six_dim_power_defect_probe.py                                  # 绿

# 全部测量 + 两张图 + 报告载荷再生成
python runs/m8646/build_report.py --write-report

# 回归测试
python -m pytest tests/abm -q
```

工件：`artifacts/m8646/six_dim_power_fixes.json`（原始测量）、
`artifacts/m8646/fig1_mde_curve.png`、`artifacts/m8646/fig2_retro_power.png`、
`docs/p143w1_n02_report.json`（报告载荷，全部数字由脚本注入）。

## 关于 `docs/p143w1_n02_report.json`

该路径原内容（522 KB）实际是 **m7287 / IN100-N04 货物系固链** 的报告，属错放；
本任务写回 ABM 六维功效门的报告载荷，原件保留在 git 历史 `acf12eab` 中，未丢失。