"""Current rights, frozen identities and encrypted curated semantic-cache catalogs."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from sqlalchemy import inspect, select

from cutback.core.semantic_cache import CacheAnchor
from cutback.evals.datasets import Case, digest
from cutback.evals.semantic_cache import CacheQualificationQuery, validate_queries
from cutback.server.campaign_content import current_policy
from cutback.server.dataset_lineage import register_groups, require_unreserved_groups
from cutback.server.db import AccessKey, DatasetRecord, Environment
from cutback.server.enterprise_db import CacheEntry
from cutback.server.semantic_cache_db import SemanticCacheMember, SemanticCacheNamespace
from cutback.server.semantic_cache_models import NamespaceInput
from cutback.server.worker import load_dataset


def namespace(session, project, identifier, *, lock=False):
    row = session.get(
        SemanticCacheNamespace, identifier, with_for_update=lock, populate_existing=True
    )
    if not row or row.project_id != project:
        raise ValueError("SEMANTIC_CACHE_NAMESPACE_NOT_FOUND")
    return row


def authority(session, row, *, serving_actor=None):
    # A namespace can be revoked while an index is being decrypted. Refresh
    # its control row as well as the key/policy; the identity map is not current authority.
    if inspect(row).persistent:
        live = session.get(SemanticCacheNamespace, row.id, populate_existing=True)
        if live is None:
            raise ValueError("SEMANTIC_CACHE_NAMESPACE_NOT_FOUND")
    now = datetime.now(UTC)
    if (
        row.state == "REVOKED"
        or row.expires_at <= now
        or row.specification_hash != digest(row.specification)
    ):
        raise ValueError("SEMANTIC_CACHE_NAMESPACE_EXPIRED_REVOKED_OR_CHANGED")
    spec = NamespaceInput.model_validate(row.specification)
    environment = session.get(Environment, row.environment_id, populate_existing=True)
    key = session.get(AccessKey, row.serving_key_id, populate_existing=True)
    if (
        not environment
        or environment.project_id != row.project_id
        or environment.name != spec.environment
        or not key
        or key.project_id != row.project_id
        or key.environment_id != row.environment_id
        or key.role != "ingest"
        or key.revoked_at
        or key.expires_at
        and key.expires_at <= now
        or serving_actor is not None
        and serving_actor != str(key.id)
        or spec.serving_key_id != key.id
        or spec.policy_id != row.policy_id
        or spec.expires_at != row.expires_at
    ):
        raise ValueError("SEMANTIC_CACHE_SERVING_KEY_OR_SCOPE_DENIED")
    policy = current_policy(session, row.project_id, row.policy_id, spec.environment, spec.workflow)
    if spec.provider not in policy.allowed_providers:
        raise ValueError("SEMANTIC_CACHE_PROVIDER_NOT_AUTHORIZED")
    return spec, policy, key


def catalog(session, row):
    records = session.execute(
        select(SemanticCacheMember, CacheEntry)
        .join(
            CacheEntry,
            (SemanticCacheMember.cache_entry_id == CacheEntry.id)
            & (SemanticCacheMember.project_id == CacheEntry.project_id),
        )
        .where(
            SemanticCacheMember.namespace_id == row.id,
            SemanticCacheMember.project_id == row.project_id,
        )
        .order_by(SemanticCacheMember.cache_entry_id)
        .limit(257)
    ).all()
    if len(records) > min(row.specification["max_entries"], 256):
        raise ValueError("SEMANTIC_CACHE_CATALOG_BOUND")
    if any(
        entry.policy_id != row.policy_id or entry.expires_at <= datetime.now(UTC)
        for _, entry in records
    ):
        raise ValueError("SEMANTIC_CACHE_CATALOG_EXPIRED_OR_OUT_OF_SCOPE")
    return records


def manifest(records):
    return [
        {
            "id": str(entry.id),
            "content_hash": member.content_hash,
            "expires_at": entry.expires_at.isoformat(),
        }
        for member, entry in records
    ]


def read_catalog(session, store, row, *, expected=None, check=None):
    spec, policy, _ = authority(session, row)
    records = catalog(session, row)
    frozen = manifest(records)
    if expected is not None and frozen != expected:
        raise ValueError("SEMANTIC_CACHE_CATALOG_CHANGED_REQUALIFY")
    anchors = []
    for member, entry in records:
        if check:
            check()
        authority(session, row)
        try:
            raw = json.loads(store.get(row.project_id, entry.object_ref))
        except Exception:
            raise ValueError("SEMANTIC_CACHE_CATALOG_CONTENT_UNAVAILABLE") from None
        if (
            digest(raw) != member.content_hash
            or raw.get("namespace_id") != str(row.id)
            or raw.get("namespace_hash") != row.specification_hash
            or raw.get("cache_id") != str(entry.id)
            or raw.get("key_hash") != entry.key_hash
        ):
            raise ValueError("SEMANTIC_CACHE_CATALOG_CONTENT_INTEGRITY_FAILURE")
        from cutback.server.content_policy import policy_content

        policy_content(raw, policy, preserve_semantics=True)
        anchor = CacheAnchor(entry.id, tuple(raw["vector"]), raw["response"])
        anchors.append(anchor)
        if check:
            check()
    return anchors, frozen, spec


def query_key(row, key, query):
    return hmac.new(
        key.digest.encode(),
        b"cutback-semantic-entry-v1:" + row.id.bytes + query.encode(),
        hashlib.sha256,
    ).hexdigest()


def project_query_aliases(session, project, loaded, *, actor, check=None):
    """Bridge raw JSON families to effective query text without retaining that text in metadata."""
    aliases, case_queries = [], {}
    for index, item in enumerate(loaded["items"]):
        if check:
            check()
        case = Case.model_validate(item["case"])
        try:
            value = json.loads(case.input)
        except (ValueError, TypeError):
            raise ValueError("SEMANTIC_CACHE_DATASET_QUERY_JSON_REQUIRED") from None
        if not isinstance(value, dict) or set(value) - {
            "query",
            "vector",
            "baseline_status",
            "baseline_output",
            "embedding_contract_sha256",
        }:
            raise ValueError("SEMANTIC_CACHE_DATASET_QUERY_FIELDS_INVALID")
        query = value.get("query")
        if type(query) is not str or not query or len(query.encode()) > 64000:
            raise ValueError("SEMANTIC_CACHE_DATASET_QUERY_BOUND")
        bridge = "semantic-query:" + digest({"query": query})
        aliases.extend(
            [
                Case(id="raw-" + str(index), input=case.input, group=bridge),
                Case(id="projected-" + str(index), input=query, group=bridge),
            ]
        )
        case_queries[case.id] = value
    groups = register_groups(session, project, aliases, actor=actor)
    require_unreserved_groups(session, project, set(groups.values()))
    return case_queries


def qualification_queries(
    session, store, row, dataset_id, qualification_spec, *, actor, register=False, check=None
):
    spec, _, _ = authority(session, row)
    dataset = session.get(DatasetRecord, dataset_id, populate_existing=True)
    if (
        not dataset
        or dataset.project_id != row.project_id
        or dataset.workflow_id != spec.workflow
        or dataset.provenance.get("policy_id") != str(row.policy_id)
    ):
        raise ValueError("SEMANTIC_CACHE_DATASET_SCOPE_MISMATCH")
    if (
        type(dataset.provenance.get("case_count")) is not int
        or not 2 <= dataset.provenance["case_count"] <= 512
    ):
        raise ValueError("SEMANTIC_CACHE_DATASET_CASE_BOUND")
    deadline = monotonic() + 30

    def bounded():
        if monotonic() >= deadline:
            raise ValueError("SEMANTIC_CACHE_DATASET_READ_TIME_BOUND")
        if check:
            check()
        authority(session, row)

    loaded, _ = load_dataset(
        session, dataset, store, check=bounded, max_cases=512, max_content_bytes=32 * 1048576
    )
    if dataset.provenance["environment"] != spec.environment:
        raise ValueError("SEMANTIC_CACHE_DATASET_ENVIRONMENT_MISMATCH")
    expected_rule = spec.rule.model_dump(mode="json", exclude={"threshold"})
    if qualification_spec.rule.model_dump(mode="json", exclude={"threshold"}) != expected_rule:
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_RULE_DIFFERS_FROM_NAMESPACE")
    if register:
        project_query_aliases(session, row.project_id, loaded, actor=actor, check=bounded)
    from cutback.server.lineage_db import DatasetFamily

    family_ids = {
        UUID(item["lineage_group_id"]) for item in loaded["items"] if item["lineage_group_id"]
    }
    family_rows = session.execute(
        select(DatasetFamily.id, DatasetFamily.canonical_id).where(
            DatasetFamily.project_id == row.project_id, DatasetFamily.id.in_(family_ids)
        )
    ).all()
    families = {str(identity): canonical for identity, canonical in family_rows}
    cases = []
    for index, item in enumerate(loaded["items"]):
        bounded()
        if item["split"] not in {"validation", "test"}:
            continue
        case = item["case"]
        if item["lineage_group_id"] not in families:
            raise ValueError("SEMANTIC_CACHE_DATASET_LINEAGE_INCOMPLETE")
        try:
            value = json.loads(case["input"])
            if value.pop("embedding_contract_sha256", None) != digest(
                spec.rule.embedding.model_dump(mode="json")
            ):
                raise ValueError("SEMANTIC_CACHE_DATASET_EMBEDDING_CONTRACT_MISMATCH")
            value.update(
                {
                    "id": str(UUID(int=index + 1)),
                    "gold": case["gold"],
                    "split": item["split"],
                    "group_sha256": digest({"family": str(families[item["lineage_group_id"]])}),
                }
            )
            cases.append(CacheQualificationQuery.model_validate(value))
        except (ValueError, KeyError, TypeError):
            raise ValueError("SEMANTIC_CACHE_QUALIFICATION_QUERY_SCHEMA_INVALID") from None
    validate_queries(qualification_spec, cases)
    return cases, dataset, loaded
