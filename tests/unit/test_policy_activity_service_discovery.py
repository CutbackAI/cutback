"""Automatic service selection uses bounded source metadata and preserves ambiguity."""

from uuid import uuid4

import pytest
from support_policy_application import application
from test_policy_activity_discovery import Metadata

from cutback.agent_policy_activity import inspect_current

SERVICE_CONTRACT = "cutback.source-policy-runtime-services.v1"


class ServiceMetadata(Metadata):
    def __init__(self):
        super().__init__()
        self.environment = uuid4()
        self.services = ["owned-autodiscovered"]
        self.truncated = False

    def __call__(self, method, path, body=None):
        if method == "GET":
            self.calls.append((method, path, body))
            return {
                "source_policy_runtime_contract": "cutback.source-policy-runtime.v1",
                "source_policy_runtime_services_contract": SERVICE_CONTRACT,
            }
        if path.endswith("/policy-runtime/services"):
            self.calls.append((method, path, body))
            return {
                "project_id": self.project,
                "environment_id": str(self.environment),
                "scope": body,
                "services": self.services,
                "truncated": self.truncated,
            }
        result = super().__call__(method, path, body)
        result["environment_id"] = str(self.environment)
        return result


def test_unique_current_service_is_discovered_without_extra_user_configuration(tmp_path):
    root = application(tmp_path)
    control = ServiceMetadata()
    result = inspect_current(root, control, control.project, producer_file="app.py")
    assert result["summary"]["scope"]["service_name"] == "owned-autodiscovered"
    assert result["provider_calls"] == 0 and len(control.calls) == 3
    assert [value[1].rsplit("/", 1)[-1] for value in control.calls] == [
        "ready",
        "services",
        "inspect",
    ]


@pytest.mark.parametrize(
    "services,truncated", [([], False), (["a", "b"], False), (["a"], True), (["a", "a"], False)]
)
def test_missing_ambiguous_duplicate_or_truncated_metadata_never_picks_a_default(
    tmp_path, services, truncated
):
    root = application(tmp_path)
    control = ServiceMetadata()
    control.services, control.truncated = services, truncated
    with pytest.raises(ValueError):
        inspect_current(root, control, control.project, producer_file="app.py")
    assert len(control.calls) == 2
