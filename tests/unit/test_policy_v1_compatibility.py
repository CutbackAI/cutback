"""Compare old wire/hash identities captured from the qualified pre-v2 installed wheel."""

import hashlib
import itertools
import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import test_source_policy_program_budget as fixture

import cutback.evals.policy_artifacts as artifacts


def test_v1_reference_execution_plan_program_and_budget_remain_byte_identical():
    expected = json.loads(
        (Path(__file__).parents[1] / "fixtures/policy-program-v1-compatibility.json").read_text(
            "utf-8"
        )
    )
    ids, nonces = itertools.count(1), itertools.count(100)
    with (
        patch.object(fixture, "uuid4", side_effect=lambda: UUID(int=next(ids))),
        patch.object(artifacts, "uuid4", side_effect=lambda: UUID(int=next(nonces))),
    ):
        prepared = fixture.program()
        specification = prepared[0]
        execution = specification.template.source_policy_execution
        actual = {
            "reference_json_sha256": hashlib.sha256(
                execution.reference.model_dump_json().encode()
            ).hexdigest(),
            "execution_json_sha256": hashlib.sha256(
                execution.model_dump_json().encode()
            ).hexdigest(),
            "plan_json_sha256": hashlib.sha256(
                specification.template.model_dump_json().encode()
            ).hexdigest(),
            "program_json_sha256": hashlib.sha256(
                specification.model_dump_json().encode()
            ).hexdigest(),
            "budget_sha256": hashlib.sha256(
                json.dumps(
                    fixture.compile_(prepared), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        }
    assert actual == {key: expected[key] for key in actual}


def test_v1_api_draft_keeps_its_existing_request_identity():
    from cutback.server.policy_program_api import SourcePolicyProgramDraft

    expected = json.loads(
        (Path(__file__).parents[1] / "fixtures/policy-program-v1-compatibility.json").read_text(
            "utf-8"
        )
    )
    value = SourcePolicyProgramDraft(
        request_id=UUID("00000000-0000-4000-8000-000000000003"),
        template_plan_id=UUID(int=1),
        baseline_snapshot_id=UUID(int=2),
        budget={"max_usd": "100", "max_requests": 200, "max_wall_seconds": 300},
    )
    assert (
        hashlib.sha256(value.model_dump_json().encode()).hexdigest()
        == expected["draft_json_sha256"]
    )
