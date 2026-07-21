# Intraday (130-Minute) Bar Role Analysis

**Date:** 2026-07-21  
**Scope:** READ-ONLY code-trace investigation of the role of 130-minute intraday bars in the `relative_strength_rotation` swing trading strategy.

---

## 1. Ranking / Selection Engine (`_relative_strength_selection`)

**Conclusion:** The ranking and selection engine depends **exclusively on daily OHLCV** data and **never reads 130-minute bar data**.

### Code Trace & Inputs
File: `testdata/strategies/relative_strength_rotation.py`

- `_relative_strength_selection` (lines 361–406) invokes `_relative_strength_daily_features(asset_data, params)` at line 373:
  ```python
  features = _relative_strength_daily_features(asset_data, params)
  ```
- `_relative_strength_daily_features` (lines 289–335) extracts daily bars via `_daily_ohlcv_from_frame(data)` at line 292:
  ```python
  daily = _daily_ohlcv_from_frame(data)
  ```
- `_daily_ohlcv_from_frame` (lines 123–138) directly consumes `df.attrs.get("daily_ohlcv")` when available:
  ```python
  daily_source = df.attrs.get("daily_ohlcv")
  if isinstance(daily_source, pd.DataFrame) and not daily_source.empty:
      return daily_source.copy()
  ```
- The exact ranking inputs computed in `_relative_strength_daily_features` (lines 295–317) are strictly daily metrics:
  - **Momentum (126-day):** `daily["Close"] / daily["Close"].shift(126) - 1` (lines 301–303)
  - **Annualized Volatility (60-day):** `daily["Close"].pct_change().rolling(60).std() * np.sqrt(252)` (lines 304–307)
  - **RS Score:** `total_return / annualized_vol` (lines 308–311)
  - **Trend SMA Filter (100-day / 200-day):** `daily["Close"] > daily["Close"].rolling(100).mean()` (lines 297–300, line 315)
  - **ATR % Filter:** `100 * atr_20 / daily["Close"] <= 12.0` (lines 295–296, line 314)
  - **Minimum Price Filter:** `daily["Close"] >= 10.0` (line 313)
- Sorting and selection (lines 400–401) operate strictly on the daily `RS_Score`:
  ```python
  rows.sort(key=lambda item: item[1], reverse=True)
  selections[date] = {symbol for symbol, _ in rows[: params["rs_top_n"]]}
  ```

---

## 2. Intraday (130-Min) Bar Consumption Points

Every location in strategy and backtest logic where 130-minute bar timestamps or data are referenced:

### Consumption Site A: Signal Timestamp Stamping
File: `testdata/strategies/relative_strength_rotation.py`, lines 409–439 (`_apply_relative_strength_rotation_top_signals`)
```python
420: normalized_dates = frame.index.normalize()
422: date_mask = normalized_dates == date
425: last_bar = frame.index[date_mask][-1]
427: signal_dates.loc[last_bar] = in_top
434: frame["ENTRY_SIGNAL"] = signal_dates.fillna(False).astype(bool)
```
- **Plain Sentence Description:** This function stamps `ENTRY_SIGNAL = True` on the timestamp of the last 130-minute bar (13:50 ET) of the rebalance date for selected symbols.
- **Classification:** **(a) Only used to set a signal timestamp / reference point on the signal day.**

### Consumption Site B: Dataset Asset Data Container Indexing
File: `testdata/runner.py`, lines 1700–1714 (`prepare_dataset`)
```python
1704: intraday = _slice_frame(_normalize_frame_timed(intraday_path, csv_timing), start_date, end_date)
1705: daily = _slice_frame(_normalize_frame_timed(daily_path, csv_timing), start_date, end_date)
1709: intraday.attrs["daily_ohlcv"] = daily
1714: asset_data[symbol] = intraday
```
- **Plain Sentence Description:** The backtest data builder wraps daily data inside the 130-minute intraday DataFrame's `attrs` dictionary and uses the 130-minute DataFrame as the outer container for historical data slicing.
- **Classification:** Container structure / indexing artifact.

### Consumption Site C: Intraday Live Paper Monitor (Provisional Bar Preview)
File: `testdata/intraday_paper_monitor.py`, lines 173–177
```python
173: "provisional_130min_open": _number(block["Open"].iloc[0]) if "Open" in block else pd.NA,
176: "provisional_130min_close": _number(block["Close"].iloc[-1]) if "Close" in block else pd.NA,
```
- **Plain Sentence Description:** Monitors intraday 5-minute bars during live/paper sessions to construct provisional 130-minute bar status reports for human oversight before market close.
- **Classification:** Operational preview reporting.

**Summary of Consumption:**
There are **zero (b) filter/trigger threshold conditions** on 130-minute bars and **zero (c) structural trade logic dependencies** on 130-minute bars.

---

## 3. Execution Model

File: `testdata/strategies/relative_strength_rotation.py`, lines 621–675 (`_run_relative_strength_rotation_backtest`)

```python
624: open_equity = _equity_at(date, "Open")
629: price = frame.loc[date, "Open"] # Exit non-target positions at next-day daily Open
655: price = frame.loc[date, "Open"] # Enter target positions at next-day daily Open
```

- **Signal Date:** Monthly rebalance date (last trading day of calendar month).
- **Execution Date & Price:** The **NEXT trading day at market OPEN** (`frame.loc[date, "Open"]`).
- **Impact of Replacing 130-Min Signal Bar with Daily Close:**
  - **Entry Price:** Unchanged (remains next-day daily Open).
  - **Selection:** Unchanged (ranking uses daily features).
  - **Execution Timing:** Unchanged (order executes at next-day daily Open).
  - **Price Difference:** None. The 130-min close of bar 3 (13:50–16:00 ET) is identical to the daily Close for complete sessions.

---

## 4. Hypothetical Removal of Intraday Data

If 130-minute data is dropped entirely and the strategy runs purely on daily OHLCV data:

### (a) Required Code Changes
1. `testdata/runner.py`: Update `_resolve_data_dirs` / `prepare_dataset` to load `daily` files directly into `asset_data[symbol]` instead of wrapping them inside `bars_130min` DataFrames.
2. `testdata/strategies/relative_strength_rotation.py`: In `_apply_relative_strength_rotation_top_signals`, stamp `ENTRY_SIGNAL` on the daily date timestamp instead of `frame.index[date_mask][-1]`.
3. `testdata/vericekici_updated.py`: Remove `aggregate_to_130min` downloads/aggregations and 130-min manifest coverage validation.

### (b) Economic Logic Alteration
- **NO ALTERATION.** The strategy is a monthly rebalance momentum swing strategy holding positions for ~30 days. Entry is at next-day Open; selection is on daily Close. Intraday price movement within the signal day does not alter ranking, entry price, or exit rules.

### (c) Impact on IEX Gap Problem
- **DISAPPEARS 100%.** All 323 residual missing blocks across 65 dates and 111 symbols in the IEX dataset exist **exclusively in `bars_130min/`** due to thin-liquidity single-venue IEX volume voids. Daily OHLCV data (`daily/`) has complete venue-consolidated session coverage with zero missing daily bars.

---

## 5. Rationale for 130-Minute Bars in Code Base

- **Repository Rationale Search:** Search across git history, comments, and contract documentation (`testdata/quantconnect_port_contract.py`) yields **no quantitative or economic rationale** requiring 130-minute bars for this strategy.
- **Historical Origin:** 130-minute bars (3 RTH blocks per 6.5-hour trading day: 09:30–11:40, 11:40–13:50, 13:50–16:00 ET) were originally introduced in legacy iterations of the codebase for intraday mean-reversion / RSI trigger experiments.
- **Current Status:** For `relative_strength_rotation`, 130-minute indexing is an **incidental historical artifact** preserved for baseline output bit-compatibility, not an essential trading requirement.

---

## 6. Summary Answer

1. **Is intraday essential or removable?**  
   **Removable.** The strategy logic is 100% daily-driven (ranking on daily Close, execution on next-day Open). Intraday 130-minute data serves only as a timestamp alignment layer.

2. **Does removing intraday kill the IEX gap problem?**  
   **YES, completely.** Eliminating the 130-minute intraday requirement eliminates all 323 thin-liquidity coverage holes on IEX, enabling clean strict validation on daily OHLCV data.
