"""Bounded historical labels; a newer scan cannot erase older workflow identities."""

from sqlalchemy import func, select

from cutback.core.inventory import workflow_identity
from cutback.server.db import WorkflowRecord


def workflow_labels(session, project):
    entries = session.execute(
        select(
            func.left(WorkflowRecord.entry_point["file"].as_string(), 500),
            func.left(WorkflowRecord.entry_point["symbol"].as_string(), 200),
        )
        .where(WorkflowRecord.project_id == project)
        .order_by(WorkflowRecord.id)
        .limit(1000)
    ).all()
    labels = {}
    for source, symbol in entries:
        if isinstance(source, str) and isinstance(symbol, str):
            identity, name = workflow_identity(source, symbol)
            labels[identity] = f"{source} · {name}"
    return labels
