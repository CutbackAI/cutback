"""Explicit routing draft publication with fresh rights and one-shot dispatch."""

import os
from pathlib import Path

from cutback.core.workspace import read_private, workspace_lock, write_private


def publish_review(
    root,
    control,
    *,
    purpose,
    repository,
    base_branch,
    head_branch,
    publish=False,
    api=None,
):
    from cutback.agent import workspace
    from cutback.agent_routing_program import current, proposal
    from cutback.application_checks import source_root
    from cutback.review_publication import GitHubReviewAPI, publish_prepared
    from cutback.routing_publication import prepare_publication

    if type(publish) is not bool:
        raise ValueError("REVIEW_PUBLICATION_REQUIRES_EXPLICIT_BOOLEAN")
    root, directory = workspace(source_root(Path(root)))
    saved = current(root, control)
    inputs_path = directory / "routing-program-review-inputs.json"
    if not inputs_path.is_file():
        raise ValueError("REVIEW_PUBLICATION_RUN_ROUTING_FINISH_FIRST")
    inputs = read_private(inputs_path)
    if (
        not isinstance(inputs, dict)
        or inputs.get("version") != "cutback.routing-review-inputs.v1"
        or inputs.get("program_id") != saved["program_id"]
        or inputs.get("program_hash") != saved["program_hash"]
        or inputs.get("base") != str(root)
    ):
        raise ValueError("REVIEW_PUBLICATION_ROUTING_REVIEW_INPUTS_CHANGED")
    # A retained source/check receipt never renews revoked content, fit, vendor,
    # confirmation or principal authority. Refresh before any GitHub operation.
    artifact = proposal(root, control, purpose=purpose)
    with workspace_lock(directory):
        latest = current(root, control)
        if (
            inputs != read_private(inputs_path)
            or latest["program_id"] != saved["program_id"]
            or latest["program_hash"] != saved["program_hash"]
        ):
            raise ValueError("REVIEW_PUBLICATION_ROUTING_REVIEW_INPUTS_CHANGED")
        review = read_private(directory / "routing-program-review.json")
        if not isinstance(review, dict) or review.get("review_sha256") != inputs.get(
            "review_sha256"
        ):
            raise ValueError("REVIEW_PUBLICATION_CURRENT_ROUTING_REVIEW_REQUIRED")
        prepared = prepare_publication(
            review,
            artifact,
            inputs["contract"],
            inputs["receipt"],
            root,
            Path(inputs["proposed"]),
            inputs["coverage"],
            branch=inputs["branch"],
            repository=repository,
            base_branch=base_branch,
            head_branch=head_branch,
        )
        if not publish:
            return {
                **prepared["intent"],
                "status": "PREVIEW_ONLY_EXPLICIT_PUBLICATION_REQUIRED",
                "remote_published": False,
                "provider_calls": 0,
                "production_deployed": False,
            }
        path = directory / "routing-program-publication.json"
        journal = (
            read_private(path)
            if path.exists()
            else {"intent": prepared["intent"], "dispatched": []}
        )
        if not isinstance(journal, dict) or journal.get("intent") != prepared["intent"]:
            raise ValueError("REVIEW_PUBLICATION_JOURNAL_INTENT_CHANGED")
        owned = api is None
        if owned:
            api = GitHubReviewAPI(os.environ.get("CUTBACK_GITHUB_TOKEN"))
        try:
            write_private(path, journal)
            return publish_prepared(
                prepared,
                api,
                journal=journal,
                save=lambda value: write_private(path, value),
            )
        finally:
            if owned:
                api.close()
