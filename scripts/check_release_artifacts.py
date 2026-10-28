"""Check release payloads, metadata and internal-file exclusion before upload."""

import email
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path


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
    print("Release identity, pre-alpha metadata, licenses and archive exclusions passed.")


if __name__ == "__main__":
    import sys

    check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist"))
