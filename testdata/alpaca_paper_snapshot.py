from __future__ import annotations

import json
import socket
import urllib.error
from urllib import parse, request

try:
    from .broker_reconciliation import ReadOnlyBrokerSnapshot
except ImportError:
    from broker_reconciliation import ReadOnlyBrokerSnapshot


ALPACA_PAPER_SNAPSHOT_SOURCE = "ALPACA_PAPER_GET_ONLY"


def _value(row, name, default=""):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def alpaca_endpoint_mode(base_url: str) -> str:
    text = str(base_url or "").lower()
    if "paper-api.alpaca.markets" in text:
        return "paper"
    if "api.alpaca.markets" in text and "paper-api" not in text:
        return "live"
    return "unknown"


class AlpacaPaperSnapshotClient:
    def __init__(self, api_key="", secret_key="", base_url="https://paper-api.alpaca.markets", urlopen=None):
        self.api_key = api_key or ""
        self.secret_key = secret_key or ""
        self.base_url = str(base_url or "").rstrip("/")
        self.urlopen = urlopen or request.urlopen
        self.methods_used: list[str] = []

    def _get_json(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + parse.urlencode(query)
        req = request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Accept": "application/json",
            },
            method="GET",
        )
        self.methods_used.append("GET")
        with self.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def read_snapshot(self, *, broker_submit_enabled=False, live_trading_enabled=False) -> ReadOnlyBrokerSnapshot:
        mode = alpaca_endpoint_mode(self.base_url)
        metadata = {
            "alpaca_snapshot_status": "WARNING",
            "alpaca_snapshot_reason": "not_checked",
            "alpaca_account_read_success": False,
            "alpaca_positions_read_success": False,
            "alpaca_open_orders_read_success": False,
            "alpaca_endpoint_mode": mode,
            "alpaca_live_endpoint_blocked": False,
            "alpaca_failure_category": "",
            "alpaca_failure_endpoint": "",
            "alpaca_failure_detail": "",
            "alpaca_sdk_import_status": "NOT_REQUIRED",
            "alpaca_account_summary": {},
            "request_methods_used": "none",
            "post_request_used": False,
            "submit_order_called": False,
        }
        if live_trading_enabled:
            metadata.update({"alpaca_snapshot_status": "FAIL", "alpaca_snapshot_reason": "live_trading_enabled", "alpaca_failure_category": "LIVE_TRADING_ENABLED"})
            return self._snapshot([], [], metadata, live_endpoint=(mode == "live"), live_trading_enabled=True)
        if broker_submit_enabled:
            metadata.update({"alpaca_snapshot_status": "FAIL", "alpaca_snapshot_reason": "broker_submit_enabled", "alpaca_failure_category": "BROKER_SUBMIT_ENABLED"})
            return self._snapshot([], [], metadata, live_endpoint=(mode == "live"))
        if mode != "paper":
            metadata.update(
                {
                    "alpaca_snapshot_status": "FAIL",
                    "alpaca_snapshot_reason": "paper_endpoint_required",
                    "alpaca_failure_category": "LIVE_ENDPOINT_BLOCKED" if mode == "live" else "ENDPOINT_ERROR",
                    "alpaca_live_endpoint_blocked": mode == "live",
                }
            )
            return self._snapshot([], [], metadata, live_endpoint=(mode == "live"))
        if not self.api_key or not self.secret_key:
            metadata.update({"alpaca_snapshot_status": "NOT_AVAILABLE", "alpaca_snapshot_reason": "missing_credentials", "alpaca_failure_category": "MISSING_CREDENTIALS"})
            return self._snapshot([], [], metadata)

        account = self._safe_get("/v2/account")
        positions = self._safe_get("/v2/positions")
        orders = self._safe_get("/v2/orders", query={"status": "open", "limit": 100})
        first_error = next((item[2] for item in [account, positions, orders] if not item[0]), {})
        metadata.update(
            {
                "alpaca_account_read_success": account[0],
                "alpaca_positions_read_success": positions[0],
                "alpaca_open_orders_read_success": orders[0],
                "request_methods_used": ",".join(sorted(set(self.methods_used))) if self.methods_used else "none",
                "alpaca_failure_category": first_error.get("category", ""),
                "alpaca_failure_endpoint": first_error.get("endpoint", ""),
                "alpaca_failure_detail": first_error.get("detail", ""),
                "alpaca_account_summary": _account_summary(account[1]) if account[0] else {},
            }
        )
        all_ok = account[0] and positions[0] and orders[0]
        metadata["alpaca_snapshot_status"] = "PASS" if all_ok else "NOT_AVAILABLE"
        metadata["alpaca_snapshot_reason"] = "ok" if all_ok else (first_error.get("category") or "READ_FAILED")
        return self._snapshot(_normalize_positions(positions[1]), _normalize_orders(orders[1]), metadata)

    def read_assets(self, symbols) -> dict[str, object]:
        assets = {}
        errors = []
        for symbol in sorted({str(item).strip().upper() for item in symbols if str(item).strip()}):
            ok, payload, error = self._safe_get(f"/v2/assets/{parse.quote(symbol)}")
            if ok:
                assets[symbol] = _asset_summary(payload)
            else:
                errors.append(error)
        first_error = errors[0] if errors else {}
        return {
            "assets": assets,
            "metadata": {
                "alpaca_assets_read_success": not errors,
                "alpaca_assets_failure_category": first_error.get("category", ""),
                "alpaca_assets_failure_endpoint": first_error.get("endpoint", ""),
                "alpaca_assets_failure_detail": first_error.get("detail", ""),
                "request_methods_used": ",".join(sorted(set(self.methods_used))) if self.methods_used else "none",
                "submit_order_called": False,
                "post_request_used": False,
            },
        }

    def _safe_get(self, path, query=None):
        try:
            payload = self._get_json(path, query=query)
        except urllib.error.HTTPError as exc:
            return False, [], _error_info(path, "AUTH_FAILED" if exc.code in {401, 403} else "HTTP_ERROR", f"http_status_{exc.code}")
        except TimeoutError:
            return False, [], _error_info(path, "TIMEOUT", "timeout")
        except socket.timeout:
            return False, [], _error_info(path, "TIMEOUT", "timeout")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", "")
            category = "TIMEOUT" if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower() else "NETWORK_ERROR"
            return False, [], _error_info(path, category, _safe_detail(reason))
        except json.JSONDecodeError:
            return False, [], _error_info(path, "PARSE_ERROR", "invalid_json")
        except Exception:
            return False, [], _error_info(path, "NETWORK_ERROR", "request_failed")
        return True, payload, {}

    def _snapshot(self, positions, orders, metadata, *, live_endpoint=False, live_trading_enabled=False):
        return ReadOnlyBrokerSnapshot(
            positions=positions,
            open_orders=orders,
            fills=None,
            source=ALPACA_PAPER_SNAPSHOT_SOURCE,
            live_endpoint=live_endpoint,
            live_trading_enabled=live_trading_enabled,
            metadata=metadata,
        )


def _normalize_positions(rows) -> list[dict[str, object]]:
    output = []
    for row in rows if isinstance(rows, list) else []:
        output.append(
            {
                "symbol": _value(row, "symbol", ""),
                "qty": _value(row, "qty", 0),
                "avg_entry_price": _value(row, "avg_entry_price", _value(row, "avg_price", 0)),
                "market_value": _value(row, "market_value", ""),
            }
        )
    return output


def _normalize_orders(rows) -> list[dict[str, object]]:
    output = []
    for row in rows if isinstance(rows, list) else []:
        output.append(
            {
                "broker_order_id": _value(row, "id", _value(row, "broker_order_id", "")),
                "client_order_id": _value(row, "client_order_id", ""),
                "symbol": _value(row, "symbol", ""),
                "side": _value(row, "side", ""),
                "qty": _value(row, "qty", ""),
                "notional": _value(row, "notional", ""),
                "status": _value(row, "status", ""),
                "submitted_at": _value(row, "submitted_at", ""),
            }
        )
    return output


def _safe_detail(value) -> str:
    text = str(value or "").strip().replace("\n", " ").replace("\r", " ")
    if not text:
        return ""
    lowered = text.lower()
    if "key" in lowered or "secret" in lowered or "token" in lowered:
        return "redacted"
    return text[:80]


def _error_info(endpoint: str, category: str, detail: str = "") -> dict[str, str]:
    return {"endpoint": endpoint, "category": category, "detail": _safe_detail(detail)}


def _account_summary(payload) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    allowed = [
        "status",
        "currency",
        "buying_power",
        "cash",
        "portfolio_value",
        "account_blocked",
        "trading_blocked",
        "transfers_blocked",
    ]
    return {key: payload.get(key, "") for key in allowed if key in payload}


def _asset_summary(payload) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    allowed = [
        "id",
        "class",
        "exchange",
        "symbol",
        "name",
        "status",
        "tradable",
        "marginable",
        "shortable",
        "easy_to_borrow",
        "fractionable",
        "maintenance_margin_requirement",
    ]
    return {key: payload.get(key, "") for key in allowed if key in payload}


def read_alpaca_paper_snapshot(api_key="", secret_key="", base_url="https://paper-api.alpaca.markets", **kwargs):
    return AlpacaPaperSnapshotClient(api_key=api_key, secret_key=secret_key, base_url=base_url).read_snapshot(**kwargs)


def read_alpaca_paper_assets(api_key="", secret_key="", base_url="https://paper-api.alpaca.markets", symbols=None):
    return AlpacaPaperSnapshotClient(api_key=api_key, secret_key=secret_key, base_url=base_url).read_assets(symbols or [])
