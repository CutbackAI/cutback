import React, { useState } from "react";
import { type API, type Data, explain } from "./ui";

export function PatchReview({
  api,
  base,
  change,
}: {
  api: API;
  base: string;
  change: Data;
}) {
  const [purpose, setPurpose] = useState("");
  const [artifact, setArtifact] = useState<Data | null>(null);
  const [error, setError] = useState("");
  return (
    <section className="patch-review">
      <h3>Source change ready for review</h3>
      <p>
        The local CLI checked the source hash and generated this diff. The
        collector retains that attestation; it cannot inspect your checkout.
        Nothing has been applied or deployed.
      </p>
      <p>
        Encrypted content expires {change.evidence.expires_at}. A scoped admin
        must record a purpose to read it.
      </p>
      <label>
        Purpose for reading source change
        <input
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          maxLength={200}
        />
      </label>
      <button
        disabled={!/^[A-Za-z0-9_.:/-]{3,200}$/.test(purpose)}
        onClick={async () => {
          try {
            setArtifact(
              await api(`${base}/changes/${change.id}/content`, {
                method: "POST",
                body: JSON.stringify({ purpose }),
              }),
            );
            setError("");
          } catch (e) {
            setArtifact(null);
            setError(explain(e));
          }
        }}
      >
        Read authorized diff and rollback
      </button>
      {error && <p role="alert">{error}</p>}
      {artifact && (
        <>
          <p>
            Source: <code>{artifact.source_file}</code> · commit{" "}
            <code>{artifact.source_commit || "No Git commit recorded"}</code>
          </p>
          <h4>Proposed change</h4>
          <pre tabIndex={0}>{artifact.diff}</pre>
          <h4>Rollback</h4>
          <pre tabIndex={0}>{artifact.rollback_diff}</pre>
          <ol>
            {artifact.staging_steps?.map((step: string) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <p>
            Apply only in your application's review branch. A staging budget and
            production decision are separate approvals.
          </p>
        </>
      )}
    </section>
  );
}
