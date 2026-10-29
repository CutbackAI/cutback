"""Check release payloads, metadata and internal-file exclusion before upload."""

import email
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


def check_application_payload(wheel_path: Path, source_path: Path, root: Path):
    """Require exact application bytes in source, wheel and sdist; never extract archives."""
    package = root / "src/cutback"
    expected = {}
    for path in package.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("application source must not contain symlinks")
        if path.is_file():
            expected[path.relative_to(package.parent).as_posix()] = path.read_bytes()
    if not expected:
        raise ValueError("application source is empty")
    with zipfile.ZipFile(wheel_path) as wheel:
        if len(wheel.namelist()) != len(set(wheel.namelist())):
            raise ValueError("wheel contains duplicate members")
        actual = {
            name: wheel.read(name)
            for name in wheel.namelist()
            if name.startswith("cutback/") and not name.endswith("/")
        }
    with tarfile.open(source_path) as source:
        members = source.getmembers()
        if len(members) != len({member.name for member in members}):
            raise ValueError("source distribution contains duplicate members")
        archived = {}
        for member in members:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 4 or parts[1:3] != ("src", "cutback"):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("source application member must be a regular file")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("source application member is unreadable")
            name = "/".join(parts[2:])
            if name in archived:
                raise ValueError("source distribution contains ambiguous application roots")
            archived[name] = stream.read()
    if actual != expected or archived != expected:
        # Do not include file contents, names or potentially private checkout paths.
        raise ValueError(
            "application payload differs between source, wheel and source distribution"
        )
    return len(expected)


def check(directory: Path):
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("expected exactly one wheel and one source distribution")
    banned = (
        "MASTER_ENGINEERING_HANDOFF",
        "private-notes",
        "secrets/",
        ".env",
        "synthetic-compose-report",
        "changed-files.txt",
        "prepare_public_release",
    )
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        (metadata_name,) = (name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(wheel.read(metadata_name))
        if re.search(r"\]\((?!https?://|#)[^)]+\)", metadata.get_payload()):
            raise ValueError("package README contains index-broken relative links")
        version = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        )["project"]["version"]
        if metadata["Name"] != "cutback" or metadata["Version"] != version:
            raise ValueError("unexpected release identity")
        if "Development Status :: 2 - Pre-Alpha" not in metadata.get_all("Classifier", []):
            raise ValueError("development status must be explicit")
        if not any(name.endswith("licenses/LICENSE") for name in names):
            raise ValueError("wheel license missing")
    with tarfile.open(sources[0]) as source:
        source_names = source.getnames()
        names += source_names
        for member in source.getmembers():
            if member.isfile() and Path(member.name).suffix in {
                ".md",
                ".py",
                ".toml",
                ".json",
                ".jsonl",
                ".ts",
                ".tsx",
                ".js",
                ".mjs",
                ".yml",
                ".yaml",
                ".txt",
                ".css",
                ".html",
                ".svg",
            }:
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError("source archive text member is unreadable")
                try:
                    stream.read().decode("utf-8")
                except UnicodeError:
                    raise ValueError(
                        "source archive contains non-UTF8 text: " + member.name
                    ) from None
        for required in (
            ".dockerignore",
            "scripts/image_acceptance.py",
            "tests/unit/test_demo_prerequisites.py",
            ".github/workflows/publish.yml",
            "scripts/check_toolchain.py",
            "docs/otel-ingestion.md",
            "infra/compose/docker-compose.yml",
            "infra/compose/prepare.py",
            "scripts/export_openapi.py",
            "scripts/generate_ts_client.py",
            "web/scripts/licenses.mjs",
            "packages/js-sdk/index.mjs",
            "infra/helm/cutback/Chart.yaml",
            "infra/terraform/kubernetes/main.tf",
            "infra/training/uv.lock",
            "infra/sdk-compat/README.md",
            "infra/sdk-compat/openai3/pyproject.toml",
            "infra/sdk-compat/openai3/uv.lock",
            "examples/experiment_walkthrough.py",
            "docs/local-training.md",
            "docs/testing-the-sample.md",
            "docs/experiment-files.md",
            "examples/support_desk/README.md",
            "docs/imports-and-review.md",
            "docs/prompt-releases.md",
            "docs/application-checks.md",
            "docs/online-monitoring.md",
            "examples/online-monitoring/sample.py",
            "docs/online-quality.md",
            "docs/agent-evaluation.md",
            "docs/task-economics.md",
            "docs/improvement-campaigns.md",
            "docs/evidence/simple-first-campaign-acceptance.json",
            "tests/integration/test_campaign_simple_first.py",
            "docs/campaign-outcomes.md",
            "docs/load-qualification.md",
            "docs/modern-model-accounting.md",
            "docs/native-modern-text.md",
            "docs/gateway-budgets.md",
            "docs/retrieval-optimization.md",
            "examples/retrieval-optimization/README.md",
            "examples/retrieval-optimization/check_application.py",
            "examples/retrieval-optimization/corpus.json",
            "src/cutback/core/retrieval.py",
            "tests/end_to_end/test_retrieval_pipeline.py",
            "examples/gateway/README.md",
            "examples/gateway/sample.py",
            "src/cutback/sample_app/gateway.py",
            "src/cutback/server/migrations/versions/0019_application_gateway.py",
            "examples/modern-pricing/sample.py",
            "examples/load-qualification/profile.json",
            "examples/load-qualification/compare.py",
            "src/cutback/server/migrations/versions/0016_effective_rate_lookup_index.py",
            "examples/improvement-campaign/README.md",
            "examples/improvement-campaign/sample.py",
            "src/cutback/server/migrations/versions/0015_budgeted_improvement_campaigns.py",
            "examples/task-economics/README.md",
            "examples/task-economics/sample.py",
            "src/cutback/server/migrations/versions/0014_application_task_outcomes.py",
            "docs/episode-semantic-judging.md",
            "tests/integration/test_semantic_episodes.py",
            "src/cutback/core/episode_judging.py",
            "src/cutback/server/migrations/versions/0017_episode_semantic_judge_budget.py",
            "examples/agent-evaluation/sample.py",
            "src/cutback/server/migrations/versions/0013_execution_episode_and_per_call_ledger.py",
            "examples/online-quality/sample.py",
            "src/cutback/server/migrations/versions/0011_online_quality.py",
            "examples/prompt-releases/sample.py",
            "examples/application-checks/commands.json",
            "src/cutback/server/migrations/versions/0010_prompts.py",
            "examples/review-workflow/README.md",
            "examples/review-workflow/generate_promptfoo_fixture.py",
            "tests/fixtures/imports/promptfoo-0.125.1-v3.json",
            "src/cutback/server/migrations/versions/0008_dataset_lineage.py",
            "src/cutback/server/migrations/versions/0009_reviews.py",
            "src/cutback/sample_app/app.py",
        ):
            if not any(name.endswith("/" + required) for name in source_names):
                raise ValueError("source distribution missing reproducibility file: " + required)
        if any(member.issym() or member.islnk() for member in source.getmembers()):
            raise ValueError("release archive must not contain symlinks")
    if any(
        marker.lower() in name.lower().replace("\\", "/") for name in names for marker in banned
    ):
        raise ValueError("internal or secret file included in archive")
    count = check_application_payload(wheels[0], sources[0], Path(__file__).resolve().parents[1])
    print(
        "Release identity, pre-alpha metadata, licenses, archive exclusions "
        f"and exact application payload ({count} files) passed."
    )


if __name__ == "__main__":
    import sys

    check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist"))
