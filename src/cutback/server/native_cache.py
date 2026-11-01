"""Direct worker cache backend; no internal HTTP tokens or authority bypass."""

import secrets
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import SecretBytes

from cutback.native_cache import NativeCacheContext, NativeResponseCacheExecution
from cutback.server.cache_api import CacheKey, CachePut, lookup_cache, put_cache
from cutback.server.content_policy import require_payloads_enabled
from cutback.server.principals import principal_role


class WorkerNativeCache(NativeResponseCacheExecution):
    def __init__(self, context: NativeCacheContext, sessions, store, *, actor, authorize):
        if not callable(authorize) or not actor:
            raise ValueError("NATIVE_CACHE_CURRENT_WORKER_AUTHORITY_REQUIRED")
        super().__init__(context)
        self.sessions, self.store = sessions, store
        self.actor, self.authorize = actor, authorize

    def current_authority(self, session):
        require_payloads_enabled()
        self.authorize(session)
        role = principal_role(session, self.scope.project_id, self.actor)
        if role not in {"admin", "project_admin"}:
            raise HTTPException(403, "NATIVE_CACHE_APPROVING_PRINCIPAL_NO_LONGER_AUTHORIZED")
        return {"actor": self.actor, "role": role, "project_id": self.scope.project_id}

    def scope_authority(self, auth, project, roles):
        if project != self.scope.project_id or auth["project_id"] != project:
            raise HTTPException(403, "NATIVE_CACHE_WORKER_PROJECT_SCOPE_DENIED")
        if auth["role"] not in {"admin", "project_admin"}:
            raise HTTPException(403, "NATIVE_CACHE_WORKER_PRINCIPAL_DENIED")

    def _post(self, path, body):
        if path == "/v1/ingest/cache/lookup":
            parsed = CacheKey.model_validate(body)
        elif path == "/v1/ingest/cache/put":
            parsed = CachePut.model_validate(body)
        else:
            raise ValueError("NATIVE_CACHE_WORKER_OPERATION_UNSUPPORTED")
        if any(
            getattr(parsed, name) != getattr(self.scope, name)
            for name in (
                "project_id",
                "policy_id",
                "workflow",
                "environment",
                "prompt_revision",
                "freshness_ref",
            )
        ):
            raise ValueError("NATIVE_CACHE_WORKER_CONTEXT_MISMATCH")
        try:
            if isinstance(parsed, CachePut):
                return 201, put_cache(
                    self.sessions, self.store, parsed, self.current_authority, self.scope_authority
                )
            return 200, lookup_cache(
                self.sessions, self.store, parsed, self.current_authority, self.scope_authority
            )
        except HTTPException as error:
            return error.status_code, None
        except Exception:
            # A successful native call is never repeated for an uncertain write.
            # The shared adapter interprets this fixed error separately on lookup.
            raise ValueError("NATIVE_CACHE_WORKER_STORAGE_UNAVAILABLE") from None


@contextmanager
def authorized_experiment_cache(
    executor,
    plan,
    sessions,
    store,
    *,
    project_id,
    policy_id,
    environment,
    actor,
    run_id,
    job_id,
    authorize,
    inputs,
    source_policy_payloads=None,
):
    """An isolated cache per approved run; the ordinary ledger reserves every call."""
    cached = [model for model in plan.candidates if model.exact_cache is not None]
    if plan.source_policy_execution is not None:
        from cutback.evals.policy_artifacts import decode_policy_bundle
        from cutback.evals.policy_models import policy_leaf_models

        policies = decode_policy_bundle(source_policy_payloads, plan.source_policy_execution)
        cached = [
            model
            for models in policy_leaf_models(plan.source_policy_execution, policies).values()
            for model in models
            if model.exact_cache is not None
        ]
    if not cached:
        yield
        return
    configure = getattr(executor, "configure_exact_cache", None)
    if not callable(configure):
        raise ValueError("EXACT_CACHE_WORKER_EXECUTOR_NOT_QUALIFIED")
    from cutback.evals.datasets import digest
    from cutback.server.cache_api import cache_policy, enabled
    from cutback.server.db import ExperimentRunRecord, JobRecord

    try:
        enabled()
    except HTTPException:
        raise ValueError("EXACT_CACHE_WORKER_CAPABILITY_DISABLED") from None
    require_payloads_enabled()
    hmac_key = secrets.token_bytes(32)

    def current(session):
        authorize(session)
        run = session.get(ExperimentRunRecord, run_id, populate_existing=True)
        job = session.get(JobRecord, job_id, populate_existing=True)
        if (
            not run
            or run.project_id != project_id
            or run.status != "RUNNING"
            or not job
            or job.run_id != run_id
            or job.status != "RUNNING"
            or not job.lease_until
            or job.lease_until <= datetime.now(UTC)
        ):
            raise ValueError("EXACT_CACHE_WORKER_CANCELLED_OR_LEASE_EXPIRED")

    adapters = {}
    from cutback.evals.exact_cache import qualify_cache_inputs
    from cutback.server.evaluation_api import policy_object_count

    for model in cached:
        qualify_cache_inputs(model, inputs)
        specification = model.exact_cache
        context = NativeCacheContext(
            project_id=project_id,
            policy_id=policy_id,
            request_hmac_key=SecretBytes(hmac_key),
            workflow=plan.workflow,
            environment=environment,
            prompt_revision=digest(model.model_dump(mode="json", exclude={"key", "exact_cache"})),
            freshness_ref=f"{specification.freshness_ref}/{run_id.hex}/{digest(model.key)[:16]}",
            ttl_seconds=specification.ttl_seconds,
            content_retention_authorized=specification.content_retention_authorized,
            immutable_or_safely_invalidated=specification.immutable_or_safely_invalidated,
        )
        adapter = WorkerNativeCache(context, sessions, store, actor=actor, authorize=current)
        # Validate every cache candidate before dispatching even the baseline.
        # A missing cache capability must not spend an approved provider budget.
        body = CacheKey(
            project_id=project_id,
            policy_id=policy_id,
            key_sha256="0" * 64,
            workflow=plan.workflow,
            environment=environment,
            provider="openai",
            model=model.model,
            prompt_revision=context.prompt_revision,
            freshness_ref=context.freshness_ref,
        )
        with sessions.begin() as session:
            auth = adapter.current_authority(session)
            policy = cache_policy(session, body, auth, adapter.scope_authority)
            if specification.ttl_seconds > policy.retention_days * 86400:
                raise ValueError("EXACT_CACHE_TTL_OUTSIDE_CURRENT_POLICY")
            if policy_object_count(session, policy_id) + len(inputs) * len(cached) > min(
                policy.max_samples, 10000
            ):
                raise ValueError("EXACT_CACHE_SHARED_PAYLOAD_QUOTA_INSUFFICIENT")
        adapters[model.key] = (model.model_dump(mode="json"), adapter)

    def factory(model):
        frozen, adapter = adapters[model.key]
        if frozen != model.model_dump(mode="json"):
            raise ValueError("EXACT_CACHE_MODEL_OUTSIDE_FROZEN_RUN")
        return adapter

    previous = configure(factory)
    try:
        yield
    finally:
        configure(previous)
