# infra/

## Scope
- `airflow-values.yaml` — Helm values for the official Airflow chart (slim, LocalExecutor)
- `namespace.yaml`, `pvc.yaml` — the `airflow` namespace and the `layoffs-work` volume
- `README.md` — every install command for k3s, Helm and Secrets on `storageguy`, in order

## Not in scope
- Secret values → created by hand on the server, never in this repo
- The DAG → `dags/`

## Conventions
- Every enabled Airflow component sets resource requests and limits
- The API server service stays `ClusterIP`; reach it with `kubectl port-forward`

## Notes for agents
- `tests/test_infra.py` checks the values stay slim; change both together
