import math
import time
import numpy as np
import pandas as pd

PORTFOLIO_REQUIRED_COLUMNS = [
    "Open",
    "High",
    "Low",
    "Close",
    "ENTRY_SIGNAL",
    "EXIT_SIGNAL",
    "SMA20_Reversion_Exit",
    "Take_Profit",
    "Hurst_Exponent",
    "Momentum_Factor",
    "Momentum_Factor_Raw",
    "ATR_Pct",
    "Disaster_Stop",
    "ATR_Take_Profit_Distance",
    "SuperTrend_ATR_22",
]
PORTFOLIO_CORE_REQUIRED_COLUMNS = list(PORTFOLIO_REQUIRED_COLUMNS)

PORTFOLIO_CONTEXT_SOURCE_COLUMNS = [
    "RSI_Value",
    "Bollinger_Lower",
    "Bollinger_Entry_Level",
    "ATR_Pct",
    "Macro_Uptrend",
    "Risk_Off",
    "Regime_Ready",
    "Quality_Filter_OK",
    "Confirmation_Mode",
    "Hurst_Filter_OK",
    "Trigger",
    "Raw_Entry_Signal",
    "Confirmed_Entry_Signal",
    "Entry_Context_Filter_OK",
    "Entry_Context_Rejected_Reason",
    "Entry_Gap_Pct",
    "Entry_BB_Distance_ATR",
    "Entry_Signal_Range_Pct",
    "Entry_Signal_Close_Position_In_Range",
]
PORTFOLIO_REQUIRED_COLUMNS = list(
    dict.fromkeys(PORTFOLIO_CORE_REQUIRED_COLUMNS + PORTFOLIO_CONTEXT_SOURCE_COLUMNS)
)

PORTFOLIO_CONTEXT_SIGNAL_COLUMNS = [
    "Signal_RSI_Value",
    "Signal_Bollinger_Lower",
    "Signal_Bollinger_Entry_Level",
    "Signal_ATR_Pct",
    "Signal_Open",
    "Signal_High",
    "Signal_Low",
    "Signal_Previous_Close",
    "Signal_Risk_Off",
    "Signal_Regime_Ready",
    "Signal_Quality_Filter_OK",
    "Signal_Confirmation_Mode",
    "Signal_Hurst_Filter_OK",
    "Signal_Trigger",
    "Signal_Raw_Entry_Signal",
    "Signal_Confirmed_Entry_Signal",
    "Signal_Macro_Uptrend",
    "Signal_Entry_Context_Filter_OK",
    "Signal_Entry_Context_Rejected_Reason",
]

TRADE_CONTEXT_REPORT_COLUMNS = [
    "Experiment Name",
    "Symbol",
    "Entry Time",
    "Exit Time",
    "Exit Reason",
    "Net PnL",
    "R Multiple",
    "MFE_R",
    "MAE_R",
    "Bars Held",
    "Entry RSI Value",
    "Entry BB Distance ATR",
    "Entry ATR Pct",
    "Entry Hurst",
    "Entry Momentum Factor",
    "Entry Signal Range Pct",
    "Entry Signal Close Position In Range",
    "Entry Gap Pct",
    "Entry Macro Uptrend",
    "Entry Risk Off",
    "Entry Regime Ready",
    "Entry Quality Filter OK",
    "Entry Confirmation Mode",
    "Entry Hurst Filter OK",
    "Entry Raw Trigger",
    "Entry Raw Entry Signal",
    "Entry Confirmed Entry Signal",
    "Entry Context Filter OK",
    "Entry Context Rejected Reason",
]

PORTFOLIO_REJECTION_COLUMNS = [
    "Rejected Already In Position",
    "Rejected Same Bar Exit",
    "Rejected Missing Open",
    "Rejected Missing Signal Close",
    "Rejected Missing Stop",
    "Rejected Missing ATR",
    "Rejected Missing Target Distance",
    "Rejected Missing Ranking Score",
    "Rejected Risk Off",
    "Rejected Max Positions",
    "Rejected Invalid Risk Per Share",
    "Rejected Insufficient Cash",
    "Rejected Zero Shares",
]

SYMBOL_ATTRIBUTION_COLUMNS = [
    "Experiment Name",
    "Symbol",
    "Trade Count",
    "Net PnL",
    "Avg R",
    "Median R",
    "Best R",
    "Worst R",
    "Win Rate",
    "Profit Factor",
    "Stop Exit Count",
    "Take Profit Exit Count",
    "SMA20 Reversion Exit Count",
    "Time Stop Exit Count",
    "Forced Final Exit Count",
]

CACHE_REQUIRED_COLUMNS = set(
    PORTFOLIO_REQUIRED_COLUMNS
    + [
        "Indicators_Ready",
        "Raw_Entry_Signal",
        "Quality_Filter_OK",
        "Avg_Dollar_Volume",
        "Confirmation_OK",
            "Confirmation_Mode",
            "Confirmed_Entry_Signal",
            "Entry_Context_Filter_OK",
            "Entry_Context_Rejected_Reason",
            "Entry_Gap_Pct",
            "Entry_BB_Distance_ATR",
            "Entry_Signal_Range_Pct",
            "Entry_Signal_Close_Position_In_Range",
            "Hurst_Filter_OK",
            "Macro_Filter_OK",
        "Is_Partial_Session_Bar",
        "Take_Profit",
        "Risk_Off",
        "Regime_Ready",
    ]
)


def _normalize_ranking_mode(ranking_mode):
    mode = str(ranking_mode).lower()
    if mode not in {"momentum", "hurst", "none"}:
        raise ValueError(
            "ranking_mode 'momentum', 'hurst' veya 'none' degerlerinden biri olmali."
        )
    return mode



def _normalize_execution_mode(execution_mode):
    mode = str(execution_mode).lower()
    if mode not in {"bar_close", "intrabar_1m"}:
        raise ValueError(
            "execution_mode 'bar_close' veya 'intrabar_1m' olmali."
        )
    return mode



def _normalize_exit_mode(exit_mode):
    mode = str(exit_mode).lower()
    if mode not in {"sma_only", "target_only", "sma_or_target", "time_or_target"}:
        raise ValueError(
            "exit_mode 'sma_only', 'target_only', 'sma_or_target' veya "
            "'time_or_target' olmali."
        )
    return mode



def _normalize_risk_off_exit_mode(risk_off_exit_mode):
    mode = str(risk_off_exit_mode).lower()
    if mode not in {"hold_existing", "close_positions"}:
        raise ValueError(
            "risk_off_exit_mode 'hold_existing' veya 'close_positions' olmali."
        )
    return mode



def _align_timestamp_to_index(value, index):
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if getattr(index, "tz", None) is not None:
        if timestamp.tzinfo is None:
            return timestamp.tz_localize(index.tz)
        return timestamp.tz_convert(index.tz)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert(None)
    return timestamp



class PortfolioManager:
    def __init__(
        self,
        asset_data,
        initial_cash=10000.0,
        max_open_positions=5,
        risk_per_trade=0.02,
        ranking_mode="momentum",
        evaluation_start=None,
        evaluation_end=None,
        execution_mode="bar_close",
        intraday_data=None,
        verbose=True,
        exit_mode="sma_or_target",
        time_stop_bars=10,
        fast_backtest=True,
        commission_rate=0.001,
        slippage_mu=0.05,
        risk_off_exit_mode="hold_existing",
    ):
        self.asset_data = asset_data
        self.intraday_data = intraday_data or {}
        self.initial_cash = float(initial_cash)
        self.max_open_positions = int(max_open_positions)
        self.risk_per_trade = float(risk_per_trade)
        self.ranking_mode = _normalize_ranking_mode(ranking_mode)
        self.execution_mode = _normalize_execution_mode(execution_mode)
        self.exit_mode = _normalize_exit_mode(exit_mode)
        self.time_stop_bars = int(time_stop_bars)
        self.fast_backtest = bool(fast_backtest)
        self.komisyon_oran = float(commission_rate)
        self.slippage_mu = float(slippage_mu)
        if self.komisyon_oran < 0:
            raise ValueError("commission_rate negatif olamaz.")
        if self.slippage_mu < 0:
            raise ValueError("slippage_mu negatif olamaz.")
        self.risk_off_exit_mode = _normalize_risk_off_exit_mode(risk_off_exit_mode)
        self.portfolio_fast_arrays_build_seconds = 0.0
        self.portfolio_loop_seconds = 0.0
        self.target_enabled = self.exit_mode in {
            "target_only",
            "sma_or_target",
            "time_or_target",
        }
        self.sma_enabled = self.exit_mode in {"sma_only", "sma_or_target"}
        self.evaluation_start = evaluation_start
        self.evaluation_end = evaluation_end
        self.verbose = verbose
        self.positions = {}
        self.trade_log = []
        self.equity_curve = []
        self.rejected_signals = 0
        self.macro_rejected_signals = 0
        self.rejection_diagnostics = self._empty_rejection_diagnostics()
        self._warned_missing_trade_context_columns = set()
        self.max_open_observed = 0
        self.max_heat_observed = 0.0
        if self.execution_mode == "intrabar_1m":
            missing_intraday = [
                symbol for symbol in self.asset_data if symbol not in self.intraday_data
            ]
            if missing_intraday:
                raise ValueError(
                    "execution_mode='intrabar_1m' icin 1m intraday veri eksik: "
                    + ", ".join(missing_intraday[:10])
                )
        self.market_data = self._build_common_market_data()
        self.market_regime = self._build_market_regime()
        self.evaluation_start = _align_timestamp_to_index(
            self.evaluation_start,
            self.market_data.index,
        )
        self.evaluation_end = _align_timestamp_to_index(
            self.evaluation_end,
            self.market_data.index,
        )
        self.timestamps = self.market_data.index
        self.fast_arrays = {}
        self.fast_regime = {}
        if self.fast_backtest:
            fast_start = time.perf_counter()
            self._build_fast_arrays()
            self.portfolio_fast_arrays_build_seconds = time.perf_counter() - fast_start

    def _build_common_market_data(self):
        prepared = {}
        required = PORTFOLIO_CORE_REQUIRED_COLUMNS

        for symbol, data in self.asset_data.items():
            missing = [column for column in required if column not in data]
            if missing:
                raise ValueError(
                    f"{symbol} eksik portfoy kolonlari: {', '.join(missing)}"
                )

            frame = data[required].copy()
            for column in PORTFOLIO_CONTEXT_SOURCE_COLUMNS:
                if column in data:
                    frame[column] = data[column]
                else:
                    self._warn_trade_context_missing(column)
                    frame[column] = np.nan
            frame["ENTRY_ORDER"] = frame["ENTRY_SIGNAL"].shift(1).fillna(False)
            frame["EXIT_ORDER"] = frame["EXIT_SIGNAL"].shift(1).fillna(False)
            frame["Signal_Time"] = pd.Series(frame.index, index=frame.index).shift(1)
            frame["Signal_SMA20_Reversion_Exit"] = (
                frame["SMA20_Reversion_Exit"].shift(1).fillna(False)
            )
            frame["Signal_Hurst"] = frame["Hurst_Exponent"].shift(1)
            frame["Signal_Momentum"] = frame["Momentum_Factor"].shift(1)
            frame["Signal_Stop"] = frame["Disaster_Stop"].shift(1)
            frame["Signal_ATR"] = frame["SuperTrend_ATR_22"].shift(1)
            frame["Signal_Take_Profit_Distance"] = (
                frame["ATR_Take_Profit_Distance"].shift(1)
            )
            frame["Signal_Close"] = frame["Close"].shift(1)
            frame["Signal_RSI_Value"] = frame["RSI_Value"].shift(1)
            frame["Signal_Bollinger_Lower"] = frame["Bollinger_Lower"].shift(1)
            frame["Signal_Bollinger_Entry_Level"] = (
                frame["Bollinger_Entry_Level"].shift(1)
            )
            frame["Signal_ATR_Pct"] = frame["ATR_Pct"].shift(1)
            frame["Signal_Open"] = frame["Open"].shift(1)
            frame["Signal_High"] = frame["High"].shift(1)
            frame["Signal_Low"] = frame["Low"].shift(1)
            frame["Signal_Previous_Close"] = frame["Close"].shift(2)
            frame["Signal_Macro_Uptrend"] = frame["Macro_Uptrend"].shift(1)
            frame["Signal_Risk_Off"] = frame["Risk_Off"].shift(1)
            frame["Signal_Regime_Ready"] = frame["Regime_Ready"].shift(1)
            frame["Signal_Quality_Filter_OK"] = frame["Quality_Filter_OK"].shift(1)
            frame["Signal_Confirmation_Mode"] = frame["Confirmation_Mode"].shift(1)
            frame["Signal_Hurst_Filter_OK"] = frame["Hurst_Filter_OK"].shift(1)
            frame["Signal_Trigger"] = frame["Trigger"].shift(1)
            frame["Signal_Raw_Entry_Signal"] = frame["Raw_Entry_Signal"].shift(1)
            frame["Signal_Confirmed_Entry_Signal"] = (
                frame["Confirmed_Entry_Signal"].shift(1)
            )
            frame["Signal_Entry_Context_Filter_OK"] = (
                frame["Entry_Context_Filter_OK"].shift(1)
            )
            frame["Signal_Entry_Context_Rejected_Reason"] = (
                frame["Entry_Context_Rejected_Reason"].shift(1)
            )
            prepared[symbol] = frame[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "ENTRY_ORDER",
                    "EXIT_ORDER",
                    "Signal_Time",
                    "Signal_SMA20_Reversion_Exit",
                    "Signal_Hurst",
                    "Signal_Momentum",
                    "Signal_Stop",
                    "Signal_ATR",
                    "Signal_Take_Profit_Distance",
                    "Signal_Close",
                    "SuperTrend_ATR_22",
                    *PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
                ]
            ]

        if not prepared:
            return pd.DataFrame()
        return pd.concat(prepared, axis=1, join="outer").sort_index()

    def _build_market_regime(self):
        regime_frames = []
        for symbol, data in self.asset_data.items():
            required = {"Risk_Off", "Regime_Ready"}
            if required.issubset(data.columns):
                regime_frames.append(
                    data[["Risk_Off", "Regime_Ready"]].rename(
                        columns=lambda column: f"{symbol}_{column}"
                    )
                )

        if not regime_frames:
            raise ValueError(
                "Makro rejim verisi bulunamadi. Varliklari benchmark_df ile "
                "build_mega_dataframe uzerinden hazirlayin."
            )

        combined = pd.concat(regime_frames, axis=1, join="outer").sort_index()
        risk_columns = [
            column for column in combined if column.endswith("_Risk_Off")
        ]
        ready_columns = [
            column for column in combined if column.endswith("_Regime_Ready")
        ]
        regime = pd.DataFrame(index=self.market_data.index)
        regime["Risk_Off"] = (
            combined[risk_columns].bfill(axis=1).iloc[:, 0]
        ).reindex(regime.index)
        regime["Regime_Ready"] = (
            combined[ready_columns].bfill(axis=1).iloc[:, 0]
        ).reindex(regime.index)
        return regime

    def _is_risk_off(self, timestamp):
        if timestamp not in self.market_regime.index:
            return True
        ready = self.market_regime.at[timestamp, "Regime_Ready"]
        risk_off = self.market_regime.at[timestamp, "Risk_Off"]
        if pd.isna(ready) or not bool(ready) or pd.isna(risk_off):
            return True
        return bool(risk_off)

    def _build_fast_arrays(self):
        self.fast_arrays = {}
        columns = [
            "Open",
            "High",
            "Low",
            "Close",
            "ENTRY_ORDER",
            "EXIT_ORDER",
            "Signal_Time",
            "Signal_SMA20_Reversion_Exit",
            "Signal_Hurst",
            "Signal_Momentum",
            "Signal_Stop",
            "Signal_ATR",
            "Signal_Take_Profit_Distance",
            "Signal_Close",
            "SuperTrend_ATR_22",
            *PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
        ]
        for symbol in sorted(self.asset_data):
            values = {}
            for column in columns:
                key = (symbol, column)
                if key in self.market_data.columns:
                    values[column] = self.market_data[key].to_numpy(copy=False)
                else:
                    values[column] = np.full(len(self.timestamps), np.nan)
            self.fast_arrays[symbol] = values
        self.fast_regime = {
            "Risk_Off": self.market_regime["Risk_Off"].to_numpy(copy=False),
            "Regime_Ready": self.market_regime["Regime_Ready"].to_numpy(copy=False),
        }

    def _is_risk_off_fast(self, index):
        ready = self.fast_regime["Regime_Ready"][index]
        risk_off = self.fast_regime["Risk_Off"][index]
        if pd.isna(ready) or not bool(ready) or pd.isna(risk_off):
            return True
        return bool(risk_off)

    def _update_trade_excursions_fast(self, symbol, index):
        position = self.positions[symbol]
        values = self.fast_arrays[symbol]
        high_price = self._finite_or_nan(values["High"][index])
        low_price = self._finite_or_nan(values["Low"][index])
        if pd.notna(high_price):
            favorable = (high_price - position["entry_price"]) * position["shares"]
            position["mfe"] = max(position["mfe"], favorable)
        if pd.notna(low_price):
            adverse = (low_price - position["entry_price"]) * position["shares"]
            position["mae"] = min(position["mae"], adverse)

    def _in_evaluation_window(self, timestamp):
        if self.evaluation_start is not None and timestamp < self.evaluation_start:
            return False
        if self.evaluation_end is not None and timestamp > self.evaluation_end:
            return False
        return True

    def _calculate_slippage_amount(self, raw_price, atr_value):
        raw_price = self._finite_or_nan(raw_price)
        atr_value = self._finite_or_nan(atr_value)
        if pd.notna(atr_value) and atr_value > 0:
            return atr_value * self.slippage_mu
        if pd.notna(raw_price) and raw_price > 0:
            return raw_price * 0.0005
        return np.nan

    def _value_at(self, timestamp, symbol, column):
        key = (symbol, column)
        if key not in self.market_data.columns:
            return np.nan
        return self.market_data.at[timestamp, key]

    def _finite_or_nan(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return np.nan
        return value if np.isfinite(value) else np.nan

    def _warn_trade_context_missing(self, column):
        if column in self._warned_missing_trade_context_columns:
            return
        print(f"Trade context column missing: {column}")
        self._warned_missing_trade_context_columns.add(column)

    def _boolean_or_nan(self, value):
        if pd.isna(value):
            return np.nan
        return bool(value)

    def _trade_context_from_signal_values(self, context):
        signal_close = self._finite_or_nan(context.get("Signal_Close"))
        signal_open = self._finite_or_nan(context.get("Signal_Open"))
        signal_high = self._finite_or_nan(context.get("Signal_High"))
        signal_low = self._finite_or_nan(context.get("Signal_Low"))
        signal_previous_close = self._finite_or_nan(
            context.get("Signal_Previous_Close")
        )
        bollinger_lower = self._finite_or_nan(context.get("Signal_Bollinger_Lower"))
        atr_value = self._finite_or_nan(context.get("Signal_ATR"))

        bb_distance_atr = (
            (signal_close - bollinger_lower) / atr_value
            if pd.notna(signal_close)
            and pd.notna(bollinger_lower)
            and pd.notna(atr_value)
            and atr_value != 0
            else np.nan
        )
        signal_range = (
            signal_high - signal_low
            if pd.notna(signal_high) and pd.notna(signal_low)
            else np.nan
        )
        signal_range_pct = (
            100 * signal_range / signal_close
            if pd.notna(signal_range)
            and pd.notna(signal_close)
            and signal_close != 0
            else np.nan
        )
        close_position_in_range = (
            (signal_close - signal_low) / signal_range
            if pd.notna(signal_close)
            and pd.notna(signal_low)
            and pd.notna(signal_range)
            and signal_range != 0
            else np.nan
        )
        gap_pct = (
            100 * (signal_open - signal_previous_close) / signal_previous_close
            if pd.notna(signal_open)
            and pd.notna(signal_previous_close)
            and signal_previous_close != 0
            else np.nan
        )

        return {
            "Entry RSI Value": self._finite_or_nan(context.get("Signal_RSI_Value")),
            "Entry Bollinger Lower": bollinger_lower,
            "Entry Bollinger Entry Level": self._finite_or_nan(
                context.get("Signal_Bollinger_Entry_Level")
            ),
            "Entry BB Distance ATR": bb_distance_atr,
            "Entry ATR Pct": self._finite_or_nan(context.get("Signal_ATR_Pct")),
            "Entry Hurst": self._finite_or_nan(context.get("Signal_Hurst")),
            "Entry Momentum Factor": self._finite_or_nan(
                context.get("Signal_Momentum")
            ),
            "Entry Signal Close": signal_close,
            "Entry Signal High": signal_high,
            "Entry Signal Low": signal_low,
            "Entry Signal Open": signal_open,
            "Entry Signal Range Pct": signal_range_pct,
            "Entry Signal Close Position In Range": close_position_in_range,
            "Entry Gap Pct": gap_pct,
            "Entry Macro Uptrend": self._boolean_or_nan(
                context.get("Signal_Macro_Uptrend")
            ),
            "Entry Risk Off": self._boolean_or_nan(context.get("Signal_Risk_Off")),
            "Entry Regime Ready": self._boolean_or_nan(
                context.get("Signal_Regime_Ready")
            ),
            "Entry Quality Filter OK": self._boolean_or_nan(
                context.get("Signal_Quality_Filter_OK")
            ),
            "Entry Confirmation Mode": context.get("Signal_Confirmation_Mode"),
            "Entry Hurst Filter OK": self._boolean_or_nan(
                context.get("Signal_Hurst_Filter_OK")
            ),
            "Entry Raw Trigger": self._boolean_or_nan(context.get("Signal_Trigger")),
            "Entry Raw Entry Signal": self._boolean_or_nan(
                context.get("Signal_Raw_Entry_Signal")
            ),
            "Entry Confirmed Entry Signal": self._boolean_or_nan(
                context.get("Signal_Confirmed_Entry_Signal")
            ),
            "Entry Context Filter OK": self._boolean_or_nan(
                context.get("Signal_Entry_Context_Filter_OK")
            ),
            "Entry Context Rejected Reason": context.get(
                "Signal_Entry_Context_Rejected_Reason"
            ),
        }

    def _trade_context_at(self, timestamp, symbol):
        context_columns = [
            "Signal_Close",
            "Signal_Hurst",
            "Signal_Momentum",
            "Signal_ATR",
            *PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
        ]
        return {
            column: self._value_at(timestamp, symbol, column)
            for column in context_columns
        }

    def _trade_context_from_fast_values(self, values, index):
        context_columns = [
            "Signal_Close",
            "Signal_Hurst",
            "Signal_Momentum",
            "Signal_ATR",
            *PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
        ]
        return {column: values[column][index] for column in context_columns}

    # Portfolio rejection diagnostics helper.
    def _empty_rejection_diagnostics(self):
        return {column: 0 for column in PORTFOLIO_REJECTION_COLUMNS}

    def _reject_candidate(self, reason, count=1):
        if reason not in self.rejection_diagnostics:
            self.rejection_diagnostics[reason] = 0
        self.rejection_diagnostics[reason] += int(count)

    def _candidate_from_signal_values(
        self,
        symbol,
        entry_order,
        open_price,
        signal_time,
        signal_close,
        hurst,
        momentum,
        stop,
        atr_value,
        target_distance,
        context=None,
    ):
        if pd.isna(entry_order) or not bool(entry_order):
            return None
        if pd.isna(open_price):
            self._reject_candidate("Rejected Missing Open")
            return None
        if pd.isna(signal_close):
            self._reject_candidate("Rejected Missing Signal Close")
            return None
        if pd.isna(stop):
            self._reject_candidate("Rejected Missing Stop")
            return None
        if pd.isna(atr_value):
            self._reject_candidate("Rejected Missing ATR")
            return None
        if self.target_enabled and pd.isna(target_distance):
            self._reject_candidate("Rejected Missing Target Distance")
            return None

        hurst_value = self._finite_or_nan(hurst)
        momentum_value = self._finite_or_nan(momentum)
        if self.ranking_mode == "momentum":
            ranking_score = momentum_value
        elif self.ranking_mode == "hurst":
            ranking_score = hurst_value
        else:
            ranking_score = np.nan
        if self.ranking_mode != "none" and pd.isna(ranking_score):
            self._reject_candidate("Rejected Missing Ranking Score")
            return None

        return {
            "symbol": symbol,
            "open": float(open_price),
            "signal_time": signal_time,
            "signal_close": float(signal_close),
            "hurst": hurst_value,
            "momentum": momentum_value,
            "ranking_score": ranking_score,
            "stop": float(stop),
            "atr": atr_value,
            "target_distance": self._finite_or_nan(target_distance),
            "entry_context": self._trade_context_from_signal_values(context or {}),
        }

    def _portfolio_equity(self, last_prices):
        holdings_value = sum(
            position["shares"] * last_prices.get(symbol, position["entry_price"])
            for symbol, position in self.positions.items()
        )
        return self.current_cash + holdings_value

    def _update_trade_excursions(self, symbol, timestamp):
        position = self.positions[symbol]
        high_price = self._finite_or_nan(self._value_at(timestamp, symbol, "High"))
        low_price = self._finite_or_nan(self._value_at(timestamp, symbol, "Low"))
        if pd.notna(high_price):
            favorable = (high_price - position["entry_price"]) * position["shares"]
            position["mfe"] = max(position["mfe"], favorable)
        if pd.notna(low_price):
            adverse = (low_price - position["entry_price"]) * position["shares"]
            position["mae"] = min(position["mae"], adverse)

    def _get_intrabar_window(self, symbol, entry_time, next_bar_time):
        if symbol not in self.intraday_data:
            raise ValueError(f"{symbol} icin 1m intraday veri bulunamadi.")
        intraday = self.intraday_data[symbol]
        if intraday.empty:
            return intraday

        start = _align_timestamp_to_index(entry_time, intraday.index)
        end = _align_timestamp_to_index(next_bar_time, intraday.index)
        return intraday.loc[(intraday.index > start) & (intraday.index <= end)]

    def _check_intrabar_exit(self, symbol, position, window):
        if window.empty:
            return None
        for timestamp, row in window.iterrows():
            high_price = self._finite_or_nan(row.get("High"))
            low_price = self._finite_or_nan(row.get("Low"))
            if pd.isna(high_price) or pd.isna(low_price):
                continue

            favorable = (high_price - position["entry_price"]) * position["shares"]
            adverse = (low_price - position["entry_price"]) * position["shares"]
            position["mfe"] = max(position["mfe"], favorable)
            position["mae"] = min(position["mae"], adverse)

            # Conservative long-path assumption: if stop and target are inside
            # the same 1m candle, the stop is considered first.
            if low_price <= position["stop"]:
                return {
                    "timestamp": timestamp,
                    "raw_exit_price": position["stop"],
                    "exit_reason": "disaster_stop",
                }
            if self.target_enabled and high_price >= position["take_profit_target"]:
                return {
                    "timestamp": timestamp,
                    "raw_exit_price": position["take_profit_target"],
                    "exit_reason": "take_profit",
                }
        return None

    def _process_intrabar_exits(self, bar_timestamp):
        if self.execution_mode != "intrabar_1m":
            return set()

        exited_symbols = set()
        for symbol in list(self.positions):
            position = self.positions[symbol]
            start_time = position.get("last_intrabar_check_time")
            if start_time is None:
                start_time = position["entry_signal_time"]
            window = self._get_intrabar_window(symbol, start_time, bar_timestamp)
            intrabar_exit = self._check_intrabar_exit(symbol, position, window)
            if intrabar_exit is None:
                position["last_intrabar_check_time"] = bar_timestamp
                continue

            atr_value = self._value_at(bar_timestamp, symbol, "Signal_ATR")
            self._close_position(
                symbol,
                intrabar_exit["raw_exit_price"],
                atr_value,
                intrabar_exit["timestamp"],
                intrabar_exit["exit_reason"],
                intrabar_exit["timestamp"],
            )
            exited_symbols.add(symbol)
        return exited_symbols

    def _close_position(
        self,
        symbol,
        raw_exit_price,
        atr_value,
        timestamp,
        exit_reason,
        exit_signal_time,
    ):
        position = self.positions.pop(symbol)
        slippage = self._calculate_slippage_amount(raw_exit_price, atr_value)
        exit_price = max(float(raw_exit_price) - slippage, 0.0)
        proceeds = exit_price * position["shares"]
        exit_commission = proceeds * self.komisyon_oran
        self.current_cash += proceeds - exit_commission

        gross_pnl = proceeds - position["entry_notional"]
        net_pnl = (
            gross_pnl - position["entry_commission"] - exit_commission
        )
        initial_risk = position["initial_risk"]
        r_multiple = net_pnl / initial_risk if initial_risk > 0 else np.nan
        risk_budget = position["portfolio_equity_at_entry"] * self.risk_per_trade
        risk_budget_used = initial_risk / risk_budget if risk_budget > 0 else np.nan
        mfe_r = position["mfe"] / initial_risk if initial_risk > 0 else np.nan
        mae_r = position["mae"] / initial_risk if initial_risk > 0 else np.nan
        self.trade_log.append(
            {
                "Symbol": symbol,
                "Execution Mode": self.execution_mode,
                "Entry Time": position["entry_time"],
                "Exit Time": timestamp,
                "Exit Reason": exit_reason,
                "Entry Signal Time": position["entry_signal_time"],
                "Entry Execution Time": position["entry_time"],
                "Entry Signal Close": position["entry_signal_close"],
                "Raw Entry Open": position["raw_entry_open"],
                "Entry Price": position["entry_price"],
                "Entry Slippage": position["entry_slippage"],
                "Entry Commission": position["entry_commission"],
                "Entry Notional": position["entry_notional"],
                "Exit Signal Time": exit_signal_time,
                "Exit Execution Time": timestamp,
                "Raw Exit Price": raw_exit_price,
                "Exit Price": exit_price,
                "Exit Slippage": slippage,
                "Exit Commission": exit_commission,
                "Shares": position["shares"],
                "Gross PnL": gross_pnl,
                "Initial Risk": position["initial_risk"],
                "Net PnL": net_pnl,
                "Initial Risk Per Share": position["initial_risk_per_share"],
                "R Multiple": r_multiple,
                "Bars Held": position["bars_held"],
                "Portfolio Equity At Entry": position["portfolio_equity_at_entry"],
                "Risk Budget Used": risk_budget_used,
                "MFE": position["mfe"],
                "MAE": position["mae"],
                "MFE_R": mfe_r,
                "MAE_R": mae_r,
                "Ranking Score": position["ranking_score"],
                "Hurst": position["hurst"],
                "Momentum Factor": position["momentum"],
                **position.get("entry_context", {}),
            }
        )

    def _open_position(self, candidate, timestamp, portfolio_equity):
        symbol = candidate["symbol"]
        raw_entry = candidate["open"]
        signal_stop = candidate["stop"]
        atr_value = candidate["atr"]
        target_distance = self._finite_or_nan(candidate.get("target_distance"))
        slippage = self._calculate_slippage_amount(raw_entry, atr_value)
        entry_price = raw_entry + slippage
        risk_per_share = entry_price - signal_stop
        if self.target_enabled and pd.isna(target_distance):
            self._reject_candidate("Rejected Missing Target Distance")
            return False
        if (
            not np.isfinite(entry_price)
            or not np.isfinite(signal_stop)
            or not np.isfinite(float(atr_value))
            or risk_per_share <= 0
        ):
            self._reject_candidate("Rejected Invalid Risk Per Share")
            return False

        risk_budget = portfolio_equity * self.risk_per_trade
        risk_sized_shares = risk_budget / risk_per_share
        max_position_notional = portfolio_equity / self.max_open_positions
        allocated_cash = min(self.current_cash, max_position_notional)
        affordable_shares = allocated_cash / (
            entry_price * (1 + self.komisyon_oran)
        )
        shares = min(risk_sized_shares, affordable_shares)
        if not np.isfinite(shares) or shares <= 0:
            self._reject_candidate("Rejected Zero Shares")
            return False

        entry_notional = entry_price * shares
        entry_commission = entry_notional * self.komisyon_oran
        total_cost = entry_notional + entry_commission
        if total_cost > self.current_cash + 1e-9:
            self._reject_candidate("Rejected Insufficient Cash")
            return False

        self.current_cash -= total_cost
        self.positions[symbol] = {
            "entry_time": timestamp,
            "entry_signal_time": candidate["signal_time"],
            "entry_signal_close": candidate["signal_close"],
            "raw_entry_open": raw_entry,
            "entry_price": entry_price,
            "entry_slippage": slippage,
            "take_profit_target": (
                entry_price + target_distance if self.target_enabled else np.nan
            ),
            "last_intrabar_check_time": candidate["signal_time"],
            "entry_notional": entry_notional,
            "entry_commission": entry_commission,
            "shares": shares,
            "stop": signal_stop,
            "bars_held": 0,
            "initial_risk_per_share": risk_per_share,
            "initial_risk": risk_per_share * shares,
            "portfolio_equity_at_entry": portfolio_equity,
            "mfe": 0.0,
            "mae": 0.0,
            "ranking_score": candidate["ranking_score"],
            "hurst": candidate["hurst"],
            "momentum": candidate["momentum"],
            "entry_context": candidate.get("entry_context", {}),
        }
        return True

    def run_backtest(self):
        if self.fast_backtest:
            return self._run_backtest_fast()
        return self._run_backtest_pandas()

    def _reset_backtest_state(self):
        self.current_cash = self.initial_cash
        self.positions = {}
        self.trade_log = []
        self.equity_curve = []
        self.rejected_signals = 0
        self.macro_rejected_signals = 0
        self.rejection_diagnostics = self._empty_rejection_diagnostics()
        self.max_open_observed = 0
        self.max_heat_observed = 0.0

    def _run_backtest_pandas(self):
        if self.market_data.empty:
            return {}

        loop_start = time.perf_counter()
        self._reset_backtest_state()
        last_prices = {}

        symbols = sorted(self.asset_data)
        for timestamp in self.market_data.index:
            in_evaluation_window = self._in_evaluation_window(timestamp)
            for symbol in symbols:
                close_price = self._value_at(timestamp, symbol, "Close")
                if pd.notna(close_price):
                    last_prices[symbol] = float(close_price)

            exited_symbols = set()
            for symbol in list(self.positions):
                exit_order = self._value_at(timestamp, symbol, "EXIT_ORDER")
                open_price = self._value_at(timestamp, symbol, "Open")
                signal_close = self._value_at(timestamp, symbol, "Signal_Close")
                signal_time = self._value_at(timestamp, symbol, "Signal_Time")
                signal_sma20_reversion = self._value_at(
                    timestamp,
                    symbol,
                    "Signal_SMA20_Reversion_Exit",
                )
                position = self.positions[symbol]
                take_profit_hit = (
                    self.target_enabled
                    and pd.notna(signal_close)
                    and float(signal_close) >= position["take_profit_target"]
                )
                sma20_reversion_hit = (
                    self.sma_enabled
                    and (
                        (pd.notna(exit_order) and bool(exit_order))
                        or (
                            pd.notna(signal_sma20_reversion)
                            and bool(signal_sma20_reversion)
                        )
                    )
                )
                disaster_stop_hit = (
                    pd.notna(signal_close)
                    and float(signal_close) < position["stop"]
                )
                time_stop_hit = position["bars_held"] > self.time_stop_bars
                if (
                    (
                        disaster_stop_hit
                        or take_profit_hit
                        or sma20_reversion_hit
                        or time_stop_hit
                    )
                    and pd.notna(open_price)
                ):
                    if disaster_stop_hit:
                        exit_reason = "disaster_stop"
                    elif take_profit_hit:
                        exit_reason = "take_profit"
                    elif sma20_reversion_hit:
                        exit_reason = "sma20_reversion"
                    else:
                        exit_reason = "time_stop"
                    atr_value = self._value_at(timestamp, symbol, "Signal_ATR")
                    self._close_position(
                        symbol,
                        float(open_price),
                        atr_value,
                        timestamp,
                        exit_reason,
                        signal_time,
                    )
                    exited_symbols.add(symbol)

            current_risk_off = self._is_risk_off(timestamp)
            if self.risk_off_exit_mode == "close_positions" and current_risk_off:
                for symbol in list(self.positions):
                    open_price = self._value_at(timestamp, symbol, "Open")
                    if pd.isna(open_price):
                        continue
                    atr_value = self._value_at(timestamp, symbol, "Signal_ATR")
                    self._close_position(
                        symbol,
                        float(open_price),
                        atr_value,
                        timestamp,
                        "Risk_Off_Exit",
                        timestamp,
                    )
                    exited_symbols.add(symbol)

            available_slots = self.max_open_positions - len(self.positions)
            candidates = []
            if in_evaluation_window:
                for symbol in symbols:
                    entry_order = self._value_at(timestamp, symbol, "ENTRY_ORDER")
                    if pd.notna(entry_order) and bool(entry_order):
                        if symbol in self.positions:
                            self._reject_candidate("Rejected Already In Position")
                            continue
                        if symbol in exited_symbols:
                            self._reject_candidate("Rejected Same Bar Exit")
                            continue
                    else:
                        continue

                    open_price = self._value_at(timestamp, symbol, "Open")
                    signal_time = self._value_at(timestamp, symbol, "Signal_Time")
                    signal_close = self._value_at(timestamp, symbol, "Signal_Close")
                    hurst = self._value_at(timestamp, symbol, "Signal_Hurst")
                    momentum = self._value_at(
                        timestamp,
                        symbol,
                        "Signal_Momentum",
                    )
                    stop = self._value_at(timestamp, symbol, "Signal_Stop")
                    atr_value = self._value_at(timestamp, symbol, "Signal_ATR")
                    target_distance = self._value_at(
                        timestamp,
                        symbol,
                        "Signal_Take_Profit_Distance",
                    )
                    context = self._trade_context_at(timestamp, symbol)
                    candidate = self._candidate_from_signal_values(
                        symbol,
                        entry_order,
                        open_price,
                        signal_time,
                        signal_close,
                        hurst,
                        momentum,
                        stop,
                        atr_value,
                        target_distance,
                        context=context,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

            if current_risk_off:
                self.macro_rejected_signals += len(candidates)
                self._reject_candidate("Rejected Risk Off", len(candidates))
                candidates = []

            if self.ranking_mode == "none":
                candidates.sort(key=lambda item: item["symbol"])
            else:
                candidates.sort(
                    key=lambda item: item["ranking_score"],
                    reverse=True,
                )
            if len(candidates) > available_slots:
                self.rejected_signals += len(candidates) - max(available_slots, 0)
                self._reject_candidate(
                    "Rejected Max Positions",
                    len(candidates) - max(available_slots, 0),
                )

            portfolio_equity = self._portfolio_equity(last_prices)
            for candidate in candidates[:max(available_slots, 0)]:
                self._open_position(candidate, timestamp, portfolio_equity)

            self._process_intrabar_exits(timestamp)

            if self.execution_mode == "bar_close":
                for symbol in list(self.positions):
                    self._update_trade_excursions(symbol, timestamp)

            for position in self.positions.values():
                position["bars_held"] += 1

            self.max_open_observed = max(
                self.max_open_observed,
                len(self.positions),
            )
            current_equity = self._portfolio_equity(last_prices)
            current_heat = (
                sum(position["initial_risk"] for position in self.positions.values())
                / current_equity
                if current_equity > 0
                else 0.0
            )
            self.max_heat_observed = max(self.max_heat_observed, current_heat)
            if in_evaluation_window:
                self.equity_curve.append((timestamp, current_equity))

        final_timestamp = self.market_data.index[-1]
        for symbol in list(self.positions):
            raw_exit_price = last_prices.get(symbol)
            if raw_exit_price is None:
                continue
            atr_value = self._value_at(
                final_timestamp,
                symbol,
                "SuperTrend_ATR_22",
            )
            self._close_position(
                symbol,
                raw_exit_price,
                atr_value,
                final_timestamp,
                "forced_final_exit",
                final_timestamp,
            )

        if self.equity_curve:
            self.equity_curve[-1] = (final_timestamp, self.current_cash)
        self.portfolio_loop_seconds = time.perf_counter() - loop_start
        return self.get_metrics()

    def _run_backtest_fast(self):
        if self.market_data.empty:
            return {}

        loop_start = time.perf_counter()
        self._reset_backtest_state()
        last_prices = {}
        symbols = sorted(self.asset_data)

        for index, timestamp in enumerate(self.timestamps):
            in_evaluation_window = self._in_evaluation_window(timestamp)
            for symbol in symbols:
                values = self.fast_arrays[symbol]
                close_price = values["Close"][index]
                if pd.notna(close_price):
                    last_prices[symbol] = float(close_price)

            exited_symbols = set()
            for symbol in list(self.positions):
                values = self.fast_arrays[symbol]
                exit_order = values["EXIT_ORDER"][index]
                open_price = values["Open"][index]
                signal_close = values["Signal_Close"][index]
                signal_time = values["Signal_Time"][index]
                signal_sma20_reversion = values[
                    "Signal_SMA20_Reversion_Exit"
                ][index]
                position = self.positions[symbol]
                take_profit_hit = (
                    self.target_enabled
                    and pd.notna(signal_close)
                    and float(signal_close) >= position["take_profit_target"]
                )
                sma20_reversion_hit = (
                    self.sma_enabled
                    and (
                        (pd.notna(exit_order) and bool(exit_order))
                        or (
                            pd.notna(signal_sma20_reversion)
                            and bool(signal_sma20_reversion)
                        )
                    )
                )
                disaster_stop_hit = (
                    pd.notna(signal_close)
                    and float(signal_close) < position["stop"]
                )
                time_stop_hit = position["bars_held"] > self.time_stop_bars
                if (
                    (
                        disaster_stop_hit
                        or take_profit_hit
                        or sma20_reversion_hit
                        or time_stop_hit
                    )
                    and pd.notna(open_price)
                ):
                    if disaster_stop_hit:
                        exit_reason = "disaster_stop"
                    elif take_profit_hit:
                        exit_reason = "take_profit"
                    elif sma20_reversion_hit:
                        exit_reason = "sma20_reversion"
                    else:
                        exit_reason = "time_stop"
                    atr_value = values["Signal_ATR"][index]
                    self._close_position(
                        symbol,
                        float(open_price),
                        atr_value,
                        timestamp,
                        exit_reason,
                        signal_time,
                    )
                    exited_symbols.add(symbol)

            current_risk_off = self._is_risk_off_fast(index)
            if self.risk_off_exit_mode == "close_positions" and current_risk_off:
                for symbol in list(self.positions):
                    values = self.fast_arrays[symbol]
                    open_price = values["Open"][index]
                    if pd.isna(open_price):
                        continue
                    atr_value = values["Signal_ATR"][index]
                    self._close_position(
                        symbol,
                        float(open_price),
                        atr_value,
                        timestamp,
                        "Risk_Off_Exit",
                        timestamp,
                    )
                    exited_symbols.add(symbol)

            available_slots = self.max_open_positions - len(self.positions)
            candidates = []
            if in_evaluation_window:
                for symbol in symbols:
                    values = self.fast_arrays[symbol]
                    entry_order = values["ENTRY_ORDER"][index]
                    if pd.notna(entry_order) and bool(entry_order):
                        if symbol in self.positions:
                            self._reject_candidate("Rejected Already In Position")
                            continue
                        if symbol in exited_symbols:
                            self._reject_candidate("Rejected Same Bar Exit")
                            continue
                    else:
                        continue

                    open_price = values["Open"][index]
                    signal_time = values["Signal_Time"][index]
                    signal_close = values["Signal_Close"][index]
                    hurst = values["Signal_Hurst"][index]
                    momentum = values["Signal_Momentum"][index]
                    stop = values["Signal_Stop"][index]
                    atr_value = values["Signal_ATR"][index]
                    target_distance = values["Signal_Take_Profit_Distance"][index]
                    context = self._trade_context_from_fast_values(values, index)
                    candidate = self._candidate_from_signal_values(
                        symbol,
                        entry_order,
                        open_price,
                        signal_time,
                        signal_close,
                        hurst,
                        momentum,
                        stop,
                        atr_value,
                        target_distance,
                        context=context,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

            if current_risk_off:
                self.macro_rejected_signals += len(candidates)
                self._reject_candidate("Rejected Risk Off", len(candidates))
                candidates = []

            if self.ranking_mode == "none":
                candidates.sort(key=lambda item: item["symbol"])
            else:
                candidates.sort(
                    key=lambda item: item["ranking_score"],
                    reverse=True,
                )
            if len(candidates) > available_slots:
                self.rejected_signals += len(candidates) - max(available_slots, 0)
                self._reject_candidate(
                    "Rejected Max Positions",
                    len(candidates) - max(available_slots, 0),
                )

            portfolio_equity = self._portfolio_equity(last_prices)
            for candidate in candidates[:max(available_slots, 0)]:
                self._open_position(candidate, timestamp, portfolio_equity)

            self._process_intrabar_exits(timestamp)

            if self.execution_mode == "bar_close":
                for symbol in list(self.positions):
                    self._update_trade_excursions_fast(symbol, index)

            for position in self.positions.values():
                position["bars_held"] += 1

            self.max_open_observed = max(
                self.max_open_observed,
                len(self.positions),
            )
            current_equity = self._portfolio_equity(last_prices)
            current_heat = (
                sum(position["initial_risk"] for position in self.positions.values())
                / current_equity
                if current_equity > 0
                else 0.0
            )
            self.max_heat_observed = max(self.max_heat_observed, current_heat)
            if in_evaluation_window:
                self.equity_curve.append((timestamp, current_equity))

        final_timestamp = self.timestamps[-1]
        final_index = len(self.timestamps) - 1
        for symbol in list(self.positions):
            raw_exit_price = last_prices.get(symbol)
            if raw_exit_price is None:
                continue
            atr_value = self.fast_arrays[symbol]["SuperTrend_ATR_22"][final_index]
            self._close_position(
                symbol,
                raw_exit_price,
                atr_value,
                final_timestamp,
                "forced_final_exit",
                final_timestamp,
            )

        if self.equity_curve:
            self.equity_curve[-1] = (final_timestamp, self.current_cash)
        self.portfolio_loop_seconds = time.perf_counter() - loop_start
        return self.get_metrics()

    def get_metrics(self):
        if not self.equity_curve:
            return {}

        equity = pd.Series(
            [value for _, value in self.equity_curve],
            index=[timestamp for timestamp, _ in self.equity_curve],
            dtype=float,
        )
        total_return = (equity.iloc[-1] / self.initial_cash - 1) * 100
        max_drawdown = ((equity - equity.cummax()) / equity.cummax()).min() * 100
        trades = pd.DataFrame(self.trade_log)
        pnl = (
            pd.to_numeric(trades["Net PnL"], errors="coerce").dropna()
            if not trades.empty and "Net PnL" in trades
            else pd.Series(dtype=float)
        )
        winners = pnl[pnl > 0]
        losers = pnl[pnl < 0]
        trade_count = len(pnl)
        win_rate = len(winners) / trade_count * 100 if trade_count else 0.0
        profit_factor = (
            winners.sum() / abs(losers.sum())
            if len(losers) and abs(losers.sum()) > 0
            else float("inf") if trade_count else 0.0
        )
        r_multiples = (
            pd.to_numeric(trades["R Multiple"], errors="coerce").dropna()
            if not trades.empty and "R Multiple" in trades
            else pd.Series(dtype=float)
        )
        exit_reasons = (
            trades["Exit Reason"]
            if not trades.empty and "Exit Reason" in trades
            else pd.Series(dtype=object)
        )
        exit_reason_counts = exit_reasons.value_counts()
        winning_r = r_multiples[r_multiples > 0]
        losing_r = r_multiples[r_multiples < 0]
        avg_r = r_multiples.mean() if len(r_multiples) else 0.0
        median_r = r_multiples.median() if len(r_multiples) else 0.0
        best_r = r_multiples.max() if len(r_multiples) else 0.0
        worst_r = r_multiples.min() if len(r_multiples) else 0.0
        avg_winner_r = winning_r.mean() if len(winning_r) else 0.0
        avg_loser_r = losing_r.mean() if len(losing_r) else 0.0
        expectancy_r = avg_r
        max_consecutive_losses = 0
        max_consecutive_wins = 0
        current_losses = 0
        current_wins = 0
        for value in pnl:
            if value < 0:
                current_losses += 1
                current_wins = 0
            elif value > 0:
                current_wins += 1
                current_losses = 0
            else:
                current_losses = 0
                current_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
            max_consecutive_wins = max(max_consecutive_wins, current_wins)

        daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
        volatility = daily_returns.std()
        sharpe = (
            daily_returns.mean() / volatility * np.sqrt(252)
            if pd.notna(volatility) and volatility > 0
            else 0.0
        )
        days = max((equity.index[-1] - equity.index[0]).days, 1)
        years = days / 365.25
        cagr = (equity.iloc[-1] / self.initial_cash) ** (1 / years) - 1

        metrics = {
            "Baslangic Kasa": round(self.initial_cash, 2),
            "Bitis Kasa": round(self.current_cash, 2),
            "Ranking Mode": self.ranking_mode,
            "Execution Mode": self.execution_mode,
            "Net Getiri (%)": round(total_return, 2),
            "CAGR (%)": round(cagr * 100, 2),
            "Max Drawdown (%)": round(max_drawdown, 2),
            "Sharpe Ratio": round(sharpe, 2),
            "Islem Sayisi": trade_count,
            "Win Rate (%)": round(win_rate, 1),
            "Profit Factor": round(profit_factor, 2),
            "Avg R": round(avg_r, 4),
            "Median R": round(median_r, 4),
            "Best R": round(best_r, 4),
            "Worst R": round(worst_r, 4),
            "Avg Winner R": round(avg_winner_r, 4),
            "Avg Loser R": round(avg_loser_r, 4),
            "Expectancy R": round(expectancy_r, 4),
            "Max Consecutive Losses": max_consecutive_losses,
            "Max Consecutive Wins": max_consecutive_wins,
            "Stop Exit Count": int(exit_reason_counts.get("disaster_stop", 0)),
            "Take Profit Exit Count": int(exit_reason_counts.get("take_profit", 0)),
            "SMA20 Reversion Exit Count": int(
                exit_reason_counts.get("sma20_reversion", 0)
            ),
            "Time Stop Exit Count": int(exit_reason_counts.get("time_stop", 0)),
            "Risk Off Exit Count": int(exit_reason_counts.get("Risk_Off_Exit", 0)),
            "Forced Final Exit Count": int(
                exit_reason_counts.get("forced_final_exit", 0)
            ),
            "Max Acik Pozisyon": self.max_open_observed,
            "Max Portfolio Heat (%)": round(self.max_heat_observed * 100, 2),
            "Reddedilen Sinyal": self.rejected_signals,
            "Makro Rejimde Reddedilen Sinyal": self.macro_rejected_signals,
        }
        metrics.update(self.rejection_diagnostics)
        return metrics




__all__ = [
    "PORTFOLIO_REQUIRED_COLUMNS",
    "PORTFOLIO_CORE_REQUIRED_COLUMNS",
    "PORTFOLIO_CONTEXT_SOURCE_COLUMNS",
    "PORTFOLIO_CONTEXT_SIGNAL_COLUMNS",
    "TRADE_CONTEXT_REPORT_COLUMNS",
    "PORTFOLIO_REJECTION_COLUMNS",
    "SYMBOL_ATTRIBUTION_COLUMNS",
    "CACHE_REQUIRED_COLUMNS",
    "_normalize_ranking_mode",
    "_normalize_execution_mode",
    "_normalize_exit_mode",
    "_normalize_risk_off_exit_mode",
    "_align_timestamp_to_index",
    "PortfolioManager",
]
