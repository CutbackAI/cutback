"""Recheck the actual data and rights behind an explicitly synthetic source plan."""


def require_fixture_inputs(plan, dataset, policy):
    if plan.native_fixture is not None and (
        dataset is None
        or dataset.provenance.get("source_type") != "synthetic_fixture"
        or policy.rights_basis != "synthetic"
    ):
        raise ValueError("NATIVE_FIXTURE_REQUIRES_SYNTHETIC_DATA_AND_RIGHTS")
