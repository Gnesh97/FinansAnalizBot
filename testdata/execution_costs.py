from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

import pandas as pd


PREVIEW_COLUMNS = [
    "strategy_name",
    "symbol",
    "action",
    "fill_timestamp",
    "fill_price",
    "shares",
    "gross_notional",
    "commission_cost",
    "slippage_cost",
    "market_impact_cost",
    "total_cost",
    "adjusted_fill_price",
    "default_zero_cost",
]


@dataclass(frozen=True)
class CommissionModel:
    commission_per_order: float = 0.0
    commission_bps: float = 0.0
    min_commission: float = 0.0


@dataclass(frozen=True)
class SlippageModel:
    slippage_bps: float = 0.0
    market_impact_bps: float = 0.0


@dataclass(frozen=True)
class ExecutionCostConfig:
    commission: CommissionModel = field(default_factory=CommissionModel)
    slippage: SlippageModel = field(default_factory=SlippageModel)

    @property
    def is_zero_cost(self) -> bool:
        return (
            float(self.commission.commission_per_order) == 0.0
            and float(self.commission.commission_bps) == 0.0
            and float(self.commission.min_commission) == 0.0
            and float(self.slippage.slippage_bps) == 0.0
            and float(self.slippage.market_impact_bps) == 0.0
        )


@dataclass(frozen=True)
class ExecutionCostResult:
    gross_notional: float
    commission_cost: float
    slippage_cost: float
    market_impact_cost: float
    total_cost: float
    adjusted_fill_price: float
    default_zero_cost: bool


def _number(value, default=0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float(default)


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def calculate_execution_cost(
    action: str,
    fill_price: float,
    shares: float,
    config: ExecutionCostConfig | None = None,
) -> ExecutionCostResult:
    cfg = config or ExecutionCostConfig()
    side = str(action).strip().upper()
    price = max(_number(fill_price), 0.0)
    share_count = abs(_number(shares))
    gross_notional = price * share_count
    commission_bps_cost = gross_notional * max(float(cfg.commission.commission_bps), 0.0) / 10000.0
    commission_before_min = max(float(cfg.commission.commission_per_order), 0.0) + commission_bps_cost
    commission_cost = max(commission_before_min, max(float(cfg.commission.min_commission), 0.0))
    slippage_cost = gross_notional * max(float(cfg.slippage.slippage_bps), 0.0) / 10000.0
    market_impact_cost = gross_notional * max(float(cfg.slippage.market_impact_bps), 0.0) / 10000.0
    price_bps = (max(float(cfg.slippage.slippage_bps), 0.0) + max(float(cfg.slippage.market_impact_bps), 0.0)) / 10000.0
    if side == "SELL":
        adjusted = price * (1.0 - price_bps)
    else:
        adjusted = price * (1.0 + price_bps)
    total = commission_cost + slippage_cost + market_impact_cost
    return ExecutionCostResult(
        gross_notional=float(gross_notional),
        commission_cost=float(commission_cost),
        slippage_cost=float(slippage_cost),
        market_impact_cost=float(market_impact_cost),
        total_cost=float(total),
        adjusted_fill_price=float(adjusted),
        default_zero_cost=cfg.is_zero_cost,
    )


def apply_execution_cost_to_trade(
    trade: dict[str, object] | pd.Series,
    config: ExecutionCostConfig | None = None,
) -> dict[str, object]:
    row = trade.to_dict() if isinstance(trade, pd.Series) else dict(trade)
    result = calculate_execution_cost(
        row.get("action", ""),
        row.get("assumed_fill_price", row.get("fill_price", 0.0)),
        row.get("shares", 0.0),
        config,
    )
    enriched = dict(row)
    enriched.update(asdict(result))
    return enriched


def _filled_trade_rows(trade_log: pd.DataFrame) -> pd.DataFrame:
    if trade_log.empty:
        return pd.DataFrame()
    status = trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper()
    return trade_log[status.eq("PAPER_FILLED")].copy()


def build_execution_cost_preview(
    trade_log: pd.DataFrame,
    config: ExecutionCostConfig | None = None,
) -> dict[str, object]:
    cfg = config or ExecutionCostConfig()
    rows = []
    for _, row in _filled_trade_rows(trade_log.copy() if trade_log is not None else pd.DataFrame()).iterrows():
        result = calculate_execution_cost(
            row.get("action", ""),
            row.get("assumed_fill_price", row.get("fill_price", 0.0)),
            row.get("shares", 0.0),
            cfg,
        )
        rows.append(
            {
                "strategy_name": _clean_text(row.get("strategy_name", "")),
                "symbol": _clean_text(row.get("symbol", "")).upper(),
                "action": _clean_text(row.get("action", "")).upper(),
                "fill_timestamp": _clean_text(row.get("date", row.get("fill_timestamp", ""))),
                "fill_price": _number(row.get("assumed_fill_price", row.get("fill_price", 0.0))),
                "shares": _number(row.get("shares", 0.0)),
                **asdict(result),
            }
        )
    preview = pd.DataFrame(rows, columns=PREVIEW_COLUMNS)
    summary = _summary(preview, cfg)
    return {"preview": preview, "summary": summary, "config": cfg}


def _summary(preview: pd.DataFrame, config: ExecutionCostConfig) -> dict[str, object]:
    return {
        "filled_trade_rows_checked": int(len(preview)),
        "total_commission_cost": float(pd.to_numeric(preview.get("commission_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
        "total_slippage_cost": float(pd.to_numeric(preview.get("slippage_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
        "total_market_impact_cost": float(pd.to_numeric(preview.get("market_impact_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
        "total_execution_cost": float(pd.to_numeric(preview.get("total_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
        "default_cost_config_is_zero": bool(config.is_zero_cost),
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return json.loads(frame.fillna("").to_json(orient="records"))


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_clean_text(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    return _markdown_table(pd.DataFrame([{"metric": key, "value": value} for key, value in values.items()]))


def write_execution_cost_preview(
    latest_dir: str | Path,
    config: ExecutionCostConfig | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_execution_cost_preview(_read_csv(latest / "paper_trade_log.csv"), config)
    preview = result["preview"]
    summary = result["summary"]
    csv_path = latest / "execution_cost_preview.csv"
    md_path = latest / "execution_cost_preview.md"
    json_path = latest / "execution_cost_preview.json"
    preview.to_csv(csv_path, index=False)
    payload = {
        "summary": summary,
        "config": {
            "commission": asdict(result["config"].commission),
            "slippage": asdict(result["config"].slippage),
        },
        "preview": _records(preview),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Execution Cost Preview",
        "",
        "## Summary",
        "",
        _metric_table(summary),
        "",
        "## Cost Config",
        "",
        _metric_table({**payload["config"]["commission"], **payload["config"]["slippage"]}),
        "",
        "## Preview Rows",
        "",
        _markdown_table(preview),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "summary": summary,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
