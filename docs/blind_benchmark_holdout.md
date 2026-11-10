# Blind Benchmark Holdout — Pre-registration + Sealed Data + Adversarial Scoring

## Overview

The Blind Benchmark Holdout system transforms "tuning against the answer" into machine-adjudicable FAIL. It implements a pre-registration protocol where:

1. **Datasets are sealed** (encrypted) before any solver can access them
2. **Solvers submit predictions** without access to ground truth (write-only)
3. **Adjudicator decrypts and scores** predictions against sealed truth
4. **Adversarial fixtures** verify the system correctly identifies FAIL
5. **Results are backfilled** to guide.benchmark_cases with full provenance

This eliminates the possibility of "looking at the answer while tuning parameters" — a common source of overfitting in benchmark validation.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BLIND BENCHMARK PIPELINE                  │
└─────────────────────────────────────────────────────────────┘

  ┌──────────────┐
  │  BENCHMARK   │
  │   DATASET    │
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      ┌──────────────────┐
  │   SEALER     │─────▶│  SEALED DATASET  │
  │ (encrypt +   │      │  + MANIFEST      │
  │  manifest)   │      │  (public)        │
  └──────────────┘      └────────┬─────────┘
                                 │
                                 │ dataset_id
                                 ▼
  ┌──────────────┐      ┌──────────────────┐
  │   SOLVER     │─────▶│   SUBMISSION     │
  │ (predictions │      │   ARTIFACT       │
  │  only)       │      │  (hash+timestamp)│
  └──────────────┘      └────────┬─────────┘
                                 │
                                 │ submission_id
                                 ▼
  ┌──────────────┐      ┌──────────────────┐
  │ ADJUDICATOR  │─────▶│  SCORING REPORT  │
  │ (decrypt +   │      │  (PASS/FAIL +    │
  │  score)      │      │   metrics)       │
  └──────────────┘      └────────┬─────────┘
                                 │
                                 ▼
                          ┌──────────────────┐
                          │  BACKFILL TO     │
                          │  GUIDE.BENCHMARK │
                          │  _CASES          │
                          └──────────────────┘

  ┌──────────────────────────────────────────────────────────┐
  │  ADVERSARIAL FIXTURES (Red Team)                         │
  │  - Inject gross errors → verify FAIL detected            │
  │  - Inject systematic bias → verify FAIL detected         │
  │  - Inject perfect predictions → verify PASS              │
  └──────────────────────────────────────────────────────────┘
```

## Security Model

### Threat Model
- **Solver has access to**: sealed dataset file (encrypted bytes)
- **Solver does NOT have**: decryption key (held exclusively by adjudicator)
- **Goal**: Make ground truth unreadable without adjudicator key

### Cryptographic Construction
- **Algorithm**: Authenticated encryption (encrypt-then-MAC)
- **Stream cipher**: SHA-256 in counter mode (key + nonce + counter → keystream)
- **Authentication**: HMAC-SHA256 over (nonce + ciphertext)
- **Format**: nonce(16B) || ciphertext || tag(32B)
- **Key separation**: Different keys for encryption and authentication (domain separation via SHA-256)

**Note**: This is NOT AES-GCM (which requires the `cryptography` library). For platform-level access control (preventing solvers from reading answers), this stdlib-only construction is sufficient.

### Access Control
- `/seal` and `/adjudicate` endpoints require `adjudicator` role (has decryption key)
- `/submit` endpoint is open to solvers (write-only, no ground truth access)
- `/report` and `/guide` endpoints are read-only (any authenticated user)

## Components

### 1. Crypto (`crypto.py`)
Authenticated encryption primitives:
- `seal_data(plaintext, master_key)` → `SealedEnvelope`
- `unseal_data(envelope, master_key)` → `plaintext`
- `generate_master_key()` → 32-byte random key

### 2. Sealer (`sealer.py`)
Dataset sealing with manifest generation:
- `DatasetSealer.seal_dataset(dataset, name, metadata)` → `SealManifest`
- `DatasetSealer.load_sealed(dataset_id)` → `(SealedEnvelope, SealManifest)`
- `DatasetSealer.verify_seal_integrity(dataset_id)` → `bool`

### 3. Submission (`submission.py`)
Write-only prediction submission:
- `PredictionSubmitter.submit(dataset_id, solver_name, predictions)` → `PredictionArtifact`
- `PredictionSubmitter.load_submission(submission_id)` → `PredictionArtifact`
- `PredictionSubmitter.list_submissions(dataset_id)` → `List[PredictionArtifact]`

### 4. Adjudicator (`adjudicator.py`)
Blind scoring engine:
- `BlindAdjudicator.adjudicate(submission_id, scoring_rules)` → `ScoringReport`
- Decrypts sealed dataset, computes errors, determines PASS/FAIL
- Generates comprehensive report with full provenance

### 5. Adversarial (`adversarial.py`)
Red team fixtures:
- `inject_adversarial(fixture_type)` → `AdversarialFixture`
- `run_adversarial_suite()` → `List[AdversarialFixture]`
- Fixture types: `gross_error`, `systematic_bias`, `random_noise`, `perfect_prediction`

### 6. Backfill (`backfill.py`)
Guide integration:
- `BenchmarkBackfill.backfill_report(report)` → `List[BenchmarkCaseEntry]`
- Writes results to `guide.benchmark_cases.json` with full provenance
- Supports querying by solver, verdict, benchmark name

## API Endpoints

```
POST /blind-benchmark/seal          — Seal a dataset (adjudicator only)
POST /blind-benchmark/submit        — Submit predictions (solver, write-only)
POST /blind-benchmark/adjudicate    — Score a submission (adjudicator only)
GET  /blind-benchmark/report/{id}   — Get scoring report
POST /blind-benchmark/adversarial   — Run adversarial fixture (testing)
GET  /blind-benchmark/guide         — Query guide.benchmark_cases
GET  /blind-benchmark/manifest/{id} — Get dataset manifest
```

## Usage Example

```python
from backend.services.blind_benchmark import (
    DatasetSealer,
    PredictionSubmitter,
    BlindAdjudicator,
    BenchmarkBackfill,
)

# 1. Setup
sealer = DatasetSealer("/path/to/sealed")
submitter = PredictionSubmitter("/path/to/submissions")
adjudicator = BlindAdjudicator(sealer, submitter, "/path/to/reports")
backfill = BenchmarkBackfill("/path/to/guide")

# 2. Seal dataset (adjudicator only)
dataset = {
    "cases": {
        "SUBOFF_01": {"Ct": 0.0035, "Rt": 92.52},
        "SUBOFF_02": {"Ct": 0.0042, "Rt": 110.3},
    }
}
manifest = sealer.seal_dataset(
    dataset=dataset,
    dataset_name="SUBOFF_resistance",
    metadata={"source": "Roddy 1990"}
)

# 3. Submit predictions (solver, no ground truth access)
artifact = submitter.submit(
    dataset_id=manifest.dataset_id,
    solver_name="holtrop_method",
    solver_version="v1.0",
    predictions={
        "SUBOFF_01": {"Ct": 0.0036, "Rt": 95.0},
        "SUBOFF_02": {"Ct": 0.0043, "Rt": 113.0},
    }
)

# 4. Adjudicate (adjudicator only)
report = adjudicator.adjudicate(
    submission_id=artifact.submission_id,
    scoring_rules={"max_relative_error_pct": 15.0}
)

print(f"Verdict: {'PASS' if report.overall_passed else 'FAIL'}")
print(f"Mean error: {report.overall_summary['mean_relative_error_pct']:.2f}%")

# 5. Backfill to guide
entries = backfill.backfill_report(report)
print(f"Created {len(entries)} guide entries")
```

## Adversarial Testing

The system includes adversarial fixtures to verify correctness:

```python
from backend.services.blind_benchmark.adversarial import run_adversarial_suite

fixtures = run_adversarial_suite(
    adjudicator=adjudicator,
    submitter=submitter,
    dataset_id=manifest.dataset_id,
    ground_truth=dataset,
)

for fixture in fixtures:
    print(f"{fixture.fixture_type}: expected_fail={fixture.expected_fail}, "
          f"actual_fail={fixture.actual_fail}, test_passed={fixture.test_passed}")
```

Expected output:
```
gross_error: expected_fail=True, actual_fail=True, test_passed=True
systematic_bias: expected_fail=True, actual_fail=True, test_passed=True
perfect_prediction: expected_fail=False, actual_fail=False, test_passed=True
```

## Provenance & Audit Trail

Every step in the pipeline is tracked with cryptographic hashes:

1. **Dataset sealing**: `plaintext_sha256` in manifest
2. **Prediction submission**: `predictions_hash` in artifact
3. **Adjudication**: `report_hash` (sha256 of report JSON)
4. **Guide entry**: Links `dataset_hash`, `submission_hash`, `report_hash`

This creates an immutable audit trail from dataset ingestion → prediction → scoring → guide entry.

## Testing

Run the test suite:
```bash
pytest backend/tests/test_blind_benchmark.py -v
```

Test coverage:
- **Crypto**: seal/unseal roundtrip, tamper detection, key separation
- **Sealer**: dataset sealing, manifest generation, integrity verification
- **Submission**: prediction submission, hash verification, listing
- **Adjudicator**: scoring, pass/fail determination, report generation
- **Adversarial**: fixture injection, FAIL detection, test suite
- **Backfill**: guide writing, provenance, querying
- **Integration**: full pipeline, blind property verification

## Design Rationale

### Why Pre-registration?
Pre-registration is standard practice in scientific experiments to prevent "p-hacking" — tuning parameters until you get the desired result. By sealing the dataset before any solver sees it, we ensure that:
- Solvers cannot "peek" at the answer while tuning
- All predictions are made blind (no information leakage)
- The adjudicator is the only party with access to ground truth

### Why Adversarial Fixtures?
Adversarial testing verifies that the adjudicator correctly identifies FAIL cases. Without this, we cannot trust that the system will catch bad predictions. The fixtures inject:
- **Gross errors** (50-100% error) → should FAIL
- **Systematic bias** (20% bias) → should FAIL (if threshold < 20%)
- **Perfect predictions** → should PASS

If the adjudicator fails to detect gross errors, the system is broken.

### Why Full Provenance?
Full provenance (hashes at every step) ensures:
- **Immutability**: Any tampering is detected
- **Reproducibility**: Anyone can verify the audit trail
- **Accountability**: Every decision is traceable to specific artifacts

## Limitations & Future Work

### Current Limitations
1. **Stdlib-only crypto**: Uses SHA-256 CTR mode instead of AES-GCM (sufficient for platform-level access control, but not cryptographically hardened)
2. **No key rotation**: Master key is static (in production, integrate with KMS)
3. **No distributed consensus**: Single adjudicator (in production, use 2-of-3 voting like truth_gates)

### Future Enhancements
1. **AES-GCM**: Integrate `cryptography` library for hardware-accelerated encryption
2. **Key management**: Integrate with platform KMS (`backend/api/kms.py`)
3. **Multi-adjudicator**: Use ReproVerdict 2-of-3 voting for adjudication
4. **Streaming**: Support large datasets that don't fit in memory
5. **Differential privacy**: Add noise to prevent membership inference attacks

## References

- **Pre-registration**: Nosek et al. 2019, "The preregistration revolution"
- **Authenticated encryption**: Bellare & Namprempre 2008, "Authenticated encryption: relations among notions..."
- **Adversarial testing**: Goodfellow et al. 2014, "Explaining and Harnessing Adversarial Examples"
- **Holdout evaluation**: Standard ML practice (test set isolation)

## Related Work in Platform

- **Truth Gates** (`backend/models/truth_gates.py`): 2-of-3 voting for repro verdicts
- **Benchmark Canon** (`backend/services/benchmark_canon.py`): Reference dataset adjudication
- **Benchmark Integrity** (`backend/services/benchmark_integrity.py`): Dataset validation gates
- **UQ Framework** (`backend/services/uq_framework/`): Uncertainty quantification with acceptance cases

The Blind Benchmark Holdout complements these systems by adding a **pre-registration layer** that prevents information leakage before adjudication begins.
