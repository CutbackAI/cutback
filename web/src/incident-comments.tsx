import React, { useEffect, useRef, useState } from "react";
import {
  Check,
  Editor,
  Field,
  Form,
  Select,
  explain,
  json,
  type API,
  type Data,
} from "./ui";

export function IncidentComments({
  api,
  base,
  incident,
  admin,
  editor,
}: {
  api: API;
  base: string;
  incident: string;
  admin: boolean;
  editor: boolean;
}) {
  const path = `${base}/incidents/${incident}/comments`;
  const [page, setPage] = useState<Data | null>(null);
  const [policyPage, setPolicyPage] = useState<Data>({ items: [] });
  const [contents, setContents] = useState<Record<string, Data>>({});
  const [histories, setHistories] = useState<Record<string, Data>>({});
  const [editing, setEditing] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftSequence, setDraftSequence] = useState(0);
  const generation = useRef(0);
  const pending = useRef({
    comment: crypto.randomUUID(),
    version: crypto.randomUUID(),
  });

  async function load() {
    const current = ++generation.current;
    setContents({});
    setEditing(null);
    const [comments, policies] = await Promise.all([
      api(path),
      editor ? api(path + "/policies") : Promise.resolve({ items: [] }),
    ]);
    if (current === generation.current) {
      setPage(comments);
      setPolicyPage(policies);
      setHistories({});
    }
  }
  useEffect(() => {
    let active = true;
    load().catch((e) => {
      if (active) setError(explain(e));
    });
    return () => {
      active = false;
      generation.current++;
    };
  }, [api, path, editor]);

  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  }
  async function acknowledged(message: string) {
    setMessage(message);
    try {
      await load();
    } catch (e) {
      setError(
        "Saved. " + explain(e) + " Refresh comments to inspect the result.",
      );
    }
  }
  const policies: Data[] = policyPage.items;
  return (
    <section aria-label="Incident comments">
      <h3>Team discussion</h3>
      <p>
        Comments are human observations, not verified application outcomes. Text
        stays in the encrypted store. Reading it requires a purpose and current
        project access.
      </p>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <button disabled={busy} onClick={() => action(load)}>
        Refresh comments and hide text
      </button>
      {admin && (
        <Editor title="Authorize encrypted team comments">
          <Form
            title="Comment storage authorization"
            submit="Authorize comment storage"
            description="Only authorize text you may share with this project's readers. Revoking the policy stops future content reads."
            disabled={!policyPage.environment}
            onSubmit={async (f) => {
              await json(api, base + "/capture-policies", {
                enabled: true,
                approval_ref: f.approval,
                rights_basis: f.rights,
                environments: [policyPage.environment],
                workflows: ["incident-comments"],
                allowed_providers: ["local"],
                sample_rate: 1,
                max_samples: Number(f.samples),
                max_payload_bytes: 64000,
                retention_days: Number(f.retention),
                expires_at: new Date(
                  Date.now() + Number(f.expiry) * 86400000,
                ).toISOString(),
              });
              await acknowledged(
                "Encrypted comment storage authorized for this incident's environment.",
              );
              return "Authorization saved.";
            }}
          >
            <Field
              label="Authorization reference"
              name="approval"
              required
              pattern="[A-Za-z0-9_.:/-]+"
            />
            <Select label="Comment data rights" name="rights">
              <option value="owner_authorized">Data owner authorized</option>
              <option value="synthetic">Synthetic examples</option>
              <option value="licensed">Licensed for this purpose</option>
            </Select>
            <Field
              label="Comment retention days"
              name="retention"
              type="number"
              min={1}
              max={30}
              defaultValue={7}
              required
            />
            <Field
              label="Authorization expires in days"
              name="expiry"
              type="number"
              min={1}
              max={30}
              defaultValue={7}
              required
            />
            <Field
              label="Maximum retained comment revisions"
              name="samples"
              type="number"
              min={1}
              max={10000}
              defaultValue={1000}
              required
            />
            <Check
              name="authorized"
              label="I authorize encrypted comment text to be shared with current project readers"
              required
            />
          </Form>
        </Editor>
      )}
      {editor &&
        (policies.length ? (
          <Form
            key={draftSequence}
            title="New incident comment"
            submit="Post encrypted comment"
            onSubmit={async (f) => {
              const request = pending.current;
              await json(
                api,
                path + "/" + request.comment,
                {
                  version_id: request.version,
                  expected_revision: 0,
                  policy_id: f.policy,
                  text: f.text,
                },
                "PUT",
              );
              // Keep both IDs after an uncertain response. A retry reconciles the same
              // canonical intent; only an acknowledged write starts another draft.
              pending.current = {
                comment: crypto.randomUUID(),
                version: crypto.randomUUID(),
              };
              setDraftSequence((value) => value + 1);
              await acknowledged("Comment saved in the encrypted store.");
            }}
          >
            <Select name="policy" label="Approved comment policy" required>
              {policies.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.approval_ref} · up to {p.retention_days} days
                </option>
              ))}
            </Select>
            <label>
              Comment text
              <textarea name="text" required maxLength={8000} rows={4} />
            </label>
          </Form>
        ) : (
          <p>
            An administrator must authorize encrypted comments for this
            environment before you can post.
          </p>
        ))}
      {policyPage.truncated && (
        <p>
          Only the first 100 enabled policies were examined. An administrator
          can review the remaining policy configuration.
        </p>
      )}
      {!page && <p role="status">Loading comment metadata…</p>}
      {page && !page.items.length && (
        <p>No retained comments for this incident.</p>
      )}
      {page?.items.map((row: Data, index: number) => {
        const content = contents[row.id];
        return (
          <article key={row.id} aria-label={`Incident comment ${index + 1}`}>
            <h4>Comment {index + 1}</h4>
            <p>
              {row.creator} · {new Date(row.created_at).toLocaleString()} ·
              revision {row.revision}
            </p>
            {row.deleted ? (
              <p>Comment deleted. Retained metadata records the erasure.</p>
            ) : (
              <>
                <Form
                  title={`Read comment ${index + 1}`}
                  submit="Read comment text"
                  onSubmit={async (f) => {
                    const current = generation.current;
                    const revision =
                      f.revision === "current"
                        ? ""
                        : "?revision=" + Number(f.revision);
                    const value = await json(
                      api,
                      path + "/" + row.id + "/content" + revision,
                      { purpose: f.purpose },
                    );
                    if (current === generation.current)
                      setContents((old) => ({ ...old, [row.id]: value }));
                  }}
                >
                  <Field
                    name="purpose"
                    label="Comment read purpose"
                    required
                    pattern="[A-Za-z0-9_.:/-]+"
                  />
                  <Select name="revision" label="Comment revision">
                    <option value="current">Current revision</option>
                    {histories[row.id]?.items.map((version: Data) => (
                      <option key={version.revision} value={version.revision}>
                        Revision {version.revision}
                      </option>
                    ))}
                  </Select>
                </Form>
                {content && (
                  <div>
                    <p>
                      Read revision {content.revision}; expires{" "}
                      {new Date(content.expires_at).toLocaleString()}.
                    </p>
                    <p
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {content.text}
                    </p>
                    <button
                      onClick={() =>
                        setContents((old) => {
                          const next = { ...old };
                          delete next[row.id];
                          return next;
                        })
                      }
                    >
                      Hide comment text
                    </button>
                  </div>
                )}
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      const current = generation.current;
                      const value = await api(
                        path + "/" + row.id + "/versions",
                      );
                      if (current === generation.current)
                        setHistories((old) => ({ ...old, [row.id]: value }));
                    })
                  }
                >
                  View retained comment revisions
                </button>
                {histories[row.id] && (
                  <p>
                    {histories[row.id].items.length} retained revisions. Expired
                    or deleted text cannot be reconstructed.
                  </p>
                )}
                {editor &&
                  row.can_edit &&
                  content?.revision === row.revision && (
                    <button
                      onClick={() =>
                        setEditing({
                          ...row,
                          text: content.text,
                          version_id: crypto.randomUUID(),
                        })
                      }
                    >
                      Edit this comment
                    </button>
                  )}
              {editing && editing.id === row.id && (
                  <Form
                    key={editing.version_id}
                    title="Revise incident comment"
                    submit="Save encrypted revision"
                    onSubmit={async (f) => {
                      await json(
                        api,
                        path + "/" + row.id,
                        {
                          version_id: editing.version_id,
                          expected_revision: editing.revision,
                          policy_id: f.policy,
                          text: f.text,
                        },
                        "PUT",
                      );
                      setEditing(null);
                      await acknowledged(
                        "New encrypted revision saved; prior retained revisions remain available under their policies.",
                      );
                    }}
                  >
                    <Select
                      name="policy"
                      label="Revision storage policy"
                      required
                    >
                      {policies.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.approval_ref}
                        </option>
                      ))}
                    </Select>
                    <label>
                      Revised comment text
                      <textarea
                        name="text"
                        required
                        maxLength={8000}
                        rows={4}
                        defaultValue={editing.text}
                      />
                    </label>
                    <button type="button" onClick={() => setEditing(null)}>
                      Cancel comment edit
                    </button>
                  </Form>
                )}
                {editor && row.can_delete && (
                  <Form
                    title={`Delete comment ${index + 1}`}
                    submit="Delete comment text"
                    danger
                    onSubmit={async () => {
                      await json(api, path + "/" + row.id + "/delete", {
                        expected_revision: row.revision,
                      });
                      await acknowledged(
                        "All retained text revisions erased. Comment metadata remains for the audit trail.",
                      );
                    }}
                  >
                    <Check
                      name="confirm"
                      label="Erase all retained text revisions of this comment"
                      required
                    />
                  </Form>
                )}
              </>
            )}
          </article>
        );
      })}
      {page?.next_cursor && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const current = generation.current;
              const next = await api(path + "?after=" + page.next_cursor);
              if (current === generation.current)
                setPage({ ...next, items: [...page.items, ...next.items] });
            })
          }
        >
          Load more comments
        </button>
      )}
    </section>
  );
}
