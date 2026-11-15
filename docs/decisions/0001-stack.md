# 0001 — Initial stack

**Status:** Accepted
**Date:** 2026-11-14

## Context
Day-one decision: pick the stack for layoffs-tracker.

## Decision
Use the **python** profile of the project template:

- **Language:** Python 3.12+
- **Packaging:** uv + hatchling, `src/` layout
- **Quality:** ruff, mypy (strict), pytest + pytest-cov with an 80% floor
- **Tasks:** Onigiri, test-gated
- **Data:** polars and parquet; the published metrics are one JSON file
- **Orchestration:** Airflow 3.x on k3s (see 0002, 0003)

## Rationale
The python profile gives the project a single `src/<pkg>/` package with
`tests/`, and one toolchain: uv, ruff, mypy, pytest. Starting from it means CI
is green on the first push.

## Consequences
- (positive) Lint, typecheck, test, and coverage gates all run in CI from day one.
- (tradeoff) No frontend. Adding one later means moving to the web-api layout (`backend/` + `frontend/`).
- (followup) ADRs 0002+ record library choices within this stack.

## Alternatives considered
- **web-api profile** — FastAPI backend + Next.js frontend. Not chosen: the only UI is the portfolio page, which lives in the portfolio repo.
