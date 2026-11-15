# AI Layoffs Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public `layoffs-tracker` repo whose monthly Airflow job, running on k3s on `storageguy`, labels new SEC layoff filings with Jev, refreshes the WARN series, and publishes `data/latest.json`, which a new portfolio page renders.

**Architecture:** A uv Python package (`layoffs_tracker`) ports the paper's SEC, Jev and WARN scripts into one CLI with a subcommand per pipeline step. Airflow (official Helm chart, LocalExecutor) runs each step as a KubernetesPodOperator pod from a GHCR image, sharing a PersistentVolume. The `publish` step pushes data to the repo with a deploy key; the Astro portfolio fetches `latest.json` at build time.

**Tech Stack:** Python 3.12, uv, polars, ruff, mypy (strict), pytest; Apache Airflow 3.x + `apache-airflow-providers-cncf-kubernetes`; k3s, Helm, GHCR, GitHub Actions; Astro (portfolio).

**Spec:** `docs/superpowers/specs/2026-11-14-layoffs-tracker-design.md` (this repo).

## Global Constraints

- Repo `tanner-k/layoffs-tracker`, **public**, scaffolded by `~/coding/project_template-profiles/scripts/scaffold.py --profile python` (branch `feat/profiles`). Name `layoffs-tracker`, package `layoffs_tracker`, Onigiri key `LT`, MIT, tutorials off. Never hand-edit placeholders; fix the template, not the project.
- The local gate is `scripts/check.sh` (ruff, ruff format, mypy strict, pytest with `--cov-fail-under=80`). Every task ends with it green. CI runs the same script.
- Branches: work on `feature/*`, PR into `dev`; `dev` → `main` is a release. GitHub operations use the `tanner-k` account.
- Onigiri tracks every task: claim before code (`oni claim KEY --session <session> --expected-revision N`), pass `--actor agent:<name>` and `--json` on every mutation, close with `oni test KEY` green then `oni set KEY --status done`. Use `oni test KEY --not-applicable --reason …` only where this plan says so.
- Label method: filings with `file_date <= 2026-09-30` are frozen paper rows (`label_method = "paper_audited"`); never re-fetched, re-labeled or added. Filings from `2026-10-01` on are `label_method = "jev"`. `METHOD_SWITCH = date(2026, 10, 1)`.
- No Haiku and no other LLM anywhere in the tracker. Jev model pin `jev-1.13.0`.
- Secrets: `TYPESAFE_API_KEY`, `SEC_USER_AGENT`, the deploy key. Read from env only; never printed, logged, written to disk, or committed. Tanner creates the Kubernetes Secrets himself; the implementer never handles the values.
- SEC: at most 5 requests/second (`MIN_INTERVAL_S = 0.2`), User-Agent from `SEC_USER_AGENT` (required, no fallback).
- Jev: at most `MAX_CALLS = 100` live calls per run; exceeding it fails the run before `publish`.
- WARN states: notice in every calendar year from 2019 through the **last complete year**, and undated share `<= 0.10` (the paper's rule).
- Schedule: cron `0 9 3 * *`, timezone `America/Denver`, `start_date` 2026-11-01, `catchup=True`, `max_active_runs=1`, `retries=2`. Each run processes the calendar month before its logical date.
- Memory: every Airflow component has requests and limits; total use including k3s stays under 3 GB on `storageguy`.
- Airflow dashboard is ClusterIP only; never exposed beyond `kubectl port-forward` over SSH or the Tailscale address.

## Review Focus

1. **A rescan window that reaches into paper months** (run for Oct 2026 rescans Aug–Oct): Aug/Sep filings must be ignored, never labeled or added. Test in Task 2: `test_rescan_window_clips_at_method_switch`.
2. **Re-running a month** (retry, catch-up overlap): no duplicate accession numbers and no empty commit. Tests in Task 7: `test_merge_is_idempotent`, `test_publish_twice_makes_one_commit`.
3. **A month with no new filings:** zero Jev calls, metrics still advance `data_through`. Test in Task 4: `test_label_new_with_nothing_new_makes_no_calls`.
4. **SEC refuses or truncates** (403, 429 after retries, `relation != "eq"`): the step fails loudly and nothing publishes. Test in Task 2: `test_search_month_raises_on_truncated_result`.
5. **January drop in WARN states:** early in a year, no state has a notice yet for the new year; the balance rule must use the last complete year. Test in Task 5: `test_balanced_states_uses_last_complete_year`.

---

### Task 0: Seed Onigiri and record decisions

**Files:** none (Onigiri store only).

- [ ] **Step 1:** `oni recall "layoffs tracker" --project LT --json` (expect not found), then create the tree. Record each returned key.

```bash
oni add --json --actor agent:<name> --kind project --slug LT --title "AI Layoffs Tracker" \
  --body "Monthly Airflow-on-k3s pipeline that keeps the layoffs paper's AI-share numbers current on the portfolio. Spec: docs/superpowers/specs/2026-11-14-layoffs-tracker-design.md"
```

| Epic | Story | Tasks (plan task → test target) |
|---|---|---|
| Pipeline code | Repo scaffold | T1 → `tests/test_core.py::test_greet_trims_name` |
| Pipeline code | Data steps | T2 → `tests/test_sec.py::test_rescan_window_clips_at_method_switch`; T3 → `tests/test_seed.py::test_seed_matches_paper_counts`; T4 → `tests/test_label.py::test_cap_exceeded_raises`; T5 → `tests/test_warn.py::test_balanced_states_uses_last_complete_year`; T6 → `tests/test_metrics.py::test_quarter_shares`; T7 → `tests/test_publish.py::test_publish_twice_makes_one_commit` |
| Run on storageguy | Image and DAG | T8 → `tests/test_cli.py::test_help_lists_subcommands`; T9 → `tests/test_dag.py::test_dag_shape` |
| Run on storageguy | Cluster | T10 → `tests/test_infra.py::test_values_are_slim`; T11 → `tests/test_published.py::test_latest_json_is_valid` |
| Portfolio page | Tracker page | T12, T13 → not applicable (portfolio has no test runner; verified by build and preview) |

- [ ] **Step 2:** Record Tanner's decisions (`oni memory record --project LT --kind decision --session e5dcbea7-5f25-4b17-97c7-efb2ad627738 --excerpt "<his words>" --title …`), one each:
  - "Let's build a layoffs tracker that we could add to the portfolio and then let it also update the metrics on some cadence"
  - "Let's keep out the Claude Haiku second pass."
  - "Let's do A." (k3s on storageguy) and "Always on with slim setup"
  - "I'm thinking B because I'm not going to change the paper" (paper's audited labels through Sep 2026)
  - "Use the new public repo. Use onigiri to track the work. Use the project_template as the design for the file structure."
- [ ] **Step 3:** `oni tree LT --json` shows 3 epics, 5 stories, 13 tasks with targets.

### Task 1: Scaffold the repo

**Files:** the scaffold output at `~/coding/layoffs-tracker/`; create `dags/context.md`, `infra/context.md`, `data/context.md`; copy spec and plan to `docs/superpowers/`.

- [ ] **Step 1:** Preflight per `skill/PROJECT_INIT.md` Phase 0 (`python3`, `uv`, `git`, `oni`, `gh auth status` shows `tanner-k`).
- [ ] **Step 2:** Write answers to a temp file (never inside the dest): `project_name` `layoffs-tracker`, `description` "Monthly tracker of AI-cited layoffs in SEC filings and WARN notices", `github_repo` `tanner-k/layoffs-tracker`, `what`/`who`/`value_prop`/`features` from the spec's Why and Goals (no double quotes, backslashes, backticks), `license` `MIT`, `tutorials` false, `project_key` `LT`.
- [ ] **Step 3:** `python3 scripts/scaffold.py --profile python --answers <tmp> --dest ~/coding/layoffs-tracker` exits 0.
- [ ] **Step 4:** In the dest: `git init -b main && uv sync && uv run pre-commit install && scripts/check.sh` → green.
- [ ] **Step 5:** Add the three `context.md` files (scope / not in scope / notes, in the template's style) and the spec + plan. Add `CLAUDE.md` rows for `dags/`, `infra/`, `data/`. Fill `docs/decisions/0001-stack.md` and add ADRs `0002-k3s-and-helm.md`, `0003-localexecutor-and-pod-tasks.md`, `0004-label-method-switch.md`, each taking its decision and reasons from the spec's Approaches table. `scripts/check.sh` green.
- [ ] **Step 6:** Commit `chore: scaffold from project template`. Then `gh repo create tanner-k/layoffs-tracker --public --source . --push` and `git checkout -b dev && git push -u origin dev`.
- [ ] **Step 7:** `oni repo add ~/coding/layoffs-tracker`; `oni set LT --repo ~/coding/layoffs-tracker`; `oni test LT-<T1>` green.

### Task 2: SEC fetch

**Files:** Create `src/layoffs_tracker/months.py`, `src/layoffs_tracker/sec.py`; Test `tests/test_sec.py`.

**Interfaces:**
- Produces: `METHOD_SWITCH: date`; `previous_month(d: date) -> date` (first day of the month before `d`); `rescan_window(run_month: date, n: int = 3) -> list[tuple[date, date]]` (first/last day of each month, oldest first, months before `METHOD_SWITCH` dropped); `Listing` (frozen dataclass, fields as in `research/build_sec_layoffs.py:132`); `Fetcher(cache_dir: Path, user_agent: str)` with `.get(url: str, cache: Path) -> str | None`; `list_filings(fetch: Fetcher, months: Sequence[tuple[date, date]]) -> list[Listing]`; `extract(fetch: Fetcher, f: Listing) -> dict[str, object]` (keys `headcount`, `headcount_pct`, `headcount_source`, `ai_mentioned`, `snippet`); `fetch_month_filings(fetch: Fetcher, months: Sequence[tuple[date, date]]) -> pl.DataFrame` (the `build()` schema at `build_sec_layoffs.py:406`).

Port from `research/build_sec_layoffs.py`: `Fetcher`, `search_month`, `to_listing`, `list_filings`, the HTML-to-text and Item 2.05 section code, `find_headcount`, `extract`, `SELF_CHECKS`. Remove the fixed month constants, the `"Mozilla/5.0"` fallback, and the module-level output paths.

- [ ] **Step 1: Write the failing tests**

```python
def test_rescan_window_clips_at_method_switch():
    assert rescan_window(date(2026, 10, 1)) == [(date(2026, 10, 1), date(2026, 10, 31))]
    assert rescan_window(date(2026, 12, 1)) == [
        (date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 31))]

def test_previous_month():
    assert previous_month(date(2027, 1, 3)) == date(2026, 12, 1)

@pytest.mark.parametrize(("sentence", "expected"), SELF_CHECKS)
def test_find_headcount(sentence, expected):
    assert find_headcount(sentence)[:2] == expected

def test_search_month_raises_on_truncated_result(fake_fetcher):  # fake returns relation "gte"
    with pytest.raises(RuntimeError, match="truncated"):
        search_month(fake_fetcher, "item205", '"Item 2.05"', date(2026, 10, 1), date(2026, 10, 31))

def test_list_filings_keeps_one_main_doc_per_item_205_filing(fake_fetcher): ...
    # two hits with the same adsh, one without "2.05" in items → one Listing

def test_fetcher_requires_user_agent():
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        Fetcher(tmp_path, user_agent="")
```

- [ ] **Step 2:** `uv run pytest tests/test_sec.py -v` → FAIL (import error first, then assertion failures once stubs exist).
- [ ] **Step 3:** Implement the interfaces above. Fakes read canned JSON from `tests/fixtures/sec/`; no test touches the network.
- [ ] **Step 4:** `uv run pytest tests/test_sec.py -v` → PASS; `scripts/check.sh` green.
- [ ] **Step 5: Port check (live, local).** With `SEC_USER_AGENT` exported from Tanner's `.env` (not printed), list Jul–Sep 2026 and compare accession numbers with `data/seed/sec_8k_layoffs.parquet` (from Task 3; run after it). Expected: identical sets. Record the result in the PR; any difference is explained or fixed first.
- [ ] **Step 6:** Commit `feat: port SEC filing search and extraction`.

### Task 3: Seed the paper's filings

**Files:** Create `src/layoffs_tracker/schema.py`, `src/layoffs_tracker/seed.py`, `data/seed/` (copies of `research/data/sec_8k_ai_reason_2019.parquet` and `research/data/sec_8k_layoffs.parquet`), `data/filings.parquet`; Test `tests/test_seed.py`.

**Interfaces:**
- Produces: `FILINGS_SCHEMA: dict[str, pl.DataType]` = `adsh` Utf8, `cik` Utf8, `company` Utf8, `file_date` Date, `is_amendment` Boolean, `headcount` Int64, `ai_reason` Boolean (null = unresolved), `ai_role` Utf8, `label_method` Utf8, `jev_model` Utf8, `labeled_at` Date. `build_seed(ai_reason_path: Path, layoffs_path: Path) -> pl.DataFrame`.
- Seed rules: rows of `sec_8k_ai_reason_2019.parquet` (2,217, file dates 2019-01-02 to 2026-09-30); `ai_reason = coalesce(ai_reason_audited, False)` (12 audited nulls are filings without text; the paper counts them as not AI-cited); `ai_role = ai_subtype_audited`; `headcount` joined from the layoffs file on `adsh`; `label_method = "paper_audited"`; `jev_model = null`; `labeled_at = 2026-10-16`.

- [ ] **Step 1: Write the failing tests**

```python
def test_seed_matches_paper_counts():
    df = pl.read_parquet("data/filings.parquet").filter(~pl.col("is_amendment"))
    by_year = df.group_by(pl.col("file_date").dt.year()).agg(pl.col("ai_reason").sum()).sort("file_date")
    assert by_year["ai_reason"].to_list() == [1, 0, 1, 2, 3, 7, 10, 26]
    assert df.filter(pl.col("file_date").dt.year() == 2026).height == 153

def test_seed_is_all_paper_rows():
    df = pl.read_parquet("data/filings.parquet")
    assert df.height == 2217 and df["label_method"].unique().to_list() == ["paper_audited"]
    assert df["ai_reason"].null_count() == 0 and df["file_date"].max() == date(2026, 9, 30)
    assert df.schema == FILINGS_SCHEMA and df["adsh"].is_unique().all()
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement `build_seed`; add `layoffs-tracker seed` (Task 7 wires the CLI; until then a `__main__` in `seed.py`) and write `data/filings.parquet`. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: seed history from the paper's audited labels`.

### Task 4: Jev labeling

**Files:** Create `src/layoffs_tracker/jev/{__init__,api,spans,questions,assemble,label}.py`; Test `tests/test_label.py`, `tests/test_jev_selftest.py`.

**Interfaces:**
- Consumes: `Listing`, `Fetcher`, `extract` (Task 2); `FILINGS_SCHEMA` (Task 3).
- Produces: `class CallCapExceeded(RuntimeError)`; `MAX_CALLS = 100`; `label_new(new: pl.DataFrame, fetch: Fetcher, transport: Transport, cache_dir: Path, *, today: date, max_calls: int = MAX_CALLS) -> pl.DataFrame` returning `FILINGS_SCHEMA` rows with `label_method = "jev"`, `ai_reason = jev_ai_reason`, `ai_role = jev_ai_role`, `jev_model = "jev-1.13.0"`, `labeled_at = today`. `http_transport(api_key: str) -> Transport`; `load_api_key() -> str` raising `ValueError("TYPESAFE_API_KEY is not set")`.

Port `research/jev_api.py`, `jev_spans.py`, `jev_questions.py`, `jev_assemble.py` as modules; port `build_view`, `make_views`, `collect`, `stage2_units`, `run_pipeline` from `verify_sec_jev2.py` into `label.py`. Drop the `.env` file loading (env only), the CLI, and the stats printing. `label_new` raises `CallCapExceeded` if `run_batches` skipped any batch because of the cap (count batches with a `None` result while calls were exhausted).

- [ ] **Step 1: Write the failing tests** (fake transport ported from the `--self-test` path)

```python
def test_label_new_with_nothing_new_makes_no_calls(fake_transport):
    out = label_new(empty_new, fetch, fake_transport, tmp_path, today=date(2026, 11, 3))
    assert out.height == 0 and fake_transport.calls == 0

def test_label_new_maps_jev_answers(fake_transport):  # canned answers: one True, one False, one unresolved
    out = label_new(three_new, fetch, fake_transport, tmp_path, today=date(2026, 11, 3))
    assert out["ai_reason"].to_list() == [True, False, None]
    assert set(out["label_method"]) == {"jev"} and out.schema == FILINGS_SCHEMA

def test_cap_exceeded_raises(fake_transport):
    with pytest.raises(CallCapExceeded):
        label_new(three_new, fetch, fake_transport, tmp_path, max_calls=0, today=date(2026, 11, 3))

def test_missing_api_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        load_api_key()
```

Plus `tests/test_jev_selftest.py`: the original `verify_sec_jev2.py --self-test` assertions as tests.

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. Ported code must pass mypy strict without blanket ignores. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: port Jev labeling without the Haiku pass`.

### Task 5: WARN series

**Files:** Create `src/layoffs_tracker/warn.py`; Test `tests/test_warn.py`, fixture `tests/fixtures/warn/notices.json` (a few hand-made notices).

**Interfaces:**
- Produces: `latest_sha() -> str`; `download(sha: str, dest: Path) -> Path` (`notices.json` from `kadoa-org/layoffs-tracker` at `sha`); `load_notices(path: Path) -> pl.DataFrame` (raises `ValueError` naming any missing column); `balanced_states(notices: pl.DataFrame, first_year: int, data_through: date) -> list[str]`; `national_month(notices: pl.DataFrame, states: Sequence[str], data_through: date) -> pl.DataFrame` with columns `month` (Date), `notices`, `workers`, `notices_missing_workers` (Int64), Jan 2019 through `data_through`, amendments excluded, missing months as 0.

Port `load_notices`, `dedupe`, `counted`, `covered_years`, `undated_share`, and the count logic of `build_state_month` from `research/build_warn.py`. Last complete year = `data_through.year` if `data_through.month == 12`, else `data_through.year - 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_balanced_states_uses_last_complete_year():
    # state AA has notices 2019–2026, none yet in 2027; BB is missing 2021
    assert balanced_states(notices, 2019, data_through=date(2027, 1, 1)) == ["AA"]

def test_balanced_states_drops_mostly_undated_state(): ...  # undated share 0.2 → excluded

def test_national_month_sums_known_workers_and_counts_missing():
    out = national_month(notices, ["AA"], data_through=date(2026, 10, 1))
    row = out.filter(pl.col("month") == date(2026, 10, 1)).row(0, named=True)
    assert (row["notices"], row["workers"], row["notices_missing_workers"]) == (3, 150, 1)

def test_load_notices_names_missing_column(tmp_path):
    with pytest.raises(ValueError, match="num_affected"): ...
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: port WARN national series`.

### Task 6: Metrics and `latest.json`

**Files:** Create `src/layoffs_tracker/metrics.py`; Test `tests/test_metrics.py`.

**Interfaces:**
- Consumes: `FILINGS_SCHEMA` frame; `national_month` frame.
- Produces: `build_latest(filings: pl.DataFrame, warn: pl.DataFrame, sources: Mapping[str, object], data_through: date, generated_at: datetime) -> dict[str, object]`, exactly:

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-03T16:05:00Z",
  "data_through": "2026-10",
  "method_switch": "2026-10",
  "sources": {"jev_model": "jev-1.13.0", "kadoa_sha": "…", "sec_phrases": ["\"Item 2.05\"", "\"Exit or Disposal Activities\""], "warn_states": ["AZ", "…"]},
  "sec_monthly": [{"month": "2019-01", "filings": 21}],
  "ai_quarterly": [{"quarter": "2026Q4", "method": "jev", "total": 40, "ai_true": 5, "unresolved": 3, "share_low": 0.125, "share_high": 0.2}],
  "ai_yearly": [{"year": 2026, "method": "paper_audited", "total": 153, "ai_true": 26, "unresolved": 0, "share_low": 0.1699, "share_high": 0.1699}],
  "warn_monthly": [{"month": "2019-01", "notices": 120, "workers": 9000, "notices_missing_workers": 10}]
}
```

Rules: originals only (`is_amendment` false); `ai_yearly` groups by year **and** method, so 2026 has a `paper_audited` row (Jan–Sep) and a `jev` row (Oct–Dec) and never mixes them; shares rounded to 4 places; `total == 0` gives `null` shares; series end at `data_through`.

- [ ] **Step 1: Write the failing tests**

```python
def test_quarter_shares():
    q = quarter(out, "2026Q4")
    assert (q["total"], q["ai_true"], q["unresolved"]) == (10, 1, 2)
    assert (q["share_low"], q["share_high"]) == (0.1, 0.3)

def test_paper_quarters_have_no_band():
    assert all(q["unresolved"] == 0 for q in out["ai_quarterly"] if q["method"] == "paper_audited")

def test_2026_yearly_splits_by_method():
    rows = [y for y in out["ai_yearly"] if y["year"] == 2026]
    assert {r["method"] for r in rows} == {"paper_audited", "jev"}

def test_amendments_excluded_and_empty_quarter_has_null_share(): ...
def test_series_end_at_data_through(): ...
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: build latest.json metrics`.

### Task 7: CLI, merge and publish

**Files:** Create `src/layoffs_tracker/cli.py`, `src/layoffs_tracker/publish.py`; Modify `pyproject.toml` (`[project.scripts] layoffs-tracker = "layoffs_tracker.cli:main"`); remove the template's `core.py` and `tests/test_core.py` (move the T1 Onigiri target to `tests/test_cli.py::test_help_lists_subcommands`); Test `tests/test_cli.py`, `tests/test_publish.py`.

**Interfaces:**
- Produces: CLI `layoffs-tracker <cmd> --work DIR`, commands in DAG order: `checkout --repo-url URL` (clone or pull into `DIR/repo`), `fetch-sec --run-month YYYY-MM` (writes `DIR/new_filings.parquet`: rescan-window filings whose `adsh` is not in `repo/data/filings.parquet`), `label [--max-calls 100]` (writes `DIR/labeled.parquet`), `fetch-warn` (writes `DIR/warn_month.parquet`, `DIR/kadoa_sha.txt`), `build-metrics --run-month YYYY-MM` (merges into `repo/data/filings.parquet`, writes `repo/data/latest.json`), `publish` (commit `data: refresh through YYYY-MM` and push; no-op if unchanged), `seed`. `merge_filings(old: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame` (keep old row on duplicate `adsh`; drop any `new` row before `METHOD_SWITCH`). `publish(repo_dir: Path, message: str) -> bool` (True if pushed).
- Git auth comes from the environment (`GIT_SSH_COMMAND` with the mounted deploy key); the code never reads the key.

- [ ] **Step 1: Write the failing tests**

```python
def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit): main(["--help"])
    for cmd in ("checkout", "fetch-sec", "label", "fetch-warn", "build-metrics", "publish", "seed"):
        assert cmd in capsys.readouterr().out

def test_merge_is_idempotent():
    once = merge_filings(old, new); assert merge_filings(once, new).equals(once)

def test_merge_drops_paper_era_rows_from_new(): ...  # new row dated 2026-09-30 → not added

def test_publish_twice_makes_one_commit(tmp_path):  # real git against a local bare remote
    assert publish(clone, "data: refresh through 2026-10") is True
    assert publish(clone, "data: refresh through 2026-10") is False
    assert commit_count(bare) == 2  # initial + one data commit
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: CLI steps, idempotent merge and publish`.

### Task 8: Container image

**Files:** Create `Dockerfile`, `.dockerignore`, `.github/workflows/image.yml`; Modify `.github/workflows/ci.yml` (`paths-ignore: ["data/**"]`).

**Interfaces:**
- Produces: image `ghcr.io/tanner-k/layoffs-tracker:main`, entrypoint `layoffs-tracker`, runs as a non-root user, includes `git` and `openssh-client`.

- [ ] **Step 1:** `docker build -t layoffs-tracker:dev .` → succeeds; `docker run --rm layoffs-tracker:dev --help` lists the 7 subcommands.
- [ ] **Step 2:** `image.yml`: on push to `main` (`paths-ignore: ["data/**"]`) and manual dispatch; `permissions: packages: write, contents: read`; build and push with `GITHUB_TOKEN`; actions SHA-pinned like `ci.yml`.
- [ ] **Step 3:** After merge to `main`, the package is set **public** in GitHub package settings (Tanner does this, one click) so k3s pulls without credentials. Verify `docker pull ghcr.io/tanner-k/layoffs-tracker:main` from `storageguy`.
- [ ] **Step 4:** Commit `ci: build and publish the tracker image`.

### Task 9: The DAG

**Files:** Create `dags/layoffs_monthly.py`; Modify `pyproject.toml` (dev group adds `apache-airflow` 3.x and `apache-airflow-providers-cncf-kubernetes`, same versions as the Helm chart's image; if uv cannot resolve, use Airflow's published constraints file); Test `tests/test_dag.py`.

**Interfaces:**
- Consumes: CLI commands (Task 7), image (Task 8).
- Produces: DAG `layoffs_monthly`; tasks `checkout → fetch_sec → label → fetch_warn → build_metrics → publish`, each a `KubernetesPodOperator` in namespace `airflow`, image `ghcr.io/tanner-k/layoffs-tracker:main`, `image_pull_policy="Always"`, PVC `layoffs-work` at `/work`, env from Secret `layoffs-secrets`, Secret `layoffs-deploy-key` mounted read-only at `/keys` for `checkout` and `publish` (`GIT_SSH_COMMAND="ssh -i /keys/id_ed25519 -o UserKnownHostsFile=/keys/known_hosts"`). `--run-month` is the month before the logical date.

- [ ] **Step 1: Write the failing test**

```python
def test_dag_shape():
    dag = DagBag(dag_folder="dags", include_examples=False).get_dag("layoffs_monthly")
    assert dag is not None and dag.catchup and dag.max_active_runs == 1
    assert [t.task_id for t in dag.topological_sort()] == [
        "checkout", "fetch_sec", "label", "fetch_warn", "build_metrics", "publish"]
    assert all(t.retries == 2 for t in dag.tasks)

def test_dag_has_no_import_errors():
    assert DagBag(dag_folder="dags", include_examples=False).import_errors == {}
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; check green. **Step 5:** Commit `feat: monthly DAG of pod tasks`.

### Task 10: k3s and Airflow on storageguy

**Files:** Create `infra/README.md` (every command below, in order), `infra/airflow-values.yaml`, `infra/namespace.yaml`, `infra/pvc.yaml`; Test `tests/test_infra.py`.

**Interfaces:**
- Produces: namespace `airflow`; PVC `layoffs-work` (`local-path`, 5Gi); Helm release `airflow` from `apache-airflow/airflow` (pinned version); Secrets `layoffs-secrets`, `layoffs-deploy-key` (created by Tanner).

Values decisions: `executor: LocalExecutor`; triggerer, StatsD, Redis, Flower, PgBouncer and workers disabled; standalone DAG processor on; `dags.gitSync` from `https://github.com/tanner-k/layoffs-tracker.git`, branch `main`, subPath `dags`; every enabled component has `resources.requests` and `resources.limits`; API server service `ClusterIP`; chart's built-in Postgres (or a small `postgres:16` Deployment if the chart's default image is unavailable).

- [ ] **Step 1: Write the failing test**

```python
def test_values_are_slim():
    v = yaml.safe_load(Path("infra/airflow-values.yaml").read_text())
    assert v["executor"] == "LocalExecutor"
    for off in ("triggerer", "statsd", "redis", "flower", "pgbouncer"):
        assert v[off]["enabled"] is False
    for comp in ("scheduler", "apiServer", "dagProcessor"):
        assert {"requests", "limits"} <= v[comp]["resources"].keys()
    assert v["apiServer"]["service"]["type"] == "ClusterIP"
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Write the values and manifests. **Step 4:** Run → PASS; check green; commit `infra: k3s and slim Airflow values`.
- [ ] **Step 5: Tanner runs (sudo, secrets):** install k3s (`curl -sfL https://get.k3s.io | sh -`), copy `/etc/rancher/k3s/k3s.yaml` to `~/.kube/config` with his user as owner, install Helm; generate the deploy key on the server (`ssh-keygen -t ed25519 -N "" -f ~/layoffs-deploy-key`) and add the `.pub` in the repo's Deploy keys with write access; create Secrets: `grep -E '^(TYPESAFE_API_KEY|SEC_USER_AGENT)=' <his .env> | ssh storageguy 'kubectl create secret generic layoffs-secrets -n airflow --from-env-file=/dev/stdin'` and `kubectl create secret generic layoffs-deploy-key -n airflow --from-file=id_ed25519=$HOME/layoffs-deploy-key --from-file=known_hosts=<(ssh-keyscan github.com)`; set the Airflow admin password.
- [ ] **Step 6:** Apply namespace and PVC; `helm upgrade --install airflow apache-airflow/airflow -n airflow -f infra/airflow-values.yaml --version <pinned>`. Expected: `kubectl get pods -n airflow` all `Running`; DAG `layoffs_monthly` listed with no import errors.
- [ ] **Step 7:** Measure: `kubectl top pods -n airflow`, `free -h` before and after. Total under 3 GB including k3s, or tighten limits and re-measure. Record the numbers in `README.md`.
- [ ] **Step 8:** Confirm the dashboard is not reachable from outside the tailnet (`kubectl get svc -n airflow` shows `ClusterIP`; open it only via `kubectl port-forward` over SSH).

### Task 11: Go-live run

**Files:** Create `tests/test_published.py`; Modify `README.md`.

- [ ] **Step 1: Write the failing test** (validates whatever is committed in `data/`)

```python
def test_latest_json_is_valid():
    d = json.loads(Path("data/latest.json").read_text())
    assert d["schema_version"] == 1 and d["method_switch"] == "2026-10"
    assert d["data_through"] >= "2026-10"
    assert all(q["share_low"] <= q["share_high"] for q in d["ai_quarterly"] if q["total"])
```

- [ ] **Step 2:** Run → FAIL (no `latest.json` yet).
- [ ] **Step 3:** Unpause the DAG; catch-up runs the 2026-11-03 slot (data month Oct 2026). All 6 tasks green in Airflow; Jev calls under 100.
- [ ] **Step 4:** `git pull`; the deploy-key commit `data: refresh through 2026-10` is on `main`; Oct 2026 rows have `label_method = "jev"`; Jul–Sep 2026 rows unchanged. Run → PASS.
- [ ] **Step 5:** README: what it is, the architecture sketch, measured memory, how to run each step locally, how to read `latest.json`. Commit `docs: README with measured footprint`.

### Task 12: Portfolio tracker page

**Repo:** `tanner-k/portfolio`, branch `feature/layoffs-tracker` cut from `claude/anthropic-fellows-paper-data-77139d` (the paper branch).

**Files:** Create `src/lib/tracker.ts`, `src/components/TrackerChart.astro`, `src/pages/research/layoffs-tracker.astro`; Modify `src/pages/research.astro` (link card), `.github/workflows/deploy.yml` (add `schedule: - cron: "0 16 * * 1"`).

**Interfaces:**
- Produces: `loadTracker(): Promise<Tracker>` fetching `https://raw.githubusercontent.com/tanner-k/layoffs-tracker/main/data/latest.json`, throwing (fails the build) on fetch error or `schema_version !== 1`; `Tracker` type mirrors the Task 6 JSON. `TrackerChart` props: `points`, optional `band` (low/high), `switchAt: "2026-10"`; renders inline SVG, no chart library, colors from the site's existing CSS.

Page content: headline from the latest quarter ("X%, up to Y% if every unresolved filing cites AI" for `jev` quarters); three charts (AI-cited share by quarter with band, SEC layoff filings by month, WARN workers by month with the missing-count share); a vertical line at Oct 2026 labeled "Labels: paper's audited method → Jev only"; under it: "Through September 2026 these are the paper's audited labels (17% of 2026 filings). From October, Jev labels new filings alone; on the paper's own filings Jev alone found fewer AI-cited filings, so compare against the top of the band."; "Data through <month>, updated <date>"; a stale note when `data_through` is more than 45 days before build time; links to the paper and the repo.

- [ ] **Step 1:** `npm run build` → succeeds; `dist/research/layoffs-tracker/index.html` contains "Data through".
- [ ] **Step 2:** Temporarily point `loadTracker` at a bad URL → `npm run build` fails; revert.
- [ ] **Step 3:** Preview (`astro dev --background`): page renders in light and dark, at 375 px width with no horizontal scroll; screenshot for the PR.
- [ ] **Step 4:** Commit `feat: layoffs tracker page`; `oni test LT-<T12> --not-applicable --reason "portfolio repo has no test runner; build and preview verified"`.

### Task 13: Ship the page

- [ ] **Step 1:** Open one PR in `tanner-k/portfolio` from `feature/layoffs-tracker` into `main` (it carries the paper branch too). Body: what changed, screenshots, test plan.
- [ ] **Step 2:** **Tanner approves and merges** (it publishes the paper and the tracker). Do not merge without his yes.
- [ ] **Step 3:** After deploy, the live page at `https://tanner-k.github.io/portfolio/research/layoffs-tracker/` shows "Data through 2026-10". Close T13 not applicable with that check as the reason.
- [ ] **Step 4:** Propose (don't apply) the resume line from the spec to Tanner.
