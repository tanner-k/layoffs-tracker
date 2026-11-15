# 0002 — k3s and the official Airflow Helm chart

**Status:** Accepted
**Date:** 2026-11-14

## Context
The tracker runs monthly on `storageguy`, a home Linux server (4 cores, 7.1 GB RAM). One goal is real Kubernetes and Airflow experience.

## Decision
Run single-node **k3s** on `storageguy` and install Airflow 3.x with the official `apache-airflow/airflow` **Helm chart**, always on, in a slim setup (Tanner's call: "Let's do A", "Always on with slim setup").

## Rationale
Kubernetes is on the job postings the project targets; plain Docker Compose is not. k3s is one binary with a small footprint, and the official chart is how Airflow is deployed in production.

## Consequences
- (positive) The same chart and values would run on a managed cluster.
- (tradeoff) Always-on Airflow uses memory between runs; every component gets limits, target under 3 GB including k3s.
- (followup) Measured memory goes in the README.

## Alternatives considered
- **Docker Compose** — simpler, but not Kubernetes.
- **Local Kubernetes on a laptop, a cloud server, or GitHub's scheduler** — the laptop sleeps, a server costs money, and GitHub's scheduler shows no Airflow.
- **Start the cluster only for each run** — Airflow would not be doing the scheduling, which is the skill being shown.
