import json
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
        return self.submit_paper_order(order_intent, paper_submit_enabled=False, confirm_token=None)

    def submit_paper_order(self, order_intent, paper_submit_enabled=False, confirm_token=None):
        if not paper_submit_enabled:
            raise RuntimeError("Alpaca paper submit is disabled by default.")
        if confirm_token != "CONFIRM_PAPER_SUBMIT":
            raise ValueError("Invalid confirmation token.")
        if "api.alpaca.markets" in self.base_url and "paper-api" not in self.base_url:
            raise ValueError("Refusing Alpaca live connection.")
        if "paper-api" not in self.base_url:
            raise ValueError("Requires a paper-api base_url.")
        side = str(order_intent.get("side", "")).lower()
        if side != "buy":
            raise ValueError("Only BUY orders are allowed in long paper submit layer.")

        import json
        payload = self.preview_order(order_intent)
        url = self.base_url + "/v2/orders"
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


class AlpacaPaperOrderSubmitClient:
    def __init__(self, api_key="", secret_key="", base_url="https://paper-api.alpaca.markets", urlopen=None):
        self.base_url = str(base_url or "").rstrip("/")
        if "paper-api.alpaca.markets" not in self.base_url:
            raise ValueError("Alpaca paper submit requires paper-api.alpaca.markets.")
        self.api_key = api_key or ""
        self.secret_key = secret_key or ""
        self.urlopen = urlopen or request.urlopen
        self.methods_used = []

    def _request_json(self, method, endpoint, *, payload=None, query=None):
        url = self.base_url + endpoint
        if query:
            url += "?" + parse.urlencode(query)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        self.methods_used.append(method)
        with self.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    def post_order(self, payload):
        return self._request_json("POST", "/v2/orders", payload=payload)

    def read_order_by_client_order_id(self, client_order_id):
        return self._request_json(
            "GET",
            "/v2/orders:by_client_order_id",
            query={"client_order_id": client_order_id},
        )
