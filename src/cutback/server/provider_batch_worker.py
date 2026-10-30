"""One durable batch step at a time. Lost billable acknowledgements never resubmit."""

import copy
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import or_, select, true

from cutback.evals.datasets import digest
from cutback.evals.metrics import evaluate, grouped_confidence
from cutback.evals.provider_batch import (
    TERMINAL,
    BatchResult,
    file_sha256,
    input_file,
    reservation_bound,
    result_files,
)
from cutback.server.campaign_content import current_policy
from cutback.server.dataset_lineage import evaluation_receipt, lock_lineage
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.principals import principal_role
from cutback.server.provider_batch_db import ProviderBatchRecord
from cutback.server.provider_batch_service import authorize_batch, batch_inputs

ACTIVE = {"QUEUED", "RUNNING", "CANCEL_REQUESTED", "RECONCILE_REQUESTED", "CLEANUP_PENDING"}


def _authority(session, store, identifier, *, settlement=False):
    row = session.get(ProviderBatchRecord, identifier, populate_existing=True)
    if not row or row.state not in (
        {"CANCEL_REQUESTED"} if settlement else {"QUEUED", "RUNNING", "RECONCILE_REQUESTED"}
    ):
        raise ValueError("BATCH_CURRENT_AUTHORITY_DENIED")
    if not settlement and (not row.deadline_at or row.deadline_at <= datetime.now(UTC)):
        raise ValueError("BATCH_CURRENT_DEADLINE_EXPIRED")
    if not settlement and (not row.lease_until or row.lease_until <= datetime.now(UTC)):
        raise ValueError("BATCH_ACTIVE_PROCESSING_DEADLINE_EXPIRED")
    if principal_role(session, row.project_id, row.approved_by or "") not in {
        "admin",
        "project_admin",
    }:
        raise ValueError("BATCH_APPROVING_PRINCIPAL_REVOKED")
    frozen = session.get(ExperimentPlanRecord, row.plan_id, populate_existing=True)
    if (
        not frozen
        or frozen.project_id != row.project_id
        or frozen.approval_status != "consumed"
        or frozen.specification["plan_hash"] != row.specification["plan_hash"]
        or row.approval_hash != digest(row.specification)
    ):
        raise ValueError("BATCH_FROZEN_APPROVAL_CHANGED")
    if not settlement:
        run = session.get(ExperimentRunRecord, row.run_id, populate_existing=True)
        job = session.get(JobRecord, row.job_id, populate_existing=True)
        if (
            not run
            or not job
            or (run.status, job.status, job.execution_kind)
            != ("RUNNING", "RUNNING", "provider_batch")
            or not job.lease_until
            or job.lease_until <= datetime.now(UTC)
        ):
            raise ValueError("BATCH_CANONICAL_RUN_CANCELLED_OR_EXPIRED")
    from cutback.evals.provider_batch import price_reference

    if row.specification["price_reference"] != price_reference():
        raise ValueError("BATCH_PRICE_CONTRACT_CHANGED")
    from cutback.server.db import Environment

    environment = session.get(Environment, row.environment_id)
    current_policy(
        session,
        row.project_id,
        frozen.specification["policy_id"],
        environment.name,
        frozen.specification["public_specification"]["workflow"],
    )


def _ledger(sessions, store, row, plan):
    def check(session):
        if monotonic() >= row._processing_deadline:
            raise ValueError("BATCH_ACTIVE_PROCESSING_DEADLINE_EXPIRED")
        _authority(session, store, row.id)

    return ExecutionLedger(
        sessions,
        row.run_id,
        row.job_id,
        plan,
        row.environment_id,
        authorize=check,
    )


def _update(sessions, identifier, lease, *, pieces=None, state=None, report=None):
    with sessions.begin() as session:
        row = session.get(ProviderBatchRecord, identifier, with_for_update=True)
        if not row or row.lease_token != lease:
            raise ValueError("BATCH_WORKER_LEASE_LOST")
        if pieces is not None:
            row.pieces = pieces
        # A cancellation racing a native call cannot be overwritten by its ACK.
        if state is not None and row.state != "CANCEL_REQUESTED":
            row.state = state
        if report is not None:
            row.report = report
        return row


def _claim(sessions, identifier=None):
    now = datetime.now(UTC)
    with sessions() as session:
        candidate = session.scalar(
            select(ProviderBatchRecord)
            .where(
                ProviderBatchRecord.state.in_(ACTIVE),
                ProviderBatchRecord.id == identifier if identifier else true(),
                or_(
                    ProviderBatchRecord.available_at.is_(None),
                    ProviderBatchRecord.available_at <= now,
                ),
                or_(
                    ProviderBatchRecord.lease_until.is_(None),
                    ProviderBatchRecord.lease_until <= now,
                ),
            )
            .order_by(ProviderBatchRecord.created_at, ProviderBatchRecord.id)
            .limit(1)
        )
    if not candidate:
        return None
    with sessions.begin() as session:
        lock_lineage(session, candidate.project_id)
        row = session.get(ProviderBatchRecord, candidate.id, with_for_update=True)
        if row.state not in ACTIVE or (row.lease_until and row.lease_until > now):
            return None
        if row.available_at and row.available_at > now:
            return None
        if row.lease_token is not None:
            row.active_uncertain_us += row.active_reserved_us
        if row.lease_token is not None and row.state not in {"CLEANUP_PENDING", "CANCEL_REQUESTED"}:
            # A process disappeared during a persisted intent. It may have sent
            # the HTTP request. Only explicit read-only reconciliation can resume.
            row.state = "UNCERTAIN"
            row.report = {
                "code": "BATCH_STEP_CRASHED_NO_AUTOMATIC_REPLAY",
                "billing_complete": False,
            }
            row.lease_token = row.lease_until = None
            row.active_reserved_us = 0  # The crashed step's full hold stays charged.
            return None
        frozen = session.get(ExperimentPlanRecord, row.plan_id)
        limit = frozen.specification["public_specification"]["budget"]["max_wall_seconds"]
        if type(limit) is not int or not 1 <= limit <= 3600:
            raise ValueError("BATCH_ACTIVE_PROCESSING_BUDGET_INVALID")
        remaining = limit * 1000000 - row.active_elapsed_us
        if row.state not in {"CANCEL_REQUESTED", "CLEANUP_PENDING"} and remaining <= 0:
            row.state = "CANCEL_REQUESTED"
        budgeted = row.state not in {"CANCEL_REQUESTED", "CLEANUP_PENDING"}
        reserved = min(180000000, max(1, remaining)) if budgeted else 0
        row.active_reserved_us = reserved
        row.active_elapsed_us += reserved
        row.lease_token, row.lease_until = (
            uuid4(),
            now + timedelta(microseconds=reserved or 180000000),
        )
        row._processing_started = monotonic()
        row._processing_deadline = row._processing_started + (reserved or 180000000) / 1000000
        if not row.started_at and row.state == "QUEUED":
            row.started_at = now
            row.deadline_at = now + timedelta(seconds=row.specification["max_turnaround_seconds"])
            job = session.get(JobRecord, row.job_id, with_for_update=True)
            run = session.get(ExperimentRunRecord, row.run_id, with_for_update=True)
            run.status = job.status = "RUNNING"
            job.lease_until = row.deadline_at + timedelta(minutes=10)
            job.attempts += 1
        return row


def _prepare(sessions, store, row):
    with sessions() as session:
        plan, cases, _, _, _ = authorize_batch(session, store, row)
        frozen = session.get(ExperimentPlanRecord, row.plan_id)
        receipt = evaluation_receipt(session, row.project_id, frozen.dataset_id, plan, row.run_id)
        if not receipt.get("complete"):
            raise ValueError("BATCH_DATASET_LINEAGE_INCOMPLETE")
    ledger, calls, pieces = _ledger(sessions, store, row, plan), [], []
    for model in [plan.baseline, *plan.candidates]:
        part = {
            "model_key": model.key,
            "operation_id": str(uuid4()),
            "state": "READY",
            "file_id": None,
            "batch_id": None,
            "cleanup": "pending",
        }
        requests = []
        for repeat in range(plan.repeats):
            for index, case in enumerate(cases):
                identifier = str(ledger.reserve(reservation_bound(model), model, "evaluation"))
                calls.append(
                    {
                        "call_id": identifier,
                        "model_key": model.key,
                        "case_index": index,
                        "repeat": repeat,
                    }
                )
                requests.append((identifier, model, case["case"]["input"]))
        # Validate every complete file before any upload can happen.
        input_file(requests)
        pieces.append(part)
    with sessions.begin() as session:
        live = session.get(ProviderBatchRecord, row.id, with_for_update=True)
        if live.lease_token != row.lease_token:
            raise ValueError("BATCH_WORKER_LEASE_LOST")
        for item in calls:
            call = session.get(ExecutionCall, UUID(item["call_id"]), with_for_update=True)
            if not call or (call.project_id, call.run_id, call.state, call.provider_batch_id) != (
                row.project_id,
                row.run_id,
                "reserved",
                None,
            ):
                raise ValueError("BATCH_CANONICAL_RESERVATION_SCOPE_MISMATCH")
            call.provider_batch_id = live.id
            call.price_sha256 = live.specification["price_reference"]["sha256"]
        live.calls, live.pieces = calls, pieces
        if live.state != "CANCEL_REQUESTED":
            live.state = "RUNNING"


def _finish(sessions, row, *, state, report):
    with sessions.begin() as session:
        lock_lineage(session, row.project_id)
        live = session.get(ProviderBatchRecord, row.id, with_for_update=True)
        if live.lease_token != row.lease_token:
            raise ValueError("BATCH_WORKER_LEASE_LOST")
        run = (
            session.get(ExperimentRunRecord, row.run_id, with_for_update=True)
            if row.run_id
            else None
        )
        job = session.get(JobRecord, row.job_id, with_for_update=True) if row.job_id else None
        live.state = (
            "CLEANUP_PENDING"
            if any(part.get("file_id") and part.get("cleanup") != "deleted" for part in live.pieces)
            else state
        )
        live.report, live.completed_at = {**report, "final_state": state}, datetime.now(UTC)
        if run:
            run.status, run.results = state, {**run.results, "provider_batch": report}
        if job:
            job.status = state
        session.add(
            AuditEvent(
                project_id=row.project_id,
                actor="provider_batch_worker",
                action="provider_batch_finished",
                details={
                    "batch_id": str(row.id),
                    "state": state,
                    "billing_complete": report.get("billing_complete", False),
                },
            )
        )


def _manifest(sessions, row, plan, cases):
    models = {model.key for model in [plan.baseline, *plan.candidates]}
    expected = {
        (key, index, repeat)
        for key in models
        for index in range(len(cases))
        for repeat in range(plan.repeats)
    }
    actual = {
        (call["model_key"], call["case_index"], call["repeat"])
        for call in row.calls
        if type(call.get("case_index")) is int and type(call.get("repeat")) is int
    }
    identifiers = {UUID(call["call_id"]) for call in row.calls}
    if (
        not expected
        or actual != expected
        or len(row.calls) != len(expected)
        or len(identifiers) != len(expected)
        or len(row.pieces) != len(models)
        or {part["model_key"] for part in row.pieces} != models
    ):
        raise ValueError("BATCH_COMPLETE_FROZEN_MANIFEST_REQUIRED")
    with sessions() as session:
        records = session.execute(
            select(ExecutionCall.id, ExecutionCall.provider_batch_id)
            .where(ExecutionCall.project_id == row.project_id, ExecutionCall.run_id == row.run_id)
            .limit(1001)
        ).all()
    if (
        len(records) != len(expected)
        or {record.id for record in records} != identifiers
        or any(record.provider_batch_id != row.id for record in records)
    ):
        raise ValueError("BATCH_CANONICAL_MANIFEST_INCOMPLETE")


def _cancel_unprepared(sessions, store, row):
    # No upload intent can precede the atomic complete manifest. These reserved
    # slots are provably undispatched even when the process died during preparation.
    with sessions() as session:
        calls = session.scalars(
            select(ExecutionCall)
            .where(ExecutionCall.project_id == row.project_id, ExecutionCall.run_id == row.run_id)
            .limit(1001)
        ).all()
    if len(calls) > 1000 or any(
        call.provider_batch_id is not None
        or call.state not in {"reserved", "uncertain", "not_started"}
        or (call.state == "not_started" and call.spent_usd != Decimal(0))
        or (call.state != "not_started" and call.spent_usd is not None)
        for call in calls
    ):
        raise ValueError("BATCH_UNPREPARED_RECEIPT_STATE_UNQUALIFIED")
    # Settlement requires no content read or new reservation; rights may have expired.
    ledger = _ledger(sessions, store, row, None)
    for call in calls:
        ledger.record(call.id, None, "not_started")
    _finish(
        sessions,
        row,
        state="CANCELLED",
        report={
            "code": "BATCH_PREPARATION_CANCELLED_BEFORE_ANY_UPLOAD_INTENT",
            "billing_complete": True,
        },
    )


def _collect(sessions, store, row, adapter, *, billing_only=False):
    with sessions() as session:
        if billing_only:
            _authority(session, store, row.id, settlement=True)
            plan, cases, _, _, grouping = batch_inputs(
                session, store, row.project_id, session.get(ExperimentPlanRecord, row.plan_id)
            )
        else:
            plan, cases, _, _, grouping = authorize_batch(session, store, row)
    _manifest(sessions, row, plan, cases)
    models = {model.key: model for model in [plan.baseline, *plan.candidates]}
    observed, total = {}, 0
    for part in row.pieces:
        with sessions() as session:
            _authority(session, store, row.id, settlement=billing_only)
        chunks = []
        for field in ("output_file_id", "error_file_id"):
            if part.get(field):
                with sessions() as session:
                    _authority(session, store, row.id, settlement=billing_only)
                chunks.extend(adapter.download(part[field]))
        total += sum(map(len, chunks))
        if total > 32 * 1048576:
            raise ValueError("BATCH_COMBINED_RESULTS_BYTE_BOUND")
        expected = {
            call["call_id"]: models[call["model_key"]]
            for call in row.calls
            if call["model_key"] == part["model_key"]
        }
        if part.get("batch_id") and part.get("total") != len(expected):
            raise ValueError("BATCH_PROVIDER_ITEM_COUNT_SCOPE_MISMATCH")
        if (
            billing_only
            and not part.get("batch_id")
            and part["state"] in {"READY", "UPLOADED", "UPLOADING"}
        ):
            observed.update(
                {
                    identifier: BatchResult(cost_usd=Decimal(0), code="BATCH_REQUEST_NOT_SUBMITTED")
                    for identifier in expected
                }
            )
        else:
            observed.update(result_files(chunks, expected))
    ledger = _ledger(sessions, store, row, plan)
    for identifier, result in observed.items():
        if result.code == "BATCH_REQUEST_NOT_SUBMITTED":
            ledger.record(identifier, None, "not_started")
        else:
            ledger.record(
                identifier, result, "completed" if result.cost_usd is not None else "usage_invalid"
            )
    if billing_only:
        _finish(
            sessions,
            row,
            state="CANCELLED",
            report={
                "code": "BATCH_CANCELLED_KNOWN_LATE_CHARGES_RECORDED",
                "billing_complete": all(
                    result.cost_usd is not None for result in observed.values()
                ),
                "heldout_quality_claim": False,
            },
        )
        return
    results = []
    for key in models:
        calls = [call for call in row.calls if call["model_key"] == key]
        repeated = []
        for repeat in range(plan.repeats):
            selected = [call for call in calls if call["repeat"] == repeat]
            answers = [observed[call["call_id"]].output for call in selected]
            metric = grouped_confidence(
                [case["case"] for case in cases],
                answers,
                [case["group_hash"] for case in cases],
                plan.metric,
                evaluate(
                    [case["case"] for case in cases], answers, plan.metric, independent_labels=False
                ),
            )
            criteria = []
            for criterion in plan.criteria:
                evidence = evaluate(
                    [case["case"] for case in cases],
                    answers,
                    criterion.metric,
                    independent_labels=False,
                )
                grouped_confidence(
                    [case["case"] for case in cases],
                    answers,
                    [case["group_hash"] for case in cases],
                    criterion.metric,
                    evidence,
                )
                criteria.append(
                    {
                        "key": criterion.key,
                        "required": criterion.required,
                        "minimum": criterion.metric.minimum,
                        "metric": evidence,
                    }
                )
            repeated.append({"repeat": repeat, "metric": metric, "criteria": criteria})
        known = [observed[call["call_id"]].cost_usd for call in calls]
        results.append(
            {
                "model_key": key,
                "requests": len(calls),
                "known_cost_requests": sum(value is not None for value in known),
                "known_cost_subtotal_usd": str(
                    sum((value for value in known if value is not None), Decimal(0))
                ),
                "modeled_cost_usd": str(
                    sum((value for value in known if value is not None), Decimal(0))
                )
                if all(value is not None for value in known)
                else None,
                "runs": repeated,
            }
        )
    # Cancellation/revocation after receipt may retain late known billing but
    # cannot turn those bytes into an authorized result or new quality claim.
    with sessions() as session:
        _authority(session, store, row.id)
    billing = all(result.cost_usd is not None for result in observed.values())
    report = {
        "execution_mode": "provider_batch",
        "billing_complete": billing,
        "models": results,
        "input_grouping": grouping,
        "heldout_quality_claim": False,
        "recommendations": [],
        "per_request_latency_ms": None,
        "turnaround_seconds": (datetime.now(UTC) - row.started_at).total_seconds(),
    }
    _finish(sessions, row, state="COMPLETED" if billing else "FAILED", report=report)


def _recover_input(sessions, store, row, part, adapter, *, content_check):
    if part.get("hint_file_id"):
        found = adapter.input_metadata(
            part["hint_file_id"], operation_id=part["operation_id"], byte_count=part["input_bytes"]
        )
    else:
        found = adapter.find_lost_upload(part["operation_id"], part["input_bytes"])
    if not found:
        raise ValueError("BATCH_UPLOAD_NOT_FOUND_IS_NOT_PROOF_OF_NO_FILE")
    if content_check:
        with sessions() as session:
            _authority(session, store, row.id)
        chunks = adapter.download(found["file_id"])
        if (
            sum(map(len, chunks)) != part["input_bytes"]
            or file_sha256(b"".join(chunks)) != part["input_sha256"]
        ):
            raise ValueError("BATCH_INPUT_FILE_CONTENT_IDENTITY_MISMATCH")
    part["file_id"], part["state"] = found["file_id"], "UPLOADED"
    part.pop("hint_file_id", None)


def _recover_submission(part, adapter):
    if part.get("hint_batch_id"):
        found = adapter.poll(
            part["hint_batch_id"], file_id=part["file_id"], operation_id=part["operation_id"]
        )
    else:
        found = adapter.find_lost_submission(part["file_id"], part["operation_id"])
    if not found:
        raise ValueError("BATCH_SUBMISSION_NOT_FOUND_IS_NOT_PROOF_OF_NO_CHARGE")
    part.update(found)
    part["state"] = "SUBMITTED"
    part.pop("hint_batch_id", None)


def run_batch_one(sessions, store, *, adapter=None, execution_enabled=False, only_batch_id=None):
    if not execution_enabled:
        return {"status": "BATCH_EXECUTION_DISABLED", "provider_calls": 0}
    owned = adapter is None
    if owned:
        key = os.getenv("CUTBACK_EXPERIMENT_OPENAI_KEY")
        if not key:
            return {"status": "NO_BATCH_PROVIDER_KEY", "provider_calls": 0}
        from cutback.evals.provider_batch_sdk import OpenAIBatchAdapter

        adapter = OpenAIBatchAdapter(api_key=key)
    row = None
    try:
        row = _claim(sessions, UUID(str(only_batch_id)) if only_batch_id else None)
        if not row:
            return {"status": "NO_ELIGIBLE_BATCH", "provider_calls": 0}
        adapter.processing_deadline = row._processing_deadline
        pieces = copy.deepcopy(row.pieces)
        if row.state == "CLEANUP_PENDING":
            for part in pieces:
                deleted = part.setdefault("deleted_file_ids", [])
                for field in ("file_id", "output_file_id", "error_file_id"):
                    identifier = part.get(field)
                    if identifier and identifier not in deleted:
                        adapter.delete_file(identifier)
                        deleted.append(identifier)
                        _update(sessions, row.id, row.lease_token, pieces=pieces)
                        return {"status": "CLEANUP_PENDING"}
                part["cleanup"] = "deleted"
            _update(sessions, row.id, row.lease_token, pieces=pieces)
            _finish(sessions, row, state=row.report["final_state"], report=row.report)
            return {"status": row.report["final_state"]}
        if row.state == "CANCEL_REQUESTED":
            # Cancellation/deletion are containment, not authorization to start
            # new inference or download payloads under a revoked content policy.
            if not pieces and row.run_id:
                _cancel_unprepared(sessions, store, row)
                return {"status": "CANCELLED"}
            for part in pieces:
                if part["state"] == "UPLOADING" and not part.get("file_id"):
                    _recover_input(sessions, store, row, part, adapter, content_check=False)
                    _update(sessions, row.id, row.lease_token, pieces=pieces)
                    return {"status": "CANCEL_REQUESTED"}
                if part["state"] == "SUBMITTING" and not part.get("batch_id"):
                    _recover_submission(part, adapter)
                    _update(sessions, row.id, row.lease_token, pieces=pieces)
                    return {"status": "CANCEL_REQUESTED"}
                if part.get("batch_id") and part.get("status") not in TERMINAL:
                    control = adapter.poll if part.get("cancel_sent") else adapter.cancel
                    snapshot = control(
                        part["batch_id"], file_id=part["file_id"], operation_id=part["operation_id"]
                    )
                    part.update(snapshot)
                    part["cancel_sent"] = True
                    _update(sessions, row.id, row.lease_token, pieces=pieces)
                    return {"status": "CANCEL_REQUESTED"}
            if row.run_id and row.calls:
                try:
                    _collect(sessions, store, row, adapter, billing_only=True)
                except ValueError:
                    _finish(
                        sessions,
                        row,
                        state="CANCELLED",
                        report={
                            "code": "BATCH_CANCELLED_CONTENT_RIGHTS_PREVENT_BILLING_RECONCILIATION",
                            "billing_complete": False,
                        },
                    )
            else:
                with sessions() as session:
                    run = session.get(ExperimentRunRecord, row.run_id) if row.run_id else None
                    complete = not run or not run.requests_used
                _finish(
                    sessions,
                    row,
                    state="CANCELLED",
                    report={
                        "code": "BATCH_CANCELLED_COMPLETED_ITEMS_MAY_BE_BILLABLE",
                        "billing_complete": complete,
                    },
                )
            with sessions() as session:
                final = session.get(ProviderBatchRecord, row.id)
                return {"status": final.state}
        with sessions() as session:
            try:
                _authority(session, store, row.id)
            except ValueError:
                _update(
                    sessions,
                    row.id,
                    row.lease_token,
                    state="CANCEL_REQUESTED",
                    report={
                        "code": "BATCH_AUTHORITY_ENDED_CONTAINMENT_REQUIRED",
                        "billing_complete": False,
                    },
                )
                return {"status": "CANCEL_REQUESTED"}
        if row.state == "QUEUED":
            _prepare(sessions, store, row)
            return {"status": "RUNNING", "provider_calls": 0}
        with sessions() as session:
            plan, cases, _, _, _ = authorize_batch(session, store, row)
        _manifest(sessions, row, plan, cases)
        if row.state == "RECONCILE_REQUESTED":
            for part in pieces:
                if part["state"] == "SUBMITTING" and not part.get("batch_id"):
                    _recover_submission(part, adapter)
                elif part["state"] == "UPLOADING":
                    _recover_input(sessions, store, row, part, adapter, content_check=True)
            _update(sessions, row.id, row.lease_token, pieces=pieces, state="RUNNING")
            return {"status": "RUNNING"}
        models = {model.key: model for model in [plan.baseline, *plan.candidates]}
        # Submit every frozen model before polling any long-running job.
        for part in sorted(pieces, key=lambda value: value["state"] not in {"READY", "UPLOADED"}):
            with sessions() as session:
                _authority(session, store, row.id)
            if part["state"] == "READY":
                blob = input_file(
                    [
                        (
                            call["call_id"],
                            models[part["model_key"]],
                            cases[call["case_index"]]["case"]["input"],
                        )
                        for call in row.calls
                        if call["model_key"] == part["model_key"]
                    ]
                )
                part["state"], part["input_bytes"], part["input_sha256"] = (
                    "UPLOADING",
                    len(blob),
                    file_sha256(blob),
                )
                _update(sessions, row.id, row.lease_token, pieces=pieces)
                uploaded = adapter.upload(blob, operation_id=part["operation_id"])
                part.update(uploaded)
                part["state"] = "UPLOADED"
                _update(sessions, row.id, row.lease_token, pieces=pieces)
                return {"status": "RUNNING"}
            if part["state"] == "UPLOADED":
                part["state"] = "SUBMITTING"
                _update(sessions, row.id, row.lease_token, pieces=pieces)
                snapshot = adapter.submit(part["file_id"], part["operation_id"])
                part.update(snapshot)
                part["state"] = "SUBMITTED"
                _update(sessions, row.id, row.lease_token, pieces=pieces)
                return {"status": "RUNNING"}
            if part["state"] == "SUBMITTED" and part.get("status") not in TERMINAL:
                part.update(
                    adapter.poll(
                        part["batch_id"], file_id=part["file_id"], operation_id=part["operation_id"]
                    )
                )
                _update(sessions, row.id, row.lease_token, pieces=pieces)
                return {"status": "RUNNING"}
        if all(part.get("status") in TERMINAL for part in pieces):
            _collect(sessions, store, row, adapter)
            return {"status": "COLLECTED"}
        raise ValueError("BATCH_DURABLE_PHASE_UNQUALIFIED")
    except Exception:
        if row:
            _update(
                sessions,
                row.id,
                row.lease_token,
                state="CLEANUP_PENDING" if row.state == "CLEANUP_PENDING" else "UNCERTAIN",
                report={
                    **row.report,
                    "code": "BATCH_VENDOR_FILE_CLEANUP_RETRY_REQUIRED",
                    "billing_complete": row.report.get("billing_complete", False),
                }
                if row.state == "CLEANUP_PENDING"
                else {"code": "BATCH_STEP_FAILED_NO_AUTOMATIC_REPLAY", "billing_complete": False},
            )
        return {"status": "UNCERTAIN", "details_suppressed": True}
    finally:
        if row:
            with sessions.begin() as session:
                live = session.get(ProviderBatchRecord, row.id, with_for_update=True)
                if live and live.lease_token == row.lease_token:
                    if live.active_reserved_us:
                        spent = max(0, int((monotonic() - row._processing_started) * 1000000))
                        live.active_elapsed_us += spent - live.active_reserved_us
                        live.active_reserved_us = 0
                    live.lease_token = live.lease_until = None
                    live.available_at = datetime.now(UTC) + timedelta(seconds=30)
        if owned:
            adapter.close()
