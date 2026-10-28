"""A playground prepares an encrypted budgeted plan; it never runs inference."""

from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import ValidationError

from cutback.core.prompts import PromptError
from cutback.evals.experiments import ExperimentPlan
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.evaluation_api import PlanInput, require_store
from cutback.server.prompt_contracts import PromptPlayground
from cutback.server.prompt_service import load_graph


def install_prompt_playground(app, sessions, authorize, scope, create_plan):
    @app.post("/v1/projects/{project_id}/prompts/playground", status_code=201)
    def prepare_playground(project_id: UUID, body: PromptPlayground, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        spec = body.specification.model_dump(mode="json")
        if body.specification.prompt_bindings or body.specification.source_binding:
            raise HTTPException(422, "PLAYGROUND_REQUIRES_UNBOUND_REGISTRY_DRAFT")
        targets = {m["key"]: m for m in [spec["baseline"], *spec["candidates"]]}
        keys = [s.model_key for s in body.selections]
        if len(keys) != len(set(keys)) or any(key not in targets for key in keys):
            raise HTTPException(422, "PLAYGROUND_MODEL_SELECTIONS_INVALID")
        spec["prompt_bindings"] = []
        schemas = []
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            for selection in body.selections:
                model = targets[selection.model_key]
                if model["provider"] == "router":
                    raise HTTPException(422, "PLAYGROUND_REQUIRES_NATIVE_TARGETS")
                loaded = load_graph(
                    session,
                    require_store(app),
                    project_id,
                    selection.version_id,
                    auth,
                    env_name=selection.environment,
                    workflow=spec["workflow"],
                    provider=model["provider"],
                    purpose=body.purpose,
                )
                surface = (
                    "messages.create"
                    if model["provider"] == "anthropic"
                    else "chat.completions.create"
                    if model["provider"] == "local"
                    else "responses.create"
                )
                try:
                    contract = loaded.graph.native_contract(
                        selection.prompt_variables,
                        surface=surface,
                        sampling_options=selection.sampling_options,
                    )
                except PromptError as exc:
                    raise HTTPException(422, str(exc)) from None
                model["instruction"], model["request_contract"] = (
                    "",
                    contract.model_dump(mode="json"),
                )
                schema = loaded.graph.documents[selection.version_id].output_schema
                if schema is not None and schema not in schemas:
                    schemas.append(schema)
                spec["prompt_bindings"].append(
                    {
                        **selection.model_dump(mode="json"),
                        "version_hash": loaded.versions[selection.version_id].document_hash,
                    }
                )
        if len(schemas) > 1:
            raise HTTPException(422, "PLAYGROUND_OUTPUT_SCHEMAS_REQUIRE_ONE_APPLICATION_CONTRACT")
        if schemas:
            required = {
                "key": "prompt_output_schema",
                "required": True,
                "weight": 1,
                "metric": {"kind": "json_schema", "minimum": 1, "schema_definition": schemas[0]},
            }
            if any(c["key"] == required["key"] for c in spec["criteria"]):
                raise HTTPException(422, "PLAYGROUND_OUTPUT_SCHEMA_CRITERION_KEY_RESERVED")
            spec["criteria"].append(required)
        try:
            prepared = ExperimentPlan.model_validate(spec)
        except ValidationError:
            raise HTTPException(422, "PLAYGROUND_PLAN_CONTRACT_INVALID") from None
        # create_plan rechecks every graph after this preparation transaction ends.
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=prepared), auth
        )
