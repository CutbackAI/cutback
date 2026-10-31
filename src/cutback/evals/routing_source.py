"""Keep fitted policies within the exact source application's existing input domain."""

from cutback.core.routing_producer import RoutingSourceBinding
from cutback.evals.datasets import digest
from cutback.evals.experiments import ModelSpec
from cutback.evals.learned_routing import fit_routes
from cutback.routing_changes import validate_source_models


def fit_source_routes(evidence, options, source: RoutingSourceBinding):
    baseline = next(model for model in evidence.models if model.key == evidence.baseline_key)
    validate_source_models(source, baseline, evidence.models)
    if any(row.input_bytes > source.max_input_bytes for row in evidence.observations):
        raise ValueError("ROUTING_SOURCE_TRAINING_OUTSIDE_APPLICATION_INPUT_BOUND")
    result = fit_routes(evidence, options)
    fitted = ModelSpec.model_validate(result["candidate"])
    routes = []
    for route in fitted.routes:
        routes.append(
            route.model_copy(
                update={"max_input_bytes": min(route.max_input_bytes, source.max_input_bytes)}
            )
        )
        if route.max_input_bytes >= source.max_input_bytes:
            break
    candidate = fitted.model_copy(update={"routes": routes})
    validate_source_models(source, baseline, [candidate])
    result.update(
        candidate=candidate.model_dump(mode="json"),
        source_binding_sha256=digest(source.model_dump(mode="json")),
        application_input_max_bytes=source.max_input_bytes,
        source_configuration_change_requires_checks=True,
    )
    return result
