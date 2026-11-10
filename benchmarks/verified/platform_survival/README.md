# benchmarks/verified/platform_survival — P148W2-N01 acceptance harness

Long-run survival & progress-maturity layer (TensorPlatform, domain-free machinery).

```
run_acceptance.py            runs A1..A10 against the real machinery, writes results.json
results.json                 verdicts + every measured number + artifact paths (machine-checked)
resume_consistency_report.json  bit-level resume table (A2 / A7 / A9)
make_doc.py                  renders docs/platform_survival.md *from* results.json (no hand-typed numbers)
make_capability_card.py      derives docs/capability_registry/cards/CAP-008-*.json + registers it
figures/                     fig1 progress timeline, fig2 stall ladder, fig3 criteria dashboard
artifacts/                   crash bundle (A1), honest-FAIL record (A6), degradation report (A10)
```

Regenerate everything:

```bash
python3 benchmarks/verified/platform_survival/run_acceptance.py
python3 benchmarks/verified/platform_survival/make_doc.py
python3 benchmarks/verified/platform_survival/make_capability_card.py
python3 -m pytest tests/test_platform_survival_progress.py \
                 tests/test_platform_survival_forensics.py \
                 tests/test_platform_survival_resume_honest_degrade.py -q
```

Notes

- The A10 field array (`*.npy`, ~19 MB) is not stored in git; its sha256 and geometry are in
  `results.json` (`criteria.A10.measured.field_npy_sha256`), and the kernel is deterministic
  given `(n, iters, seed)`.
- The live-crash test (A1) needs `gdb` and ptrace permission; without them it skips and the
  synthetic-transcript tests still cover the assembly rules.