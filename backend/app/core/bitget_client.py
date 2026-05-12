import hashlib
import hmac
import time
import base64
import json
import requests
from typing import Any


class BitgetClient:
    BASE_URL = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign(self, timestamp: str, method: str, path: str, body: str) -> str:
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    def _request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        url = self.BASE_URL + path
        body = json.dumps(data) if data else ""
        # Bitget V2 requires the query string to be part of the signed path for GET requests.
        # Format: /path?key1=val1&key2=val2  (the "?" is included in the signed string).
        # requests encodes params independently, so we build the query string manually here
        # only for signing, while still letting requests handle the actual URL encoding.
        if method == "GET" and params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            sign_path = f"{path}?{qs}"
        else:
            sign_path = path
        headers = self._headers(method, sign_path, body)

        for attempt in range(3):
            try:
                if method == "GET":
                    resp = self.session.get(url, headers=headers, params=params, timeout=10)
                else:
                    resp = self.session.post(url, headers=headers, data=body, timeout=10)
                if not resp.ok:
                    raise BitgetAPIError(str(resp.status_code), resp.text[:300])
                result = resp.json()
                if result.get("code") not in ("00000", 0, "0"):
                    raise BitgetAPIError(result.get("code"), result.get("msg", ""))
                return result.get("data", result)
            except BitgetAPIError:
                raise
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(1.5 ** attempt)

    # ── Market data ──────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, granularity: str = "1h", limit: int = 50) -> list[dict]:
        data = self._request("GET", "/api/v2/spot/market/candles", params={
            "symbol": symbol,
            "granularity": granularity,
            "limit": str(limit),
        })
        # Returns list of [ts, open, high, low, close, baseVol, quoteVol]
        return [
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in (data if isinstance(data, list) else [])
        ]

    def get_ticker(self, symbol: str) -> dict:
        data = self._request("GET", "/api/v2/spot/market/tickers", params={"symbol": symbol})
        row = data[0] if isinstance(data, list) and data else data
        return {"price": float(row["lastPr"]), "symbol": row["symbol"]}

    # ── Account ───────────────────────────────────────────────────────────────

    def get_spot_balance(self, coin: str = "USDT") -> float:
        data = self._request("GET", "/api/v2/spot/account/assets", params={"coin": coin})
        for item in (data if isinstance(data, list) else []):
            if item.get("coin") == coin:
                return float(item.get("available", 0))
        return 0.0

    # ── Grid bot ──────────────────────────────────────────────────────────────

    def get_bot_detail(self, bot_id: str) -> dict:
        data = self._request("GET", "/api/v2/spot/bot/bot-detail", params={"botId": bot_id})
        return data if isinstance(data, dict) else (data[0] if data else {})

    def modify_grid_bot(
        self,
        bot_id: str,
        symbol: str,
        lower_price: float,
        upper_price: float,
        grid_num: int,
        invest_amount: float,
        grid_type: str = "geometric",
    ) -> dict:
        return self._request("POST", "/api/v2/spot/bot/modify-grid", data={
            "botId": bot_id,
            "symbol": symbol,
            "lowerPrice": str(lower_price),
            "upperPrice": str(upper_price),
            "gridNum": grid_num,
            "investAmount": str(invest_amount),
            "gridType": grid_type,
        })

    def get_bot_pnl(self, bot_id: str) -> dict:
        data = self._request("GET", "/api/v2/spot/bot/bot-detail", params={"botId": bot_id})
        row = data if isinstance(data, dict) else (data[0] if data else {})
        return {
            "total_pnl": float(row.get("totalProfit", 0)),
            "grid_profit": float(row.get("gridProfit", 0)),
            "floating_pnl": float(row.get("floatProfit", 0)),
            "invest_amount": float(row.get("investAmount", 0)),
            "grid_num": int(row.get("gridNum", 0)),
            "lower_price": float(row.get("lowerPrice", 0)),
            "upper_price": float(row.get("upperPrice", 0)),
            "grid_type": row.get("gridType", "geometric"),
            "status": row.get("status", ""),
        }


class BitgetAPIError(Exception):
    def __init__(self, code: str, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Bitget API error {code}: {msg}")
