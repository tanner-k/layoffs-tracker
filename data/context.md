# data/

## Scope
- `filings.parquet` — one row per SEC 8-K Item 2.05 filing, paper rows through Sep 2026 then Jev rows
- `latest.json` — the metrics the portfolio page reads (`schema_version` 1)
- `seed/` — the paper's two input files the history is built from

## Not in scope
- Raw SEC pages and Jev caches → the cluster's work volume, never committed
- Code → `src/layoffs_tracker/`

## Conventions
- Rows dated on or before 2026-09-30 are frozen paper rows; the pipeline never changes them
- Only the `publish` step commits here, as `data: refresh through YYYY-MM`; CI skips data-only commits

## Notes for agents
- A change to a past month shows up in this folder's git history; explain any you make
