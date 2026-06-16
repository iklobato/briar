.PHONY: help venv install test smoke build clean

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
BRIAR ?= .venv/bin/briar

help:
	@echo "Targets:"
	@echo "  venv     Create .venv/ and `pip install -e .` (defines the briar entry point)"
	@echo "  install  Same as venv (compatibility alias)"
	@echo "  test     Run the unit-test suite (stdlib unittest)"
	@echo "  smoke    Help-parse every CLI subcommand"
	@echo "  build    python -m build (sdist + wheel)"
	@echo "  clean    Remove build artifacts"

venv:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -e .
	@echo
	@echo "→ source .venv/bin/activate, then run \`briar --help\`"

install: venv

test:
	$(PY) -m unittest discover -v -s tests

smoke:
	@$(BRIAR) --help >/dev/null && echo "  ✓ top-level"
	@for c in extract runbook scaffold context dashboard agent \
	          auth plan secrets journal mcp chat telemetry version; do \
	    $(BRIAR) "$$c" --help >/dev/null 2>&1 && echo "  ✓ $$c" \
	      || { echo "  ✗ $$c"; exit 1; }; \
	done

build:
	$(PY) -m build

clean:
	rm -rf build dist *.egg-info src/*.egg-info \
	       src/briar/__pycache__ tests/__pycache__ \
	       src/briar/**/__pycache__ src/briar/**/**/__pycache__
