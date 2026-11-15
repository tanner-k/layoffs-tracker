# dags/

## Scope
- `layoffs_monthly.py` — the one Airflow DAG: six KubernetesPodOperator tasks, one per CLI step
- git-sync on the cluster reads this folder from `main`

## Not in scope
- Pipeline logic → `src/layoffs_tracker/` (each task only calls `layoffs-tracker <step>`)
- Helm values and manifests → `infra/`

## Conventions
- A task runs the image `ghcr.io/tanner-k/layoffs-tracker:main`; the DAG never imports the package
- Secrets reach pods as Kubernetes Secrets; the DAG names them, never holds values

## Notes for agents
- `tests/test_dag.py` loads this folder with `DagBag`; keep it import-clean
