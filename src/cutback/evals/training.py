"""Explicit local LoRA training contracts; no hosted trainer or model download."""

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from cutback.core.models import Identifier, StrictModel


class TrainingSpec(StrictModel):
    version: Literal["local-lora-v1"] = "local-lora-v1"
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_model_ref: Identifier
    base_model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_rights_ref: Identifier
    trained_model_owner: Identifier
    training_rights_confirmed: Literal[True]
    target_source: Literal["gold", "reference"] = "gold"
    teacher_terms_ref: Identifier | None = None
    device: Literal["cpu", "cuda"] = "cpu"
    max_gpu_hours: Decimal = Field(default=Decimal(0), ge=0, le=100)
    max_wall_seconds: int = Field(default=300, ge=10, le=3600)
    max_usd: Decimal = Field(default=Decimal(0), ge=0, le=10000)
    hourly_compute_upper_usd: Decimal = Field(default=Decimal(0), ge=0, le=1000)
    max_steps: int = Field(default=100, ge=1, le=10000)
    max_sequence_tokens: int = Field(default=512, ge=16, le=4096)
    learning_rate: float = Field(default=0.0001, ge=0.000001, le=0.01)
    lora_rank: int = Field(default=8, ge=1, le=64)
    seed: int = Field(default=17, ge=0, le=2**31 - 1)
    retention_days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def rights_and_budget(self):
        if self.target_source == "reference" and not self.teacher_terms_ref:
            raise ValueError("TEACHER_OUTPUT_TRAINING_TERMS_REQUIRED")
        if (
            self.device == "cuda"
            and self.max_gpu_hours < Decimal(self.max_wall_seconds + 10) / 3600
        ):
            raise ValueError("GPU_HOUR_CAP_BELOW_WALL_BOUND")
        if self.device == "cpu" and self.max_gpu_hours:
            raise ValueError("CPU_TRAINING_HAS_NO_GPU_HOURS")
        if self.max_usd < self.reservation():
            raise ValueError("TRAINING_BUDGET_BELOW_COMPUTE_BOUND")
        return self

    def reservation(self):
        return self.hourly_compute_upper_usd * Decimal(self.max_wall_seconds + 10) / 3600


def registered_model(spec: TrainingSpec, *, verify=False):
    registry = json.loads(os.getenv("CUTBACK_TRAINING_MODELS", "{}"))
    record = registry.get(spec.base_model_ref)
    if not isinstance(record, dict) or record.get("sha256") != spec.base_model_sha256:
        raise ValueError("TRAINING_BASE_MODEL_NOT_REGISTERED")
    path = Path(record["path"])
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("TRAINING_BASE_MODEL_PATH_INVALID")
    if verify and model_digest(path) != spec.base_model_sha256:
        raise ValueError("TRAINING_BASE_MODEL_CHANGED")
    return path.resolve()


def model_digest(root: Path):
    """Hash only a bounded, operator-owned safetensors model/tokenizer directory."""
    root = root.resolve(strict=True)
    total, count = 0, 0
    result = hashlib.sha256()
    for directory, dirs, files in os.walk(root, followlinks=False):
        if any((Path(directory) / item).is_symlink() for item in dirs + files):
            raise ValueError("TRAINING_MODEL_SYMLINK_DENIED")
        dirs.sort()
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix not in {".json", ".safetensors", ".txt", ".model"}:
                raise ValueError("TRAINING_MODEL_FILE_TYPE_DENIED")
            total += path.stat().st_size
            count += 1
            if total > 17179869184 or count > 1000:
                raise ValueError("TRAINING_MODEL_RESOURCE_BOUND")
            result.update(path.relative_to(root).as_posix().encode() + b"\0")
            result.update(str(path.stat().st_size).encode() + b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1048576):
                    result.update(chunk)
    if not count:
        raise ValueError("EMPTY_TRAINING_MODEL_DIRECTORY")
    return result.hexdigest()
