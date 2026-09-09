from pathlib import Path

from openai import OpenAI


MODEL = "gpt-5.1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "research" / "prompt.txt"


def run_prompt() -> str:
    """Send the research prompt to GPT-5.1 and return the response text."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = OpenAI()
    response = client.responses.create(model=MODEL, input=prompt)
    return response.output_text


if __name__ == "__main__":
    print(run_prompt())