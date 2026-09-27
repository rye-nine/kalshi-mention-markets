import json
import logging
import random
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import dataclass

import certifi

from .engineer_prompt import KALSHI_API_ENDPOINT
from .prompter import run_pipeline

logger = logging.getLogger(__name__)

SEED = 670 # Fixed seed for reproducibility in random selection of historical prices

# just make sure the seed works

# PRESETS:
MIN_EDGE = 0.03
SELL_AT_DISTANCE = 0.01

ALPHA = 0.7  # Weight for market price in MIXMCP calculation

@dataclass
class earnings_call:
	market_tickers: list[str]
	timestamp: datetime

def get_all_market_tickers(earnings_event_ticker) -> list[str]:
	if not isinstance(earnings_event_ticker, str) or not earnings_event_ticker.strip():
		raise ValueError("earnings_event_ticker must not be empty.")

	market_tickers = []
	cursor = None
	while True:
		params = {
			"event_ticker": earnings_event_ticker.strip(),
			"limit": 1000,
		}
		if cursor:
			params["cursor"] = cursor

		payload = _kalshi_get("/markets", params)
		for market in payload.get("markets", []):
			ticker = market.get("ticker")
			if isinstance(ticker, str):
				market_tickers.append(ticker)

		cursor = payload.get("cursor")
		if not cursor:
			break

	logger.info(
		"Found %d markets for earnings event %r.",
		len(market_tickers),
		earnings_event_ticker.strip(),
	)
	return market_tickers

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
	if isinstance(value, datetime):
		return value
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


def _historical_market_data(market_ticker):
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
	return market, candlesticks


def random_historical_yes_price(market_ticker, seed=SEED) -> tuple[datetime, float]:
	"""Return one random historical ``(timestamp, YES price)`` for a resolved market.

	The timestamp is timezone-aware and in UTC. The YES price is returned in
	dollars, so a Kalshi price of 37 cents is returned as ``0.37``.
	"""
	_, candlesticks = _historical_market_data(market_ticker)
	candlestick = random.Random(seed).choice(candlesticks)
	timestamp = _parse_timestamp(candlestick["end_period_ts"])
	price = _yes_close_dollars(candlestick)
	logger.info("Selected %s at %s with YES price %.4f", market_ticker, timestamp, price)
	return timestamp, price

def determine_kelly(market_price, probability, multiplier=1.0):
	# we already assume that an edge is present, so we don't need to check for that here
	if not (0 < market_price < 1):
		raise ValueError(f"market_price must be between 0 and 1, got {market_price}")
	if not (0 < probability < 1):
		raise ValueError(f"probability must be between 0 and 1, got {probability}")

	if probability > market_price:
		# Buy YES
		kelly_fraction = (probability - market_price) / (1 - market_price)
	else:
		# Buy NO
		kelly_fraction = (market_price - probability) / market_price
	return kelly_fraction * multiplier

def simulate_trade(market_ticker, available_capital, timestamp=None):
	market, candlesticks = _historical_market_data(market_ticker)
	entry_timestamp = _parse_timestamp(
		market["open_time"] if timestamp is None else timestamp
	)
	if entry_timestamp.tzinfo is None:
		entry_timestamp = entry_timestamp.replace(tzinfo=timezone.utc)

	sorted_candlesticks = sorted(
		candlesticks,
		key=lambda value: _parse_timestamp(value["end_period_ts"]),
	)
	entry_candlestick = next(
		(
			candlestick
			for candlestick in sorted_candlesticks
			if _parse_timestamp(candlestick["end_period_ts"]) >= entry_timestamp
		),
		None,
	)
	if entry_candlestick is None:
		raise ValueError(
			f"No historical market price is available at or after timestamp {entry_timestamp}."
		)
	entry_price = _yes_close_dollars(entry_candlestick)
	if not 0.50 <= entry_price <= 0.70:
		logger.info("No trade at %s: price %.4f is outside the 0.50-0.70 range", entry_timestamp, entry_price)
		logger.info("Trade summary for %s: trade_count=0 details=[]", market_ticker)
		return 0.0, 0, f"Trade was not made. Market price was {entry_price} and so our probability was not calculated."
	probability = run_pipeline(
		market_ticker=market_ticker,
		alpha=ALPHA,
		market_price=entry_price,
		date=entry_timestamp.date().isoformat(),
	)
	if not isinstance(probability, (int, float)):
		raise ValueError(f"run_pipeline returned a non-numeric probability: {probability!r}")

	if abs(probability - entry_price) < MIN_EDGE:
		logger.info("No trade at %s: probability %.4f, price %.4f", entry_timestamp, probability, entry_price)
		logger.info("Trade summary for %s: trade_count=0 details=[]", market_ticker)
		return 0.0, 0, f"Trade was not made. Market price was {entry_price} and our probability was {probability}, so our edge was too small."

	bought_yes = probability > entry_price
	position_price = entry_price if bought_yes else 1 - entry_price
	kelly_fraction = determine_kelly(entry_price, probability)
	buy_amount = kelly_fraction * available_capital
	contract_count = buy_amount / position_price
	trade_details = {
		"side": "YES" if bought_yes else "NO",
		"buy_price": position_price,
		"buy_amount": buy_amount,
		"bought_at": entry_timestamp,
		"sold": False,
		"sell_price": None,
		"profit": None,
	}
	logger.info(
		"Bought %s at %s for %.4f with estimated probability %.4f",
		"YES" if bought_yes else "NO",
		entry_timestamp,
		buy_amount,
		probability,
	)

	for candlestick in sorted_candlesticks:
		current_timestamp = _parse_timestamp(candlestick["end_period_ts"])
		if current_timestamp <= entry_timestamp:
			continue
		current_price = _yes_close_dollars(candlestick)
		if abs(current_price - probability) <= SELL_AT_DISTANCE:
			profit_per_contract = current_price - entry_price if bought_yes else entry_price - current_price
			profit = contract_count * profit_per_contract
			trade_details["sold"] = True
			trade_details["sell_price"] = current_price
			trade_details["profit"] = profit
			logger.info("Sold at %s for profit %.4f", current_timestamp, profit)
			logger.info(
				"Trade summary for %s: trade_count=1 details=%s",
				market_ticker,
				trade_details,
			)
			return profit, 1, f"Trade was made. We were able to sell out before market concluded. Profit {profit}"

	logger.info("Market %s resolved before reaching the sell distance.", market.get("ticker", market_ticker))
	trade_details["profit"] = -buy_amount
	logger.info(
		"Trade summary for %s: trade_count=1 details=%s",
		market_ticker,
		trade_details,
	)
	return -buy_amount, 1, f"Trade was made. We were not able to sell out before market concluded. Profit {-buy_amount}"

# TEST EARNINGS CALL:

# MARKET_TICKERS = get_all_market_tickers("KXEARNINGSMENTIONKR-26SEP11")
# call = earnings_call(
# 	market_tickers= MARKET_TICKERS,
# 	timestamp = random_historical_yes_price(MARKET_TICKERS[0], seed=SEED)[0]
# )

def simulate_earnings_call(earnings_call, starting_capital = 104.74):
	if not isinstance(starting_capital, (int, float)):
		raise ValueError("starting_capital must be a number. Got a {}".format(type(starting_capital).__name__))
	capital_to_track = starting_capital
	total_trades = 0
	trade_messages = []
	for market_ticker in earnings_call.market_tickers:
		try:
			profit, result, message = simulate_trade(market_ticker, capital_to_track, earnings_call.timestamp)
			capital_to_track += profit
			total_trades += result
			changed_message = f"Market {market_ticker}: {message}\n"
			trade_messages.append(changed_message)
		except Exception as e:
			logger.error("Error simulating trade for market %s: %s", market_ticker, e)
	logger.info("Presets used: MIN_EDGE=%.2f, SELL_AT_DISTANCE=%.2f", MIN_EDGE, SELL_AT_DISTANCE)
	return capital_to_track, total_trades, trade_messages

def create_earnings_events(earnings_call_list, random = True): # HELPER FUNCTION
	earnings_list = []
	for thing in earnings_call_list:
		market_tickers = get_all_market_tickers(thing)
		timestamp = random_historical_yes_price(market_tickers[0], seed=SEED)[0] if random else None
		earnings_list.append(earnings_call(market_tickers=market_tickers, timestamp=timestamp))
	return earnings_list

def backtest(earnings_call_list = ["KXEARNINGSMENTIONCOST-26SEP24", "KXEARNINGSMENTIONBB-26SEP24", 
                                   "KXEARNINGSMENTIONGIS-26SEP23", "KXEARNINGSMENTIONCBRL-26SEP23", 
								   "KXEARNINGSMENTIONAZO-26SEP22", "KXEARNINGSMENTIONKR-26SEP11"], starting_capital = 104.74, random = True):
	earnings_list = create_earnings_events(earnings_call_list, random = random)
	capital_to_track = starting_capital
	total_trades = 0
	trade_messages = []
	for earnings_call in earnings_list:
		new_capital, trades, _ = simulate_earnings_call(earnings_call, capital_to_track)
		capital_to_track = new_capital
		total_trades += trades
		trade_messages.append(f"Earnings call for {earnings_call}: Capital after trades: {capital_to_track}, Trades made: {trades}\n")
	return capital_to_track, total_trades, trade_messages
