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
from cutback.cli_agent import register_agent
from cutback.cli_batch import register_batch
from cutback.cli_checks import register_checks
from cutback.cli_compression import register_compression
from cutback.cli_criteria import register_criteria
from cutback.cli_episodes import register_episodes
from cutback.cli_guardrails import register_guardrails
from cutback.cli_qualification import register_qualification
from cutback.cli_retrieval import register_retrieval
from cutback.cli_routing import register_routing
from cutback.cli_scenarios import register_scenario
from cutback.cli_security import register_security
from cutback.cli_semantic_cache import register_semantic_cache
from cutback.cli_tools import register_tools
from cutback.cli_workflows import register

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Find AI calls, compare cost and quality, and review changes. Start with: cutback demo",
)
collector = typer.Typer(no_args_is_help=True, help="Run and inspect a self-hosted collector.")
app.add_typer(collector, name="collector")
db_app = typer.Typer(no_args_is_help=True, help="Apply PostgreSQL schema migrations.")
app.add_typer(db_app, name="db")
project_app = typer.Typer(no_args_is_help=True, help="Create scoped collector projects.")
app.add_typer(project_app, name="projects")
keys_app = typer.Typer(no_args_is_help=True, help="Create scoped credentials in a private file.")
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
def demo(
    port: int = typer.Option(8766, min=1024, max=65535),
    open_browser: bool = True,
    ticket_router: bool = False,
    provider_batch: bool = False,
    semantic_cache: bool = False,
    tool_image_id: str | None = None,
    security_suite: bool = False,
    runtime_guardrails: bool = False,
    guarded_tools: bool = False,
    guarded_security: bool = False,
    joint_model_context: bool = False,
    reviewed_criteria: bool = False,
    semantic_rubrics: bool = False,
    learned_routing: bool = False,
    source_routing: bool = False,
    generated_scenarios: bool = False,
):
    """Explore the complete sample. Requires Docker and Git; no API key or paid calls."""
    try:
        from cutback.demo import launch_demo

        launch_demo(
            port,
            open_browser,
            ticket_router=ticket_router,
            provider_batch=provider_batch,
            semantic_cache=semantic_cache,
            tool_image_id=tool_image_id,
            security_suite=security_suite,
            runtime_guardrails=runtime_guardrails,
            guarded_tools=guarded_tools,
            guarded_security=guarded_security,
            joint_model_context=joint_model_context,
            reviewed_criteria=reviewed_criteria,
            semantic_rubrics=semantic_rubrics,
            learned_routing=learned_routing,
            source_routing=source_routing,
            generated_scenarios=generated_scenarios,
        )
    except ImportError:
        fail(
            "DEMO_EXTRAS_REQUIRED",
            (
                "Install cutback[server,openai] or run uv sync --locked --all-"
                "extras --dev in the repository."
            ),
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "DEMO_GIT_REQUIRED":
            fail(
                "DEMO_GIT_REQUIRED",
                "Install Git and make git available on PATH, then retry the source-bound demo.",
            )
        fail(
            "DEMO_SETUP_FAILED",
            (
                "Start Docker Desktop, check the selected port, and retry. No "
                "application or provider credentials are needed."
            ),
        )


@app.command()
def sample(destination: Path):
    """Copy an independent synthetic support-desk project for offline scanning."""
    import shutil

    from cutback.demo import sample_cases

    root = Path(__file__).parent / "sample_app"
    if destination.exists():
        fail(
            "SAMPLE_DESTINATION_EXISTS",
            "Choose a new directory; existing files are never overwritten.",
        )
    destination.mkdir(parents=True)
    shutil.copyfile(root / "app.py", destination / "app.py")
    (destination / "cases.jsonl").write_text(
        "\n".join(json.dumps(case) for case in sample_cases()) + "\n", encoding="utf-8"
    )
    (destination / ".gitignore").write_text(".cutback/\n.env\n.venv/\n", encoding="utf-8")
    (destination / "README.md").write_text(
        "# Support-desk sample\n\nOwned synthetic cases; no customer data.\n\n"
        "Run `cutback scan .` and `cutback opportunities --target .` here."
        " These parse files without running the app.\n\nRun `cutback demo` "
        "for the complete PostgreSQL/collector/worker/dashboard "
        "walkthrough. Docker must be running; install "
        "cutback[server,openai]. No provider key or paid call is needed.\n\n"
        "The app accepts an explicit native SDK HTTP client. Do not "
        "replace its synthetic transport with a real provider without "
        "permitted data and a bounded approved budget.\n",
        encoding="utf-8",
    )
    typer.echo(
        f"Sample created: {destination}\nNext: cutback scan {destination}\nThen: cutback demo"
    )


@app.command()
def doctor(json_output: Annotated[bool, typer.Option("--json")] = False):
    """Check installation, optional dependencies and offline defaults."""
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
    """Create safe local configuration without changing application code."""
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
    language: Annotated[list[str] | None, typer.Option("--language")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Find AI calls by parsing source; never runs the application."""
    from cutback.scanner import scan

    try:
        result = scan(target, languages=language)
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
    """Summarize inventory or observed collector usage."""
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
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Explain what to measure and evaluate at each AI call site."""
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

    findings = static_opportunities(result)
    if json_output:
        output(findings)
    else:
        typer.echo("AI call sites to investigate (source inventory, not measured savings):")
        previous = None
        for item in findings["opportunities"]:
            if item["source"] != previous:
                typer.echo(
                    f"  {item['source']}  model: {item['baseline_model'] or 'resolved at runtime'}"
                )
                previous = item["source"]
            typer.echo(f"    Strategy: {item['strategy']}  quality: not evaluated")
            if item.get("candidates"):
                typer.echo("    Candidates to evaluate: " + ", ".join(item["candidates"]))
            typer.echo("    Evidence limit: " + item["reason"].replace("_", " "))
            typer.echo("    Next: " + item["next_action"].replace("_", " "))
        typer.echo("Spend and savings: unknown until usage and quality are measured.")
        typer.echo(
            "Next: run 'cutback demo' to see observation and evaluation, or "
            "follow docs/application-onboarding.md for your application."
        )


@app.command()
def instrument(
    target: Path = Path("."),
    entrypoint: Path | None = None,
    plan: bool = True,
    apply: bool = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    handler: str | None = None,
):
    """Prepare a reviewable instrumentation diff and rollback."""
    from cutback.core.instrumentation_plan import instrument_plan

    try:
        proposal = instrument_plan(target, entrypoint, handler)
        root = target.resolve(strict=True)
        if apply:
            from cutback.core.workspace import private_directory, write_private

            location = private_directory(root, ".cutback")
            write_private(location / "source-map.json", proposal["source_map"])
            (root / proposal["source_file"]).write_bytes(proposal["after"].encode("utf-8"))
        if json_output:
            output(
                {
                    "status": "applied" if apply else "plan_only",
                    "diff": proposal["diff"],
                    "source_map": proposal["source_map"],
                    "source_map_path": ".cutback/source-map.json",
                    "content_capture": False,
                    "deployment": "not_performed",
                    "lifecycle": proposal["lifecycle"],
                }
            )
        else:
            typer.echo(proposal["diff"])
    except (ValueError, OSError, SyntaxError):
        fail("INSTRUMENTATION_PLAN_REQUIRES_REVIEW", "inspect entrypoint and existing bootstrap")


@collector.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8080, dev_http: bool = False):
    """Serve using the configured scoped credentials."""
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
def collector_up(
    profile: Literal["dev"] = "dev",
    target: Path = Path("."),
    port: int = 8080,
    allow_payloads: bool = False,
):
    """Start a persistent loopback collector from the installed wheel; Docker PostgreSQL only."""
    from cutback.local_stack import up

    try:
        output(up(target, port=port, allow_payloads=allow_payloads))
    except (ValueError, OSError, subprocess.SubprocessError):
        fail(
            "LOCAL_COLLECTOR_NOT_READY",
            "check server extra, Docker Desktop, free port and collector status",
        )


@collector.command("status")
def collector_status(endpoint: str | None = None, target: Path = Path(".")):
    """Collector status using the configured scoped credentials."""
    if endpoint:
        output(request("GET", endpoint, "/v1/health/ready"))
    else:
        from cutback.local_stack import status

        output(status(target))


@collector.command("down")
def collector_down(target: Path = Path(".")):
    """Stop only this workspace's owned resources; preserve database and encrypted artifacts."""
    from cutback.local_stack import down

    try:
        output(down(target))
    except (ValueError, OSError, subprocess.SubprocessError):
        fail("LOCAL_COLLECTOR_STOP_FAILED", "inspect the owned local collector status")


@db_app.command("migrate")
def migrate():
    """Migrate using the configured scoped credentials."""
    from cutback.server.migrate import CURRENT_REVISION
    from cutback.server.migrate import migrate as run

    try:
        run()
    except Exception:
        fail(
            "MIGRATION_FAILED",
            "inspect database connectivity and migration version; credentials are not logged",
        )
    output({"status": "migrated", "revision": CURRENT_REVISION})


@project_app.command("create")
def create_project(
    slug: str, environment: str = "staging", endpoint: str = "http://127.0.0.1:8080"
):
    """Create project using the configured scoped credentials."""
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
        Literal["ingest", "viewer", "analyst", "editor", "project_admin", "prompt_reader"],
        typer.Option("--role"),
    ],
    destination: Annotated[Path, typer.Option("--output")],
    environment: str = "staging",
    endpoint: str = "http://127.0.0.1:8080",
    expires_in_days: int = 30,
):
    # Exclusive file creation prevents overwriting existing secrets; tokens never printed.
    """Create key using the configured scoped credentials."""
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

register_agent(app, output, fail)
register_checks(app, output, fail)
register_qualification(app, output, fail)
register_episodes(app, output, fail)
register_tools(app, output, request, fail)
register_security(app, output, request, fail)
register_guardrails(app, output, request, fail)
register_retrieval(app, output, request, fail)
register_compression(app, output, request, fail)
register_compression(app, output, request, fail, joint=True)
register_criteria(app, output, request, fail)
register_scenario(app, output, request, fail)
register_routing(app, output, request, fail)
register_batch(app, output, request, fail)
register_semantic_cache(app, output, request, fail)

if __name__ == "__main__":
    app()
