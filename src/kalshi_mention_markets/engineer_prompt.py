import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import ssl
import certifi


SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DUMP_PATH = Path(__file__).resolve().parents[2] / "data" / "serpapi_dump.json"


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
