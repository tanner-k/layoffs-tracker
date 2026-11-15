# Project Map — layoffs-tracker

> **`AGENTS.md` is a symlink to this file.** Edit `CLAUDE.md` only; AGENTS.md follows automatically.
> This file is the entry point for any AI agent working in this repo.
> **Work is managed by Onigiri** (`.onigiri.toml`, project key `LT`). Run `oni next` to get your task.

## 1. What this project is
Monthly tracker of AI-cited layoffs in SEC filings and WARN notices

## 2. Working state
This project is "working" when **all** of these are true:

- `scripts/check.sh` passes: lint, format, typecheck, tests with ≥ 80% coverage, script tests, scaffold check.
- CI is green on `dev`.
- Every Onigiri task marked done has its test green (`oni test KEY`).

## 3. Project map
```
layoffs-tracker/
├── README.md                ← what the project is + how to run it
├── CLAUDE.md                ← this file (AI agent context)
├── AGENTS.md                ← symlink to CLAUDE.md
├── CHANGELOG.md             ← shipped work, newest first
├── .onigiri.toml            ← Onigiri: project key + test/collect/lint commands
├── pyproject.toml           ← dependencies + ruff, mypy, pytest, coverage config
├── uv.lock                  ← pinned dependency versions (commit it)
├── .python-version
├── .pre-commit-config.yaml  ← hooks that run on every commit
├── .editorconfig
├── .gitignore
├── .env.example
├── LICENSE
├── .github/
│   ├── PULL_REQUEST_TEMPLATE.md
│   ├── dependabot.yml
│   └── workflows/
│       └── ci.yml                ← every PR: runs scripts/check.sh, uploads junit
├── scripts/
│   ├── check.sh                  ← the whole local gate; CI runs exactly this
│   └── check_scaffold.py         ← fails on unfilled placeholders (+ test)
├── src/layoffs_tracker/     ← the package: one CLI step per DAG task
├── dags/                    ← the Airflow DAG (git-synced to the cluster)
├── infra/                   ← Helm values, k8s manifests, server install steps
├── data/                    ← published filings.parquet, latest.json, the paper seed
├── tests/                   ← pytest tests; node ids are Onigiri test targets
└── docs/
    ├── context.md
    ├── architecture.md
    ├── testing.md           ← TDD contract, test targets, JUnit, fixtures, coverage
    └── decisions/           ← one ADR per architectural decision
        └── 0001-stack.md
```

## 4. Where to do what
| If you're working on... | Go to... | Read first |
|---|---|---|
| Package code | `src/layoffs_tracker/` | `docs/architecture.md` |
| Tests | `tests/` | `docs/testing.md` |
| The Airflow DAG | `dags/` | `dags/context.md` |
| Cluster, Helm, Secrets | `infra/` | `infra/context.md` |
| Published data | `data/` | `data/context.md` |
| Architecture decision | `docs/decisions/` | latest ADR |
| Workflow / process question | `CLAUDE.md` (here) | this file |

## 5. Global skills / MCPs
Apply across the whole repo:

- `npx skills add vercel-labs/skills --skill find-skills` — lets the agent discover skills for this project's stack
- `npx skills add mattpocock/skills` — workflow skills: /grill-with-docs (alignment + ADRs), /tdd, /triage

## 6. Workflow rules
1. **Branches:** `main` = prod (protected). `dev` = staging. All PRs target `dev`. `dev` → `main` is a release.
2. **Orient first:** run `oni recall "<topic>" --project LT --json` before starting work. Expand a relevant epic or story with `oni recall --expand LT`; read tasks only after that.
3. **Work items:** `oni add` / `oni next` / `oni set KEY --status done`. A task cannot close while its test is red; see [`docs/testing.md`](./docs/testing.md).
4. **Tests first:** write the failing test, watch it fail, then implement.
5. **Docs first:** when you make an architectural decision, add an ADR in `docs/decisions/`.
6. **Hooks on commit:** pre-commit runs ruff and whitespace fixes. Do not bypass with `--no-verify`.
7. **CI green to merge:** every PR must pass `.github/workflows/ci.yml`, which runs `scripts/check.sh`.
8. **Identical canon:** AGENTS.md is a symlink to CLAUDE.md. Edit CLAUDE.md only; CI verifies they match.

## 7. Tech stack
- Language: Python 3.12+
- Packaging: uv (`pyproject.toml`, `uv.lock`), hatchling, `src/` layout
- Quality: ruff (lint + format), mypy (strict), pytest + pytest-cov (≥ 80% coverage)
- Hooks: pre-commit
