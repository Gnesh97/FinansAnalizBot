# Phase 3B — Correction Note

**Date:** 2026-07-19
**Corrects:** `reports/audit/phase3b_universe_bk_retirement_20260718.md`, line 5.

## The error

That report states:

> **Reason:** BK (Bank of New York Mellon) renamed to BNY effective 2025-07-07; CUSIP unchanged; ticker symbol BK retired from exchanges.

The date **2025-07-07** is wrong.

## The correct date

BK's NYSE ticker change to BNY was effective **2026-05-21** (announced 2026-05-11), per the BNY press release and the Phase 2D-verified evidence already on file. This is the date carried in the approved, hash-verified `EARLY_TERMINAL` exception record itself:

```
testdata/baselines/rs_v2_sip_coverage_exceptions.json
  "symbol": "BK",
  "exception_type": "EARLY_TERMINAL",
  "approved_last_session": "2026-05-20",
  "reason": "BK ... changed its NYSE ticker symbol from BK to BNY, effective 2026-05-21 (announced 2026-05-11) ...",
  "evidence_source": "https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html",
  "evidence_date": "2026-05-11",
  "evidence_hash": "e5e3785a8537696db4f2b9e3d12ab6df5684fe9e1adb55a9ef59e974cf2a17a2"
```

`approved_last_session: 2026-05-20` (the last real trading day under BK) is consistent with an effective change date of **2026-05-21**, not 2025-07-07.

## Scope of the error

This was a transcription error made when summarizing the rationale into the Phase 3B report's header table. It did not affect any code, hash, or validator logic — the universe retirement itself (178→177, BK removed, hash `026f66d1...`) was performed correctly and is unaffected by this date. The evidence hash cited in that same report line (`e5e3785a`) is correct and matches the real exception record.

The original report (`phase3b_universe_bk_retirement_20260718.md`) is left unmodified, per the immutable-reports rule. This note stands as the correction of record.
