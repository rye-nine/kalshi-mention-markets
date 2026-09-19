from pathlib import Path
import logging

from openai import OpenAI

from .engineer_prompt import execute_engineer_prompt, get_market_price, get_information


MODEL = "gpt-5.1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "research" / "actual_prompt.txt"
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "gpt_output.txt"

logger = logging.getLogger(__name__)


def run_prompt(model = MODEL) -> str:
    """Send the research prompt to GPT-5.1 and return the response text."""
    logger.info("Loading research prompt from %s", PROMPT_PATH)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    logger.info("Sending %d-character prompt to OpenAI model %s", len(prompt), MODEL)
    client = OpenAI()
    try:
        response = client.responses.create(model=MODEL, input=prompt)
    except Exception:
        logger.exception("OpenAI response request failed")
        raise
    OUTPUT_PATH.write_text(response.output_text, encoding="utf-8")
    logger.info("Saved OpenAI response (%d characters) to %s", len(response.output_text), OUTPUT_PATH)
    return response.output_text


def parse_probability_from_text():
    """Parse the probability from the GPT output text."""
    # Assuming the probability is in the format "probability: **0.75**"
    text = OUTPUT_PATH.read_text(encoding="utf-8")
    logger.info("Parsing probability from GPT output (%d characters)", len(text))
    for line in text.splitlines():
        if "Probability:" in line:
            try:
                temp = line.split("Probability:")[1].strip()
                probability = float(temp)
                logger.info("Parsed probability %.4f", probability)
                return probability / 100
            except ValueError:
                logger.debug("Could not parse probability line: %r", line)
                continue
    logger.warning("No probability found in GPT output")
    return "Nothing found. Please check the output text for the correct format."

def calculate_mixmcp(probability, market_price, alpha = 0.7):
    """Calculate the MIXMCP (Mixed Market Confidence Probability)."""
    # check if probability is an int or float
    if not isinstance(probability, (int, float)):
        logger.error("Invalid probability type: %s", type(probability).__name__)
        raise ValueError(f"Probability must be an int or float, got {type(probability)}. Review parse_probability_from_text() to ensure it returns a number.")
    # check if market_price is an int or float
    if not isinstance(market_price, (int, float)):
        logger.error("Invalid market price type: %s", type(market_price).__name__)
        raise ValueError(f"Market price must be an int or float, got {type(market_price)}.")
    mixmcp = (alpha * market_price) + ((1 - alpha) * probability)
    logger.info("Calculated MIXMCP %.4f using alpha %.3f", mixmcp, alpha)
    return mixmcp


def run_pipeline(market_ticker, alpha = 0.7, market_price = None, date = None, prior_market_price = None):
    """Market price should only be provided if we're using historical data. If not provided, the current market price will be fetched from Kalshi."""

    """Get necessary information for the market and run the entire research pipeline."""
    market_dictionary = get_information(market_ticker)
    company = market_dictionary.get("company")
    word = market_dictionary.get("word")
    """Run the entire research pipeline."""

    logger.info("Starting full pipeline for company %r, event %r, word %r", company, market_ticker, word)
    execute_engineer_prompt(market_ticker, company, word, date=date, prior_market_price=prior_market_price)
    # print all the text in the prompt output file
    run_prompt()
    parsed_probability = parse_probability_from_text()
    mixmcp = calculate_mixmcp(parsed_probability, get_market_price(market_ticker), alpha=alpha) if (market_price is None) else calculate_mixmcp(parsed_probability, market_price, alpha=alpha)
    logger.info("Full pipeline completed with MIXMCP %.4f", mixmcp)
    return mixmcp


if __name__ == "__main__":
    print(run_prompt())