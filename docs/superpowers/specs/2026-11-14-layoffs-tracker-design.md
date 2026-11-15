# AI Layoffs Tracker — design

Date: 2026-11-14. Status: approved 2026-11-14, with Tanner's additions: "Use the new public repo. Use onigiri to track the work. Use the project_template as the design for the file structure."

## Why

Tanner's working paper ("Layoffs, AI and New Businesses") measured how often US companies cite AI when they announce layoffs. The numbers stop at September 2026. Two goals:

1. **Keep the numbers current** on his portfolio site, refreshed monthly with no manual work.
2. **Gain real, demonstrable experience with Airflow and Kubernetes.** Airbnb's ML Engineer posting (and many like it) asks for 3+ of TensorFlow, PyTorch, Kubernetes, Spark, Airflow, Kafka, Hive. His resume shows PyTorch and "Kubernetes a bit". A working, public pipeline he built and runs closes part of that gap honestly.

Success: a public repo plus a portfolio page that shows numbers through last month, updated by an Airflow job running on Kubernetes on his own server, and that he can explain end to end in an interview.

## Goals

- A monthly pipeline that finds new SEC 8-K Item 2.05 layoff filings, labels whether AI is cited as a reason (Jev only), pulls the WARN layoff notices, computes the tracker metrics, and publishes them.
- A portfolio page that shows those metrics with the date the data runs through.
- Airflow on single-node Kubernetes (k3s) on `storageguy`, deployed with the official Helm chart, always on in a slim setup.

## Non-goals

- Re-running the paper's regressions or the business-applications analysis. The tracker is descriptive only.
- The Claude Haiku second pass (Tanner removed it) and the human blind audit (can't run monthly).
- Spark, Kafka, Hive. Data is far too small for Spark to be credible here.
- Multi-node clusters, monitoring stacks, alerting services, a custom dashboard UI.

## Anti-goals (failure even if it "works")

- The page shows a different AI share than the paper without saying why.
- Airflow's dashboard or any secret is reachable from the public internet, or a secret lands in git.
- The tracker starves `storageguy` of memory for whatever else it does.
- The page silently goes stale.

## Constraints

| Item | Fact |
|---|---|
| Server | `storageguy` (SSH alias), Ubuntu 26.04, 4 cores, 7.1 GB RAM (5.6 GB free), 72 GB disk free, Docker 29 + Compose installed, no Kubernetes yet, up 15 days, reached over Tailscale |
| Root access | Installing k3s needs sudo; Tanner runs that command himself |
| Secrets | `TYPESAFE_API_KEY` (Jev), `SEC_USER_AGENT` (name + contact email; SEC returns 403 without it), a GitHub write credential. Tanner creates them; the builder never handles their values |
| SEC | EDGAR full-text search, 10 requests/second limit, User-Agent with contact required |
| Jev | Pinned model `jev-1.13.0` in `research/jev_api.py` |
| Portfolio | `tanner-k/portfolio`, public, Astro, GitHub Pages via `.github/workflows/deploy.yml` (on push to main + manual) |
| Paper branch | The research page that links the paper is on branch `claude/anthropic-fellows-paper-data-77139d`, 30 commits ahead of `main`, not merged. The tracker page is built on top of that branch, and both reach `main` in one PR that Tanner approves (my call) |

## Approaches considered

| Decision | Options | Picked (by Tanner) | Why |
|---|---|---|---|
| Which project | Airline data on Spark; **layoffs tracker**; search on public listings data | Layoffs tracker | Builds on his own paper and gives a live portfolio piece |
| Where it runs | Local Kubernetes on his Mac; cloud server ($5–12/mo); GitHub's scheduler; **his Linux server** | `storageguy` | Already always on, free, he controls it |
| Packaging | Docker Compose; **Kubernetes (k3s) + Helm** | k3s | Kubernetes is on Airbnb's list; plain Docker isn't. The tracker still ships as a Docker image |
| Uptime | **Always on, slim**; start only for each run (timer) | Always on, slim | Airflow does the scheduling itself, which is the skill being shown |
| Labels for new filings | Jev + Haiku second pass; **Jev only** | Jev only | Tanner's call |
| Labels for history (2019 – Sep 2026) | Jev only for every year; **the paper's audited labels** | Paper's audited labels | The paper is final and won't change; the tracker should agree with it for the months it covers |

## How it works

```
storageguy (k3s, single node)
  Airflow (Helm chart, LocalExecutor): scheduler, api-server, dag-processor, Postgres
    DAG layoffs_monthly  (3rd of each month, catch-up on)
      fetch_sec  ->  label_jev  ->  fetch_warn  ->  build_metrics  ->  publish
      each task = KubernetesPodOperator running ghcr.io/tanner-k/layoffs-tracker
      shared PersistentVolume (k3s local-path): raw SEC cache, working files
                                   |
                                   v  git push (deploy key)
github.com/tanner-k/layoffs-tracker   data/filings.parquet, data/latest.json
                                   |
                                   v  fetched at build time
tanner-k.github.io/portfolio/research/layoffs-tracker   (Pages rebuild weekly)
```

### Repo (my call)

A new **public** repo `tanner-k/layoffs-tracker` (Tanner's call): a reviewer can read it on its own; public is needed for the raw JSON fetch and a public image on GHCR.

**File structure (Tanner's call): scaffolded from `project_template`'s Python profile** with its own `scripts/scaffold.py` (the `feat/profiles` branch, checked out at `~/coding/project_template-profiles`; it is the only version with a Python profile). Answers: name `layoffs-tracker`, package `layoffs_tracker`, Onigiri key `LT`, license MIT, tutorials off; the repo is created `--public` instead of the skill's default `--private`. That gives `README.md`, `CLAUDE.md` + `AGENTS.md` symlink, `.onigiri.toml`, `CHANGELOG.md`, `pyproject.toml` (ruff, mypy strict, pytest, 80% coverage), `.pre-commit-config.yaml`, `scripts/check.sh` (the single gate CI runs), `.github/` (CI, dependabot, PR template), `src/layoffs_tracker/`, `tests/`, `docs/` (context, architecture, testing, `decisions/0001-stack.md`). Added on top, each with a `context.md` like the template's folders:

- `src/layoffs_tracker/`: one CLI with a subcommand per DAG task.
- `dags/layoffs_monthly.py`.
- `infra/`: Helm values and Kubernetes manifests (namespace, PVC, RBAC for pod launching), k3s install notes.
- `data/`: published outputs (below) and the seed.
- `Dockerfile`; a second workflow builds and pushes the image to GHCR on `main` (data-only commits skip CI via path filters).
- ADRs in `docs/decisions/` for k3s + Helm, LocalExecutor + KubernetesPodOperator, and the label-method switch.

**Work is tracked in Onigiri** (Tanner's call) under project `LT`: project → epic → story → task, each task with a test target, claimed before work and closed only with its test green.

### Code reuse

Port, don't rewrite, from `portfolio/research/`:

| From | For |
|---|---|
| `build_sec_layoffs.py` (`search_month`, `item_205_section`, `find_headcount`, `extract`, rate-limited cached fetcher) | `fetch_sec` |
| `verify_sec_jev2.py`, `jev_api.py`, `jev_spans.py`, `jev_questions.py`, `jev_assemble.py` | `label_jev` (keep the self-tests and fake transport) |
| `build_warn.py` | `fetch_warn` |

Changes: take a month range instead of fixed constants; drop everything Haiku and blind-audit.

### Data

- **History is seeded, not re-labeled.** Jan 2019 – Sep 2026 uses the **paper's audited labels** (`ai_reason_audited`, `ai_subtype_audited` in `sec_8k_ai_reason_2019.parquet`), with affected employees from `sec_8k_layoffs.parquet`. These match the paper exactly. No API spend, and no Haiku runs anywhere in the tracker.
- **New months (Oct 2026 on) are labeled live by Jev only.** The method changes on Oct 1, 2026, which is also a quarter boundary, so no quarter mixes the two.
- `data/filings.parquet`: one row per original 8-K Item 2.05 filing (amendments kept but flagged and excluded from metrics, as in the paper): accession number, company, file date, affected employees, `ai_reason` (true / false / null = unresolved, live months only), `ai_role`, label method (`paper_audited` or `jev`), Jev model version, labeled-at date.
- Each run rescans the **last 3 months** (filings can be indexed late) and labels only accession numbers it hasn't seen. Runs are idempotent.
- WARN: re-derived each run from the latest commit of Kadoa's open `layoffs-tracker` main (a tracker has to follow new data; `build_warn.py` pins `7a4a3b4` only because the paper is frozen). The SHA is recorded in `latest.json`, and git history of `data/` shows any change to past months. (My call.)
- WARN states: the paper's `balanced_2019` rule (states covered every month since Jan 2019; 25 states today), applied the same way each run, so coverage changes don't fake a trend. Notices are always counted; workers affected sums known counts, and the share of notices missing a count is published next to it. (My call.)
- Jev spend cap: at most 100 API calls per run (a normal month should need far fewer). Over the cap, the run fails before `publish`, so nothing half-labeled ships. (My call.)

### Metrics in `data/latest.json`

- `schema_version`, `generated_at`, `data_through` (last complete month), `sources` (Jev model, Kadoa SHA, SEC search terms).
- SEC layoff filings per month, Jan 2019 on.
- AI-cited filings **per quarter and per year**: `total`, `ai_true`, `unresolved`, `share_low = ai_true / total`, `share_high = (ai_true + unresolved) / total`. Quarters, not months: ~15–30 filings a month is too noisy.
- WARN notices and workers affected per month, national, Jan 2019 on (if Kadoa lacks a workers field, notices only).

**Through Sep 2026** the page shows the paper's numbers: one line, no band (`unresolved = 0`, so `share_low = share_high`). 2026 Jan–Sep reads 17% (26 of 153), as in the paper.

**From Oct 2026**, Jev leaves some filings unresolved. They show as a gray band: "X%, up to Y% if every unresolved filing cites AI."

**The method switch is marked on every chart** (my call): a vertical line at Oct 2026 labeled "Labels: paper's audited method → Jev only", plus one sentence under the chart. Why it matters: on the paper's own 2019 – Sep 2026 filings, Jev alone found fewer AI-cited filings than the audited labels. The lower edge of the band will likely read low; the band's upper edge is the fair comparison.

| Year | Filings | Paper (audited) | Jev alone, same filings | Jev unresolved |
|---|---|---|---|---|
| 2024 | 335 | 7 | 2 | 20 |
| 2025 | 255 | 10 | 7 | 12 |
| 2026 Jan–Sep | 153 | 26 | 17 | 14 |

### The Airflow setup (slim, always on)

- k3s single node. Tanner runs the install command (sudo). k3s uses its own containerd, alongside the existing Docker.
- Official `apache-airflow` Helm chart, Airflow 3.x, **LocalExecutor**. On: scheduler, api-server, dag-processor, Postgres. Off: triggerer, workers, Redis, Flower, StatsD, PgBouncer.
- Every component gets memory requests and limits. Target: **under 3 GB total including k3s**; measure after deploy and record the real numbers in the README.
- DAGs arrive by git-sync from the tracker repo.
- Schedule: 3rd of each month 09:00 America/Denver, for the previous month. `catchup=True` from Oct 2026, `max_active_runs=1`, 2 retries per task.
- Task image `ghcr.io/tanner-k/layoffs-tracker:main`, always pulled.
- Dashboard: ClusterIP only. Reached by `kubectl port-forward` over SSH (or bound to the Tailscale address). Never exposed publicly. Tanner sets the admin password.

### Secrets

Kubernetes Secrets in the tracker namespace, created by Tanner with commands the plan gives him (reading from his own `.env`, nothing echoed). `publish` pushes with a **deploy key** generated on the server: write access to `layoffs-tracker` only, no expiry. Tanner adds its public half in the repo settings.

### Portfolio page

- New page `src/pages/research/layoffs-tracker.astro`, linked from the research page.
- At build time, fetch `latest.json` from the tracker repo, check `schema_version`, and **fail the build** if the fetch or check fails (Pages keeps the last good deploy).
- Charts drawn as static inline SVG at build time, matching the site's existing style. No chart library.
- Shows: "Data through <month>, updated <date>", a stale note if `data_through` is more than 45 days old, the one-line Jev-only vs paper note, links to the paper and the repo.
- `deploy.yml` gains a weekly `schedule` so late monthly runs show up within a week.

### Testing

- Unit tests: quarter bucketing, share low/high math, amendment exclusion, 3-month rescan window, "only new accession numbers" logic.
- Seed check: AI-cited original filings per year from the seed equal the paper's audited counts (2019–2026: 1, 0, 1, 2, 3, 7, 10, 26; 2026 = 26 of 153).
- Ported Jev self-tests with the fake transport.
- DAG import test (no import errors, schedule and task order as designed).
- **Port check:** fetch Jul–Sep 2026 live and compare accession numbers with the paper's `sec_8k_layoffs.parquet`; differences explained or fixed before go-live.
- 80% coverage on `tracker/`. CI runs it all.

## Decided by Tanner

- Layoffs tracker on the portfolio, updated on a cadence.
- No Haiku second pass.
- Runs on `storageguy`, k3s + Airflow, Docker image.
- Always on, slim.
- History uses the paper's audited labels through Sep 2026; Jev only after ("I'm not going to change the paper").

## My calls (flagged for review)

Public `layoffs-tracker` repo; seed history from the paper's Jev fields; quarterly shares with an unresolved band; monthly on the 3rd with a 3-month rescan; LocalExecutor + KubernetesPodOperator; git-sync; deploy key; build-time SVG charts; weekly site rebuild; no alerting beyond the page's stale note and Airflow's own UI; Kadoa main tracked with SHA recorded; WARN on the 25 `balanced_2019` states; 100-call Jev cap per run; tracker page built on the paper branch, one PR to `main`.; method-switch marker on every chart.

## Left to the builder

Exact chart and Helm versions, memory numbers per component, CLI names, file layout inside `tracker/`, test structure, chart styling within the site's look.

## Resume line (only once it runs)

"Built a monthly Airflow pipeline on single-node Kubernetes (k3s, Helm, KubernetesPodOperator, GHCR, GitHub Actions) that tracks AI-cited layoffs in SEC filings and publishes to my portfolio."
