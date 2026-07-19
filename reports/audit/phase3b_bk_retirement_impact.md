# Phase 3B -- BK Retirement Strategy-Impact Disclosure (offline, read-only)

- Universe A (178, BK included, archived): 178 symbols loaded, 762 trades, total_return = 165.9100%
- Universe B (177, BK retired, active): 177 symbols loaded, 773 trades, total_return = 165.8200%
- Total return delta (177 minus 178): -0.0900 percentage points
- Trade count delta: 11

BK WAS selected/traded 4 time(s) in the 178-universe run:
  - Entry 2023-02-01 00:00:00 / Exit 2023-04-03 00:00:00 / Net PnL -39.91 / Exit Reason rs_rotation_exit
  - Entry 2023-12-01 00:00:00 / Exit 2024-07-01 00:00:00 / Net PnL 127.95 / Exit Reason rs_rotation_exit
  - Entry 2024-09-03 00:00:00 / Exit 2025-05-01 00:00:00 / Net PnL 112.76 / Exit Reason rs_rotation_exit
  - Entry 2025-08-01 00:00:00 / Exit 2026-02-02 00:00:00 / Net PnL 133.44 / Exit Reason rs_rotation_exit

Note: individual non-BK trade fills can still shift slightly between the two runs because removing a candidate changes the top-30 ranking pool size by one slot on any rebalance date where BK would otherwise have ranked in range; the total_return delta above is the authoritative, already-net-of-that-effect comparison.
