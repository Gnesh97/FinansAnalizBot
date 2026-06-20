from urllib import parse, request

try:
    from .broker_interface import BrokerInterface
except ImportError:
    from broker_interface import BrokerInterface


class AlpacaPaperBroker(BrokerInterface):
    def __init__(self, api_key="", secret_key="", base_url="https://paper-api.alpaca.markets"):
        self.base_url = str(base_url).rstrip("/")
        if "paper-api" not in self.base_url:
            raise ValueError("AlpacaPaperBroker requires a paper-api base_url.")
        self.api_key = api_key
        self.secret_key = secret_key

    def _get_json(self, endpoint, query=None):
        url = self.base_url + endpoint
        if query:
            url += "?" + parse.urlencode(query)
        req = request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
            },
            method="GET",
        )
        with request.urlopen(req, timeout=20) as response:
            import json

            return json.loads(response.read().decode("utf-8"))

    def get_account(self):
        return self._get_json("/v2/account")

    def get_positions(self):
        return self._get_json("/v2/positions")

    def get_open_orders(self):
        return self._get_json("/v2/orders", query={"status": "open", "limit": 100})

    def preview_order(self, order_intent):
        side = str(order_intent.get("side", "")).lower()
        payload = {
            "symbol": str(order_intent.get("symbol", "")).upper(),
            "side": side,
            "type": str(order_intent.get("order_type", "market")).lower(),
            "time_in_force": str(order_intent.get("time_in_force", "day")).lower(),
        }
        notional = order_intent.get("notional", "")
        estimated_qty = order_intent.get("estimated_qty", "")
        if str(notional).strip() not in {"", "nan", "None", "<NA>"}:
            payload["notional"] = str(notional)
        elif str(estimated_qty).strip() not in {"", "nan", "None", "<NA>"}:
            payload["qty"] = str(estimated_qty)
        return payload

    def submit_order(self, order_intent):
        raise RuntimeError("Order submission disabled in current build.")
