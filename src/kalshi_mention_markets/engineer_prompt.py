import json
import os
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

def clear_all_files():
    """Clear the contents of all relevant files."""
    for path in [DUMP_PATH, PRIOR_TRANSCRIPT_PATH, PROMPT_ACTUAL]:
        path.write_text("", encoding="utf-8")

def _search_serpapi(query, result_count, api_key):
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
		raise RuntimeError(f"SerpAPI request failed: {payload['error']}")
	results = payload.get("news_results", [])[:result_count]
	if len(results) != result_count:
		raise RuntimeError(
			f"SerpAPI returned {len(results)} news results for {query!r}; "
			f"expected {result_count}."
		)
	return results


def query_serp(company, keyword):
    # clear the serpapi_dump.json file before writing new results
    DUMP_PATH.write_text("", encoding="utf-8")   
    """Fetch the requested Google results and save them to the data dump."""
    api_key = os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        raise RuntimeError("Set SERPAPI_KEY before querying SerpAPI.")

    searches = {
        company: 34,
        f"{company} {keyword}": 33,
        f"{company} earnings": 33,
    }
    results = {
		query: [
			{
				"title": result.get("title"),
				"snippet": result.get("snippet"),
				"source": result.get("source"),
				"date": result.get("date"),
			}
			for result in _search_serpapi(query, result_count, api_key)
		]
        for query, result_count in searches.items()
    }
    DUMP_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results



def _roic_get(path, params, api_key):
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
			raise
		try:
			payload = json.load(error)
		except (TypeError, ValueError):
			payload = {}
		message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
		detail = message or error.reason or "unknown error"
		raise RuntimeError(
			f"ROIC AI request failed ({error.code}) for {path}: {detail}"
		) from error
	if "error" in payload:
		raise RuntimeError(f"ROIC AI request failed: {payload['error']}")
	return payload


def get_market_price(market_ticker, word):
	"""Return the latest YES price for the matching market, in dollars."""
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
				return float(price)

		cursor = payload.get("cursor")
		if not cursor:
			break

	raise LookupError(
		f"Kalshi could not find a market containing {word!r} "
		f"for event {market_ticker!r}."
	)


def get_prior_earnings_call(company):
	"""Fetch the latest available earnings-call transcript for a company."""
	api_key = os.environ.get("ROIC_API_KEY")
	if not api_key:
		raise RuntimeError("Set ROIC_API_KEY before querying ROIC AI.")
	if not company or not company.strip():
		raise ValueError("company must not be empty.")

	search = _roic_get(
		"/tickers/search",
		{"query": company.strip(), "search_by": "name", "limit": 10},
		api_key,
	)
	tickers = search.get("data", [])
	if not tickers:
		search = _roic_get(
			"/tickers/search",
			{"query": company.strip(), "limit": 10},
			api_key,
		)
		tickers = search.get("data", [])
	if not tickers:
		raise LookupError(f"ROIC AI could not find a ticker for {company!r}.")

	identifier = tickers[0]["symbol"]
	earnings = _roic_get(
		"/earnings-calls",
		{"identifier": identifier, "order": "desc", "limit": 20},
		api_key,
	)
	for event in earnings.get("data", []):
		fiscal_year = event.get("fiscal_year")
		fiscal_quarter = event.get("fiscal_quarter")
		if not isinstance(fiscal_year, int) or not isinstance(fiscal_quarter, int):
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
				continue
			raise
		PRIOR_TRANSCRIPT_PATH.write_text(
			json.dumps(transcript, indent=2), encoding="utf-8"
		)
		return transcript

	raise LookupError(f"ROIC AI has no transcript available for {company!r}.")

def format_news():
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
				f"{field}: {article.get(field) or ''}"
				if field != "source"
				else f"source: {source or ''}"
				for field in ("title", "snippet", "source", "date")
			)
		)

	return "\n\n".join(formatted_articles)

def input_context(market_ticker, word):
	 # erase all content from actual_prompt.txt before writing new prompt
    with open(PROMPT_TEMPLATE, "r", encoding="utf-8") as source:
        content = source.read()

    with open(PROMPT_ACTUAL, "w", encoding="utf-8") as destination:
        destination.write(content)
    
    with open(PRIOR_TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    transcript_text = "\n\n".join(
        f"{entry['speaker']}: {entry['text']}"
        for entry in data["transcript"]
        )


    text_to_add = f"""**KEYWORD**: {word} \n \n
**CURRENT MARKET PRICE / MARKET-IMPLIED PROBABILITY**: {get_market_price(market_ticker, word)} \n \n
**PREVIOUS EARNINGS CALL TRANSCRIPT**: \n 
{transcript_text} \n \n
**RECENT NEWS**: \n
{format_news()}"""
	
    with open(PROMPT_ACTUAL, "a", encoding="utf-8") as file:
        file.write("\n\n" + text_to_add)

    return text_to_add

def execute_engineer_prompt(market_ticker, company, word):
	"""Execute the engineer prompt with the given market ticker, company, and name."""
	# Clear all relevant files before starting
	clear_all_files()

	# Query SerpAPI for news articles
	query_serp(company, word)

	# Fetch the prior earnings call transcript
	get_prior_earnings_call(company)

	# Prepare the input context for the prompt
	input_context(market_ticker, word)

	return "Engineer prompt executed successfully."
