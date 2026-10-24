# Pass the reviewed collector image digest; never build from an implicit latest tag.
ARG CUTBACK_BASE_IMAGE
FROM ${CUTBACK_BASE_IMAGE}
USER root
COPY infra/training/pyproject.toml infra/training/uv.lock /opt/training/
RUN UV_PROJECT_ENVIRONMENT=/opt/training/.venv uv sync --project /opt/training --locked --no-dev
ENV CUTBACK_TRAINING_PYTHON=/opt/training/.venv/bin/python \
    CUTBACK_PROCESS=worker CUTBACK_EXPERIMENT_EXECUTION=false \
    CUTBACK_TRAINING_EXECUTION=false CUTBACK_TRAINING_ALLOW_CUDA=false
USER 10001
