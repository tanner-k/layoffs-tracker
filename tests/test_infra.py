from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[1] / "infra"


@pytest.fixture(scope="module")
def v() -> Any:
    return yaml.safe_load((INFRA / "airflow-values.yaml").read_text())


def test_values_are_slim(v: Any) -> None:
    assert v["executor"] == "LocalExecutor"
    for off in ("triggerer", "statsd", "redis", "flower", "pgbouncer"):
        assert v[off]["enabled"] is False
    for comp in ("scheduler", "apiServer", "dagProcessor"):
        assert {"requests", "limits"} <= v[comp]["resources"].keys()
    assert v["apiServer"]["service"]["type"] == "ClusterIP"


def test_every_container_has_a_memory_limit(v: Any) -> None:
    for comp in ("scheduler", "apiServer", "dagProcessor", "migrateDatabaseJob", "createUserJob"):
        assert v[comp]["resources"]["limits"]["memory"]
    assert v["postgresql"]["primary"]["resources"]["limits"]["memory"]
    assert v["dags"]["gitSync"]["resources"]["limits"]["memory"]


def test_dags_come_from_this_repo(v: Any) -> None:
    g = v["dags"]["gitSync"]
    assert g["enabled"] is True and g["repo"] == "https://github.com/tanner-k/layoffs-tracker.git"
    assert (g["branch"], g["subPath"]) == ("main", "dags")


def test_airflow_version_matches_the_test_env(v: Any) -> None:
    import airflow

    assert v["airflowVersion"] == v["defaultAirflowTag"] == airflow.__version__


def test_manifests() -> None:
    ns = yaml.safe_load((INFRA / "namespace.yaml").read_text())
    pvc = yaml.safe_load((INFRA / "pvc.yaml").read_text())
    assert ns["kind"] == "Namespace" and ns["metadata"]["name"] == "airflow"
    assert pvc["metadata"] == {"name": "layoffs-work", "namespace": "airflow"}
    assert pvc["spec"]["storageClassName"] == "local-path"
