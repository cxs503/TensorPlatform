# Resume start points — plan 249

> Generated mechanically from `artifacts/salvage/*/manifest.json` by
> `python3 -m tools.salvage.cli resume-report`; no number below is hand-copied.

* plan title: 谱系继承波·多学科验证前沿与成熟化 Wave 2 (Lineage Wave 2 · Multi-disciplinary Validation Frontier & Maturity from Plan 70)
* entries: 11 (plan seed mission + campaign node missions)
* baseline: `main` @ `638d5c6d537f`
* candidate mode: `dirty`

Salvage evidence is **material, not acceptance**: no node status moves because of
this report. Each row below tells a re-dispatch which modules already exist and
which acceptance items are still open.

| mission | node | repo | ext_id | files kept | code/doc/data | pruned | scratch dropped | criteria covered |
|---|---|---|---|---|---|---|---|---|
| m6866 | None | - | PLAN-SEED | 2 | 0/1/1 | 0 | 0 | 0/0 |
| m7132 | blocked | ship-performance-platform | P71-N06 | 0 | 0/0/0 | 0 | 0 | 0/5 |
| m7133 | blocked | TensorLBM | P71-N01 | 0 | 0/0/0 | 0 | 0 | 0/6 |
| m7134 | blocked | TensorSPH | P71-N02 | 44 | 16/1/27 | 0 | 0 | 5/5 |
| m7135 | blocked | TensorBEM | P71-N03 | 0 | 0/0/0 | 0 | 0 | 0/6 |
| m7136 | blocked | TensorFEM | P71-N04 | 7 | 3/1/3 | 0 | 24 | 0/5 |
| m7137 | blocked | ship-performance-platform | P71-N05 | 0 | 0/0/0 | 0 | 0 | 0/5 |
| m7138 | blocked | ship-performance-platform | P71-N07 | 0 | 0/0/0 | 0 | 0 | 0/5 |
| m7139 | blocked | ship-performance-platform | P71-N08 | 0 | 0/0/0 | 0 | 0 | 0/7 |
| m7140 | blocked | ship-performance-platform | P71-N09 | 0 | 0/0/0 | 0 | 0 | 0/6 |
| m7141 | blocked | TensorPlatform | P71-N10 | 0 | 0/0/0 | 0 | 0 | 0/6 |

## Per-mission start points

### m6866 — 谱系继承波·多学科验证前沿与成熟化 Wave 2 (Lineage Wave 2 · Multi-disciplinary Validation Frontier & Maturity from Plan 70)

* node status observed: `None`; repo `None`; ext_id `PLAN-SEED`
* manifest: `m6866/manifest.json` (sha256 `354354f8355a09f0…`)
* inherited material (2 files):
  * `docs/ARBITER_SEED_REPORT.md`
  * `artifacts/seed_report.json`

### m7132 — ITTC-78 模型-实船阻力换算成熟化

* node status observed: `blocked`; repo `ship-performance-platform`; ext_id `P71-N06`
* manifest: `m7132/manifest.json` (sha256 `d68ce4a64309d500…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] (1+k)与经验公式相对误差≤10%
  * [0.00] C_A落在ITTC-78经验带±20%
  * [0.00] ΔC_F落在ITTC-1978推荐值±15%
  * [0.00] 实船阻力预报相对误差≤10%
  * [0.00] pytest断言通过

### m7133 — TensorLBM 自由衰减各向同性湍流 LES 验证面

* node status observed: `blocked`; repo `TensorLBM`; ext_id `P71-N01`
* manifest: `m7133/manifest.json` (sha256 `6e05616fd3043b2f…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 能谱E(k)与CBC1971实验相对误差≤20%
  * [0.00] 衰减指数n∈[1.15,1.30]
  * [0.00] 惯性子区指数与-5/3相差≤5%
  * [0.00] 128^3 vs 64^3能谱积分误差≤10%
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7134 — TensorSPH 楔形体入水冲击验证面

* node status observed: `blocked`; repo `TensorSPH`; ext_id `P71-N02`
* manifest: `m7134/manifest.json` (sha256 `b3d6aaae92a45624…`)
* inherited material (44 files):
  * `benchmarks/claim_validity/validity.py`
  * `benchmarks/verification_ledger/build_ledger.py`
  * `benchmarks/verification_ledger/make_overcredit_doc.py`
  * `benchmarks/wedge_entry_small_beta/run.py`
  * `benchmarks/wedge_wagner_consistency/alpha_probe.py`
  * `benchmarks/wedge_wagner_consistency/make_doc.py`
  * `benchmarks/wedge_wagner_consistency/make_report.py`
  * `benchmarks/wedge_wagner_consistency/run.py`
  * `pyproject.toml`
  * `runs/alpha_probe.py`
  * `src/tensorsph/wedge_entry.py`
  * `src/tensorsph/wedge_wagner.py`
  * `tests/test_contact_angle_instrument_m6503.py`
  * `tests/test_lint_gate.py`
  * `tests/test_wedge_wagner.py`
  * `tests/test_wedge_wagner_runner_contract.py`
  * `docs/verification_wedge_wagner_consistency.md`
  * `docs/figures/fig1_field_contour.png`
  * `docs/figures/fig2_pmax_vs_beta.png`
  * `docs/figures/fig3_wetting_law.png`
  * `docs/figures/fig4_compressibility.png`
  * `docs/figures/fig5_profile.png`
  * `runs/alpha_probe_0.02.json`
  * `runs/alpha_probe_0.02.log`
  * … 20 more in the manifest
* acceptance items with an evidence candidate (lexical pre-screen only):
  * [0.80] 压力峰值与Wagner理论相对误差≤15% → `benchmarks/wedge_wagner_consistency/make_report.py`
  * [0.50] 湿面扩展斜率与理论相差≤10% → `benchmarks/wedge_wagner_consistency/make_report.py`
  * [0.50] 射流根位置与理论相差≤10%半宽 → `benchmarks/wedge_wagner_consistency/make_report.py`
  * [0.67] 负控通过 → `benchmarks/claim_validity/validity.py`
  * [0.75] pytest断言通过 → `benchmarks/wedge_wagner_consistency/make_report.py`
* static usability of salvaged code: {'usable': 8}

### m7135 — TensorBEM Wigley 船体六自由度运动 RAO 验证面

* node status observed: `blocked`; repo `TensorBEM`; ext_id `P71-N03`
* manifest: `m7135/manifest.json` (sha256 `dee8fcb100f8f897…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] heave RAO峰值频率与实验相差≤5%
  * [0.00] heave RAO峰值幅值与实验相对误差≤15%
  * [0.00] pitch RAO峰值频率与实验相差≤5%
  * [0.00] roll RAO共振幅值与实验相对误差≤20%
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7136 — TensorFEM 加筋板格 Smith 渐进塌陷验证面

* node status observed: `blocked`; repo `TensorFEM`; ext_id `P71-N04`
* manifest: `m7136/manifest.json` (sha256 `70760b2ee966323d…`)
* inherited material (7 files):
  * `benchmarks/smith_csr_panel_interaction.py`
  * `src/tensorfem/panel_smith_csr_verification.py`
  * `tests/test_panel_smith_csr_verification.py`
  * `tests/slow_lane_manifest.txt`
  * `artifacts/smith_csr_panel/psi000_n50.json`
  * `artifacts/smith_csr_panel/psi025_n50.json`
  * `artifacts/smith_csr_panel/psi050_n50.json`
* **missing** acceptance items (no salvaged material matched):
  * [0.09] 极限承载力与Smith方法相对误差≤10%
  * [0.00] CSR交互曲线5点均满足≤1.05
  * [0.00] 网格收敛≤3%
  * [0.00] 负控通过
  * [0.25] pytest断言通过
* static usability of salvaged code: {'import_error': 2, 'usable': 1}

### m7137 — 螺旋桨空泡初生包络线验证面

* node status observed: `blocked`; repo `ship-performance-platform`; ext_id `P71-N05`
* manifest: `m7137/manifest.json` (sha256 `4107e60382451cdc…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 空泡初生包络线与Burrill曲线相对误差≤15%
  * [0.00] Strasburg修正后误差≤10%
  * [0.00] 尺度效应换算差异在10%~30%
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7138 — 横摇阻尼 Ikeda 五分量分解 + 船体梁湿模态交叉验证面

* node status observed: `blocked`; repo `ship-performance-platform`; ext_id `P71-N07`
* manifest: `m7138/manifest.json` (sha256 `aa1442701f4e4d46…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 横摇阻尼与实验相对误差≤20%
  * [0.00] 湿模态频率比落在0.55~0.75
  * [0.00] MAC_ii≥0.90(前3阶)
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7139 — 螺旋桨脉动压力→船体振动响应链

* node status observed: `blocked`; repo `ship-performance-platform`; ext_id `P71-N08`
* manifest: `m7139/manifest.json` (sha256 `7b7d941caccf780f…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 叶频脉动压力与ITTC推荐值相对误差≤20%
  * [0.00] 倍叶频比值落在0.2~0.5
  * [0.00] 衰减率与e^(-3z/D)拟合指数相差≤15%
  * [0.00] a_peak与实船测量相对误差≤30%
  * [0.00] a_peak随n²标度指数与2相差≤15%
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7140 — 装载工况→静水剪力/弯矩分布的装载仪内核

* node status observed: `blocked`; repo `ship-performance-platform`; ext_id `P71-N09`
* manifest: `m7140/manifest.json` (sha256 `ce92db0f218897bc…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 端部条件|Q(0)|,|Q(L)|,|M(0)|,|M(L)|<1e-10
  * [0.00] 总量恒等式|∫w-∫b|/Δ<1e-10
  * [0.00] Q_max/M_max利用率≤100%
  * [0.00] 梯形vs Simpson差异≤0.1%
  * [0.00] 负控通过
  * [0.00] pytest断言通过

### m7141 — 历史验收声明的重跑复证与漂移记账成熟化

* node status observed: `blocked`; repo `TensorPlatform`; ext_id `P71-N10`
* manifest: `m7141/manifest.json` (sha256 `1b08bb8c7bf4b6a2…`)
* inherited material (0 files):
* **missing** acceptance items (no salvaged material matched):
  * [0.00] 抽样覆盖≥50个done节点≥5个repo
  * [0.00] repro_rate以95%Wilson置信区间报告区间宽度≤20%
  * [0.00] drift_report.json包含漂移类型分类
  * [0.00] repro_pipeline.py可独立执行
  * [0.00] 负控通过
  * [0.00] pytest断言通过

## Acceptance run (machine-checked)
* A1: PASS — replay plan 249: every entry classified and manifest sha256 re-computed
* A2: PASS — negative control: 3 merged done missions, 100% merged-content prune, zero false partial
* A3: PASS — status invariance: campaign_nodes.status snapshot diff = 0 across backfill
* A4: PASS — static usability: py_compile/import verdict for at least 8 code files, no assumed usability
* A5: PASS — idempotency: two replays produce byte-identical manifests
* all_passed: True
* evidence rows (kind=`salvage`): 3
* PostgreSQL backfill channel: `blocked` (                                   ^)

