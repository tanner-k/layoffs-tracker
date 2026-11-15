# 0003 — LocalExecutor with KubernetesPodOperator tasks

**Status:** Accepted
**Date:** 2026-11-14

## Context
Six steps run once a month on one node. Airflow needs an executor, and each step needs the package and its dependencies.

## Decision
Use **LocalExecutor** (no workers, Redis or Flower). Each DAG task is a **KubernetesPodOperator** pod running the tracker image `ghcr.io/tanner-k/layoffs-tracker:main`. Pods share the `layoffs-work` volume.

## Rationale
LocalExecutor is the smallest executor that works. Pod tasks keep the tracker's dependencies out of the Airflow image, so the chart's stock image is used unchanged, and each step is a plain container that also runs with `docker run`.

## Consequences
- (positive) Upgrading Airflow and changing the tracker are independent.
- (tradeoff) Each task pays a pod start (seconds); irrelevant for a monthly job.
- (followup) The scheduler's service account needs rights to create pods in the `airflow` namespace.

## Alternatives considered
- **KubernetesExecutor** — a pod per task for Airflow itself too; more moving parts for no gain at this size.
- **CeleryExecutor** — needs Redis and workers; built for scale this job doesn't have.
- **PythonOperator in a custom Airflow image** — couples tracker dependencies to the Airflow image.
