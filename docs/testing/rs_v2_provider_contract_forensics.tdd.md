# RS V2 Provider Contract Forensics - TDD Evidence

## Scope

Fail-closed provider request-window forensics and contract-gate provenance for
`rs_m126_top30_sma100_no_regime_v2_clean_stock`. Strategy, benchmark, frozen
dataset, broker, and durable paper state were out of scope.

## RED / GREEN Sequence

1. RED: 30 tests ran; missing forensics module errored and the permissive
   one-valid-endpoint canary assertion failed. GREEN: 38 passed.
2. RED: `ForensicsRunSpec` missing. GREEN: 40 passed.
3. RED: CLI `build_parser` missing. GREEN: 42 passed.
4. RED: outcome classifier missing. GREEN: 43 passed.
5. RED: persisted-evidence reclassifier missing. GREEN: 44 passed.
6. Reviewer regressions covered invalid canary status strings, missing evidence,
   partial endpoint errors, and selected-contract binding. GREEN: 50 passed.
7. RED: six `run_root` errors plus two permissive provenance failures proved
   hashes were only syntax-checked and minimal status JSON was trusted.
   GREEN: 52 passed after byte/hash/timestamp and full provenance validation.
8. RED: hash-consistent semantic tampering was accepted. GREEN: classifier was
   re-derived from the exact 3 x 4 x 2 detail matrix; the regression passed.

## Final Verification

- Focused V2: 153 passed, 0 failed.
- Full repository: 591 passed, 0 failed.
- `py_compile`: PASS.
- Independent Python review: zero CRITICAL/HIGH.
- Frozen referenced files: 540 checked, 0 missing, 0 mismatched.
- Frozen mutation count: 0.

The first final focused process hit a 120-second harness timeout; it emitted no
failure and was rerun with a sufficient process limit. Coverage tooling and the
ECC quality hook were unavailable, so no coverage percentage is claimed.

## Checkpoint Policy

No TDD checkpoint commit was created. The repository was already dirty with
user-owned changes; preserving those changes took precedence over checkpoint
commits.
