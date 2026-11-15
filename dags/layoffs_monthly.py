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
SECRET = "layoffs-secrets"
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
GIT_SSH = k8s.V1EnvVar(
    name="GIT_SSH_COMMAND",
    value="ssh -i /keys/id_ed25519 -o UserKnownHostsFile=/keys/known_hosts -o IdentitiesOnly=yes",
)
# Non-root, no privilege escalation, no capabilities, default seccomp.
LOCKED = {
    "runAsNonRoot": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "seccompProfile": {"type": "RuntimeDefault"},
}
RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "100m", "memory": "256Mi"}, limits={"cpu": "1", "memory": "768Mi"}
)


def secret_env(*keys: str) -> list[k8s.V1EnvVar]:
    """Only the named keys of the `layoffs-secrets` Secret, as environment variables."""
    ref = k8s.V1SecretKeySelector
    return [
        k8s.V1EnvVar(name=k, value_from=k8s.V1EnvVarSource(secret_key_ref=ref(name=SECRET, key=k)))
        for k in keys
    ]


def step(
    name: str, *args: str, secrets: tuple[str, ...] = (), git: bool = False
) -> KubernetesPodOperator:
    """One pod running `layoffs-tracker <name> --work /work <args>`."""
    env = [*secret_env(*secrets), *([GIT_SSH] if git else [])]
    return KubernetesPodOperator(
        task_id=name.replace("-", "_"),
        name=f"layoffs-{name}",
        namespace=NAMESPACE,
        image=IMAGE,
        image_pull_policy="Always",
        arguments=[name, "--work", "/work", *args],
        volumes=[WORK, KEY] if git else [WORK],
        volume_mounts=[WORK_MOUNT, KEY_MOUNT] if git else [WORK_MOUNT],
        env_vars=env or None,
        security_context={"runAsUser": UID, "runAsGroup": UID, "fsGroup": UID},
        container_security_context=LOCKED,
        automount_service_account_token=False,  # the steps never call the Kubernetes API
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
        >> step("fetch-sec", "--run-month", RUN_MONTH, secrets=("SEC_USER_AGENT",))
        >> step("label", secrets=("SEC_USER_AGENT", "TYPESAFE_API_KEY"))
        >> step("fetch-warn")
        >> step("build-metrics", "--run-month", RUN_MONTH)
        >> step("publish", git=True)
    )
