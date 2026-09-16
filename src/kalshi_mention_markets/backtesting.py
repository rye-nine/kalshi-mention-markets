import json
import logging
import random
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from .engineer_prompt import KALSHI_API_ENDPOINT

logger = logging.getLogger(__name__)

SEED = 42 # Fixed seed for reproducibility in random selection of historical prices

# just make sure the seed works

def _kalshi_get(path, params):
	request = Request(
		f"{KALSHI_API_ENDPOINT}{path}?{urlencode(params)}",
		headers={"Accept": "application/json"},
	)
	context = ssl.create_default_context(cafile=certifi.where())
	try:
		with urlopen(request, context=context) as response:
			return json.load(response)
	except (HTTPError, URLError, ValueError) as error:
		raise RuntimeError(f"Kalshi request failed for {path}: {error}") from error


def _parse_timestamp(value):
	if isinstance(value, (int, float)):
		return datetime.fromtimestamp(value, tz=timezone.utc)
	if isinstance(value, str):
		return datetime.fromisoformat(value.replace("Z", "+00:00"))
	raise ValueError(f"Unsupported Kalshi timestamp: {value!r}")


def _yes_close_dollars(candlestick):
	yes_bid = candlestick.get("yes_bid", {})
	price = yes_bid.get("close_dollars")
	if price is None:
		price = yes_bid.get("close")
	if price is None:
		price = candlestick.get("yes_price")
	if price is None:
		raise ValueError("Kalshi candlestick does not contain a YES close price.")
	price = float(price)
	return price if price <= 1 else price / 100


def random_historical_yes_price(market_ticker, seed=42) -> tuple[datetime, float]:
	"""Return one random historical ``(timestamp, YES price)`` for a resolved market.

	The timestamp is timezone-aware and in UTC. The YES price is returned in
	dollars, so a Kalshi price of 37 cents is returned as ``0.37``.
	"""
	if not isinstance(market_ticker, str) or not market_ticker.strip():
		raise ValueError("market_ticker must not be empty.")
	market_ticker = market_ticker.strip()

	market_payload = _kalshi_get(f"/markets/{market_ticker}", {})
	market = market_payload.get("market", {})
	series_ticker = market_ticker.split("-", 1)[0]
	try:
		start_ts = int(_parse_timestamp(market["open_time"]).timestamp())
		end_ts = int(_parse_timestamp(market["close_time"]).timestamp())
	except (KeyError, TypeError, ValueError) as error:
		raise RuntimeError(
			f"Kalshi market {market_ticker!r} has no usable open/close time."
		) from error
	if end_ts <= start_ts:
		raise RuntimeError(f"Kalshi market {market_ticker!r} has an invalid time range.")

	candles_payload = _kalshi_get(
		f"/series/{series_ticker}/markets/{market_ticker}/candlesticks",
		{"start_ts": start_ts, "end_ts": end_ts, "period_interval": 60},
	)
	candlesticks = candles_payload.get("candlesticks", [])
	if not candlesticks:
		raise LookupError(f"Kalshi market {market_ticker!r} has no historical candlesticks.")

	candlestick = random.Random(seed).choice(candlesticks)
	timestamp = _parse_timestamp(candlestick["end_period_ts"])
	price = _yes_close_dollars(candlestick)
	logger.info("Selected %s at %s with YES price %.4f", market_ticker, timestamp, price)
	return timestamp, price

