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

layoffs-tracker is a A monthly pipeline that finds new SEC 8-K Item 2.05 layoff filings, labels whether AI is cited as a reason, pulls WARN layoff notices, and publishes the metrics for Readers of the working paper Layoffs, AI and New Businesses, and anyone tracking how often companies blame AI for layoffs, designed to Keeps the paper's AI-share numbers current on the portfolio with no manual work, run by Airflow on single-node Kubernetes.

- Monthly SEC 8-K Item 2.05 search with a three-month rescan
- Jev labels for new filings, with the paper's audited labels through September 2026
- National WARN notice series on a fixed set of balanced states

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

## Configuration

Settings come from environment variables. Add each one to `.env.example` with
a comment, and list it here.

| Variable | Description |
|---|---|
| _none yet_ | |

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
