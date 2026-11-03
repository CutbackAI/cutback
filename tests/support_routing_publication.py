"""Actual checked routing applications; declared gates exercise transport only."""

import shutil
from pathlib import Path
from uuid import uuid4

from support_routing_application import application

import cutback
from cutback.application_checks import ApplicationCommand, git, prepare, run_checks
from cutback.evals.experiments import ModelSpec
from cutback.routing_changes import (
    RoutingSourceProposal,
    inspect_configuration,
    prepare_patch,
    replacement_bytes,
    review_change,
)


def checked_routing(tmp_path, *, learned=True, container=None):
    base = application(tmp_path)
    baseline, binding = inspect_configuration(base, "app.py")
    cheaper = baseline.model_copy(
        update={"key": "cheap", "model": "gpt-4.1-mini", "max_output_tokens": 32}
    )
    routes = (
        [
            {"max_input_bytes": 100, "target": cheaper},
            {"max_input_bytes": binding.max_input_bytes, "target": baseline},
        ]
        if learned
        else [{"max_input_bytes": binding.max_input_bytes, "target": cheaper}]
    )
    proposed_policy = ModelSpec(
        key="selected", provider="router", model="input-size-v1", routes=routes
    )
    # This is a declared unit fixture, not collector/customer quality evidence.
    bound = RoutingSourceProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        baseline=baseline,
        proposed=proposed_policy,
        decision="heldout_review_required",
    )
    artifact = prepare_patch(base, bound)
    proposed = tmp_path / "proposed-routing"
    shutil.copytree(base, proposed)
    (proposed / "routing.json").write_bytes(replacement_bytes(bound))
    git(proposed, "add", "routing.json")
    git(
        proposed,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent checked routing proposal",
    )
    # Import the exact enclosing source/installed package in the native check.
    command = ApplicationCommand(
        key="native-routing-consumer",
        arguments=[
            "-c",
            "import sys; sys.path.insert(0,"
            + repr(str(Path(cutback.__file__).resolve().parents[1]))
            + "); from check_application import main; main()",
        ],
    )
    contract = prepare(base, proposed, [command])
    if container:
        from cutback.application_container import isolate

        # The reviewed image owns its package path, not the host checkout.
        command.arguments = ["check_application.py"]
        contract = isolate(prepare(base, proposed, [command]), container)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"]
    coverage, branch = {"ticket-priority": [command.key]}, "cutback/checked-routing"
    review = review_change(artifact, contract, receipt, base, proposed, coverage, branch=branch)
    entries = {}
    for raw in git(base, "ls-tree", "-z", "HEAD").split(b"\0"):
        if raw:
            header, name = raw.split(b"\t", 1)
            mode, kind, blob = header.decode().split()
            assert kind == "blob"
            entries[name.decode()] = (mode, blob)
    return dict(
        base=base,
        proposed=proposed,
        artifact=artifact,
        contract=contract,
        receipt=receipt,
        coverage=coverage,
        review=review,
        entries=entries,
        options=dict(
            branch=branch,
            repository="example-owner/routing-app",
            base_branch="main",
            head_branch=branch,
        ),
    )
