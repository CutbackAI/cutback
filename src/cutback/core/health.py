from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from cutback import __version__
from cutback.core.models import Identifier, StrictModel

CounterName = Literal[
    "observed",
    "queued",
    "dropped",
    "exported",
    "rejected",
    "export_failed",
    "normalization_failed",
    "queue_depth",
    "captured_queued",
    "captured_exported",
    "captured_dropped",
    "capture_enabled",
    "gateway_delegated",
    "native_profile_unconfigured",
    "native_profile_unsupported",
    "native_profile_dropped",
    "native_profile_busy",
    "native_profile_queued",
    "native_profile_exported",
    "native_profile_overhead_us_total",
]


class HealthBatch(StrictModel):
    project_id: UUID
    instance_id: UUID
    environment: Identifier
    service_name: Identifier
    sdk_version: Identifier = __version__
    counters: dict[CounterName, Annotated[int, Field(strict=True, ge=0, le=10**15)]]
