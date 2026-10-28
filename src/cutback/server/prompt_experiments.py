"""Bind a frozen experiment to exact, currently authorized prompt graphs."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from cutback.server.content_policy import policy_content
from cutback.server.db import AccessKey
from cutback.server.principals import principal_role
from cutback.server.prompt_db import PromptExperimentUse
from cutback.server.prompt_service import environment, load_graph


def validate_bindings(session, store, project, plan, auth):
    models = {m.key: m for m in [plan.baseline, *plan.candidates]}
    loaded = []
    for binding in plan.prompt_bindings or []:
        model = models[binding.model_key]
        contract = model.request_contract
        if contract is None or contract.version != "native-prompt-v1" or model.instruction:
            raise HTTPException(422, "PROMPT_NATIVE_CONTRACT_REQUIRED")
        graph = load_graph(
            session,
            store,
            project,
            binding.version_id,
            auth,
            env_name=binding.environment,
            workflow=plan.workflow,
            provider=model.provider,
        )
        if graph.versions[binding.version_id].document_hash != binding.version_hash:
            raise HTTPException(409, "PROMPT_BINDING_VERSION_HASH_MISMATCH")
        schema = graph.graph.documents[binding.version_id].output_schema
        if schema is not None:
            metrics = [plan.metric, *(c.metric for c in plan.criteria if c.required)]
            if not any(
                m.kind == "json_schema"
                and m.minimum == 1
                and m.output_field is None
                and m.schema_definition == schema
                for m in metrics
            ):
                raise HTTPException(422, "PROMPT_REQUIRED_OUTPUT_SCHEMA_CHECK_MISSING")
        try:
            for policy in graph.policies.values():
                policy_content(
                    {"variables": binding.prompt_variables, "sampling": binding.sampling_options},
                    policy,
                    preserve_semantics=True,
                )
            expected = graph.graph.native_contract(
                binding.prompt_variables,
                surface=contract.sdk_surface,
                sampling_options=binding.sampling_options,
            )
        except ValueError:
            raise HTTPException(422, "PROMPT_VARIABLE_POLICY_OR_TEMPLATE_INVALID") from None
        if expected.model_dump(mode="json") != contract.model_dump(mode="json"):
            raise HTTPException(409, "PROMPT_BINDING_NATIVE_CONTRACT_MISMATCH")
        loaded.append((binding, graph))
    return loaded


def register_bindings(session, project, plan_id, plan, auth, loaded):
    for binding, graph in loaded:
        target = environment(session, project, binding.environment)
        for version in graph.versions:
            session.add(
                PromptExperimentUse(
                    project_id=project,
                    plan_id=plan_id,
                    version_id=version,
                    model_key=binding.model_key,
                    actor=auth["actor"],
                    environment_id=target.id,
                )
            )


def recheck_bindings(session, store, project, plan_row, plan):
    if not plan.prompt_bindings:
        return
    uses = session.scalars(
        select(PromptExperimentUse).where(
            PromptExperimentUse.project_id == project,
            PromptExperimentUse.plan_id == plan_row.id,
        )
    ).all()
    actors = {r.actor for r in uses}
    if len(actors) != 1:
        raise ValueError("PROMPT_EXPERIMENT_CONTENT_AUTHORIZATION_UNAVAILABLE")
    actor = next(iter(actors))
    role = principal_role(session, project, actor)
    key = (
        session.get(AccessKey, UUID(actor))
        if actor not in {"bootstrap_admin"} and not actor.startswith("oidc:")
        else None
    )
    auth = {
        "actor": actor,
        "project_id": project,
        "role": "oidc" if actor.startswith("oidc:") else role,
        "environment_id": key.environment_id if key else None,
    }
    loaded = validate_bindings(session, store, project, plan, auth)
    expected = {
        (b.model_key, version, environment(session, project, b.environment).id)
        for b, graph in loaded
        for version in graph.versions
    }
    actual = {(r.model_key, r.version_id, r.environment_id) for r in uses}
    if actual != expected:
        raise ValueError("PROMPT_EXPERIMENT_CONSUMER_INDEX_MISMATCH")
