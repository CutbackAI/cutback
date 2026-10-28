import { AsyncLocalStorage } from "node:async_hooks";
import { randomBytes, randomUUID } from "node:crypto";
import * as http from "node:http";
import * as https from "node:https";

const VERSION = "0.1.0-dev.2";
const supported = { openai: "7.43.0", anthropic: "1.3.0" };
const identifier = (value) =>
  typeof value === "string" &&
  /^[A-Za-z0-9_.:/-]{1,200}$/.test(value) &&
  !/^sk[-_]|^bearer/i.test(value)
    ? value
    : null;
const integer = (value) =>
  Number.isSafeInteger(value) && value >= 0 && value <= 1e12 ? value : null;
const uid = (value) =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function endpoint(value) {
  const url = new URL(value);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !(
      url.protocol === "https:" ||
      (url.protocol === "http:" &&
        ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
    )
  )
    throw new Error(
      "Collector must use HTTPS or loopback HTTP, without credentials, query or fragment.",
    );
  return url;
}

/** Native SDK transport hook. No provider calls, payload capture or export occur unless enabled. */
export function createObserver(options = {}) {
  const enabled = options.enabled === true,
    context = new AsyncLocalStorage(),
    capacity = options.capacity ?? 512;
  if (!Number.isInteger(capacity) || capacity < 1 || capacity > 10000)
    throw new Error("Invalid bounded capacity.");
  const service = identifier(options.serviceName || "node-app"),
    environment = identifier(options.environment || "staging");
  if (!service || !environment)
    throw new Error("Invalid service or environment identifier.");
  const queue = [],
    active = new Set(),
    counters = {
      observed: 0,
      queued: 0,
      exported: 0,
      dropped: 0,
      export_failed: 0,
      normalization_failed: 0,
    };
  const instance = randomUUID();
  let closed = false,
    exporting = null;
  const underlying = options.fetch ?? globalThis.fetch.bind(globalThis);
  // Dedicated agents bypass ambient proxy configuration. TLS verification is
  // explicit, including when the application changes global fetch/TLS defaults.
  const httpAgent = new http.Agent({ keepAlive: false, maxSockets: 2 });
  const httpsAgent = new https.Agent({
    keepAlive: false,
    maxSockets: 2,
    rejectUnauthorized: true,
  });
  const directExport = (url, init) =>
    new Promise((resolve, reject) => {
      const transport = url.protocol === "https:" ? https : http;
      const request = transport.request(
        url,
        {
          method: init.method,
          headers: init.headers,
          signal: init.signal,
          agent: url.protocol === "https:" ? httpsAgent : httpAgent,
          rejectUnauthorized: true,
        },
        (response) => {
          response.on("error", reject);
          response.resume();
          resolve(
            new Response(null, {
              status: response.statusCode,
              headers: response.headers,
            }),
          );
        },
      );
      request.on("error", reject);
      request.end(init.body);
    });
  const exportFetch = options.exportFetch ?? directExport;
  let collector, token;
  if (enabled) {
    collector = endpoint(options.endpoint);
    token = options.token ?? process.env.CUTBACK_PROJECT_TOKEN;
    if (
      !uid(options.projectId) ||
      typeof token !== "string" ||
      token.length < 32
    )
      throw new Error(
        "A project UUID and scoped ingestion token are required.",
      );
  }
  const timeout = options.exportTimeoutMs ?? 300;
  if (timeout < 10 || timeout > 2000)
    throw new Error("Export timeout must be 10–2000 ms.");
  const settle = options.observationTimeoutMs ?? 60000;
  if (settle < 10 || settle > 300000)
    throw new Error("Observation deadline must be 10–300000 ms.");
  function emit(event) {
    counters.observed++;
    if (closed || queue.length >= capacity) {
      counters.dropped++;
      return;
    }
    queue.push(event);
    counters.queued++;
  }
  async function send(path, body, deadlineMs = timeout) {
    const response = await exportFetch(new URL(path, collector), {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(deadlineMs),
      redirect: "error",
      credentials: "omit",
    });
    await response.body?.cancel();
    if (!response.ok) throw new Error("EXPORT_FAILED");
  }
  async function flush({ timeoutMs = 1000 } = {}) {
    if (timeoutMs < 0 || timeoutMs > 10000)
      throw new Error("Flush deadline must be 0–10000 ms.");
    if (!enabled) return true;
    if (!exporting)
      exporting = (async () => {
        while (queue.length) {
          const events = queue.splice(0, 50);
          try {
            await send("/v1/ingest/events", {
              schema_version: "1.0",
              project_id: options.projectId,
              events,
            });
            counters.exported += events.length;
          } catch {
            counters.export_failed++;
            counters.dropped += events.length;
          }
        }
      })().finally(() => {
        exporting = null;
      });
    let timer;
    try {
      return await Promise.race([
        exporting.then(() => true),
        new Promise((resolve) => {
          timer = setTimeout(() => resolve(false), timeoutMs);
          timer.unref?.();
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }
  async function healthExport(deadlineMs = timeout) {
    if (!enabled || closed) return;
    try {
      await send(
        "/v1/ingest/health",
        {
          project_id: options.projectId,
          instance_id: instance,
          environment,
          service_name: service,
          sdk_version: VERSION,
          counters: { ...counters, queue_depth: queue.length },
        },
        deadlineMs,
      );
    } catch {
      counters.export_failed++;
    }
  }
  const interval = enabled
    ? setInterval(() => {
        void flush();
        void healthExport();
      }, 10000)
    : null;
  interval?.unref();
  function health() {
    return {
      ...counters,
      queue_depth: queue.length,
      active_observations: active.size,
      enabled: enabled && !closed,
    };
  }
  function run(metadata, fn) {
    const workflowId =
        metadata.workflowId === undefined
          ? null
          : identifier(metadata.workflowId),
      callSiteId = identifier(metadata.callSiteId || "unattributed");
    if ((metadata.workflowId !== undefined && !workflowId) || !callSiteId)
      throw new Error("Invalid attribution identifier.");
    return context.run(
      {
        workflowId,
        callSiteId,
        logicalId: randomUUID(),
        trace: randomBytes(16).toString("hex"),
        attempt: 0,
      },
      fn,
    );
  }

  async function observedFetch(input, init) {
    if (!enabled || closed) return underlying(input, init);
    let url;
    try {
      url = new URL(
        typeof input === "string" || input instanceof URL ? input : input.url,
      );
    } catch {
      return underlying(input, init);
    }
    const provider =
      url.hostname === "api.openai.com"
        ? "openai"
        : url.hostname === "api.anthropic.com"
          ? "anthropic"
          : null;
    const surface =
      provider === "openai"
        ? url.pathname === "/v1/responses"
          ? "responses.create"
          : url.pathname === "/v1/chat/completions"
            ? "chat.completions.create"
            : null
        : provider === "anthropic" && url.pathname === "/v1/messages"
          ? "messages.create"
          : null;
    if (!provider || !surface) return underlying(input, init);
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    if (headers.get("x-stainless-package-version") !== supported[provider]) {
      counters.normalization_failed++;
      return underlying(input, init);
    }
    if (active.size >= capacity) {
      counters.dropped++;
      return underlying(input, init);
    }
    const attribution = context.getStore() ?? {
      logicalId: null,
      workflowId: null,
      callSiteId: "unattributed",
      trace: randomBytes(16).toString("hex"),
      attempt: null,
    };
    let request = {};
    try {
      if (
        typeof init?.body === "string" &&
        Buffer.byteLength(init.body) <= 1048576
      )
        request = JSON.parse(init.body);
    } catch {}
    const requested = identifier(request.model);
    const isText =
      provider === "openai" && surface === "responses.create"
        ? typeof request.input === "string"
        : Array.isArray(request.messages) &&
          request.messages.every((m) => typeof m.content === "string");
    const metering =
      isText && !request.tools && !request.audio && !request.modalities &&
      !request.extra_body && !request.web_search_options
        ? "text_only"
        : "unsupported_units";
    const requestedTier = identifier(request.service_tier);
    request = null; // Keep only approved metadata across the request await.
    const started = performance.now(),
      event = {
        event_schema_version: "1.0",
        event_id: randomUUID(),
        occurred_at: new Date().toISOString(),
        environment,
        service_name: service,
        sdk_version: VERSION,
        trace_id: attribution.trace,
        span_id: randomBytes(8).toString("hex"),
        parent_span_id: null,
        request_attempt_id: randomUUID(),
        logical_request_id: attribution.logicalId,
        call_site_id: attribution.callSiteId,
        workflow_id: attribution.workflowId,
        source_commit: null,
        provider,
        sdk_surface: surface,
        requested_model: requested,
        response_model: null,
        region: null,
        deployment: null,
        service_tier: requestedTier,
        duration_ms: 0,
        status: "incomplete",
        http_status: null,
        error_type: null,
        retry_number:
          attribution.attempt === null ? null : attribution.attempt++,
        input_tokens: null,
        output_tokens: null,
        cache_read_tokens: null,
        cache_write_tokens: null,
        cache_write_1h_tokens: null,
        reasoning_tokens: null,
        usage_source: "unavailable",
        metering_status: metering,
        content_capture_status: "disabled",
        sampling_policy_id: "all-supported-calls-v1",
      };
    let ended = false,
      usage = null,
      complete = false,
      responseModel = null,
      serviceTier = null;
    function finish(status) {
      if (ended) return;
      ended = true;
      clearTimeout(deadline);
      active.delete(finish);
      event.status = status;
      event.duration_ms = Math.min(
        86400000,
        Math.max(0, performance.now() - started),
      );
      event.response_model = responseModel;
      event.service_tier = serviceTier ?? event.service_tier;
      if (usage && status === "success") {
        const data = normalize(provider, usage);
        Object.assign(event, data);
      }
      emit(event);
      usage = null;
    }
    active.add(finish);
    const deadline = setTimeout(() => finish("incomplete"), settle);
    deadline.unref();
    function capture(value) {
      if (ended || !value || typeof value !== "object") return;
      const result = value.response ?? value.message ?? value;
      responseModel = identifier(result.model) ?? responseModel;
      serviceTier = identifier(result.service_tier) ?? serviceTier;
      if (result.usage) usage = { ...(usage ?? {}), ...result.usage };
      if (value.type === "message_delta" && value.usage)
        usage = { ...(usage ?? {}), ...value.usage };
      if (
        value.type === "response.completed" ||
        value.type === "message_stop" ||
        (!value.type && result.object === "chat.completion") ||
        value.type === "message" ||
        (!value.type &&
          result.object === "response" &&
          result.status === "completed")
      )
        complete = true;
      if (
        value.type === "response.incomplete" ||
        value.type === "response.failed" ||
        result.status === "incomplete" ||
        result.status === "failed"
      ) {
        complete = false;
        event.error_type = "provider_incomplete";
        finish("incomplete");
      }
      if (value.type === "response.completed" || value.type === "message_stop")
        finish("success");
    }
    try {
      const response = await underlying(input, init);
      event.http_status = response.status;
      if (!response.ok || !response.body) {
        finish(response.ok ? "incomplete" : "error");
        return response;
      }
      const stream = response.headers
        .get("content-type")
        ?.includes("text/event-stream");
      let buffer = "",
        overflow = false;
      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      const body = new ReadableStream(
        {
          async pull(controller) {
            try {
              const chunk = await reader.read();
              if (chunk.done) {
                buffer += decoder.decode();
                if (!stream && !overflow) {
                  try {
                    capture(JSON.parse(buffer));
                  } catch {}
                }
                buffer = "";
                finish(complete ? "success" : "incomplete");
                controller.close();
                return;
              }
              if (!ended) {
                const decoded =
                  chunk.value.byteLength <= 65536
                    ? decoder.decode(chunk.value, { stream: true })
                    : "";
                if (chunk.value.byteLength > 65536) {
                  overflow = true;
                  buffer = "";
                  counters.normalization_failed++;
                }
                if (stream) {
                  buffer += decoded;
                  let split;
                  while ((split = buffer.indexOf("\n")) >= 0) {
                    const line = buffer.slice(0, split).replace(/\r$/, "");
                    buffer = buffer.slice(split + 1);
                    if (line.startsWith("data: ") && line.length <= 65536) {
                      const text = line.slice(6);
                      if (text === "[DONE]") {
                        complete = true;
                        finish("success");
                      } else {
                        try {
                          capture(JSON.parse(text));
                        } catch {}
                      }
                    }
                  }
                  if (buffer.length > 65536) {
                    buffer = "";
                    overflow = true;
                    counters.normalization_failed++;
                  }
                } else if (!overflow) {
                  buffer += decoded;
                  if (buffer.length > 65536) {
                    buffer = "";
                    overflow = true;
                    counters.normalization_failed++;
                  }
                }
              }
              controller.enqueue(chunk.value);
            } catch (error) {
              buffer = "";
              event.error_type = "stream_transport_error";
              finish("incomplete");
              controller.error(error);
            }
          },
          async cancel(reason) {
            buffer = "";
            finish("cancelled");
            await reader.cancel(reason);
          },
        },
        { highWaterMark: 0 },
      );
      function wrapMetadata(value) {
        for (const key of ["url", "redirected", "type"])
          Object.defineProperty(value, key, { value: response[key] });
        const clone = value.clone.bind(value);
        value.clone = () => wrapMetadata(clone());
        return value;
      }
      return wrapMetadata(
        new Response(body, {
          status: response.status,
          statusText: response.statusText,
          headers: response.headers,
        }),
      );
    } catch (error) {
      event.error_type =
        error?.name === "AbortError" ? "aborted" : "transport_error";
      finish(error?.name === "AbortError" ? "cancelled" : "error");
      throw error;
    }
  }
  async function close({ timeoutMs = 1000 } = {}) {
    const deadline = performance.now() + timeoutMs;
    clearInterval(interval);
    for (const finish of active) finish("incomplete");
    await flush({ timeoutMs });
    const remaining = Math.floor(deadline - performance.now());
    if (remaining > 0) {
      let timer;
      try {
        await Promise.race([
          healthExport(Math.min(timeout, remaining)),
          new Promise((resolve) => {
            timer = setTimeout(resolve, remaining);
            timer.unref?.();
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
    }
    closed = true;
    httpAgent.destroy();
    httpsAgent.destroy();
    if (queue.length) {
      counters.dropped += queue.length;
      queue.length = 0;
    }
    return health();
  }
  return { fetch: observedFetch, run, flush, close, health };
}

function normalize(provider, u) {
  const input = integer(u.input_tokens ?? u.prompt_tokens),
    output = integer(u.output_tokens ?? u.completion_tokens);
  if (input === null || output === null) return {};
  const cached = integer(
    provider === "openai"
      ? (u.input_tokens_details ?? u.prompt_tokens_details)?.cached_tokens
      : u.cache_read_input_tokens,
  );
  const reasoning = integer(
    (u.output_tokens_details ?? u.completion_tokens_details)?.reasoning_tokens,
  );
  if (
    (provider === "openai" && cached !== null && cached > input) ||
    (reasoning !== null && reasoning > output)
  )
    return {};
  let five = integer(u.cache_creation?.ephemeral_5m_input_tokens),
    hour = integer(u.cache_creation?.ephemeral_1h_input_tokens);
  const creation = integer(u.cache_creation_input_tokens);
  if (provider === "anthropic" && creation === 0) {
    const detail = u.cache_creation;
    if ([detail?.ephemeral_5m_input_tokens, detail?.ephemeral_1h_input_tokens]
      .some((value) => value != null && (typeof value !== "number" || value !== 0))) return {};
    five = hour = 0;
  }
  if (provider === "anthropic" && five !== null && hour !== null &&
      creation !== null && five + hour !== creation) return {};
  const details = u.input_tokens_details ?? u.prompt_tokens_details;
  const writes = integer(details?.cache_write_tokens);
  const result = {
    input_tokens: input,
    output_tokens: output,
    cache_read_tokens: cached,
    cache_write_tokens:
      provider === "anthropic" ? five : writes,
    cache_write_1h_tokens:
      provider === "anthropic"
        ? hour
        : null,
    reasoning_tokens: reasoning,
    usage_source: "provider_reported",
  };
  if (provider === "openai" && details?.cache_write_tokens_details != null) {
    const short = integer(details.cache_write_tokens_details.ttl_30m_tokens),
      long = integer(details.cache_write_tokens_details.ttl_12h_tokens);
    if (short === null || long === null || writes === null ||
        short + long !== writes || (cached !== null && cached + writes > input)) return {};
    Object.assign(result, {event_schema_version: "1.1",
      cache_write_30m_tokens: short, cache_write_12h_tokens: long});
  }
  return result;
}
