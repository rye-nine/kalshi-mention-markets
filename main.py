import argparse
import importlib
import inspect
import json
import logging
import sys
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent / "src" / "kalshi_mention_markets"
PACKAGE_NAME = "src.kalshi_mention_markets"

logger = logging.getLogger(__name__)


def discover_functions():
	"""Find public functions in every Python module under the source directory."""
	logger.info("Discovering public functions in %s", SOURCE_DIR)
	functions = {}
	for module_path in SOURCE_DIR.glob("*.py"):
		if module_path.name.startswith("_"):
			continue
		logger.debug("Importing module %s", module_path.stem)
		module = importlib.import_module(f"{PACKAGE_NAME}.{module_path.stem}")
		for name, function in inspect.getmembers(module, inspect.isfunction):
			if not name.startswith("_") and function.__module__ == module.__name__:
				if name in functions:
					logger.error("Duplicate public function discovered: %s", name)
					raise RuntimeError(f"Duplicate function name: {name}")
				functions[name] = function
	logger.info("Discovered %d public functions", len(functions))
	return functions


def build_parser(functions):
	logger.debug("Building CLI parser for %d functions", len(functions))
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
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
	logger.info("Starting command-line function runner")
	functions = discover_functions()
	parser = build_parser(functions)
	arguments = vars(parser.parse_args())
	function_name = arguments.pop("function")
	logger.info("Invoking function %s with arguments %s", function_name, arguments)
	result = functions[function_name](**arguments)
	logger.info("Function %s completed with result type %s", function_name, type(result).__name__)
	if result is not None:
		logger.debug("Serializing result from %s", function_name)
		print(json.dumps(result, indent=2, default=str))
		#print(f"Output type: {type(result).__name__}")

if __name__ == "__main__":
	main()
