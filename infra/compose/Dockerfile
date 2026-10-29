FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS installer
FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
ARG DEBIAN_GIT_VERSION=1:2.47.3-0+deb13u1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        git=${DEBIAN_GIT_VERSION} git-man=${DEBIAN_GIT_VERSION} ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=installer /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON=3.11.18 UV_PYTHON_PREFERENCE=only-managed
RUN uv python install 3.11.18
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE .python-version ./
COPY src ./src
RUN uv sync --locked --no-dev --extra server --extra instrumentation --extra openai --extra anthropic \
    --extra identity --extra commercial --extra storage --no-editable \
    && useradd --uid 10001 --create-home cutback
USER 10001
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "cutback.server.boot"]
