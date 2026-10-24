import React, { useId, useState } from "react";

export type Data = Record<string, any>;
export type API = (path: string, options?: RequestInit) => Promise<any>;
export const json = (api: API, path: string, body: unknown, method = "POST") =>
  api(path, { method, body: JSON.stringify(body) });
export const values = (form: HTMLFormElement) =>
  Object.fromEntries(new FormData(form).entries());
export const list = (value: unknown) =>
  String(value || "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
export const money = (value: unknown) =>
  value === null || value === undefined
    ? "Unknown"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 6,
      }).format(Number(value));
export function explain(error: unknown) {
  const code = (error as Error)?.message || "REQUEST_FAILED";
  const messages: Record<string, string> = {
    PAYLOAD_STORAGE_DISABLED:
      "Encrypted content storage is disabled. An operator must authorize and configure it before importing examples.",
    PAYLOAD_STORAGE_NOT_CONFIGURED:
      "The approved encrypted store is not configured on this collector.",
    SCOPE_DENIED: "Your role does not permit this action in this project.",
    INVALID_OR_EXPIRED_KEY:
      "This key is expired or revoked. Connect with an authorized replacement.",
    INVALID_SCHEMA_OR_CONTENT_FIELDS:
      "Check the required fields, allowed identifiers and file format.",
    NO_COMPATIBLE_CHEAPER_MODEL_SUBMIT_REVIEWED_PROMPT_VARIANT:
      "No cheaper supported model is available. Submit a reviewed prompt variant in the advanced plan editor.",
    DATASET_EXCEEDS_MODEL_INPUT_BOUND:
      "These examples exceed the selected input bound. Review input size before planning.",
    PRICE_CONTRACT_CHANGED_REAPPROVAL_REQUIRED:
      "The price catalog changed. Create and approve a new frozen plan.",
    PROJECT_SLUG_EXISTS:
      "That project name already exists. Choose a different name.",
    SCHEDULE_AGGREGATE_BUDGET_TOO_SMALL:
      "The total schedule budget must cover every permitted run and request.",
    LIVE_BILLING_NOT_APPROVED:
      "Live billing is disabled. Use a configured sandbox until launch is authorized.",
    STRIPE_OPERATION_UNCERTAIN_RETRY_SAME_REQUEST_ID:
      "The checkout outcome is uncertain. Retry using the same request reference.",
  };
  return messages[code] || code.replaceAll("_", " ").toLowerCase();
}
export function Field({
  label,
  name,
  hint,
  ...props
}: {
  label: string;
  name: string;
  hint?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <label className="field" htmlFor={id}>
      {label}
      <input id={id} name={name} {...props} />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
export function Select({
  label,
  name,
  children,
  ...props
}: {
  label: string;
  name: string;
} & React.SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <label className="field" htmlFor={id}>
      {label}
      <select id={id} name={name} {...props}>
        {children}
      </select>
    </label>
  );
}
export function Check({
  label,
  name,
  ...props
}: {
  label: string;
  name: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="checkbox">
      <input type="checkbox" name={name} {...props} />
      <span>{label}</span>
    </label>
  );
}
export function Form({
  title,
  description,
  children,
  onSubmit,
  submit = "Save",
  danger = false,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  onSubmit: (
    data: Record<string, FormDataEntryValue>,
  ) => Promise<string | void>;
  submit?: string;
  danger?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  async function handle(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = values(event.currentTarget);
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      setSuccess((await onSubmit(data)) || "Saved.");
    } catch (error) {
      setError(explain(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="editor-form" onSubmit={handle} aria-label={title}>
      <h3>{title}</h3>
      {description && <p className="hint">{description}</p>}
      <fieldset disabled={busy}>
        <legend className="sr-only">{title}</legend>
        <div className="form-grid">{children}</div>
        <button
          className={danger ? "danger" : undefined}
          disabled={busy}
          type="submit"
        >
          {busy ? "Working…" : submit}
        </button>
      </fieldset>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="form-success" role="status">
          {success}
        </p>
      )}
    </form>
  );
}
export function Editor({
  title,
  children,
  open = false,
}: {
  title: string;
  children: React.ReactNode;
  open?: boolean;
}) {
  return (
    <details className="editor" open={open || undefined}>
      <summary>{title}</summary>
      {children}
    </details>
  );
}
export function Tag({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={"tag " + tone}>{children}</span>;
}
export function saveFile(
  data: unknown,
  name: string,
  mime = "application/json",
) {
  const blob =
    data instanceof Blob
      ? data
      : new Blob(
          [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
          { type: mime },
        );
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
