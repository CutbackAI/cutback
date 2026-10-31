"""Nonbillable bootstrap contracts; receipts never contain credential values."""

import hashlib
import re
from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime, SecretStr, StringConstraints, field_validator

from cutback.core.models import StrictModel
from cutback.core.request_profiles import fingerprint

BOOTSTRAP_CONTRACT = "cutback.nonbillable-bootstrap.v1"
ScopedRole = Literal["ingest", "viewer", "analyst", "editor", "project_admin", "prompt_reader"]
SetupName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]


class ScopedKeyInput(StrictModel):
    request_id: UUID4
    role: ScopedRole
    environment: SetupName = "staging"
    expires_at: AwareDatetime
    token: SecretStr

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value):
        return value.astimezone(UTC)

    @field_validator("token")
    @classmethod
    def bounded_credential(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value.get_secret_value()):
            raise ValueError("SCOPED_KEY_REQUIRES_GENERATED_URLSAFE_CREDENTIAL")
        return value


class ScopedKeyReceipt(StrictModel):
    id: UUID4
    project_id: UUID
    role: ScopedRole
    environment: SetupName
    expires_at: AwareDatetime
    request_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def scoped_key_fingerprint(body, *, project_id):
    return fingerprint(
        {
            "contract": BOOTSTRAP_CONTRACT,
            "project_id": str(UUID(str(project_id))),
            **body.model_dump(mode="json", exclude={"token"}),
            "token_sha256": hashlib.sha256(body.token.get_secret_value().encode()).hexdigest(),
        }
    )
