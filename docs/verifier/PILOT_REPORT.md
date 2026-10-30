# Pilot 台账：阻力计算验证（端到端）

## 基本信息

| 字段 | 值 |
|------|-----|
| Mission ID | 537 |
| 试点域 | resistance（阻力） |
| 候选 ID | pilot-resistance-v1 |
| 候选版本 | 1.0.0 |
| 验证器 | tp-ver-resistance |
| 题库路径 | tools/verifier/question_bank/questions.db |
| 题库权限 | 0o600 (secure) |
| 台账路径 | artifacts/credit_ledger.jsonl |
| 报告路径 | artifacts/pilot_verification_report.json |

## 出题阶段（Verifier 角色）

### 题库生成

```
python tools/verifier/generate_questions.py
```

生成 15 道题目，全部 commit（冻结）：

| # | 题目 | 域 | 难度 | 出处 |
|---|------|-----|------|------|
| 1 | ITTC-1957 Cf at Re=1e6 | resistance | basic | ITTC-1957 |
| 2 | ITTC-1957 Cf at Re=5e6 | resistance | basic | ITTC-1957 |
| 3 | ITTC-1957 Cf at Re=1e7 | resistance | basic | ITTC-1957 |
| 4 | ITTC-1957 Cf at Re=1e8 | resistance | basic | ITTC-1957 |
| 5 | ITTC-1957 Cf at Re=1e9 | resistance | basic | ITTC-1957 |
| 6 | Laminar flat plate Cf at Re_L=1e5 | resistance | basic | Blasius 1908 |
| 7 | Laminar flat plate Cf at Re_L=5e5 | resistance | basic | Blasius 1908 |
| 8 | Laminar flat plate Cf at Re_L=1e6 | resistance | basic | Blasius 1908 |
| 9 | Turbulent flat plate Cf at Re_L=1e6 | resistance | intermediate | Prandtl-Schlichting |
| 10 | Turbulent flat plate Cf at Re_L=1e7 | resistance | intermediate | Prandtl-Schlichting |
| 11 | Turbulent flat plate Cf at Re_L=1e8 | resistance | intermediate | Prandtl-Schlichting |
| 12 | Turbulent flat plate Cf at Re_L=1e9 | resistance | intermediate | Prandtl-Schlichting |
| 13 | Holtrop-Mennen form factor: Series 60 Cb=0.6 | resistance | intermediate | Holtrop 1982 |
| 14 | Holtrop-Mennen form factor: Bulk carrier Cb=0.7 | resistance | intermediate | Holtrop 1982 |
| 15 | Holtrop-Mennen form factor: Tanker Cb=0.8 | resistance | intermediate | Holtrop 1982 |

### 权限验证

```python
check_worker_access("question_bank/questions.db")
# → {"secure": true, "file_mode": "0o600"}
```

## 实现阶段（Worker 角色）

候选实现：`tools/verifier/pilots/resistance_calculator.py`

实现内容：
- ITTC-1957 摩擦系数公式
- Blasius 层流平板公式
- Prandtl-Schlichting 湍流平板公式
- Holtrop-Mennen 形状因子简化公式

Worker **不可见**题库内容（权限隔离）。

## 验收阶段（Verifier 角色）

### 运行命令

```bash
python tools/verifier/tp_ver_resistance.py \
  --candidate pilots/resistance_calculator.py \
  --candidate-id pilot-resistance-v1 \
  --mission-id 537 \
  --self-test-result pass \
  --populate
```

### 验收结果

| 指标 | 值 |
|------|-----|
| 总题数 | 15 |
| 通过 | 15 |
| 失败 | 0 |
| 错误 | 0 |
| 通过率 | 100% |
| 总判定 | **PASS** |
| 耗时 | 1.77s |

### 逐题偏差

| 题目 | 偏差 | 容差 | 判定 |
|------|------|------|------|
| ITTC-1957 Re=1e6 | 0.0 | 0.1% | ✓ |
| ITTC-1957 Re=5e6 | 0.0 | 0.1% | ✓ |
| ITTC-1957 Re=1e7 | 0.0 | 0.1% | ✓ |
| ITTC-1957 Re=1e8 | 0.0 | 0.1% | ✓ |
| ITTC-1957 Re=1e9 | 0.0 | 0.1% | ✓ |
| Blasius Re_L=1e5 | 0.0 | 0.1% | ✓ |
| Blasius Re_L=5e5 | 0.0 | 0.1% | ✓ |
| Blasius Re_L=1e6 | 0.0 | 0.1% | ✓ |
| P-S Re_L=1e6 | 0.0 | 0.5% | ✓ |
| P-S Re_L=1e7 | 0.0 | 0.5% | ✓ |
| P-S Re_L=1e8 | 0.0 | 0.5% | ✓ |
| P-S Re_L=1e9 | 0.0 | 0.5% | ✓ |
| HM Cb=0.6 | 0.0 | 1.0% | ✓ |
| HM Cb=0.7 | 0.0 | 1.0% | ✓ |
| HM Cb=0.8 | 0.0 | 1.0% | ✓ |

## Credit Ledger 记录

```json
{
  "id": "f68aebd7-e3cb-45e9-b55c-db08d19bfdcc",
  "candidate_id": "pilot-resistance-v1",
  "candidate_version": "1.0.0",
  "mission_id": 537,
  "verifier_id": "tp-ver-resistance",
  "domain": "resistance",
  "verdict": "pass",
  "questions_passed": 15,
  "questions_failed": 0,
  "questions_total": 15,
  "pass_rate": 1.0,
  "evidence_hash": "8ffcbb67592931eda40b921237dae125159f73c290e58ea28e15a81f73e911c9",
  "self_test_result": "pass",
  "self_test_note": "Self-test results are informational only; acceptance requires independent verification"
}
```

## 机器判据验证

| 判据 | 验证 | 结果 |
|------|------|------|
| 题库对 worker 不可读 | `check_worker_access()` → secure=true | ✓ PASS |
| 试点台账可回放 | `artifacts/credit_ledger.jsonl` 含完整记录 | ✓ PASS |
| verifier 裁决落 credit_ledger | entry with evidence_hash | ✓ PASS |
| 自测不替代验收 | self_test_note 显式声明 | ✓ PASS |
| 12 项单元测试全通过 | `pytest -v` → 12 passed | ✓ PASS |
