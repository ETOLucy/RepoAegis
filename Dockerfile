FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --prefix=/install ".[postgres]"

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --create-home agent

COPY --from=builder /install /usr/local
WORKDIR /app
COPY configs ./configs

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "repo_maintenance_agent.main:build_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
