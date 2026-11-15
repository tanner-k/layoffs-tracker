from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import pendulum
import pytest
from airflow.dag_processing.dagbag import DagBag

DAGS = Path(__file__).resolve().parents[1] / "dags"
STEPS = ["checkout", "fetch_sec", "label", "fetch_warn", "build_metrics", "publish"]
IMAGE = "ghcr.io/tanner-k/layoffs-tracker:main"


@pytest.fixture(scope="module")
def bag() -> DagBag:
    return DagBag(dag_folder=str(DAGS), include_examples=False)


@pytest.fixture(scope="module")
def dag(bag: DagBag) -> Any:
    return bag.dags["layoffs_monthly"]


def test_dag_has_no_import_errors(bag: DagBag) -> None:
    assert bag.import_errors == {}


def test_dag_shape(dag: Any) -> None:
    assert dag is not None and dag.catchup and dag.max_active_runs == 1
    assert [t.task_id for t in dag.topological_sort()] == STEPS
    assert all(t.retries == 2 for t in dag.tasks)


def test_schedule(dag: Any) -> None:
    assert dag.timetable.expression == "0 9 3 * *"
    assert str(dag.timetable.timezone) == "America/Denver"
    assert dag.start_date == pendulum.datetime(2026, 11, 1, tz="America/Denver")


def test_tasks_are_pods_from_the_tracker_image(dag: Any) -> None:
    for t in dag.tasks:
        assert (t.image, t.image_pull_policy, t.namespace) == (IMAGE, "Always", "airflow")
        assert t.arguments[0] == t.task_id.replace("_", "-") and "/work" in t.arguments
        assert {m.mount_path for m in t.volume_mounts} >= {"/work"}
        assert t.security_context["runAsUser"] == 10001
        assert t.container_resources.limits["memory"]


def test_secrets_reach_only_the_steps_that_need_them(dag: Any) -> None:
    def keys(t: Any) -> bool:
        return "/keys" in {m.mount_path for m in t.volume_mounts}

    def env_secret(t: Any) -> bool:
        return any(e.secret_ref.name == "layoffs-secrets" for e in (t.env_from or []))

    assert [t.task_id for t in dag.tasks if keys(t)] == ["checkout", "publish"]
    assert [t.task_id for t in dag.tasks if env_secret(t)] == ["fetch_sec", "label"]
    key_vol = next(v for v in dag.get_task("publish").volumes if v.secret)
    assert key_vol.secret.default_mode == 0o440


@pytest.mark.parametrize(
    ("logical", "month"),
    [
        (pendulum.datetime(2026, 11, 3, 16, tz="UTC"), "2026-10"),
        (pendulum.datetime(2027, 1, 3, 16, tz="UTC"), "2026-12"),
    ],
)
def test_run_month_is_the_month_before_the_logical_date(
    dag: Any, logical: pendulum.DateTime, month: str
) -> None:
    args = dag.get_task("fetch_sec").arguments
    template = args[args.index("--run-month") + 1]
    assert jinja2.Template(template).render(logical_date=logical) == month
