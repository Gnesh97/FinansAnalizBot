# `rs_m126_top30_sma100_no_regime` LEAN port

Modes are deliberately separate:

- `STATIC_PARITY`: frozen current local CSV universe. Implementation-control run only; survivorship-biased.
- `PIT_VALIDATION`: future Fundamental Universe experiment. Not exact local-universe parity.

## Status

`main.py` is a non-executable safety skeleton due to contract blockers.

### Blocker Statuses

- **delisting**: `NON_BLOCKING_AFTER_IMPACT_AUDIT` for legacy STATIC (exact validation confirms early terminal candidates BK and PARA are not selected or held on/after terminal dates).
- **price normalization**: `STATIC_BLOCKING` (provenance of adjustments and feed cannot be verified from actual legacy files).
- **split/dividend provenance**: `STATIC_BLOCKING` (provenance of adjustments and split/dividend events cannot be verified from actual legacy files).
- **PIT universe rule**: `PIT_BLOCKING` (no reproducible historical point-in-time universe rule exists for the current local 200-symbol universe).

### Executive Statuses

- **STATIC_PARITY**: `NON_EXECUTABLE` (blocked by price normalization and split/dividend blockers).
- **PIT_VALIDATION**: `NON_EXECUTABLE` (blocked by PIT universe rule blocker).

## QuantConnect cloud setup

1. Create a Python project named `rs_m126_top30_sma100_no_regime`.
2. Upload `main.py` and `static_parity_universe.json` from this folder.
3. Set project parameter `universe_mode=STATIC_PARITY`; do not use `PIT_VALIDATION` as universe parity.
4. Select US Equity data, start/end only after all contract blockers are resolved, and run a backtest—not live trading.
5. Copy event diagnostics from results and compare them with `testdata/reports/latest/quantconnect_local_reference_events.csv`.

No current cloud run is authorized. Resolve all `BLOCKING` fields in `quantconnect_port_contract.json` first.
