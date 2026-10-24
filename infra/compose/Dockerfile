FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE .python-version ./
COPY src ./src
RUN uv sync --locked --no-dev --extra server --extra instrumentation --extra openai --extra anthropic \
    --extra identity --extra commercial --extra storage --no-editable \
    && useradd --uid 10001 --create-home cutback
USER 10001
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "cutback.server.boot"]
