from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field

from cutback.core.models import StrictModel


class ScannerConfig(StrictModel):
    languages: list[Literal["python"]] = ["python"]
    exclude: list[str] = Field(default_factory=list)
    include_prompt_literals: Literal[False] = False
    follow_local_call_graph: bool = True
    max_file_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    max_files: int = Field(default=20000, ge=1, le=100000)


class ProjectConfig(StrictModel):
    name: str = "local-project"


class TelemetryConfig(StrictModel):
    enabled: bool = False
    capture_content: Literal[False] = False


class PricesConfig(StrictModel):
    source: Literal["bundled"] = "bundled"
    offline: Literal[True] = True


class ExperimentsConfig(StrictModel):
    automatic_paid_calls: Literal[False] = False
    max_usd_per_run: Literal[0] = 0


class SafetyConfig(StrictModel):
    production_patches_autoapply: Literal[False] = False


class LocalConfig(StrictModel):
    version: Literal[1] = 1
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    prices: PricesConfig = Field(default_factory=PricesConfig)
    experiments: ExperimentsConfig = Field(default_factory=ExperimentsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


def load_config(root: Path) -> LocalConfig:
    path = root / ".cutback" / "config.yaml"
    if not path.exists():
        return LocalConfig()
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("configuration path must stay inside scan root")
    if path.stat().st_size > 65536:
        raise ValueError("configuration exceeds 64 KiB")
    return LocalConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def validate_endpoint(endpoint: str) -> str:
    url = urlsplit(endpoint)
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise ValueError("endpoint must not contain credentials, query, or fragment")
    loopback = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if url.scheme != "https" and not (url.scheme == "http" and loopback):
        raise ValueError("HTTPS required outside loopback")
    return endpoint.rstrip("/")
