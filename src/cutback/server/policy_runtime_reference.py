"""Recheck retained exact-scope metadata; no content reads, reservations or provider calls."""

from decimal import Context, localcontext

from cutback.server.policy_activity_api import inspect_activity


def require_reference(session, project, environment, source, policy, reference, *, now=None):
    reference.require_current(source, now=now)
    scope = reference.summary.scope
    cache = policy.cache_execution
    if (
        str(reference.summary.project_id) != str(project)
        or reference.summary.environment_id != environment.id
        or scope.environment != environment.name
        or policy.policy_sha256 != source.policy_sha256
        or cache is None
        or reference.summary.cache_cost_basis_refs != [cache.operation_cost_basis_ref]
    ):
        raise ValueError("POLICY_RUNTIME_REFERENCE_PROJECT_ENVIRONMENT_OR_POLICY_CHANGED")
    with localcontext(Context(prec=64)):
        expected = reference.summary.known_cache_operations * cache.operation_cost_upper_usd
    if reference.summary.cache_component_upper_usd != expected:
        raise ValueError("POLICY_RUNTIME_REFERENCE_DECLARED_CACHE_COST_CHANGED")
    reference.require_retained(inspect_activity(session, project, scope))
    return reference
