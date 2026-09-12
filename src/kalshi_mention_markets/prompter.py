from pathlib import Path

from openai import OpenAI

from .engineer_prompt import execute_engineer_prompt


MODEL = "gpt-5.6-luna"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "research" / "actual_prompt.txt"
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "gpt_output.txt"


def run_prompt() -> str:
    """Send the research prompt to GPT-5.1 and return the response text."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = OpenAI()
    response = client.responses.create(model=MODEL, input=prompt)
    OUTPUT_PATH.write_text(response.output_text, encoding="utf-8")
    return response.output_text

def run_pipeline(market_ticker, company, word):
    """Run the entire research pipeline."""
    execute_engineer_prompt(market_ticker, company, word)
    # print all the text in the prompt output file
    run_prompt()
    print(OUTPUT_PATH.read_text(encoding="utf-8"))
    return f"Pipeline completed successfully. Prompt output written to {OUTPUT_PATH}."


if __name__ == "__main__":
    print(run_prompt())