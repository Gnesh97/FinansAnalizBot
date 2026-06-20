import numpy as np
import pandas as pd

COMPACT_SCORE_COLUMNS = [
    "Return/DD Score",
    "R Quality Score",
    "Penalty Score",
    "Compact Score",
]


SURVIVORSHIP_BIAS_WARNING = (
    "Bu backtest survivorship-bias-free değildir. Eğer evren bugünkü mevcut "
    "hisselerden oluşuyorsa, geçmiş performans olduğundan iyi görünebilir. "
    "Canlıya alınmadan önce historical universe / delisted data ile "
    "doğrulanmalıdır."
)

DIVIDEND_ADJUSTMENT_WARNING = (
    "Fiyat datası adjusted değilse uzun dönem testlerde özellikle temettü "
    "veren hisselerde sonuçlar sapabilir. Adjusted OHLC data kullanımı önerilir."
)



def calculate_buyhold_metrics(close_series, start, end):
    close = pd.to_numeric(close_series, errors="coerce").dropna().sort_index()
    if close.empty:
        raise ValueError("Benchmark close serisi bos.")

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if close.index.tz is not None:
        if start.tzinfo is None:
            start = start.tz_localize(close.index.tz)
            end = end.tz_localize(close.index.tz)
        else:
            start = start.tz_convert(close.index.tz)
            end = end.tz_convert(close.index.tz)
    elif close.index.tz is None and start.tzinfo is not None:
        start = start.tz_convert(None)
        end = end.tz_convert(None)

    start_candidates = close.loc[:start]
    if start_candidates.empty:
        raise ValueError("Benchmark icin baslangic fiyatindan once veri yok.")
    start_price = float(start_candidates.iloc[-1])
    if not np.isfinite(start_price) or start_price <= 0:
        raise ValueError("Benchmark baslangic fiyati gecersiz.")

    path = close.loc[(close.index > start) & (close.index <= end)]
    path = pd.concat([pd.Series([start_price], index=[start]), path])
    path = path[~path.index.duplicated(keep="last")].sort_index().ffill().dropna()
    if len(path) < 2:
        raise ValueError("Benchmark icin test araliginda yeterli veri yok.")

    normalized = path / start_price
    total_return = (normalized.iloc[-1] - 1) * 100
    max_drawdown = ((normalized - normalized.cummax()) / normalized.cummax()).min() * 100
    days = max((normalized.index[-1] - normalized.index[0]).days, 1)
    years = days / 365.25
    cagr = (normalized.iloc[-1]) ** (1 / years) - 1
    return {
        "Return (%)": round(total_return, 2),
        "CAGR (%)": round(cagr * 100, 2),
        "MaxDD (%)": round(max_drawdown, 2),
    }



def calculate_equal_weight_benchmark(asset_data, start, end):
    # Survivorship bias: evren bugunun hisselerinden olusuyorsa bu buy-hold
    # benchmark gecmis performansi oldugundan iyi gosterebilir.
    normalized_series = []
    skipped_symbols = []
    for symbol, data in asset_data.items():
        if "Close" not in data:
            skipped_symbols.append(symbol)
            continue
        close = pd.to_numeric(data["Close"], errors="coerce").dropna().sort_index()
        start_candidates = close.loc[:start]
        if start_candidates.empty:
            skipped_symbols.append(symbol)
            continue
        start_price = float(start_candidates.iloc[-1])
        if not np.isfinite(start_price) or start_price <= 0:
            skipped_symbols.append(symbol)
            continue
        path = close.loc[(close.index > start) & (close.index <= end)]
        path = pd.concat([pd.Series([start_price], index=[start]), path])
        path = path[~path.index.duplicated(keep="last")].sort_index().ffill()
        normalized_series.append(path / start_price)

    if not normalized_series:
        raise ValueError("Equal-weight benchmark icin kullanilabilir close serisi yok.")
    normalized = pd.concat(normalized_series, axis=1).sort_index().ffill()
    normalized = normalized.loc[(normalized.index >= start) & (normalized.index <= end)]
    equal_weight_close = normalized.mean(axis=1).dropna()
    return calculate_buyhold_metrics(equal_weight_close, start, end)



def _prefixed_benchmark_metrics(prefix, metrics):
    return {
        f"{prefix} BuyHold Return (%)": metrics["Return (%)"],
        f"{prefix} BuyHold CAGR (%)": metrics["CAGR (%)"],
        f"{prefix} BuyHold MaxDD (%)": metrics["MaxDD (%)"],
    }



def _numeric_series(df, column):
    return pd.to_numeric(df[column], errors="coerce")


def _add_compact_scores(report):
    net_return = _numeric_series(report, "Net Getiri (%)").fillna(0.0)
    max_dd = _numeric_series(report, "Max Drawdown (%)").fillna(0.0)
    expectancy = _numeric_series(report, "Expectancy R").fillna(0.0)
    avg_winner = _numeric_series(report, "Avg Winner R").fillna(0.0)
    avg_loser = _numeric_series(report, "Avg Loser R").fillna(0.0)
    worst_r = _numeric_series(report, "Worst R").fillna(0.0)
    max_losses = _numeric_series(report, "Max Consecutive Losses").fillna(0.0)
    trade_count = _numeric_series(report, "Islem Sayisi").fillna(0.0)

    dd_abs = max_dd.abs()
    report["Return/DD Score"] = (net_return / dd_abs.replace(0, np.nan)).fillna(0.0)
    report["R Quality Score"] = expectancy + avg_winner + avg_loser
    report["Penalty Score"] = worst_r.abs() + max_losses * 0.1
    report["Compact Score"] = (
        expectancy
        - dd_abs / 100
        + trade_count.clip(upper=100) / 1000
        - worst_r.abs() / 10
    )
    for column in COMPACT_SCORE_COLUMNS:
        report[column] = report[column].replace([np.inf, -np.inf], np.nan)
    return report


def _write_compact_markdown(report, markdown_path, allowed_symbols, generated_at):
    # Report generation helper.
    allowed_symbols = allowed_symbols or []
    symbol_text = ", ".join(map(str, allowed_symbols)) if allowed_symbols else "All / file filter"
    ranked = report.sort_values("Compact Score", ascending=False, na_position="last")
    display_columns = [
        "Experiment Name",
        "Strategy Name",
        "Strategy Family",
        "Compact Score",
        "Expectancy R",
        "Net Getiri (%)",
        "Max Drawdown (%)",
        "Islem Sayisi",
        "Trade Count",
        "Profit Factor",
        "Worst R",
        "Avg Holding Bars",
        "Turnover Estimate",
        "Avg Positions Held",
        "Max Positions Held",
        "QQQ BuyHold Return (%)",
        "EqualWeight Universe Return (%)",
        "Strategy Excess Return vs QQQ (%)",
    ]
    display_columns = [column for column in display_columns if column in ranked]
    best = ranked.head(5)[display_columns]
    worst = ranked.tail(5)[display_columns]
    try:
        from .diagnostics import build_signal_diagnostics_table
    except ImportError:
        from diagnostics import build_signal_diagnostics_table
    signal_summary = build_signal_diagnostics_table(ranked)
    try:
        from .diagnostics import build_portfolio_rejection_table
    except ImportError:
        from diagnostics import build_portfolio_rejection_table
    rejection_summary = build_portfolio_rejection_table(ranked)
    def _markdown_table(df):
        try:
            return df.to_markdown(index=False)
        except Exception:
            return df.to_string(index=False)
    warnings = []
    if (_numeric_series(report, "Islem Sayisi") < 30).any():
        warnings.append("- Islem sayisi 30'dan az olan experimentler guvenilir degil.")
    if (_numeric_series(report, "Worst R") < -2).any():
        warnings.append("- Worst R < -2 olan experimentler riskli.")
    if (_numeric_series(report, "Profit Factor") < 1).any():
        warnings.append("- Profit Factor < 1 olan experimentler negatif edge olabilir.")
    if (_numeric_series(report, "Expectancy R") <= 0).any():
        warnings.append("- Expectancy R <= 0 olan experimentler zayif.")
    if not warnings:
        warnings.append("- Otomatik uyari kriterlerine takilan experiment yok.")
    warnings.extend(
        [
            f"- {SURVIVORSHIP_BIAS_WARNING}",
            f"- {DIVIDEND_ADJUSTMENT_WARNING}",
        ]
    )

    content = [
        "# Strategy Compact Report",
        "",
        f"- Run tarihi/saat: {generated_at}",
        f"- Kullanilan sembol sayisi: {len(allowed_symbols) if allowed_symbols else 'All'}",
        f"- Kullanilan semboller: {symbol_text}",
        f"- Toplam experiment sayisi: {len(report)}",
        "",
        "## En iyi 5 experiment",
        "",
        _markdown_table(best),
        "",
        "## En kotu 5 experiment",
        "",
        _markdown_table(worst),
        "",
        "## Signal diagnostics",
        "",
        _markdown_table(signal_summary),
        "",
        "## Portfolio rejection diagnostics",
        "",
        _markdown_table(rejection_summary),
        "",
        "## Uyarilar",
        "",
        *warnings,
        "",
        "## ChatGPT'ye gonderilecek dosya onerisi",
        "",
        "- strategy_compact_report.csv",
        "- strategy_compact_report.md",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")




__all__ = [
    "calculate_buyhold_metrics",
    "calculate_equal_weight_benchmark",
    "COMPACT_SCORE_COLUMNS",
    "SURVIVORSHIP_BIAS_WARNING",
    "DIVIDEND_ADJUSTMENT_WARNING",
    "_prefixed_benchmark_metrics",
    "_numeric_series",
    "_add_compact_scores",
    "_write_compact_markdown",
]
