import React, { useEffect, useRef, useState } from "react";
import { explain } from "./ui";

const encode = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
export function handleSSOReturn() {
  const params = new URLSearchParams(location.search);
  if (!params.has("code") && !params.has("error")) return false;
  const message = {
    type: "cutback-oidc-return",
    code: params.get("code"),
    state: params.get("state"),
    error: params.has("error"),
  };
  history.replaceState(null, "", location.pathname);
  if (window.opener) window.opener.postMessage(message, location.origin);
  if (message.state && /^[A-Za-z0-9_-]{43}$/.test(message.state)) {
    const channel = new BroadcastChannel("cutback-oidc-" + message.state);
    channel.postMessage(message);
    setTimeout(() => {
      channel.close();
      window.close();
    }, 100);
  }
  return true;
}
async function post(path: string, body: unknown) {
  const response = await fetch("/v1/auth/oidc/" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    redirect: "error",
    credentials: "omit",
    signal: AbortSignal.timeout(15000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.code || "IDENTITY_LOGIN_FAILED");
  return data;
}
export function SSO({
  connected,
}: {
  connected: (token: string) => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const pending = useRef<{
    verifier: string;
    state: string;
    popup: Window;
    channel: BroadcastChannel;
    expires: number;
  } | null>(null);
  const loginVersion = useRef(0);
  const receive = useRef<(message: any) => Promise<void>>(async () => {});
  const connect = useRef(connected);
  connect.current = connected;
  useEffect(() => {
    let active = true;
    fetch("/v1/auth/config", {
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
    })
      .then((r) => r.json())
      .then((data) => {
        if (active) setEnabled(!!data.browser_login);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    receive.current = async (data) => {
      const current = pending.current;
      if (
        !current ||
        data?.type !== "cutback-oidc-return" ||
        data.state !== current.state
      )
        return;
      const version = loginVersion.current;
      pending.current = null;
      current.channel.close();
      current.popup.close();
      try {
        if (data.error) throw new Error("IDENTITY_LOGIN_DENIED");
        const result = await post("exchange", {
          code: data.code,
          state: current.state,
          verifier: current.verifier,
        });
        if (version === loginVersion.current)
          await connect.current(result.access_token);
      } catch (e) {
        setError(explain(e));
      } finally {
        setBusy(false);
      }
    };
    function message(event: MessageEvent) {
      if (
        event.origin === location.origin &&
        event.source === pending.current?.popup
      )
        void receive.current(event.data);
    }
    const timer = setInterval(() => {
      if (pending.current && Date.now() > pending.current.expires) {
        pending.current.channel.close();
        pending.current.popup.close();
        pending.current = null;
        setBusy(false);
        setError("Identity login expired. Start a new sign-in.");
      }
    }, 1000);
    window.addEventListener("message", message);
    return () => {
      window.removeEventListener("message", message);
      loginVersion.current++;
      clearInterval(timer);
      pending.current?.channel.close();
      pending.current?.popup.close();
      pending.current = null;
    };
  }, []);
  async function start() {
    const version = ++loginVersion.current;
    setError("");
    setBusy(true);
    const popup = window.open(
      "about:blank",
      "cutback-identity",
      "popup,width=560,height=720",
    );
    if (!popup) {
      setError("Allow the identity sign-in popup to continue.");
      setBusy(false);
      return;
    }
    try {
      const verifier = encode(crypto.getRandomValues(new Uint8Array(32)));
      const challenge = encode(
        new Uint8Array(
          await crypto.subtle.digest(
            "SHA-256",
            new TextEncoder().encode(verifier),
          ),
        ),
      );
      const result = await post("start", { challenge });
      if (version !== loginVersion.current) {
        popup.close();
        return;
      }
      const channel = new BroadcastChannel("cutback-oidc-" + result.state);
      channel.onmessage = (event) => void receive.current(event.data);
      pending.current = {
        verifier,
        state: result.state,
        popup,
        channel,
        expires: Date.now() + 300000,
      };
      popup.location.replace(result.authorization_url);
    } catch (e) {
      popup.close();
      setBusy(false);
      setError(explain(e));
    }
  }
  return enabled ? (
    <div className="sso">
      <button disabled={busy} onClick={() => void start()}>
        {busy
          ? "Waiting for identity provider…"
          : "Sign in with organization SSO"}
      </button>
      {busy && (
        <button
          className="secondary"
          onClick={() => {
            loginVersion.current++;
            pending.current?.channel.close();
            pending.current?.popup.close();
            pending.current = null;
            setBusy(false);
          }}
        >
          Cancel identity sign-in
        </button>
      )}
      <p className="hint">
        Identity tokens stay in this page’s memory. Closing this page signs you
        out.
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  ) : null;
}
