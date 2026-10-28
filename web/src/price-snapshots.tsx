import React, { useEffect, useRef, useState } from "react";
import { type API, type Data, explain } from "./ui";

const price = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "Unknown";

function CacheRates({ rate, provider }: { rate: Data; provider: string }) {
  return rate.input_token_contract ? <>
    30m: {price(rate.cache_write_30m)}<br />12h: {price(rate.cache_write_12h)}
  </> : provider === "anthropic" ? <>
    5m: {price(rate.cache_write)}<br />1h: {price(rate.cache_write_1h)}
  </> : <>No qualified write rate</>;
}

function ContextPremium({ rate }: { rate: Data }) {
  return rate.long_context_threshold != null ? <p className="hint">
    Above {rate.long_context_threshold} input tokens: input/cache ×
    {rate.long_context_input_multiplier}; output ×{rate.long_context_output_multiplier}.
  </p> : null;
}

export function PriceSnapshots({ data, api, base }: { data: Data; api: API; base: string }) {
  const [limit, setLimit] = useState(50);
  const [overrides, setOverrides] = useState<Data[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setOverrides(data.overrides || []);
    setCursor(data.next_after || null);
    setBusy(false);
    setError("");
    return () => { generation.current++; };
  }, [data, base]);
  async function moreOverrides() {
    if (!cursor || busy) return;
    const epoch = generation.current;
    setBusy(true);
    try {
      const page = await api(`${base}/prices?limit=50&after=${encodeURIComponent(cursor)}`);
      if (epoch !== generation.current) return;
      setOverrides((previous) => Array.from(new Map<string, Data>(
        [...previous, ...page.overrides].map((row: Data) => [row.id, row]),
      ).values()));
      setCursor(page.next_after);
      setError("");
    } catch (error) { if (epoch === generation.current) setError(explain(error)); }
    finally { if (epoch === generation.current) setBusy(false); }
  }
  const snapshots: Data[] = Array.isArray(data.observation_catalogs)
    ? data.observation_catalogs
    : data.catalog ? [data.catalog] : [];
  return (
    <section aria-label="Observed price snapshots">
      <h3>Public list-price snapshots</h3>
      <p>
        Rates estimate the cost of observed usage. Missing usage, unsupported
        units or unqualified processing tiers leave the full cost unknown.
        Model and request-contract compatibility is checked separately for paid experiments.
      </p>
      {snapshots.filter((snapshot) => Array.isArray(snapshot.rates)).map((snapshot) => (
        <article key={snapshot.snapshot_id}>
          <h4>{snapshot.snapshot_id}</h4>
          <p>
            Checked {snapshot.checked_at}. {snapshot.snapshot_id === data.catalog?.snapshot_id
              ? "Primary pricing snapshot."
              : "Additional observation snapshot; target execution needs separate qualification."}
          </p>
          <div className="table-scroll" tabIndex={0}>
            <table>
              <caption>{snapshot.snapshot_id} · USD per million tokens</caption>
              <thead><tr>
                <th>Provider / model</th><th>Input</th><th>Cached reads</th>
                <th>Cache writes</th><th>Output</th><th>Source</th>
              </tr></thead>
              <tbody>{snapshot.rates.slice(0, limit).map((rate: Data) => (
                <tr key={`${rate.provider}/${rate.model}`}>
                  <th scope="row">
                    {rate.provider} / {rate.model}
                    <ContextPremium rate={rate} />
                  </th>
                  <td>{price(rate.input)}</td><td>{price(rate.cache_read)}</td>
                  <td><CacheRates rate={rate} provider={rate.provider} /></td>
                  <td>{price(rate.output)}</td>
                  <td><a href={rate.source} target="_blank" rel="noreferrer">
                    Source for {rate.model}
                  </a></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {snapshot.rates.length > limit && <button onClick={() => setLimit((previous) => previous + 50)}>
            Show more rates from {snapshot.snapshot_id}
          </button>}
        </article>
      ))}
      <h3>Effective-dated project rates</h3>
      <p>Owner-declared quotes apply only to their exact provider, model, region,
        tier and time window. Earlier recorded valuations stay frozen.</p>
      {!overrides.length ? <p>No retained project contract rates.</p> : <div className="table-scroll" tabIndex={0}>
        <table>
          <caption>Project contract rates · USD per million tokens</caption>
          <thead><tr><th>Provider / model</th><th>Exact scope</th><th>Effective period</th>
            <th>Input / reads / output</th><th>Cache writes</th></tr></thead>
          <tbody>{overrides.map((row) => <tr key={row.id}>
            <th scope="row">{row.provider} / {row.model}<ContextPremium rate={row.rates} /></th>
            <td>Region: {row.region || "Unspecified (exact match)"}<br />
              Tier: {row.service_tier || "Unspecified (exact match)"}</td>
            <td>{row.valid_from ? new Date(row.valid_from).toLocaleString() : "Start unavailable"}
              <br />{row.valid_to ? `Until ${new Date(row.valid_to).toLocaleString()}` : "No end declared"}</td>
            <td>{price(row.rates.input)} / {price(row.rates.cache_read)} / {price(row.rates.output)}</td>
            <td><CacheRates rate={row.rates} provider={row.provider} /></td>
          </tr>)}</tbody>
        </table>
      </div>}
      {cursor && <button disabled={busy} onClick={moreOverrides}>Load more project rates</button>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
