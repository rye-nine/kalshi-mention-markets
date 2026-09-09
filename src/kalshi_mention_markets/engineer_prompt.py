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
PROMPT = Path(__file__).resolve().parents[2] / "research" / "prompt.txt"

def _search_serpapi(query, result_count, api_key):
    params = urlencode(
        {
            "engine": "google",
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
    return payload.get("organic_results", [])[:result_count]


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
        query: _search_serpapi(query, result_count, api_key)
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

def input_context():
     with open(PROMPT, "r") as f:
           lines = f.readlines()
     lines.insert(2, "INSERTED LINE\n")
