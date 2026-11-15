#!/usr/bin/env bash
# The whole local gate. CI runs exactly this script, so green here is green there.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing --cov-fail-under=80 --junitxml=junit.xml
uv run python -m unittest discover -s scripts -v
uv run python scripts/check_scaffold.py
