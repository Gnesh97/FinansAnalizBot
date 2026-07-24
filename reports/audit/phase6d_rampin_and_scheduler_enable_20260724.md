# Phase 6D — Ramp-In Cap Adjustment, Backtest Equivalence Proof, and Scheduler Enablement

**Date:** 2026-07-24  
**Scope:** Raise DSS/paper ramp-in cap default to 30 (uncapped top-30 entry); prove frozen backtest unaffected; render clean full top-30 DSS report; harden Windows Task Scheduler batch launcher; state pre-flight verification command, install steps, and decision-event archive conflict status.

---

## 1. Ramp-In Cap Adjustment

**Operator Decision:** Raise default `--paper-max-new-positions-per-day` so the DSS/paper path enters the full top-30 per day, matching the frozen backtest's entry behavior.

**Diff:**

```diff
--- a/testdata/universe_scanner.py
+++ b/testdata/universe_scanner.py
@@ -228,7 +228,7 @@ def build_parser():
     parser.add_argument(
         "--paper-max-new-positions-per-day",
         type=int,
-        default=5,
+        default=30,
         help="Maximum actionable new BUY rows per day for ramp-in.",
     )

--- a/testdata/runner.py
+++ b/testdata/runner.py
@@ -3915,7 +3915,7 @@ def _run_paper_signal_report(
     ledger_path,
     paper_strategy_mode="main",
     include_watchlist=False,
-    paper_max_new_positions_per_day=5,
+    paper_max_new_positions_per_day=30,
 ):

@@ -7801,7 +7801,7 @@ def run_strategy_experiments(
     paper_report=False,
     paper_strategy="main",
     paper_include_watchlist=False,
-    paper_max_new_positions_per_day=5,
+    paper_max_new_positions_per_day=30,
     command=None,
```

---

## 2. Proof: Frozen Backtest Unaffected

**Mechanism:** The ramp-in cap parameter `paper_max_new_positions_per_day` is evaluated exclusively inside `_run_paper_signal_report` (the paper/DSS signal reporting path at `runner.py:3968-3976`). It is **not** present in `_run_strategy` or `_run_relative_strength_rotation_backtest`, which execute strategy selection directly against the backtest engine using `rs_top_n=30`.

**Empirical Verification:** `tests/test_daily_only_equivalence_guard.py` re-run directly against the frozen baseline fingerprint:

```
Ran 1 test in 2.157s

OK
```

- Total Return (%): **165.9133%** (pinned target: 165.9133%)
- Trade Count: **762** (pinned target: 762)
- Win Rate (%): **50.50%** (pinned target: 50.50%)
- Max Drawdown (%): **-20.47%** (pinned target: -20.47%)

The backtest figures are provably identical to the penny and percentage point.

---

## 3. Full Top-30 DSS Report

Rendered clean against as-of data date `2026-07-21` (profile `rs_v2_tiingo`):

```text
DECISION SUPPORT -- enter orders manually at next open
As-of data date: 2026-07-21
Universe: 177 symbols (hash 026f66d1...)
Strategy: rs_m126_top30_sma100_no_regime_v2_clean_stock
Generated at: 2026-07-24T18:07:08.023360Z

BUY (30):
  AAPL: price=327.74, target_weight=3.33%, rank=20, score=1.013611700707829, reason=initial_paper_state;latest_entry_signal;missing_next_open
  AMAT: price=564.55, target_weight=3.33%, rank=22, score=0.9294058251528656, reason=initial_paper_state;latest_entry_signal;missing_next_open
  AMD: price=544.43, target_weight=3.33%, rank=5, score=1.5375256922745022, reason=initial_paper_state;latest_entry_signal;missing_next_open
  BAC: price=61.22, target_weight=3.33%, rank=25, score=0.8474609900435067, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CAT: price=889.97, target_weight=3.33%, rank=27, score=0.7878940066700718, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CRWD: price=191.15, target_weight=3.33%, rank=13, score=1.2299465614094265, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CSCO: price=112.18, target_weight=3.33%, rank=17, score=1.093399628640556, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CVS: price=110.60, target_weight=3.33%, rank=8, score=1.430514239671172, reason=initial_paper_state;latest_entry_signal;missing_next_open
  DDOG: price=254.79, target_weight=3.33%, rank=9, score=1.3894302655554962, reason=initial_paper_state;latest_entry_signal;missing_next_open
  EOG: price=143.44, target_weight=3.33%, rank=12, score=1.2707282359806444, reason=initial_paper_state;latest_entry_signal;missing_next_open
  FDX: price=315.19, target_weight=3.33%, rank=23, score=0.8947049214225391, reason=initial_paper_state;latest_entry_signal;missing_next_open
  FTNT: price=158.10, target_weight=3.33%, rank=2, score=1.9595193328054803, reason=initial_paper_state;latest_entry_signal;missing_next_open
  HUM: price=404.34, target_weight=3.33%, rank=16, score=1.0988458376204926, reason=initial_paper_state;latest_entry_signal;missing_next_open
  INTC: price=105.45, target_weight=3.33%, rank=14, score=1.2133540351483785, reason=initial_paper_state;latest_entry_signal;missing_next_open
  KO: price=81.97, target_weight=3.33%, rank=30, score=0.767559242631074, reason=initial_paper_state;latest_entry_signal;missing_next_open
  LIN: price=505.03, target_weight=3.33%, rank=28, score=0.7848752949974682, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MPC: price=319.76, target_weight=3.33%, rank=1, score=2.3708165727497987, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MRVL: price=207.96, target_weight=3.33%, rank=10, score=1.3798245014550898, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MU: price=970.82, target_weight=3.33%, rank=6, score=1.532584602116228, reason=initial_paper_state;latest_entry_signal;missing_next_open
  NUE: price=233.20, target_weight=3.33%, rank=19, score=1.056447541024826, reason=initial_paper_state;latest_entry_signal;missing_next_open
  OXY: price=56.50, target_weight=3.33%, rank=24, score=0.8880651999699122, reason=initial_paper_state;latest_entry_signal;missing_next_open
  PANW: price=342.15, target_weight=3.33%, rank=7, score=1.5308395621190851, reason=initial_paper_state;latest_entry_signal;missing_next_open
  PSX: price=212.27, target_weight=3.33%, rank=4, score=1.6582056440123645, reason=initial_paper_state;latest_entry_signal;missing_next_open
  STLD: price=233.52, target_weight=3.33%, rank=21, score=0.9421949638717964, reason=initial_paper_state;latest_entry_signal;missing_next_open
  TGT: price=138.48, target_weight=3.33%, rank=29, score=0.7794001461426582, reason=initial_paper_state;latest_entry_signal;missing_next_open
  TXN: price=291.30, target_weight=3.33%, rank=18, score=1.056901653056706, reason=initial_paper_state;latest_entry_signal;missing_next_open
  UNH: price=436.35, target_weight=3.33%, rank=11, score=1.3034763591405885, reason=initial_paper_state;latest_entry_signal;missing_next_open
  UNP: price=293.13, target_weight=3.33%, rank=15, score=1.1920017641754923, reason=initial_paper_state;latest_entry_signal;missing_next_open
  USB: price=63.71, target_weight=3.33%, rank=26, score=0.8177870097025564, reason=initial_paper_state;latest_entry_signal;missing_next_open
  VLO: price=314.80, target_weight=3.33%, rank=3, score=1.8746857525177387, reason=initial_paper_state;latest_entry_signal;missing_next_open

SELL: none today.
```

**Verification of top 5 items:**
- **MPC:** price=319.76, weight=3.33%, rank=1, score=2.3708165727497987 (Identical to Phase 6C)
- **FTNT:** price=158.10, weight=3.33%, rank=2, score=1.9595193328054803 (Identical to Phase 6C)
- **VLO:** price=314.80, weight=3.33%, rank=3, score=1.8746857525177387 (Identical to Phase 6C)
- **PSX:** price=212.27, weight=3.33%, rank=4, score=1.6582056440123645 (Identical to Phase 6C)
- **AMD:** price=544.43, weight=3.33%, rank=5, score=1.5375256922745022 (Identical to Phase 6C)

---

## 4. Hardening `scripts/run_dss_daily.bat`

**Problem Addressed:** Windows Task Scheduler service accounts may execute batch files under restricted or non-standard `PATH` environments where bare `python` is undefined (producing error 9009 before `dss_scheduler.py` initializes, which would prevent Telegram error dispatching).

**Implementation:** `scripts/run_dss_daily.bat` was updated to implement multi-tier fallback resolution:
1. Check `.env` for `PYTHON_PATH` or `PYTHON_EXECUTABLE` configuration.
2. Check bare `python` on active system `PATH`.
3. Check standard Windows installation directories (`%LOCALAPPDATA%\Programs\Python\Python31x\python.exe` and `C:\Python31x\python.exe`).
4. If resolution fails, log a timestamped error message to `reports/dss/scheduler.log` and exit with code 9009.
5. All execution steps and standard outputs redirect into `reports/dss/scheduler.log`.

---

## 5. Operator Pre-Flight Verification Command

To verify `.env` loading, signal generation, report rendering, and **real Telegram notification delivery** without waiting 3.8 hours for a data refresh and without altering production scheduler state:

**Linux / macOS:**
```bash
python3 testdata/dss_scheduler.py --skip-refresh --force --state-path reports/dss/preflight_test_state.json
```

**Windows:**
```cmd
python testdata\dss_scheduler.py --skip-refresh --force --state-path reports\dss\preflight_test_state.json
```

**Properties:**
- `--skip-refresh`: Bypasses Tiingo daily acquisition (~3.8h).
- `--force`: Bypasses NYSE trading day gate and previous report status check.
- `--state-path reports/dss/preflight_test_state.json`: Ensures `reports/dss/scheduler_state.json` is not altered.
- Does not modify acquisition manifests or broker ledger files.
- Dispatches a live DSS report to Telegram.

---

## 6. Final Task Scheduler Installation Steps (Windows)

1. Open **Task Scheduler** → **Create Task** (do not use "Create Basic Task").
2. **General Tab:**
   - Name: `FinansAnalizBot Nightly DSS`
   - Select **Run whether user is logged on or not**.
   - Check **Run with highest privileges** (if Python installation requires administrative context).
3. **Triggers Tab:**
   - New Trigger → **Daily**.
   - Start Time: **23:30:00** (Turkey Time / GMT+3).
   - Recur every: `1` day.
   - Do not add weekday filters (the scheduler handles market session gating internally).
4. **Actions Tab:**
   - Action: **Start a program**.
   - Program/script: `C:\Users\<user>\Desktop\BOT - Kopya\scripts\run_dss_daily.bat`
   - Start in: `C:\Users\<user>\Desktop\BOT - Kopya`
5. **Settings Tab:**
   - Check **Run task as soon as possible after a scheduled start is missed**.
   - Set **Stop the task if it runs longer than**: `6 hours`.
6. **Interpreter Verification:**
   - If Task Scheduler uses a service account, specify `PYTHON_PATH=C:\Path\To\python.exe` inside `.env` to guarantee interpreter resolution.

---

## 7. Decision-Event Archive Conflict Status

**Root Cause:** Session `2026-07-24` holds decision records created during Phase 6A (`commit a49e1f9`). Re-running decision generation for `2026-07-24` on a newer commit produces identical decision keys with a modified commit hash, triggering a `FileExistsError` / `ValueError: conflicting duplicate event id` in `baseline_parity.append_decision_event`.

**Status & Resolution:**
- This is the archive working as designed to prevent silent overwrites of historical decisions under different code versions.
- **Self-Resolution:** On the next trading day (`2026-07-27`), the new session timestamp generates distinct `event_id` hashes, resolving the conflict automatically.
- **Operator Action Required:** **NONE**. Do NOT delete, alter, or purge archived event files.

---

## 8. Test Suite & Code Review

Full test suite execution:

```
Ran 724 tests in 442.193s

OK
```

**Reconciliation:**
- Phase 6C baseline: 723 tests.
- Added in Phase 6D: 1 test (`TestRampInCapDefaultBehavior.test_default_ramp_in_cap_is_30_and_sells_are_uncapped` in `tests/test_dss_report.py`).
- **Total:** 724 tests passing, 0 failures.

---

## 9. Safety & Policy Assertions

- `submit_order_called`: `false`
- `post_request_used`: `false`
- `cancel_replace_used`: `false`
- `broker_submit_enabled`: `false`
- `live_trading_enabled`: `false`
- `short_trading_enabled`: `false`
- Trade logic, strategy ranking formulas, and benchmark rules: **UNTOUCHED**.
- Universe: **177 symbols (hash `026f66d1...`) UNTOUCHED**.
- `rs_v2_sip` and `rs_v2_clean` profiles: **UNTOUCHED**.
- API Credentials (`TIINGO_API_KEY`, `TELEGRAM_*`): Env-only, never logged or printed.

---

*Report written immutably to reports/audit/phase6d_rampin_and_scheduler_enable_20260724.md.*
