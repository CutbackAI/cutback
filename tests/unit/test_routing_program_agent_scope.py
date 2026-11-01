"""A same-project parent cannot silently substitute a different source/template."""

from copy import deepcopy

import pytest

from cutback.agent_routing_program import check_receipt_scope


@pytest.mark.parametrize("field", ["template", "workflow", "source", "missing"])
def test_agent_rejects_changed_program_source_receipt_before_approval(field):
    saved = {
        "request": {"template_plan_id": "owned-template"},
        "workflow": "owned-workflow",
        "source": {"producer_sha256": "a" * 64},
    }
    row = {
        "template_plan_id": "owned-template",
        "specification": {
            "template": {
                "workflow": "owned-workflow",
                "routing_execution": {"source": saved["source"]},
            }
        },
    }
    check_receipt_scope(saved, row)
    changed = deepcopy(row)
    if field == "template":
        changed["template_plan_id"] = "another-template"
    elif field == "workflow":
        changed["specification"]["template"]["workflow"] = "another-workflow"
    elif field == "source":
        changed["specification"]["template"]["routing_execution"]["source"]["producer_sha256"] = (
            "b" * 64
        )
    else:
        changed["specification"]["template"]["routing_execution"] = None
    with pytest.raises(ValueError, match="COLLECTOR_TEMPLATE_OR_SOURCE_CHANGED"):
        check_receipt_scope(saved, changed)
