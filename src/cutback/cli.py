import difflib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer

from cutback import __version__
from cutback.cli_workflows import register

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Evidence-aware AI cost discovery and observation.",
)
collector = typer.Typer(no_args_is_help=True)
app.add_typer(collector, name="collector")
db_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
project_app = typer.Typer(no_args_is_help=True)
app.add_typer(project_app, name="projects")
keys_app = typer.Typer(no_args_is_help=True)
app.add_typer(keys_app, name="keys")


def output(value, destination: Path | None = None):
    text = json.dumps(value, indent=2, ensure_ascii=True, default=str) + "\n"
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    else:
        typer.echo(text, nl=False)


def fail(code: str, next_action: str, exit_code=2) -> NoReturn:
    output({"code": code, "next_action": next_action})
    raise typer.Exit(exit_code)


def request(method, endpoint, path, *, token_env="CUTBACK_CONTROL_TOKEN", **kwargs):
    from cutback.core.config import validate_endpoint

    try:
        import httpx

        base = validate_endpoint(endpoint)
        token = os.environ.get(token_env)
        if not token:
            fail("NO_CONTROL_CREDENTIAL", f"set {token_env} to the required scoped key")
        response = httpx.request(
            method,
            base + path,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            **kwargs,
        )
        if response.is_error or response.is_redirect:
            fail(
                "COLLECTOR_REQUEST_FAILED",
                f"check endpoint, credential scope, and service status ({response.status_code})",
            )
        return response.json()
    except ImportError:
        fail("MISSING_EXTRA", "install cutback[instrumentation] for remote reports")
    except (ValueError, httpx.HTTPError):
        fail("COLLECTOR_UNREACHABLE_OR_INVALID", "check endpoint, TLS and collector availability")


def version_callback(value: bool):
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = False,
):
    pass


@app.command()
def doctor(json_output: Annotated[bool, typer.Option("--json")] = False):
    from cutback.pricing import Catalog

    result = {
        "version": __version__,
        "python": sys.version.split()[0],
        "extras": {
            name: importlib.util.find_spec(name) is not None
            for name in ("openai", "fastapi", "psycopg", "httpx")
        },
        "pricing_snapshot_id": Catalog().snapshot_id,
        "telemetry_enabled": os.getenv("CUTBACK_ENABLED", "false").lower() == "true",
        "content_capture": "disabled_by_default_explicit_policy_required",
        "network_probe": "not_performed",
        "paid_calls": "never_implicit",
        "release": "development_not_production_validated",
    }
    output(result)


@app.command("init")
def initialize(mode: Literal["local"] = "local", target: Path = Path(".")):
    from cutback.core.config import LocalConfig

    path = target.resolve() / ".cutback" / "config.yaml"
    if path.exists():
        fail("CONFIG_EXISTS", "review existing configuration; no overwrite performed")
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(LocalConfig().model_dump()), encoding="utf-8")
    output({"config": str(path), "mode": mode, "telemetry": "disabled"})


@app.command("scan")
def scan_command(
    target: Annotated[Path, typer.Argument()] = Path("."),
    format: Literal["json", "table"] = "table",
    destination: Annotated[Path | None, typer.Option("--output")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    from cutback.scanner import scan

    try:
        result = scan(target)
    except (OSError, ValueError, RecursionError):
        fail(
            "SCAN_INPUT_INVALID", "check directory, configuration schema and readable source files"
        )
    if format == "json" or json_output or destination:
        output(result, destination)
        return
    typer.echo(
        "Discovered static candidates; historical tokens, cost, latency and quality: unknown."
    )
    for site in result["call_sites"]:
        typer.echo(
            f"{site['source_file']}:{site['line']}  {site['provider']}  "
            f"{site['sdk_surface']}  {site['requested_model'] or 'runtime_unresolved'}"
        )
    typer.echo(f"{len(result['call_sites'])} candidate sites; {len(result['warnings'])} warnings.")


def period_start(period: str):
    match = re.fullmatch(r"([1-9][0-9]{0,3})([hd])", period)
    if not match:
        fail("INVALID_PERIOD", "use a period such as 1h or 7d (maximum 395d)")
    amount, unit = int(match[1]), match[2]
    delta = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
    if delta > timedelta(days=395):
        fail("INVALID_PERIOD", "maximum period is 395d")
    return datetime.now(UTC) - delta


@app.command()
def report(
    source: Literal["scan", "collector"] = "scan",
    target: Path = Path("."),
    endpoint: str = "http://127.0.0.1:8080",
    project: str | None = None,
    period: str = "7d",
    group_by: Literal["model", "provider", "workflow", "environment", "day"] = "model",
    destination: Annotated[Path | None, typer.Option("--output")] = None,
):
    if source == "scan":
        from cutback.scanner import scan

        try:
            output(scan(target), destination)
        except (OSError, ValueError, RecursionError):
            fail("SCAN_INPUT_INVALID", "check scan target and configuration")
        return
    from uuid import UUID

    try:
        project_id = UUID(project or os.environ.get("CUTBACK_PROJECT_ID", ""))
    except ValueError:
        fail("PROJECT_REQUIRED", "supply --project UUID or CUTBACK_PROJECT_ID")
    result = request(
        "GET",
        endpoint,
        f"/v1/projects/{project_id}/usage",
        params={"start": period_start(period).isoformat(), "group_by": group_by},
    )
    output(result, destination)


@app.command()
def opportunities(
    target: Path = Path("."),
    source: Literal["scan", "collector"] = "scan",
    project: str | None = None,
    period: str = "7d",
    endpoint: str = "http://127.0.0.1:8080",
):
    if source == "collector":
        from uuid import UUID

        try:
            project_id = UUID(project or os.getenv("CUTBACK_PROJECT_ID", ""))
        except ValueError:
            fail("PROJECT_REQUIRED", "supply --project UUID or CUTBACK_PROJECT_ID")
        output(
            request(
                "GET",
                endpoint,
                f"/v1/projects/{project_id}/opportunities",
                params={"start": period_start(period).isoformat()},
            )
        )
        return
    from cutback.scanner import scan

    result = scan(target)
    from cutback.optimizer import static_opportunities

    output(static_opportunities(result))


@app.command()
def instrument(
    target: Path = Path("."),
    entrypoint: Path | None = None,
    plan: bool = True,
    apply: bool = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    import ast

    root = target.resolve(strict=True)
    if entrypoint is None:
        candidates = [
            root / p
            for p in ("main.py", "app.py", "src/main.py", "__main__.py")
            if (root / p).is_file()
        ]
        if len(candidates) != 1:
            fail(
                "ENTRYPOINT_REQUIRED",
                "specify --entrypoint relative/path.py after inspecting process startup",
            )
        path = candidates[0]
    else:
        path = root / entrypoint
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root)
        or path.stat().st_size > 1_048_576
    ):
        fail("UNSAFE_ENTRYPOINT", "choose a small Python source file inside target")
    before = path.read_text(encoding="utf-8")
    tree = ast.parse(before)
    if "# cutback:bootstrap" in before:
        fail(
            "ALREADY_INSTRUMENTED", "inspect existing bootstrap and avoid duplicate instrumentation"
        )
    insert_after = 0
    for i, node in enumerate(tree.body):
        if (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            insert_after = node.end_lineno or node.lineno
        else:
            break
    bootstrap = (
        "\n# cutback:bootstrap — observation only; requires CUTBACK_ENABLED=true\n"
        "import os as _cutback_os\n"
        'if _cutback_os.getenv("CUTBACK_ENABLED", "false").lower() == "true":\n'
        "    try:\n"
        "        import cutback as _cutback\n"
        "        _cutback_observer = _cutback.init(\n"
        '            service_name=_cutback_os.environ["CUTBACK_SERVICE_NAME"],\n'
        '            environment=_cutback_os.environ["CUTBACK_ENVIRONMENT"],\n'
        "            capture_content=False,\n"
        "        )\n"
        "    except Exception:\n"
        "        import warnings as _cutback_warnings\n"
        '        _cutback_warnings.warn("Cutback initialization failed; inference continues",\n'
        "                               RuntimeWarning)\n\n"
    )
    lines = before.splitlines(keepends=True)
    after = "".join(lines[:insert_after]) + bootstrap + "".join(lines[insert_after:])
    ast.parse(after)
    relative = path.relative_to(root).as_posix()
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    if apply:
        path.write_text(after, encoding="utf-8")
    if json_output:
        output(
            {
                "status": "applied" if apply else "plan_only",
                "diff": diff,
                "content_capture": False,
                "deployment": "not_performed",
                "prerequisites": [
                    "install pinned cutback[openai]",
                    "set scoped collector environment",
                    "review startup order, SDK retry and Lambda flush limitations",
                ],
            }
        )
    else:
        typer.echo(diff)


@collector.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8080, dev_http: bool = False):
    import uvicorn

    from cutback.server.api import create_app

    try:
        application = create_app(allow_insecure_http=dev_http)
    except Exception:
        fail("COLLECTOR_CONFIGURATION_INVALID", "check runtime secrets and PostgreSQL settings")
    uvicorn.run(
        application,
        host=host,
        port=port,
        access_log=False,
        proxy_headers=False,
        log_level="warning",
    )


@collector.command("up")
def collector_up(profile: Literal["dev"] = "dev"):
    path = Path(__file__).resolve().parents[2] / "infra/compose/docker-compose.yml"
    if not path.exists():
        fail("COMPOSE_SOURCE_REQUIRED", "use the source distribution and follow docs/deployment.md")
    result = subprocess.run(
        ["docker", "compose", "-f", str(path), "up", "-d", "--build"], check=False
    )
    raise typer.Exit(result.returncode)


@collector.command("status")
def collector_status(endpoint: str = "http://127.0.0.1:8080"):
    output(request("GET", endpoint, "/v1/health/ready"))


@db_app.command("migrate")
def migrate():
    from cutback.server.migrate import migrate as run

    try:
        run()
    except Exception:
        fail(
            "MIGRATION_FAILED",
            "inspect database connectivity and migration version; credentials are not logged",
        )
    output({"status": "migrated", "revision": "0002"})


@project_app.command("create")
def create_project(
    slug: str, environment: str = "staging", endpoint: str = "http://127.0.0.1:8080"
):
    output(
        request(
            "POST",
            endpoint,
            "/v1/projects",
            token_env="CUTBACK_ADMIN_TOKEN",
            json={"slug": slug, "environment": environment},
        )
    )


@keys_app.command("create")
def create_key(
    project: Annotated[str, typer.Option("--project")],
    role: Annotated[
        Literal["ingest", "viewer", "analyst", "editor", "project_admin"], typer.Option("--role")
    ],
    destination: Annotated[Path, typer.Option("--output")],
    environment: str = "staging",
    endpoint: str = "http://127.0.0.1:8080",
    expires_in_days: int = 30,
):
    # Exclusive file creation prevents overwriting existing secrets; tokens never printed.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        result = request(
            "POST",
            endpoint,
            f"/v1/projects/{project}/keys",
            token_env="CUTBACK_ADMIN_TOKEN",
            json={"role": role, "environment": environment, "expires_in_days": expires_in_days},
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        destination.unlink(missing_ok=True)
        raise
    output(
        {
            "credential_file": str(destination.resolve()),
            "role": role,
            "next_action": "restrict file ACL; load token into runtime secret store",
        }
    )


register(app, output, request, fail)

if __name__ == "__main__":
    app()
