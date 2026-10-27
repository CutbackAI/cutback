import React, { useRef, useState } from "react";
import { explain, type API } from "./ui";

export function SpanContent({ api, path }: { api: API; path: string }) {
  const [purpose, setPurpose] = useState("");
  const [value, setValue] = useState<Record<string, string | null> | null>(
    null,
  );
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const version = useRef(0);
  return (
    <details
      onToggle={(e) => {
        if (!e.currentTarget.open) {
          version.current++;
          setValue(null);
          setPurpose("");
          setError("");
          setLoading(false);
        }
      }}
    >
      <summary>Authorized encrypted content</summary>
      <p>
        Reading requires an active rights policy and records your purpose in the
        audit log.
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const current = ++version.current;
          setLoading(true);
          setValue(null);
          setError("");
          try {
            const result = await api(path, {
              method: "POST",
              body: JSON.stringify({ purpose }),
            });
            if (current === version.current) setValue(result.content);
          } catch (err) {
            if (current === version.current) setError(explain(err));
          } finally {
            if (current === version.current) setLoading(false);
          }
        }}
      >
        <label>
          Content read purpose
          <input
            required
            maxLength={200}
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
          />
        </label>
        <button disabled={loading || !purpose}>
          {loading
            ? "Reading authorized content…"
            : "Read encrypted span content"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {value && (
        <>
          <pre>{JSON.stringify(value, null, 2)}</pre>
          <button
            onClick={() => {
              version.current++;
              setValue(null);
            }}
          >
            Hide span content
          </button>
        </>
      )}
    </details>
  );
}
