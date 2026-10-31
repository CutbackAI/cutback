import { type Data } from "./ui";

const count = (value: unknown) =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? String(value)
    : "Unknown";

export function ExactCacheContracts({
  plan,
  planId,
}: {
  plan: Data;
  planId: string;
}) {
  const candidates: Data[] = (plan.candidates || []).filter(
    (model: Data) => model.exact_cache,
  );
  if (!candidates.length) return null;
  return (
    <section aria-label={`Exact cache contracts for plan ${planId}`}>
      <h4>Exact cache approval</h4>
      <p>
        Each experiment starts with a separate empty cache. Replays measure this
        declared request sequence; they do not establish production hit rate.
      </p>
      {candidates.map((model) => (
        <div key={model.key}>
          <p>
            <strong>{model.key} · encrypted exact cache</strong>
            <br />
            Freshness: <code>{model.exact_cache.freshness_ref}</code> · TTL:{" "}
            {count(model.exact_cache.ttl_seconds)} seconds
          </p>
          <p>
            Declared upper cost per cache operation:{" "}
            <code>{model.exact_cache.operation_cost_upper_usd} USD</code>
            <br />
            Basis: <code>{model.exact_cache.operation_cost_basis_ref}</code>.
            This is an approved infrastructure bound, not an invoice or measured
            bill.
          </p>
          <p>
            Retention authority:{" "}
            {model.exact_cache.content_retention_authorized === true
              ? "Explicitly authorized"
              : "Unknown"}
            . Immutable or safely invalidated context:{" "}
            {model.exact_cache.immutable_or_safely_invalidated === true
              ? "Explicitly authorized"
              : "Unknown"}
            .
          </p>
        </div>
      ))}
    </section>
  );
}

export function ExactCacheEvidence({
  evidence,
}: {
  evidence: Data | undefined;
}) {
  if (!evidence) return null;
  return (
    <div role="status" aria-label="Exact cache trial">
      <strong>Encrypted exact cache</strong>
      <br />
      {count(evidence.cache_hits)} hits · {count(evidence.native_sdk_calls)}{" "}
      native SDK calls · {count(evidence.logical_requests)} logical requests
      <br />
      {evidence.execution_qualified === true
        ? "Qualified for this replay"
        : evidence.execution_qualified === false
          ? "Cache execution not qualified"
          : "Cache execution unknown"}
      <p className="hint">
        Cached usage is historical and is not priced again. The subtotal
        includes the declared cache-operation upper bound. Production hit rate,
        whole-task cost and invoice-verified savings remain unknown.
      </p>
    </div>
  );
}
