"""Explicit, resumable publication of a refreshed campaign application review."""

import os
from pathlib import Path

from cutback.core.workspace import read_private, workspace_lock, write_private


def publish_review(
    root, control, *, purpose, repository, base_branch, head_branch, publish=False, api=None
):
    from cutback.agent import workspace
    from cutback.agent_campaign import current, proposal
    from cutback.campaign_review import review_change
    from cutback.review_publication import GitHubReviewAPI, prepare_publication, publish_prepared

    if type(publish) is not bool:
        raise ValueError("REVIEW_PUBLICATION_REQUIRES_EXPLICIT_BOOLEAN")
    # Refresh current collector rights/confirmation before accessing the source.
    saved = current(root, control)
    inputs = saved.get("application_review_inputs")
    if not isinstance(inputs, dict) or not saved.get("application_review"):
        raise ValueError("REVIEW_PUBLICATION_RUN_CAMPAIGN_REVIEW_FIRST")
    artifact = proposal(root, control, purpose=purpose, sample_only=inputs["sample_only"])
    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        if saved.get("application_review_inputs") != inputs:
            raise ValueError("REVIEW_PUBLICATION_APPLICATION_REVIEW_CHANGED")
        review = review_change(
            artifact,
            inputs["contract"],
            inputs["receipt"],
            Path(inputs["base"]),
            Path(inputs["proposed"]),
            inputs["coverage"],
            branch=inputs["branch"],
        )
        if review != saved["application_review"]:
            raise ValueError("REVIEW_PUBLICATION_APPLICATION_REVIEW_CHANGED")
        prepared = prepare_publication(
            review,
            artifact,
            Path(inputs["base"]),
            Path(inputs["proposed"]),
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
        path = directory / "campaign-publication.json"
        journal = (
            read_private(path)
            if path.exists()
            else {"intent": prepared["intent"], "dispatched": []}
        )
        if journal.get("intent") != prepared["intent"]:
            raise ValueError("REVIEW_PUBLICATION_JOURNAL_INTENT_CHANGED")
        # Construct credentials only after all local review/intent checks pass.
        owned = api is None
        if owned:
            api = GitHubReviewAPI(os.environ.get("CUTBACK_GITHUB_TOKEN"))
        try:
            write_private(path, journal)
            result = publish_prepared(
                prepared, api, journal=journal, save=lambda value: write_private(path, value)
            )
            saved["review_publication"] = result
            write_private(directory / "campaign.json", saved)
            return result
        finally:
            if owned:
                api.close()
