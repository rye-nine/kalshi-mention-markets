import json
import logging
import os
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen
import ssl
import certifi
from urllib.error import URLError
import pickle



SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DUMP_PATH = Path(__file__).resolve().parents[2] / "data" / "serpapi_dump.json"

ROIC_API_ENDPOINT = "https://api.roic.ai/v3.0.0"
PRIOR_TRANSCRIPT_PATH = Path(__file__).resolve().parents[2] / "data" / "prior_transcript.json"

PROMPT_TEMPLATE = Path(__file__).resolve().parents[2] / "research" / "prompt_template.txt"
PROMPT_ACTUAL = Path(__file__).resolve().parents[2] / "research" / "actual_prompt.txt"

KALSHI_API_ENDPOINT = "https://api.elections.kalshi.com/trade-api/v2"

logger = logging.getLogger(__name__)

def clear_all_files():
	"""Clear the contents of all relevant files."""
	logger.info("Clearing prompt and data files: %s", [str(path) for path in [DUMP_PATH, PRIOR_TRANSCRIPT_PATH, PROMPT_ACTUAL]])
	for path in [DUMP_PATH, PRIOR_TRANSCRIPT_PATH, PROMPT_ACTUAL]:
		path.write_text("", encoding="utf-8")
	logger.info("Finished clearing prompt and data files.")

def _search_serpapi(query, result_count, api_key, require_count=True):
	logger.info("Querying SerpAPI for %r with requested result count %s.", query, result_count)
	params = urlencode(
		{
			"engine": "google_news",
			"q": query,
			"api_key": api_key,
			"num": result_count,
		}
	)

	context = ssl.create_default_context(cafile=certifi.where())

	with urlopen(f"{SERPAPI_ENDPOINT}?{params}", context=context) as response:
		payload = json.load(response)
	if "error" in payload:
		logger.error("SerpAPI returned an error for %r: %s", query, payload['error'])
		raise RuntimeError(f"SerpAPI request failed: {payload['error']}")
	results = payload.get("news_results", [])[:result_count]
	logger.info("SerpAPI returned %d results for %r (requested %d).", len(results), query, result_count)
	if require_count and len(results) != result_count:
		logger.warning("SerpAPI result count mismatch for %r: got %d, expected %d.", query, len(results), result_count)
		raise RuntimeError(
			f"SerpAPI returned {len(results)} news results for {query!r}; "
			f"expected {result_count}."
		)
	return results


def _parse_news_date(value):
	if not value:
		logger.debug("Skipping news date parse for empty value.")
		return None
	try:
		parsed = _as_datetime(value)
		logger.debug("Parsed news date %r as %s.", value, parsed)
		return parsed
	except ValueError:
		try:
			parsed = datetime.strptime(value, "%m/%d/%Y, %I:%M %p, %z UTC")
			logger.debug("Parsed alternate news date %r as %s.", value, parsed)
			return parsed
		except ValueError:
			logger.debug("Could not parse news date %r with either ISO or fallback format.", value)
			return None


def _is_on_or_before(value, reference_datetime):
	news_datetime = _parse_news_date(value)
	if news_datetime is None:
		logger.debug("News date %r is missing or invalid; retaining article.", value)
		return True
	if reference_datetime.tzinfo is None and news_datetime.tzinfo is not None:
		reference_datetime = reference_datetime.replace(
			tzinfo=datetime.now().astimezone().tzinfo
		)
	elif reference_datetime.tzinfo is not None and news_datetime.tzinfo is None:
		news_datetime = news_datetime.replace(tzinfo=reference_datetime.tzinfo)
	result = news_datetime <= reference_datetime
	logger.debug("Compared news date %s to reference %s; result=%s.", news_datetime, reference_datetime, result)
	return result


def query_serp(company, keyword, date=None):
	# clear the serpapi_dump.json file before writing new results
	logger.info("Starting SerpAPI query for company=%r keyword=%r date=%r.", company, keyword, date)
	DUMP_PATH.write_text("", encoding="utf-8")
	"""Fetch the requested Google results and save them to the data dump."""
	api_key = os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
	if not api_key:
		logger.error("SERPAPI_KEY is missing before querying SerpAPI.")
		raise RuntimeError("Set SERPAPI_KEY before querying SerpAPI.")
	reference_datetime = datetime.now() if date is None else _as_datetime(date)
	logger.info("Using reference_datetime=%s for filtering results.", reference_datetime)

	searches = {
		company: 34,
		f"{company} {keyword}": 33,
		f"{company} earnings": 33,
	}
	results = {
		query: [
			{
				"title": result.get("title"),
				"snippet": result.get("link"),
				"source": result.get("source"),
				"date": result.get("date"),
			}
			for result in _search_serpapi(query, 100, api_key, require_count=False)
			if _is_on_or_before(result.get("date"), reference_datetime)
		][:result_count]
		for query, result_count in searches.items()
	}
	DUMP_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
	result_count = sum(len(search_results) for search_results in results.values())
	logger.info("Incorporated %d search results into %s.", result_count, DUMP_PATH)
	return results



def _roic_get(path, params, api_key):
	logger.info("Calling ROIC API path=%s params=%s.", path, params)
	query = urlencode(params)
	request = Request(
		f"{ROIC_API_ENDPOINT}{path}?{query}",
		headers={"Authorization": f"Bearer {api_key}"},
	)
	context = ssl.create_default_context(cafile=certifi.where())
	try:
		with urlopen(request, context=context) as response:
			payload = json.load(response)
	except HTTPError as error:
		if error.code == 404:
			logger.warning("ROIC API returned 404 for path=%s params=%s.", path, params)
			raise
		try:
			payload = json.load(error)
		except (TypeError, ValueError):
			payload = {}
		message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
		detail = message or error.reason or "unknown error"
		logger.error("ROIC AI request failed (%s) for %s: %s", error.code, path, detail)
		raise RuntimeError(
			f"ROIC AI request failed ({error.code}) for {path}: {detail}"
		) from error
	if "error" in payload:
		logger.error("ROIC AI response contains error payload for %s: %s", path, payload['error'])
		raise RuntimeError(f"ROIC AI request failed: {payload['error']}")
	logger.info("ROIC API path=%s returned payload keys=%s.", path, list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
	return payload


def get_market_price(market_ticker, word):
	"""Return the latest YES price for the matching market, in dollars."""
	logger.info("Looking up Kalshi market price for ticker=%r word=%r.", market_ticker, word)
	if not isinstance(market_ticker, str) or not market_ticker.strip():
		raise ValueError("market_ticker must not be empty.")
	if not isinstance(word, str) or not word.strip():
		raise ValueError("word must not be empty.")

	search_word = word.strip().casefold()
	cursor = None
	context = ssl.create_default_context(cafile=certifi.where())

	while True:
		params = {
			"event_ticker": market_ticker.strip(),
			"limit": 1000,
		}
		if cursor:
			params["cursor"] = cursor
		request = Request(
			f"{KALSHI_API_ENDPOINT}/markets?{urlencode(params)}",
			headers={"Accept": "application/json"},
		)
		logger.debug("Fetching Kalshi market page with params=%s.", params)
		try:
			with urlopen(request, context=context) as response:
				payload = json.load(response)
		except HTTPError as error:
			raise RuntimeError(
				f"Kalshi request failed ({error.code}) for event "
				f"{market_ticker!r}: {error.reason}"
			) from error

		for market in payload.get("markets", []):
			market_text = " ".join(
				str(market.get(field, ""))
				for field in ("yes_sub_title", "subtitle", "ticker")
			).casefold()
			if search_word in market_text:
				price = market.get("last_price_dollars")
				if price is None:
					legacy_price = market.get("last_price")
					price = legacy_price / 100 if legacy_price is not None else None
				if price is None:
					raise RuntimeError(
						f"Kalshi market {market.get('ticker', '<unknown>')!r} "
						"does not have a latest YES price."
					)
				logger.info("Found matching Kalshi market %r for word %r with price %s.", market.get("ticker"), word, price)
				return float(price)

		cursor = payload.get("cursor")
		if not cursor:
			break

	logger.warning("No Kalshi market found containing %r for event %r.", word, market_ticker)
	raise LookupError(
		f"Kalshi could not find a market containing {word!r} "
		f"for event {market_ticker!r}."
	)


def _as_datetime(value):
	logger.debug("Normalizing datetime value %r.", value)
	if isinstance(value, datetime):
		return value
	if isinstance(value, date_type):
		return datetime.combine(value, datetime.min.time())
	if isinstance(value, str):
		try:
			parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
			logger.debug("Converted ISO datetime string %r to %s.", value, parsed)
			return parsed
		except ValueError as error:
			raise ValueError(f"date must be a datetime, date, or ISO-formatted string: {value!r}") from error
	raise TypeError("date must be a datetime, date, or ISO-formatted string.")


def get_prior_earnings_call(company, date=None):
	"""Fetch the latest earnings-call transcript available at ``date``."""
	logger.info("Fetching prior earnings call for company=%r date=%r.", company, date)
	api_key = os.environ.get("ROIC_API_KEY")
	if not api_key:
		logger.error("ROIC_API_KEY is missing before querying ROIC AI.")
		raise RuntimeError("Set ROIC_API_KEY before querying ROIC AI.")
	if not company or not company.strip():
		raise ValueError("company must not be empty.")
	reference_datetime = datetime.now() if date is None else _as_datetime(date)
	logger.info("Using reference_datetime=%s for transcript selection.", reference_datetime)

	search = _roic_get(
		"/tickers/search",
		{"query": company.strip(), "search_by": "name", "limit": 10},
		api_key,
	)
	tickers = search.get("data", [])
	if not tickers:
		logger.info("Ticker search by name returned no results for %r; retrying without search_by.", company)
		search = _roic_get(
			"/tickers/search",
			{"query": company.strip(), "limit": 10},
			api_key,
		)
		tickers = search.get("data", [])
	if not tickers:
		raise LookupError(f"ROIC AI could not find a ticker for {company!r}.")

	identifier = tickers[0]["symbol"]
	logger.info("Selected ROIC ticker %r for company %r.", identifier, company)
	earnings = _roic_get(
		"/earnings-calls",
		{"identifier": identifier, "order": "desc", "limit": 20},
		api_key,
	)
	for event in earnings.get("data", []):
		event_date = event.get("date")
		if event_date is not None and _as_datetime(event_date) > reference_datetime:
			logger.debug("Skipping event %s because its date %s is after the reference date %s.", event.get("id"), event_date, reference_datetime)
			continue
		fiscal_year = event.get("fiscal_year")
		fiscal_quarter = event.get("fiscal_quarter")
		if not isinstance(fiscal_year, int) or not isinstance(fiscal_quarter, int):
			logger.debug("Skipping event %s due to missing fiscal year/quarter metadata.", event.get("id"))
			continue
		try:
			transcript = _roic_get(
				f"/earnings-calls/{identifier}",
				{
					"fiscal_year": fiscal_year,
					"fiscal_quarter": fiscal_quarter,
				},
				api_key,
			)
		except HTTPError as error:
			if error.code == 404:
				logger.warning("Transcript not found for %s at FY%s Q%s.", identifier, fiscal_year, fiscal_quarter)
				continue
			raise
		PRIOR_TRANSCRIPT_PATH.write_text(
			json.dumps(transcript, indent=2), encoding="utf-8"
		)
		logger.info("Saved transcript for %s to %s.", identifier, PRIOR_TRANSCRIPT_PATH)
		return transcript

	logger.warning("No transcript available for company %r from ROIC API.", company)
	raise LookupError(f"ROIC AI has no transcript available for {company!r}.")

def format_news():
	logger.info("Formatting news articles from %s.", DUMP_PATH)
	with open(DUMP_PATH, "r", encoding="utf-8") as file:
		news = json.load(file)

	articles = (
		article
		for results in news.values()
		for article in results
	)
	formatted_articles = []

	for article in articles:
		source = article.get("source")
		if isinstance(source, dict):
			source = source.get("name", "")

		formatted_articles.append(
			"\n".join(
				[
					f"title: {article.get('title') or ''}",
					f"link: {article.get('snippet') or ''}",
					f"source: {source or ''}",
					f"date: {article.get('date') or ''}",
				]
			)
		)

	formatted_output = "\n\n".join(formatted_articles)
	logger.info("Formatted %d news articles into a prompt string of length %d.", len(formatted_articles), len(formatted_output))
	return formatted_output

def input_context(market_ticker, word):
	logger.info("Building prompt input context for market_ticker=%r word=%r.", market_ticker, word)
	 # erase all content from actual_prompt.txt before writing new prompt
	with open(PROMPT_TEMPLATE, "r", encoding="utf-8") as source:
		content = source.read()

	with open(PROMPT_ACTUAL, "w", encoding="utf-8") as destination:
		destination.write(content)
	logger.info("Loaded template from %s and initialized %s.", PROMPT_TEMPLATE, PROMPT_ACTUAL)
	
	with open(PRIOR_TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
		data = json.load(f)

	transcript_text = "\n\n".join(
		f"{entry['speaker']}: {entry['text']}"
		for entry in data["transcript"]
		)
	logger.info("Prepared transcript excerpt with %d entries.", len(data.get("transcript", [])))


	text_to_add = f"""**KEYWORD**: {word} \n \n
**CURRENT MARKET PRICE / MARKET-IMPLIED PROBABILITY**: {get_market_price(market_ticker, word)} \n \n
**PREVIOUS EARNINGS CALL TRANSCRIPT**: \n 
{transcript_text} \n \n
**RECENT NEWS**: \n
{format_news()}"""
	
	with open(PROMPT_ACTUAL, "a", encoding="utf-8") as file:
		file.write("\n\n" + text_to_add)
	logger.info("Appended prompt context to %s; final size=%d characters.", PROMPT_ACTUAL, len(text_to_add))

	return text_to_add

def execute_engineer_prompt(market_ticker, company, word):
	"""Execute the engineer prompt with the given market ticker, company, and name."""
	logger.info("Executing engineer prompt for market_ticker=%r company=%r word=%r.", market_ticker, company, word)
	# Clear all relevant files before starting
	clear_all_files()

	# Query SerpAPI for news articles
	logger.info("Querying news for %r.", company)
	query_serp(company, word)

	# Fetch the prior earnings call transcript
	logger.info("Fetching prior earnings call for %r.", company)
	get_prior_earnings_call(company)

	# Prepare the input context for the prompt
	logger.info("Preparing prompt input context.")
	input_context(market_ticker, word)

	logger.info("Engineer prompt execution completed successfully.")
	return "Engineer prompt executed successfully."
