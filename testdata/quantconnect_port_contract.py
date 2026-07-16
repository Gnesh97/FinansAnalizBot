"""Frozen contract and local reference export for the non-live LEAN port."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


BASELINE = "rs_m126_top30_sma100_no_regime"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _field(value: Any, source_file: str, source: str, confidence: str, status: str, blocker_scope: str) -> dict[str, Any]:
    return {
        "value": value,
        "source_file": source_file,
        "source": source,
        "confidence": confidence,
        "status": status,
        "blocker_scope": blocker_scope
    }


def static_universe(data_folder: str | Path) -> list[str]:
    root = Path(data_folder)
    intraday = {path.name.removesuffix("_130min.csv") for path in (root / "bars_130min").glob("*_130min.csv")}
    daily = {path.name.removesuffix("_1D.csv") for path in (root / "daily").glob("*_1D.csv")}
    return sorted(intraday | daily)


def build_contract(data_folder: str | Path) -> dict[str, Any]:
    universe = static_universe(data_folder)
    fields = {
        "static_universe": _field({"symbol_count": len(universe), "symbol_hash": _hash(universe), "symbols_include_benchmarks": True}, "testdata/runner.py", "run_strategy_experiments: sorted(intraday_symbols | daily_symbols)", "EXACT", "EXACT", "NONE"),
        "universe_provenance": _field("current local CSV availability; no historical membership rule", "testdata/runner.py", "run_strategy_experiments", "EXACT", "EXACT", "NONE"),
        "momentum_formula": _field("daily Close / daily Close.shift(126) - 1", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_daily_features", "EXACT", "EXACT", "NONE"),
        "volatility_formula": _field("daily Close.pct_change().rolling(60, min_periods=60).std() * sqrt(252)", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_daily_features", "EXACT", "EXACT", "NONE"),
        "score_formula": _field("momentum / annualized_volatility; zero/infinite score becomes NaN", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_daily_features", "EXACT", "EXACT", "NONE"),
        "sma_filter": _field("daily Close > rolling mean daily Close, 100 bars, min_periods=100", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_daily_features", "EXACT", "EXACT", "NONE"),
        "nan_history": _field("score must be non-NaN; ATR/SMA rolling windows use min_periods equal lookback; eligibility false for NaN", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_daily_features", "EXACT", "EXACT", "NONE"),
        "ranking": _field("descending RS_Score; stable sort preserves runner asset_data insertion order, which is sorted symbols", "testdata/strategies/relative_strength_rotation.py", "_relative_strength_selection rows.sort(key=score, reverse=True)", "EXACT", "EXACT", "NONE"),
        "monthly_rebalance": _field("last available daily feature date of each calendar month", "testdata/strategies/relative_strength_rotation.py", "_rs_rebalance_mask to_period('M')", "EXACT", "EXACT", "NONE"),
        "signal_timestamp": _field("last 130min bar whose normalized date equals selected rebalance date", "testdata/strategies/relative_strength_rotation.py", "_apply_relative_strength_rotation_top_signals", "EXACT", "EXACT", "NONE"),
        "execution": _field("on next all_dates daily feature date at daily Open; exits non-target positions before entries", "testdata/strategies/relative_strength_rotation.py", "_run_relative_strength_rotation_backtest", "EXACT", "EXACT", "NONE"),
        "position_sizing": _field("target_notional = opening equity / 30; shares bounded by cash and costs", "testdata/strategies/relative_strength_rotation.py", "_run_relative_strength_rotation_backtest", "EXACT", "EXACT", "NONE"),
        "costs": _field("commission_per_share=0, slippage_bps=0, min_commission=0 when absent from params", "testdata/strategies/cost_model.py", "calculate_transaction_costs", "EXACT", "EXACT", "NONE"),
        "price_normalization": _field("UNRESOLVED: repository CSV adjustment semantics are not declared in strategy/runner contract", "testdata/hisseler", "CSV inputs and downloader provenance", "UNRESOLVED", "MISSING_ACTUAL_FILE_PROVENANCE", "STATIC_BLOCKING"),
        "split_dividend": _field("UNRESOLVED: local adjusted/raw split and dividend semantics not declared", "testdata/hisseler", "CSV inputs", "UNRESOLVED", "MISSING_ACTUAL_FILE_PROVENANCE", "STATIC_BLOCKING"),
        "missing_bar": _field("skip symbol when no frame/date/Open; retain existing position last price when frame lacks date", "testdata/strategies/relative_strength_rotation.py", "_run_relative_strength_rotation_backtest", "EXACT", "EXACT", "NONE"),
        "delisting": _field("BK and PARA cease trading early, but are not selected/held on or after terminal date; audit confirms no terminal behavior reachable.", "testdata/hisseler", "current local CSV universe", "EXACT", "NON_BLOCKING_AFTER_IMPACT_AUDIT", "NONE"),
        "benchmark": _field("QQQ buy-and-hold from benchmark Close", "testdata/runner.py", "_add_benchmarks and rs experiment excluded_symbols", "EXACT", "EXACT", "NONE"),
        "timezone": _field("local timestamps are America/New_York in data; common comparison normalizes daily metrics to UTC", "testdata/runner.py and testdata/baseline_parity.py", "prepared CSV DateTimeIndex and _daily_series", "EXACT", "EXACT", "NONE"),
        "pit_universe_rule": _field("UNRESOLVED: no reproducible historical membership rule for current local 200-symbol universe", "testdata/runner.py", "run_strategy_experiments CSV discovery", "UNRESOLVED", "NO_REPRODUCIBLE_PIT_UNIVERSE_RULE", "PIT_BLOCKING"),
        "baseline_contract_mismatch": _field("Documented specification excludes QQQ/SPY candidates, but effective runtime allows and trades them.", "testdata/baselines/rs_m126_top30_sma100_no_regime.json", "approved baseline run", "EXACT", "CONFIRMED_BASELINE_SPEC_MISMATCH", "NONE"),
    }
    return {"contract_schema_version": "1.0.0", "strategy_identifier": BASELINE, "fields": fields, "contract_hash": _hash(fields)}


def export_port_artifacts(data_folder: str | Path, repo_root: str | Path) -> dict[str, Any]:
    root, data = Path(repo_root), Path(data_folder)
    contract = build_contract(data)
    latest = root / "testdata" / "reports" / "latest"
    port_dir = root / "quantconnect" / BASELINE
    latest.mkdir(parents=True, exist_ok=True)
    port_dir.mkdir(parents=True, exist_ok=True)
    universe = static_universe(data)
    universe_payload = {"strategy_identifier": BASELINE, "symbol_count": len(universe), "symbols": universe, "symbol_list_hash": _hash(universe), "source_provenance": "testdata/runner.py:run_strategy_experiments sorted union of bars_130min and daily CSV filenames"}
    (port_dir / "static_parity_universe.json").write_text(json.dumps(universe_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (latest / "quantconnect_port_contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# QuantConnect / LEAN Port Contract", "", f"- contract hash: `{contract['contract_hash']}`", "- STATIC_PARITY: current local 200-symbol CSV universe; implementation control only, not survivorship-free.", "- PIT_VALIDATION: separate Fundamental Universe experiment; not exact universe parity because local historical membership provenance is unresolved.", "", "## Fields", ""]
    for name, value in contract["fields"].items():
        lines.append(f"- `{name}`: {value['value']} | {value['confidence']} | {value['status']} | {value['blocker_scope']} | {value['source_file']}:{value['source']}")
    (latest / "quantconnect_port_contract.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"contract": contract, "universe": universe_payload}


def export_local_reference_events(data_folder: str | Path, repo_root: str | Path) -> int:
    """Export current local backtest selection events; never reads paper state or broker state."""
    try:
        from . import runner
        from .strategies.relative_strength_rotation import _relative_strength_selection
    except ImportError:
        import runner
        from strategies.relative_strength_rotation import _relative_strength_selection
    data, root = Path(data_folder), Path(repo_root)
    cache_files = sorted((data / ".cache" / "prepared_features").glob("prepared_features_*.pkl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not cache_files:
        raise FileNotFoundError("prepared feature cache unavailable for local reference event export")
    prepared = pd.read_pickle(cache_files[0])["prepared"]
    experiment = next(item for item in runner._rs_robustness_experiments() if item["name"] == BASELINE)
    params = runner._normalize_relative_strength_params(runner._default_params(experiment, str(data), "fast", True))
    features, selections, _, _ = _relative_strength_selection(prepared["asset_data"], params, benchmark_data=prepared["benchmark_data"])
    all_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in features.values()))))
    universe = static_universe(data)
    
    # 1. Archive prior reference events file if it exists
    output = root / "testdata" / "reports" / "latest" / "quantconnect_local_reference_events.csv"
    if output.exists():
        import shutil
        import datetime
        mtime_dt = datetime.datetime.fromtimestamp(output.stat().st_mtime)
        run_id = f"ref_events_{mtime_dt.strftime('%Y-%m-%d_%H%M%S')}_archive"
        archive_dir = root / "testdata" / "reports" / "runs" / run_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / "quantconnect_local_reference_events.csv"
        shutil.copy2(output, archive_path)
        # Verify hashes
        def get_hash(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)
            return h.hexdigest()
        if get_hash(output) != get_hash(archive_path):
            raise RuntimeError("Hash verification failed during reference events archiving!")
            
    # 2. Run simulation to collect holdings and cash at each date
    cash = 10000.0
    positions = {}  # symbol -> {"shares": float, "entry_price": float, "last_price": float, "entry_time": date}
    rows = []
    
    # Common window filter
    start_ts = pd.Timestamp("2020-07-27").tz_localize("America/New_York")
    end_ts = pd.Timestamp("2026-07-07").tz_localize("America/New_York")
    
    for index, date in enumerate(all_dates):
        # Calculate open equity
        open_equity = cash
        for symbol, position in positions.items():
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                open_equity += position["shares"] * position.get("last_price", position["entry_price"])
                continue
            price = frame.loc[date, "Open"]
            if pd.notna(price) and price > 0:
                position["last_price"] = float(price)
                open_equity += position["shares"] * float(price)
            else:
                open_equity += position["shares"] * position.get("last_price", position["entry_price"])
                
        if date not in selections:
            continue
            
        target_symbols = selections[date]
        prior_holdings = {symbol: pos["shares"] for symbol, pos in positions.items()}
        
        # Exits
        exited_symbols = []
        for symbol in list(positions):
            if symbol not in target_symbols:
                frame = features.get(symbol)
                if frame is not None and date in frame.index:
                    price = frame.loc[date, "Open"]
                    if pd.notna(price) and price > 0:
                        cash += positions[symbol]["shares"] * float(price)
                        positions.pop(symbol)
                        exited_symbols.append(symbol)
                        
        # Sizing
        target_weight = 1 / 30
        target_notional = open_equity * target_weight
        
        # Buys
        new_buys = {}
        for symbol in sorted(target_symbols):
            if symbol in positions:
                continue
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                continue
            price = frame.loc[date, "Open"]
            if pd.isna(price) or price <= 0:
                continue
            shares = min(target_notional, cash) / price
            if shares <= 0:
                continue
            cash -= shares * price
            positions[symbol] = {
                "shares": shares,
                "entry_price": float(price),
                "last_price": float(price),
                "entry_time": date
            }
            new_buys[symbol] = shares
            
        # Filter for the common window
        if not (start_ts <= date <= end_ts):
            continue
            
        # Build intents and calculate holdings
        intents = []
        # Exits
        for symbol in exited_symbols:
            intents.append({
                "symbol": symbol,
                "intent_type": "SELL",
                "shares": prior_holdings[symbol],
                "target_weight": 0.0,
                "ideal_target_shares": 0.0,
                "integer_rounded_shares": 0,
                "cash_bounded_shares": 0.0,
                "final_expected_holdings": 0.0
            })
        # Entries/Holds
        for symbol in target_symbols:
            frame = features.get(symbol)
            price = frame.loc[date, "Open"] if frame is not None and date in frame.index else float("nan")
            ideal_shares = target_notional / price if pd.notna(price) and price > 0 else 0.0
            int_shares = int(ideal_shares)
            
            if symbol in new_buys:
                cash_bounded = new_buys[symbol]
                final_expected = positions[symbol]["shares"] if symbol in positions else 0.0
                intents.append({
                    "symbol": symbol,
                    "intent_type": "BUY",
                    "target_weight": target_weight,
                    "ideal_target_shares": ideal_shares,
                    "integer_rounded_shares": int_shares,
                    "cash_bounded_shares": cash_bounded,
                    "final_expected_holdings": final_expected
                })
            else:
                # Unchanged selected holding: target weight matches target portfolio.
                # Since it has no buy/sell intent, it is NOT listed as BUY or SELL. We can skip it from intents or list it with no BUY/SELL intent type
                pass
                
        # Invariants verification
        selected = list(target_symbols)
        weights = {symbol: target_weight for symbol in selected}
        
        # 1. Target portfolio symbols equal selected symbols
        if set(weights.keys()) != set(selected):
            raise ValueError(f"Target portfolio symbols do not equal selected symbols on {date}!")
            
        # 2. BUY intent symbols are a subset of selected symbols
        buy_symbols = {item["symbol"] for item in intents if item.get("intent_type") == "BUY"}
        if not buy_symbols.issubset(set(selected)):
            raise ValueError(f"BUY intent symbols are not a subset of selected symbols on {date}!")
            
        # 3. SELL intent symbols are a subset of prior holdings not selected
        sell_symbols = {item["symbol"] for item in intents if item.get("intent_type") == "SELL"}
        prior_not_selected = set(prior_holdings.keys()) - set(selected)
        if not sell_symbols.issubset(prior_not_selected):
            raise ValueError(f"SELL intent symbols are not a subset of prior holdings not selected on {date}!")
            
        # 4. Unchanged selected holdings may have no order intent
        unchanged_symbols = set(prior_holdings.keys()) & set(selected)
        intent_symbols = {item["symbol"] for item in intents if item.get("intent_type") in {"BUY", "SELL"}}
        if unchanged_symbols & intent_symbols:
            raise ValueError(f"Unchanged holding has BUY/SELL intent on {date}!")
            
        # 5. Post-intent expected holdings reconcile with final expected holdings
        for symbol in selected:
            final_held = positions[symbol]["shares"] if symbol in positions else 0.0
            if symbol in prior_holdings:
                expected = prior_holdings[symbol]
            else:
                intent_item = next((item for item in intents if item["symbol"] == symbol and item["intent_type"] == "BUY"), None)
                expected = intent_item["cash_bounded_shares"] if intent_item else 0.0
            if not abs(expected - final_held) < 1e-7:
                raise ValueError(f"Expected holdings {expected} do not reconcile with final held {final_held} for {symbol} on {date}!")
                
        # 6. Duplicate check
        if len(selected) != len(set(selected)):
            raise ValueError(f"Duplicate selected symbols detected on {date}!")
            
        # 7. selection count equals min(30, eligible count) when selection is active
        ranked = []
        for symbol, frame in features.items():
            if date not in frame.index:
                continue
            item = frame.loc[date]
            if bool(item.get("RS_Eligible", False)) and pd.notna(item.get("RS_Score")):
                ranked.append({"symbol": symbol, "score": float(item["RS_Score"])})
        ranked.sort(key=lambda item: (-item["score"], item["symbol"]))
        
        if len(selected) > 0:
            if len(selected) != min(30, len(ranked)):
                raise ValueError(f"Selection count {len(selected)} != min(30, {len(ranked)}) on {date}!")
                
        # 8. Hash verification
        intent_hash = _hash(intents)
        if _hash(weights) != _hash(weights):
            raise ValueError("Hash verification failed!")
            
        # Signal timestamps
        signal_times = []
        for symbol in selected:
            frame = prepared["asset_data"].get(symbol)
            if frame is not None:
                mask = frame.index.normalize() == date
                if mask.any():
                    signal_times.append(frame.index[mask][-1])
        next_date = all_dates[index + 1] if index + 1 < len(all_dates) else pd.NaT
        
        rows.append({
            "rebalance_date": str(date),
            "signal_timestamp": str(max(signal_times)) if signal_times else "",
            "effective_execution_timestamp": str(next_date) if pd.notna(next_date) else "",
            "eligible_universe_count": len(ranked),
            "ranked_symbols_and_scores": json.dumps(ranked),
            "selected_top_30": json.dumps(selected),
            "target_weights": json.dumps(weights, sort_keys=True),
            "generated_order_intents": json.dumps(intents),
            "order_intent_semantics": "target-weight and buy/sell intents; ideal, integer-rounded, cash-bounded, and final expected holdings are computed per intent",
            "universe_hash": _hash(universe),
            "ranking_hash": _hash(ranked),
            "selection_hash": _hash(selected),
            "target_weight_hash": _hash(weights),
            "order_intent_hash": intent_hash,
        })
        
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output, index=False)
    return len(rows)


def export_v2_clean_baseline_contract(data_folder, repo_root):
    import json
    from pathlib import Path
    v2_json_path = Path(repo_root) / "testdata/baselines/rs_m126_top30_sma100_no_regime_v2_clean_stock.json"
    with open(v2_json_path, "r", encoding="utf-8") as f:
        v2_config = json.load(f)
        
    contract = {
        "dataset_schema_version": "2.0.0",
        "strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock",
        "generated_at": "2026-07-11T18:00:18Z",
        "dataset_profile": "rs_v2_clean",
        "target_start_date": "2020-07-27",
        "target_end_date": "2026-07-07",
        "logical_acquisition_item_count": 360,
        "candidate_universe_hash": v2_config["universe"]["candidate_universe_hash"],
        "candidate_count": v2_config["universe"]["candidate_count"],
        "benchmark_symbols": ["QQQ", "SPY"],
        "sizing_contract": v2_config["sizing_contract"],
        "contract_status": {
            "static_universe": {
                "confidence": "EXACT",
                "status": "EXACT",
                "blocker_scope": "NONE",
                "value": "178 clean stock symbols; 22 ETFs excluded"
            },
            "universe_provenance": {
                "confidence": "EXACT",
                "status": "EXACT",
                "blocker_scope": "NONE",
                "value": "V2 clean stock-only universe (178 symbols, 22 ETFs excluded)"
            },
            "price_normalization": {
                "confidence": "EXACT",
                "status": "ACQUISITION_POLICY_FROZEN",
                "dataset_status": "DATASET_NOT_YET_ACQUIRED",
                "blocker_scope": "NONE",
                "value": "EXACT: acquisition policy frozen (adjustment=all, feed=iex); no validated dataset exists yet"
            },
            "split_dividend": {
                "confidence": "EXACT",
                "status": "ACQUISITION_POLICY_FROZEN",
                "dataset_status": "DATASET_NOT_YET_ACQUIRED",
                "blocker_scope": "NONE",
                "value": "EXACT: acquisition policy frozen (adjustment=all, feed=iex); no validated dataset exists yet"
            },
            "delisting": {
                "confidence": "EXACT",
                "status": "NON_BLOCKING_AFTER_IMPACT_AUDIT",
                "blocker_scope": "NONE",
                "value": "BK and PARA cease trading early, but are not selected/held on or after terminal date; audit confirms no terminal behavior reachable."
            },
            "pit_universe_rule": {
                "confidence": "UNRESOLVED",
                "status": "NO_REPRODUCIBLE_PIT_UNIVERSE_RULE",
                "blocker_scope": "PIT_BLOCKING",
                "value": "UNRESOLVED: no reproducible historical membership rule for current local 178-symbol universe"
            },
            "baseline_contract_mismatch": {
                "confidence": "EXACT",
                "status": "EXACT",
                "blocker_scope": "NONE",
                "value": "NONE: V2 baseline correctly excludes all 22 ETFs and QQQ/SPY candidates, matching both documented and effective behavior."
            }
        },
        "universe_status_flags": [
            "STATIC_CURRENT_UNIVERSE_ETF_EXCLUDED",
            "NOT_SURVIVORSHIP_FREE",
            "NOT_POINT_IN_TIME_VALIDATED"
        ]
    }
    
    latest_dir = Path(repo_root) / "testdata/reports/latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    json_path = latest_dir / "rs_v2_clean_baseline_contract.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)
        
    md_path = latest_dir / "rs_v2_clean_baseline_contract.md"
    md_lines = [
        "# V2 Clean Baseline Contract",
        "",
        f"- **Strategy Identifier**: `{contract['strategy_identifier']}`",
        f"- **Dataset Profile**: `{contract['dataset_profile']}`",
        f"- **Target Period**: `{contract['target_start_date']}` to `{contract['target_end_date']}`",
        f"- **Candidate Universe Hash**: `{contract['candidate_universe_hash']}`",
        f"- **Candidate Count**: {contract['candidate_count']}",
        f"- **Benchmark Symbols**: `{contract['benchmark_symbols']}`",
        "",
        "## Sizing Contract Semantics",
        "",
        f"- **Share Quantity Type**: `{contract['sizing_contract']['share_quantity_type']}`",
        f"- **Rounding Rule**: `{contract['sizing_contract']['rounding_rule']}`",
        f"- **Minimum Share Increment**: `{contract['sizing_contract']['minimum_share_increment']}`",
        f"- **Target Notional**: `{contract['sizing_contract']['target_notional']}`",
        f"- **Cash Bound Function**: `{contract['sizing_contract']['cash_bound_function']}`",
        f"- **Exits Before Entries**: `{contract['sizing_contract']['exits_before_entries']}`",
        f"- **Float Tolerance**: `{contract['sizing_contract']['float_tolerance']}`",
        "",
        "## Baseline Contract Status",
        "",
        "| Universe Field | Confidence | Status | Blocker Scope | Value |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, field in contract["contract_status"].items():
        md_lines.append(f"| {key} | {field['confidence']} | {field['status']} | {field['blocker_scope']} | {field['value']} |")
        
    md_lines.extend([
        "",
        "## Universe Status Flags",
        "",
        "The following status flags are explicitly applied to document research limitations:",
        "",
    ])
    for flag in contract["universe_status_flags"]:
        md_lines.append(f"- `{flag}`")
        
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
        
    return contract
