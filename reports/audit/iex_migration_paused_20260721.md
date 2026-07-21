# IEX Migration Paused & Active Dataset Decision

**Date:** 2026-07-21  
**Operator Decision:** PAUSE IEX migration and retain existing SIP dataset (target end date 2026-07-17) as active dataset profile.

---

## 1. Summary of Decision

The IEX data profile (`rs_v2_clean`) migration is paused. The bot and research tools remain configured to use the `rs_v2_sip` dataset profile backed by the verified SIP market dataset (`rs_v2_sip_market_data`, target end date `2026-07-17`, 358/358 items valid, hash `026f66d1...`).

---

## 2. Rationale for Pausing IEX Migration

Step 1 live acquisition of `rs_v2_clean` (IEX feed) completed 179/179 symbol downloads without rate-limiting, but strict manifest validation failed. Forensics revealed that IEX possesses structural thin-liquidity coverage holes:

- **Residual missing blocks:** 323 residual missing 130-minute blocks across 65 dates and 111 symbols, beyond the previously assumed "6 known gaps".
- **Root cause:** On single-venue IEX, low-volume/high-price stocks (e.g., BKNG, CMG) experience 130-minute intervals with zero trades, resulting in absent bars.
- **Historical context:** The "6 known gaps" inventory was compiled during an earlier validator-suppression era (`c0340ad`, `aef661d`) when coverage failures were swallowed. Reverting the swallow (`fbf0372`) exposed the full extent of IEX's venue-specific coverage gaps.

---

## 3. Subscription & Data Feed Constraint

- The `rs_v2_sip` acquisition/refresh path is fully operational in code, but live acquisition requires the Alpaca **Algo Trader Plus ($99/mo)** subscription to access the SIP feed.
- The **Free tier** permits IEX feed acquisition only.

---

## 4. Options for Future Sessions

1. **Upgrade Subscription:** Subscribe to Alpaca's Algo Trader Plus ($99/mo) tier to enable daily SIP market data refreshes (`rs_v2_sip`).
2. **Accept IEX Exception Burden:** Author multi-hundred exception records for IEX's ongoing thin-liquidity gaps and manage per-date exception overhead.
3. **Alternative Consolidated Providers:** Evaluate alternative consolidated tape providers (e.g., Polygon.io, Databento) for complete market coverage.

---

## 5. Current Safe State

- **Active Profile:** `rs_v2_sip` (default in `bot.py` `--dss-data-profile` and `universe_scanner.py` `--data-profile`).
- **Data Integrity:** SIP dataset (`rs_v2_sip_market_data`) is intact, valid through `2026-07-17`, status `COMPLETE_VALIDATED`.
- **Codebase Soundness:** Bug fix for `drift_check_start=None` regression committed; DSS report pipeline fully functional.
- **Daily Refresh Status:** Blocked solely by API tier restriction (SIP data feed access).
