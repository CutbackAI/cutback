"""Fit a whole source policy from authenticated train-only native receipts."""

from decimal import Context, localcontext

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.evals.datasets import digest
from cutback.evals.experiments import ModelSpec
from cutback.evals.learned_routing import SourcePolicyRoutingTrainingEvidence, fit_routes
from cutback.evals.policy_artifacts import decode_policy_bundle
from cutback.evals.policy_routing_fit import fit_current_routes
from cutback.server.policy_service import validate_binding
from cutback.server.routing_service import authenticated_training


def fit_policy(session, store, project, training_run_id, options):
    """No provider calls, persistence, content grant or heldout quality claim.

    The owner holds the project lock/content transaction and must persist the fit
    with the parent program's immutable derivation before drafting a validation.
    """
    evidence, plan, dataset, policy_id, policy, expiry = authenticated_training(
        session, store, project, training_run_id, source_policy=True
    )
    execution = plan.source_policy_execution
    payloads = validate_binding(session, store, project, plan, dataset.id)
    policies = decode_policy_bundle(payloads, execution)
    original = policies[execution.reference.key]
    if any(row.input_bytes > execution.source.max_input_bytes for row in evidence.observations):
        raise ValueError("POLICY_TRAINING_OUTSIDE_SOURCE_INPUT_DOMAIN")
    with localcontext(Context(prec=80)):
        result = (
            fit_current_routes(evidence, options)
            if isinstance(evidence, SourcePolicyRoutingTrainingEvidence)
            else fit_routes(evidence, options)
        )
    fitted = ModelSpec.model_validate(result["candidate"])
    routes = []
    for route in fitted.routes:
        routes.append(
            route.model_copy(
                update={
                    "max_input_bytes": min(route.max_input_bytes, execution.source.max_input_bytes)
                }
            )
        )
        if route.max_input_bytes >= execution.source.max_input_bytes:
            break
    raw = original.model_dump(mode="json")
    raw["strategy"] = ModelSpec.model_validate(
        {
            **fitted.model_dump(mode="json"),
            "routes": [route.model_dump(mode="json") for route in routes],
        }
    ).model_dump(mode="json")
    # V2 trains native leaves without cache, while its derived evaluation policy
    # keeps the exact current configuration. Activation is a separate frozen flag.
    if execution.version != "cutback.source-policy-execution.v2":
        raw["cache_execution"] = None
    candidate = SourceImprovementPolicy.model_validate(raw)
    return (
        candidate,
        {
            "version": "cutback.source-policy-fit.v1",
            "training_run_id": str(training_run_id),
            "training_plan_sha256": evidence.source_plan_sha256,
            "source_binding_sha256": digest(execution.source.model_dump(mode="json")),
            "dataset_id": str(dataset.id),
            "dataset_sha256": dataset.immutable_hash,
            "options": options.model_dump(mode="json"),
            "fit": result,
            "policy_sha256": candidate.policy_sha256,
            "application_input_max_bytes": execution.source.max_input_bytes,
            "heldout_quality_claim": False,
            "fit_compute_usd": None,
            "routing_hosting_usd": None,
            "source_change_authorized": False,
        },
        policy_id,
        policy,
        expiry,
    )
