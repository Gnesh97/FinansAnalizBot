## EXECUTIVE SUMMARY
Verdict:        run_alpaca_paper_check migrated from hand-rolled urllib REST calls to the
                 alpaca-py SDK's TradingClient. TDD-driven. GREEN.
Changed:        testdata/alpaca_broker_reporting.py (_alpaca_get_json removed; account/
                 positions/orders reads now via TradingClient); new
                 tests/test_alpaca_paper_check.py (5 tests, this function's first direct test
                 coverage).
Suite:          846 tests, GREEN; reconciled against prior 841 (+5 new).
Anomalies:      None.
Open items:     alpaca_paper_snapshot.py (the shared, safety-engineered broker-snapshot client
                 used by 5 other report functions) was deliberately NOT migrated -- scoped out
                 as a separate, higher-risk future decision (see Section 1).
Operator action: None required.

---

## 1. Context

Prompted by the operator recalling that an earlier project version "used alpaca-py, then it
was changed" -- investigated via git history before touching anything (workflow-first
research, not an assumption). Findings, evidence-backed:

- `alpaca-py` has been a declared dependency since the very first commit that introduced
  Alpaca integration (`0df8661`, "Add local paper trading pipeline and backup workflow",
  2026-06-20) and is genuinely used there and still today for **market data** fetching
  (`StockHistoricalDataClient` in `runner.py`'s `_fetch_pending_raw_5min`,
  `delisted_alpaca_price_coverage.py`, `v2_gap_recovery_runner.py`,
  `vericekici_updated.py`).
- The hand-rolled `urllib`-based `_alpaca_get_json`/`_alpaca_config` **broker/account** REST
  calls were present in that exact same first commit, alongside the SDK usage -- there is no
  evidence anywhere in git history or docs of a deliberate migration *away* from alpaca-py for
  broker calls. The two code paths (SDK for data, hand-rolled REST for broker/account) coexisted
  from day one; this was a scope split, not a reversion.

Given that, the operator approved a scoped migration: only the narrow, low-risk
`run_alpaca_paper_check` path (`_alpaca_get_json`, called only there, informational/manual CLI
only, not part of the nightly chain). Investigating the broader broker surface first
(`alpaca_paper_snapshot.py`, used by `run_broker_reconciliation`,
`run_alpaca_paper_submit_readiness`, `run_alpaca_paper_submit_one`,
`run_broker_order_lifecycle_monitor`, `run_create_broker_cutover_baseline`) found it is a
deliberately safety-engineered client -- read-only endpoint-mode gating (refuses non-paper
URLs), credential redaction in error messages (`_safe_detail` strips anything containing
"key"/"secret"/"token"), a `methods_used` list that proves only GET requests are ever made, and
dependency-injectable `urlopen` for testing. Migrating that to the SDK would mean rebuilding
all of those guarantees against alpaca-py's own exception surface, not just swapping the
transport -- explicitly scoped out of this phase as a separate, more careful undertaking.

## 2. What changed

`testdata/alpaca_broker_reporting.py`:
- Removed `_alpaca_get_json` (the hand-rolled `urllib.request` GET helper) entirely -- its
  only caller was `run_alpaca_paper_check`.
- `run_alpaca_paper_check`'s account/positions/orders fetch now uses
  `alpaca.trading.client.TradingClient(api_key, secret_key, paper=True)`,
  `.get_account()`, `.get_all_positions()`,
  `.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100))` -- the SDK import is
  lazy, inside the function, guarded the same way `_fetch_pending_raw_5min` already guards its
  own alpaca-py import (`except ModuleNotFoundError as import_error: if import_error.name !=
  "alpaca": raise` -- a missing SDK produces a friendly install hint, not a cryptic traceback).
- Field mapping preserves the exact same `summary` dict shape callers already depend on:
  `account.status.value` (SDK returns an `AccountStatus` enum; `.value` gives back the plain
  string the old raw-JSON code returned), `buying_power`/`cash`/`equity`/`portfolio_value` stay
  `Optional[str]` on the SDK side -- same type as the old dict-string values, no conversion
  needed. `pattern_day_trader` is `Optional[bool]` on both sides.
- Removed the now-dead `json`/`urllib.parse`/`urllib.request` imports (their only user was the
  deleted function).
- `_alpaca_config` is unchanged (still used by five other functions in this module to resolve
  credentials/base_url for `alpaca_paper_snapshot.py`'s client, which was not touched).

runner.py: removed `_alpaca_get_json` from both dual-import re-export branches (dead name, no
longer defined anywhere).

## 3. Tests (TDD, RED -> GREEN)

`run_alpaca_paper_check` had **zero direct test coverage** before this phase (confirmed via
grep across `tests/` for both the function name and `_alpaca_get_json`/`urlopen` patterns) --
this migration is also this function's first dedicated test file. 5 tests, written first
(RED, confirmed failing against the old urllib implementation hitting a real -- fake-credential
-- network call and getting a genuine 401), then the implementation changed to GREEN:

- `test_refuses_non_paper_endpoint` -- non-paper `base_url` never constructs a `TradingClient`.
- `test_missing_credentials` -- empty key/secret never constructs a `TradingClient`.
- `test_successful_connection_uses_trading_client_and_populates_summary` -- mocks
  `alpaca.trading.client.TradingClient`, asserts it's constructed with
  `(api_key, secret_key, paper=True)`, and that every `summary` field maps correctly from the
  mocked account/positions/orders.
- `test_trading_client_error_is_captured_as_failed_not_raised` -- an SDK exception is caught
  and surfaced as `connection_status: failed` + a warning, never propagates.
- `test_no_submit_order_or_cancel_order_in_module` -- static source-text guard (matches the
  same pattern already used in `test_alpaca_submit_readiness.py`) asserting
  `submit_order(`/`cancel_order(`/`replace_order(` never appear in this file -- this module
  stays dry-run-only.

Credentials in these tests are fake, fixed strings (`"key"`/`"secret"`) passed only into mocked
objects that never make a real network call -- nothing resembling the earlier session's
credential-printing incident (phase14) was at risk here, and nothing was printed.

## 4. Verification evidence

```
$ python3 -m py_compile testdata/alpaca_broker_reporting.py testdata/runner.py
COMPILE_OK

$ python3 -c "import testdata.runner"
package-mode import OK
```
```
$ python3 -m unittest tests.test_alpaca_paper_check -v
Ran 5 tests in 0.219s
OK
```
```
$ python3 -m unittest tests.test_alpaca_submit_readiness tests.test_alpaca_paper_snapshot \
    tests.test_alpaca_paper_submit_one tests.test_execution_engine \
    tests.test_broker_cutover_baseline tests.test_alpaca_paper_check
Ran 72 tests in 0.581s
OK
```
```
$ python3 testdata/run_test_suite.py
Ran 846 tests in 404.780s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 841 before this phase, 846 after (+5, exactly the new test file -- no other
tests added or removed).

Code review: AST-based import-usage sweep over the modified module found no now-dead
top-level names beyond the expected `__future__.annotations` self-reference; no hardcoded
secrets; no `submit_order`/`cancel_order`/`replace_order` calls (enforced by its own test).

## 5. Git state

```
$ git status --porcelain (before this phase's commit)
 M testdata/alpaca_broker_reporting.py
 M testdata/runner.py
?? tests/test_alpaca_paper_check.py
```

## 6. Open items

1. `alpaca_paper_snapshot.py`'s safety-engineered broker-snapshot client (used by
   `run_broker_reconciliation`, `run_alpaca_paper_submit_readiness`,
   `run_alpaca_paper_submit_one`, `run_broker_order_lifecycle_monitor`,
   `run_create_broker_cutover_baseline`) remains hand-rolled `urllib` -- deliberately, per
   Section 1. A future SDK migration there would need to rebuild its read-only enforcement,
   credential redaction, and endpoint-mode gating against alpaca-py's exception surface before
   being safe to attempt.
2. `runner.py` decomposition backlog (Clusters 3 and 5, highest risk) remains queued,
   unaffected by this phase.
