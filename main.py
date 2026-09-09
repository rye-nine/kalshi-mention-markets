import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path
#from src.kalshi_mention_markets.engineer_prompt import query_serp


SOURCE_DIR = Path(__file__).resolve().parent / "src" / "kalshi_mention_markets"
sys.path.insert(0, str(SOURCE_DIR))


def discover_functions():
	"""Find public functions in every Python module under the source directory."""
	functions = {}
	for module_path in SOURCE_DIR.glob("*.py"):
		if module_path.name.startswith("_"):
			continue
		module = importlib.import_module(module_path.stem)
		for name, function in inspect.getmembers(module, inspect.isfunction):
			if not name.startswith("_") and function.__module__ == module.__name__:
				if name in functions:
					raise RuntimeError(f"Duplicate function name: {name}")
				functions[name] = function
	return functions


def build_parser(functions):
	parser = argparse.ArgumentParser(description="Run project functions for testing.")
	subparsers = parser.add_subparsers(dest="function", required=True)

	for name, function in sorted(functions.items()):
		subparser = subparsers.add_parser(name, help=inspect.getdoc(function))
		for parameter in inspect.signature(function).parameters.values():
			if parameter.kind not in (
				inspect.Parameter.POSITIONAL_OR_KEYWORD,
				inspect.Parameter.KEYWORD_ONLY,
			):
				raise TypeError(f"Unsupported parameter kind in {name}: {parameter.name}")
			option = f"--{parameter.name.replace('_', '-')}"
			kwargs = {"dest": parameter.name}
			if parameter.default is inspect.Parameter.empty:
				kwargs["required"] = True
			else:
				kwargs["default"] = parameter.default
			subparser.add_argument(option, **kwargs)
	return parser


def main():
	functions = discover_functions()
	parser = build_parser(functions)
	arguments = vars(parser.parse_args())
	function_name = arguments.pop("function")
	result = functions[function_name](**arguments)
	if result is not None:
		print(json.dumps(result, indent=2, default=str))
	#query_serp("Nvidia", "AI")

if __name__ == "__main__":
	main()
