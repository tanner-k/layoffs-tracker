<div align="center">

<!-- Add a logo at docs/logo.png, then uncomment:
<img src="docs/logo.png" alt="layoffs-tracker logo" width="80" height="80">
-->

# layoffs-tracker

Monthly tracker of AI-cited layoffs in SEC filings and WARN notices

[![CI][ci-shield]][ci-url]
[![License][license-shield]][license-url]
[![Python][python-shield]][python-url]

[Docs](docs/) · [Report bug][bug-url] · [Request feature][feature-url]

</div>

<details>
<summary>Table of contents</summary>

- [About](#about)
  - [Built with](#built-with)
- [Getting started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
- [Commands](#commands)
- [Configuration](#configuration)
- [Roadmap](#roadmap)
- [Project map](#project-map)
- [Changelog](#changelog)
- [License](#license)

</details>

## About

<!-- Add a screenshot at docs/screenshot.png, then uncomment:
![Screenshot](docs/screenshot.png)
-->

Each month this pipeline finds new SEC 8-K Item 2.05 (layoff) filings, asks
[Jev](https://typesafe.ai) whether each cites AI as a reason, pulls WARN layoff notices, and
publishes [`data/latest.json`](data/latest.json) for the
[portfolio tracker page](https://tanner-k.github.io/portfolio/research/layoffs-tracker/). It
keeps the working paper *Layoffs, AI and New Businesses* current: through September 2026 the
numbers are the paper's audited labels; from October 2026, Jev labels new filings alone.

- Monthly SEC full-text search with a three-month rescan for late-indexed filings
- AI labels: the paper's audited labels through Sep 2026, Jev only after (marked on every chart)
- National WARN series on states covered every year since 2019

```
storageguy (k3s, one node)
  Airflow 3 (Helm chart, LocalExecutor): scheduler, api-server, dag-processor, Postgres
    DAG layoffs_monthly, 09:00 America/Denver on the 3rd, for the month before
      checkout -> fetch_sec -> label -> fetch_warn -> build_metrics -> publish
      each task = a KubernetesPodOperator pod of ghcr.io/tanner-k/layoffs-tracker:main
      sharing the layoffs-work volume
                         | git push (deploy key): data/filings.parquet, data/latest.json
                         v
  github.com/tanner-k/layoffs-tracker  ->  portfolio build fetches latest.json weekly
```

Decisions: [docs/decisions/](docs/decisions/). Server setup: [infra/README.md](infra/README.md).

### Built with

[![Python][python-shield]][python-url]
[![uv][uv-shield]][uv-url]
[![pytest][pytest-shield]][pytest-url]
[![Ruff][ruff-shield]][ruff-url]

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Installation

1. Clone the repo
   ```bash
   git clone https://github.com/tanner-k/layoffs-tracker.git layoffs-tracker
   cd layoffs-tracker
   ```
2. Install dependencies
   ```bash
   uv sync
   ```
3. Install the git hooks
   ```bash
   uv run pre-commit install
   ```
4. Copy the environment file
   ```bash
   cp .env.example .env
   ```

## Commands

| Command | What it does |
|---|---|
| `scripts/check.sh` | Everything CI runs: lint, format, typecheck, tests + 80% coverage floor |
| `uv run pytest` | Run the tests |
| `uv run ruff check . --fix` | Lint and auto-fix |
| `uv run ruff format .` | Format |
| `uv run mypy` | Typecheck (strict) |
| `uv run layoffs-tracker --help` | The pipeline steps (each takes `--work DIR`) |
| `uv run layoffs-tracker seed` | Rebuild `data/filings.parquet` from the paper's labels |
| `uv run python scripts/port_check.py` | Live check: SEC search for Jul-Sep 2026 matches the paper |
| `uv run python scripts/jev_check.py` | Live check: the tracker's Jev labels vs the paper's Jev run, Jan-Sep 2026 |

Run one month by hand, in DAG order (needs both variables below):

```bash
W=/tmp/lt-work
uv run layoffs-tracker checkout --work $W --repo-url https://github.com/tanner-k/layoffs-tracker.git
uv run layoffs-tracker fetch-sec --work $W --run-month 2026-10
uv run layoffs-tracker label --work $W            # at most 100 Jev calls, else it fails
uv run layoffs-tracker fetch-warn --work $W
uv run layoffs-tracker build-metrics --work $W --run-month 2026-10
```

`publish` commits and pushes `data/`; leave it to the cluster.

## Configuration

Settings come from environment variables. Add each one to `.env.example` with
a comment, and list it here.

| Variable | Description |
|---|---|
| `SEC_USER_AGENT` | `Your Name your@email`; SEC refuses requests without a contact |
| `TYPESAFE_API_KEY` | Jev API key (model pinned to `jev-1.13.0`) |
| `GIT_SSH_COMMAND` | Cluster only: points git at the mounted deploy key |

## Reading `latest.json`

| Key | What |
|---|---|
| `data_through` | Last complete month, `YYYY-MM` |
| `method_switch` | `2026-10`: first month labeled by Jev alone |
| `sec_monthly` | Original Item 2.05 filings per month (amendments excluded) |
| `ai_quarterly`, `ai_yearly` | `total`, `ai_true`, `unresolved`, `share_low` = ai_true/total, `share_high` = (ai_true+unresolved)/total, per period and `method`; 2026 has a paper row and a Jev row |
| `warn_monthly` | WARN notices, workers (known counts) and notices missing a count, for `sources.warn_states` |

Gotcha: on the paper's own filings, the paper's Jev run found fewer AI-cited filings than the
audited labels (2026 Jan-Sep: 17 vs 26, 14 unresolved), so compare Jev quarters against
`share_high`. That run asked Jev ~25 questions per filing; the tracker asks only the AI question.
`scripts/jev_check.py` compares the two on the same filings (live; needs both variables).

## Footprint

Memory on storageguy, k3s plus Airflow: not measured yet (target under 3 GB).

## Roadmap

Planned work lives in Onigiri: `oni tree LT`.

## Project map

See [CLAUDE.md](CLAUDE.md) (also [AGENTS.md](AGENTS.md)) for the map of this repo.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT © 2026 layoffs-tracker contributors. See [LICENSE](LICENSE).

<!-- Reference-style links: every badge and repo URL is defined once, here. -->
[ci-shield]: https://github.com/tanner-k/layoffs-tracker/actions/workflows/ci.yml/badge.svg
[ci-url]: https://github.com/tanner-k/layoffs-tracker/actions/workflows/ci.yml
[bug-url]: https://github.com/tanner-k/layoffs-tracker/issues/new?labels=bug
[feature-url]: https://github.com/tanner-k/layoffs-tracker/issues/new?labels=enhancement
[license-shield]: https://img.shields.io/badge/license-MIT-blue
[license-url]: LICENSE
[python-shield]: https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white
[python-url]: https://www.python.org/
[uv-shield]: https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white
[uv-url]: https://docs.astral.sh/uv/
[pytest-shield]: https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white
[pytest-url]: https://docs.pytest.org/
[ruff-shield]: https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black
[ruff-url]: https://docs.astral.sh/ruff/
