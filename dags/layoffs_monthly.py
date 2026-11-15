"""Monthly AI-layoffs tracker: six pod tasks, one per `layoffs-tracker` step.

Runs at 09:00 America/Denver on the 3rd of each month for the month before. Each task is a
pod from the tracker image sharing the `layoffs-work` volume; Airflow only schedules.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

IMAGE = "ghcr.io/tanner-k/layoffs-tracker:main"
NAMESPACE = "airflow"
REPO_URL = "git@github.com:tanner-k/layoffs-tracker.git"
UID = 10001
# The month before the logical date (the run on 2026-11-03 processes 2026-10).
RUN_MONTH = "{{ logical_date.subtract(months=1).strftime('%Y-%m') }}"

WORK = k8s.V1Volume(
    name="work",
    persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(claim_name="layoffs-work"),
)
KEY = k8s.V1Volume(
    name="deploy-key",
    # Root-owned and group-readable via fsGroup: OpenSSH accepts a key not owned by the user.
    secret=k8s.V1SecretVolumeSource(secret_name="layoffs-deploy-key", default_mode=0o440),
)
WORK_MOUNT = k8s.V1VolumeMount(name="work", mount_path="/work")
KEY_MOUNT = k8s.V1VolumeMount(name="deploy-key", mount_path="/keys", read_only=True)
SECRETS = [k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="layoffs-secrets"))]
GIT_SSH = {
    "GIT_SSH_COMMAND": "ssh -i /keys/id_ed25519 -o UserKnownHostsFile=/keys/known_hosts "
    "-o IdentitiesOnly=yes"
}
RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "100m", "memory": "256Mi"}, limits={"cpu": "1", "memory": "768Mi"}
)


def step(name: str, *args: str, secrets: bool = False, git: bool = False) -> KubernetesPodOperator:
    """One pod running `layoffs-tracker <name> --work /work <args>`."""
    return KubernetesPodOperator(
        task_id=name.replace("-", "_"),
        name=f"layoffs-{name}",
        namespace=NAMESPACE,
        image=IMAGE,
        image_pull_policy="Always",
        arguments=[name, "--work", "/work", *args],
        volumes=[WORK, KEY] if git else [WORK],
        volume_mounts=[WORK_MOUNT, KEY_MOUNT] if git else [WORK_MOUNT],
        env_from=SECRETS if secrets else None,
        env_vars=GIT_SSH if git else None,
        security_context={"runAsUser": UID, "runAsGroup": UID, "fsGroup": UID},
        container_resources=RESOURCES,
        in_cluster=True,
        get_logs=True,
        on_finish_action="delete_pod",
    )


with DAG(
    dag_id="layoffs_monthly",
    schedule="0 9 3 * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Denver"),
    catchup=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["layoffs-tracker"],
    doc_md=__doc__,
):
    (
        step("checkout", "--repo-url", REPO_URL, git=True)
        >> step("fetch-sec", "--run-month", RUN_MONTH, secrets=True)
        >> step("label", secrets=True)
        >> step("fetch-warn")
        >> step("build-metrics", "--run-month", RUN_MONTH)
        >> step("publish", git=True)
    )
