"""Source fits retain existing native frames, input domains and legacy hash shapes."""

from uuid import uuid4

import pytest
from test_learned_routing import options, training
from test_routing_source_changes import application

from cutback.core.input_routing import LearnedRoutingBinding, RoutingTrainingExecution
from cutback.evals.experiments import ModelSpec, route_target
from cutback.evals.routing_source import fit_source_routes
from cutback.routing_changes import inspect_configuration


def owned_evidence(tmp_path):
    root = application(tmp_path)
    baseline, source = inspect_configuration(root, "app.py")
    evidence = training(cheap_passes=[True, True, False, False, True, True])
    small = baseline.model_copy(update={"key": "small", "model": "gpt-4.1-mini"})
    old_key = evidence.baseline_key
    evidence.models = [baseline, small]
    evidence.baseline_key = baseline.key
    for row in evidence.observations:
        if row.model_key == old_key:
            row.model_key = baseline.key
    return evidence, source


def test_source_fit_keeps_original_tail_and_refuses_input_outside_domain(tmp_path):
    evidence, source = owned_evidence(tmp_path)
    result = fit_source_routes(evidence, options(), source)
    candidate = ModelSpec.model_validate(result["candidate"])
    assert candidate.routes[-1].max_input_bytes == 4000
    assert route_target(candidate, "x" * 4000).key == evidence.baseline_key
    with pytest.raises(ValueError, match="NO_APPROVED_ROUTE"):
        route_target(candidate, "x" * 4001)
    assert result["source_patch_eligible"] is False
    assert result["source_configuration_change_requires_checks"] is True
    for row in evidence.observations:
        if row.input_bytes == 70:
            row.input_bytes = 4001
    with pytest.raises(ValueError, match="OUTSIDE_APPLICATION_INPUT_BOUND"):
        fit_source_routes(evidence, options(), source)


def test_source_fit_cannot_change_instructions_or_increase_output_bound(tmp_path):
    evidence, source = owned_evidence(tmp_path)
    for change in [{"instruction": "different"}, {"max_output_tokens": 65}]:
        original = evidence.models[1]
        evidence.models[1] = original.model_copy(update=change)
        with pytest.raises(ValueError, match="PRESERVE_FRAME_AND_OUTPUT_BOUND"):
            fit_source_routes(evidence, options(), source)
        evidence.models[1] = original


def test_optional_source_never_changes_existing_v15_serialized_hash_inputs():
    training = RoutingTrainingExecution()
    assert training.model_dump(mode="json") == {
        "phase": "training",
        "transport": "native_provider",
        "max_cases": 256,
    }
    fitted = LearnedRoutingBinding(fit_id=uuid4(), fit_sha256="a" * 64, training_run_id=uuid4())
    assert set(fitted.model_dump(mode="json")) == {
        "phase",
        "transport",
        "fit_id",
        "fit_sha256",
        "training_run_id",
    }
