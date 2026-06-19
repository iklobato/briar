.PHONY: help venv install pytest test fmt lint typecheck check smoke build clean

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
BRIAR ?= .venv/bin/briar

help:
	@echo "Targets:"
	@echo "  venv       Create .venv/ and install the package + tools (editable, [test,dev])"
	@echo "  install    Same as venv (compatibility alias)"
	@echo "  pytest     Run the unit-test suite (pytest, what CI uses)"
	@echo "  test       Run the legacy stdlib-unittest discovery"
	@echo "  fmt        Format with black"
	@echo "  lint       Lint with ruff (repo has known debt; clean per-file)"
	@echo "  typecheck  Type-check with mypy (repo has known debt; clean per-file)"
	@echo "  check      Run the green gate: pytest (what release CI enforces)"
	@echo "  smoke      Help-parse every CLI subcommand"
	@echo "  build      python -m build (sdist + wheel)"
	@echo "  clean      Remove build artifacts"

venv:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -e ".[test,dev]"
	@echo
	@echo "next: source .venv/bin/activate, then run 'briar --help'"

install: venv

pytest:
	$(PY) -m pytest -ra -m "not integration"

test:
	$(PY) -m unittest discover -v -s tests

fmt:
	$(PY) -m black src tests

lint:
	$(PY) -m ruff check src tests

typecheck:
	$(PY) -m mypy

# The release CI gate. Scoped to the unit suite because the repo is not
# yet black/ruff/mypy-clean repo-wide (those run per-file in review). Once
# the lint/type debt is paid down, fold `lint typecheck` in here.
check: pytest
	@echo "tests passed"

smoke:
	@$(BRIAR) --help >/dev/null && echo "  ok top-level"
	@for c in extract runbook scaffold context dashboard agent \
	          auth plan secrets journal telemetry version \
	          init config doctor completion; do \
	    $(BRIAR) "$$c" --help >/dev/null 2>&1 && echo "  ok $$c" \
	      || { echo "  FAIL $$c"; exit 1; }; \
	done

build:
	$(PY) -m build

clean:
	rm -rf build dist *.egg-info src/*.egg-info \
	       src/briar/__pycache__ tests/__pycache__ \
	       src/briar/**/__pycache__ src/briar/**/**/__pycache__
