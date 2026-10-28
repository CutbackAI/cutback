"""Old or delayed approvals cannot undo a later explicit stop or unsafe receipt."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from support_gateway import gateway_request, issue_gateway_key, prepare_gateway

from cutback.server.gateway_db import GatewayPolicyRow
from cutback.server.gateway_service import finish, reserve

pytestmark = pytest.mark.postgres


def path_for(context):
    return context["path"] + f"/policies/{context['policy']['id']}"


def current(context):
    return context["client"].get(path_for(context), headers=context["headers"]).json()["policy"]


def intent(context, reference=None):
    policy = current(context)
    body = {
        "approval_hash": policy["approval_hash"],
        "expected_state_version": policy["state_version"],
    }
    if reference is not None:
        body["approval_ref"] = str(reference)
    return body


def post(context, action, body):
    return context["client"].post(
        path_for(context) + "/" + action, headers=context["headers"], json=body
    )


@pytest.mark.parametrize("replayed", ["initial", "current_resume", "older_resume"])
def test_consumed_activation_approval_cannot_resume_after_later_disable(api, tenant, replayed):
    context = prepare_gateway(api, tenant)
    with context["app"].state.sessions() as session:
        initial = session.get(GatewayPolicyRow, UUID(context["policy"]["id"])).approval_ref
    references = {"initial": str(initial)}
    assert post(context, "disable", intent(context)).status_code == 200
    for name in ("older_resume", "current_resume"):
        references[name] = str(uuid4())
        body = intent(context, references[name])
        accepted = post(context, "resume", body)
        assert accepted.status_code == 200, accepted.text
        assert post(context, "resume", body).status_code == 200
        assert post(context, "disable", intent(context)).status_code == 200
    # Even an accidental refresh of the version cannot recycle an activation identity.
    replay = post(context, "resume", intent(context, references[replayed]))
    assert replay.status_code == 409
    assert replay.json()["code"] == "GATEWAY_ACTIVATION_APPROVAL_ALREADY_CONSUMED"
    assert current(context)["state"] == "DISABLED"


@pytest.mark.parametrize("same_reference", [True, False])
def test_concurrent_resume_consumes_one_activation_and_same_intent_is_idempotent(
    api, tenant, same_reference
):
    context = prepare_gateway(api, tenant)
    assert post(context, "disable", intent(context)).status_code == 200
    first = str(uuid4())
    references = [first, first if same_reference else str(uuid4())]
    frozen = intent(context)

    def resume(reference):
        return post(context, "resume", {**frozen, "approval_ref": reference}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(resume, references))
    assert statuses == ([200, 200] if same_reference else [200, 409])
    with context["app"].state.sessions() as session:
        row = session.get(GatewayPolicyRow, UUID(context["policy"]["id"]))
        assert len(row.approval_refs) == 2
        assert str(row.approval_ref) in references


def test_a_new_stop_invalidates_an_unused_delayed_resume_and_old_stops_cannot_undo_new_review(
    api, tenant
):
    context = prepare_gateway(api, tenant)
    original_stop = intent(context)
    stopped = post(context, "disable", original_stop)
    assert stopped.status_code == 200
    version = stopped.json()["state_version"]
    assert post(context, "disable", original_stop).json()["state_version"] == version
    delayed = intent(context, uuid4())
    # This intent was reviewed while off but is delayed in a network/client queue.
    again = post(context, "disable", intent(context))
    assert again.status_code == 200 and again.json()["state_version"] == version + 1
    late = post(context, "resume", delayed)
    assert late.status_code == 409 and "STATE_VERSION_CONFLICT" in late.json()["code"]
    assert current(context)["state"] == "DISABLED"
    assert post(context, "resume", intent(context, uuid4())).status_code == 200
    assert post(context, "disable", original_stop).status_code == 409
    assert post(context, "resume", delayed).status_code == 409
    assert current(context)["state"] == "APPROVED"


def test_new_unsafe_evidence_invalidates_prepared_review_even_while_already_blocked(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    calls = [
        reserve(sessions, key["token"], uuid4(), "a" * 64, gateway_request()) for _ in range(2)
    ]
    code = "GATEWAY_ACTUAL_RESPONSE_OUTSIDE_APPROVED_CONTRACT"
    finish(sessions, calls[0], None, code=code)
    delayed = intent(context, uuid4())
    version = current(context)["state_version"]
    finish(sessions, calls[1], None, code=code)
    assert current(context)["state_version"] == version + 1
    finish(sessions, calls[1], None, code=code)
    assert current(context)["state_version"] == version + 1
    assert post(context, "resume", delayed).status_code == 409
    assert post(context, "resume", intent(context, uuid4())).status_code == 200


def test_activation_history_bound_cannot_reset_the_existing_window(api, tenant):
    context = prepare_gateway(api, tenant)
    identity = UUID(context["policy"]["id"])
    sessions = context["app"].state.sessions
    assert post(context, "disable", intent(context)).status_code == 200
    with sessions.begin() as session:
        row = session.get(GatewayPolicyRow, identity)
        row.approval_refs = [str(row.approval_ref), *[str(uuid4()) for _ in range(1023)]]
    response = post(context, "resume", intent(context, uuid4()))
    assert response.status_code == 429
    assert response.json()["code"] == "GATEWAY_APPROVAL_HISTORY_CAPACITY_REACHED"
    with sessions() as session:
        row = session.get(GatewayPolicyRow, identity)
        assert row.state == "DISABLED" and len(row.approval_refs) == 1024
    with pytest.raises(IntegrityError), sessions.begin() as session:
        row = session.get(GatewayPolicyRow, identity)
        row.approval_refs = [*row.approval_refs, str(uuid4())]
