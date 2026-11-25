# The tracker image: one CLI, one subcommand per Airflow task.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

# Non-root. The DAG runs pods with fsGroup 10001 so the work volume and the deploy key
# (mode 0440, owned by root) are readable.
RUN useradd --create-home --uid 10001 --user-group tracker
USER 10001
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["layoffs-tracker"]
CMD ["--help"]
