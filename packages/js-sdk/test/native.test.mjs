import { test } from "node:test";
import assert from "node:assert/strict";
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import { createObserver } from "../index.mjs";

const project = "01111111-1111-4111-8111-111111111111";
const openai = {
  id: "resp_synthetic",
  object: "response",
  created_at: 1,
  model: "gpt-4.1-mini",
  status: "completed",
  output: [
    {
      id: "msg_synthetic",
      type: "message",
      role: "assistant",
      content: [
        { type: "output_text", text: "PRIVATE RESPONSE", annotations: [] },
      ],
    },
  ],
  usage: {
    input_tokens: 20,
    output_tokens: 5,
    input_tokens_details: { cached_tokens: 2 },
    output_tokens_details: { reasoning_tokens: 1 },
    total_tokens: 25,
  },
};
const anthropic = {
  id: "msg_synthetic",
  type: "message",
  role: "assistant",
  model: "claude-haiku-4-5",
  content: [{ type: "text", text: "PRIVATE RESPONSE" }],
  stop_reason: "end_turn",
  stop_sequence: null,
  usage: {
    input_tokens: 20,
    output_tokens: 5,
    cache_read_input_tokens: 3,
    cache_creation_input_tokens: 4,
    cache_creation: {
      ephemeral_5m_input_tokens: 2,
      ephemeral_1h_input_tokens: 2,
    },
  },
};
function setup(fetch, options = {}) {
  const batches = [];
  const observer = createObserver({
    enabled: true,
    endpoint: "http://127.0.0.1:8080",
    projectId: project,
    token: "synthetic-ingestion-token-at-least-32-characters",
    fetch,
    exportFetch: async (url, options) => {
      batches.push(JSON.parse(options.body));
      return new Response("{}", {
        headers: { "content-type": "application/json" },
      });
    },
    ...options,
  });
  return { observer, batches };
}
const events = (batches) => batches.flatMap((b) => b.events ?? []);
const reply = (value) =>
  new Response(JSON.stringify(value), {
    headers: {
      "content-type": "application/json",
      "x-request-id": "native-request-id",
    },
  });

test("native OpenAI APIPromise, response methods, bytes and metadata survive observation", async () => {
  const { observer, batches } = setup(async () => reply(openai));
  const client = new OpenAI({
    apiKey: "synthetic-not-a-key",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  const promise = observer.run(
    { workflowId: "classify", callSiteId: "callsite:test" },
    () =>
      client.responses.create({
        model: "gpt-4.1-mini",
        input: "PRIVATE PROMPT",
      }),
  );
  assert.equal(typeof promise.withResponse, "function");
  assert.equal(typeof promise.asResponse, "function");
  const { data, response, request_id } = await promise.withResponse();
  assert.equal(response instanceof Response, true);
  assert.equal(request_id, "native-request-id");
  assert.equal(data.output_text, "PRIVATE RESPONSE");
  await observer.close();
  const [event] = events(batches);
  assert.equal(event.event_schema_version, "1.0");
  assert.equal(event.status, "success");
  assert.equal(event.input_tokens, 20);
  assert.equal(event.cache_read_tokens, 2);
  assert.equal(event.workflow_id, "classify");
  assert.equal(event.retry_number, 0);
  assert.equal(JSON.stringify(batches).includes("PRIVATE"), false);
});

test("native Anthropic message and terminal SSE usage are observed without changing events", async () => {
  const { observer, batches } = setup(async () => reply(anthropic));
  const client = new Anthropic({
    apiKey: "synthetic-not-a-key",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  assert.deepEqual(
    (
      await client.messages.create({
        model: "claude-haiku-4-5",
        max_tokens: 10,
        messages: [{ role: "user", content: "PRIVATE PROMPT" }],
      })
    ).content,
    anthropic.content,
  );
  await observer.close();
  const [event] = events(batches);
  assert.equal(event.status, "success");
  assert.equal(event.cache_write_tokens, 4);
  assert.equal(event.cache_write_1h_tokens, 2);
  assert.equal(event.response_model, "claude-haiku-4-5");
  const frames = [
    {
      type: "message_start",
      message: {
        ...anthropic,
        usage: { ...anthropic.usage, output_tokens: 0 },
      },
    },
    {
      type: "content_block_start",
      index: 0,
      content_block: { type: "text", text: "" },
    },
    {
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: "PRIVATE STREAM" },
    },
    {
      type: "message_delta",
      delta: { stop_reason: "end_turn", stop_sequence: null },
      usage: { output_tokens: 7 },
    },
    { type: "message_stop" },
  ];
  const streaming = setup(
    async () =>
      new Response(
        frames
          .map(
            (frame) =>
              "event: " +
              frame.type +
              "\ndata: " +
              JSON.stringify(frame) +
              "\n\n",
          )
          .join(""),
        { headers: { "content-type": "text/event-stream" } },
      ),
  );
  const sdk = new Anthropic({
    apiKey: "synthetic",
    fetch: streaming.observer.fetch,
    maxRetries: 0,
  });
  const stream = await sdk.messages.create({
    model: "claude-haiku-4-5",
    max_tokens: 10,
    messages: [{ role: "user", content: "PRIVATE" }],
    stream: true,
  });
  const received = [];
  for await (const frame of stream) received.push(frame);
  assert.deepEqual(received, frames);
  await streaming.observer.close();
  assert.equal(events(streaming.batches)[0].output_tokens, 7);
  assert.equal(JSON.stringify(streaming.batches).includes("PRIVATE"), false);
});

test("native OpenAI stream, retry attempts and error identity remain intact", async () => {
  let attempts = 0;
  const { observer, batches } = setup(async () =>
    ++attempts === 1
      ? new Response('{"error":{"message":"PRIVATE ERROR"}}', {
          status: 429,
          headers: {
            "content-type": "application/json",
            "retry-after-ms": "1",
          },
        })
      : reply(openai),
  );
  const client = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 1,
  });
  await observer.run({ workflowId: "retry" }, () =>
    client.responses.create({ model: "gpt-4.1-mini", input: "PRIVATE" }),
  );
  await observer.close();
  const rows = events(batches);
  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map((r) => r.retry_number),
    [0, 1],
  );
  assert.equal(rows[0].status, "error");
  assert.equal(rows[0].logical_request_id, rows[1].logical_request_id);
  const frames = [
    {
      type: "response.created",
      response: { ...openai, status: "in_progress", usage: null },
    },
    {
      type: "response.output_text.delta",
      delta: "PRIVATE STREAM",
      output_index: 0,
      content_index: 0,
      sequence_number: 1,
    },
    { type: "response.completed", response: openai },
  ];
  const streaming = setup(
    async () =>
      new Response(
        frames
          .map((frame) => "data: " + JSON.stringify(frame) + "\n\n")
          .join("") + "data: [DONE]\n\n",
        { headers: { "content-type": "text/event-stream" } },
      ),
  );
  const sdk = new OpenAI({
    apiKey: "synthetic",
    fetch: streaming.observer.fetch,
    maxRetries: 0,
  });
  const stream = await sdk.responses.create({
    model: "gpt-4.1-mini",
    input: "PRIVATE",
    stream: true,
  });
  assert.equal(typeof stream.controller.abort, "function");
  const received = [];
  for await (const frame of stream) received.push(frame);
  assert.deepEqual(received, frames);
  await streaming.observer.close();
  assert.equal(events(streaming.batches)[0].input_tokens, 20);
  const failed = setup(async () => {
    throw new TypeError("PRIVATE FAILURE");
  });
  const broken = new OpenAI({
    apiKey: "synthetic",
    fetch: failed.observer.fetch,
    maxRetries: 0,
  });
  await assert.rejects(
    () => broken.responses.create({ model: "gpt-4.1-mini", input: "x" }),
    OpenAI.APIConnectionError,
  );
  await failed.observer.close();
  assert.equal(events(failed.batches)[0].error_type, "transport_error");
});

test("outages, bounded queues, abandoned bodies, disabled mode and custom endpoints fail open", async () => {
  const { observer } = setup(async () => reply(openai), {
    capacity: 1,
    exportFetch: async () => {
      throw new Error("collector offline");
    },
  });
  const sdk = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  for (let i = 0; i < 3; i++)
    assert.equal(
      (await sdk.responses.create({ model: "gpt-4.1-mini", input: "x" }))
        .output_text,
      "PRIVATE RESPONSE",
    );
  assert.equal(observer.health().queue_depth, 1);
  assert.equal(observer.health().dropped, 2);
  await observer.close();
  assert.equal(observer.health().dropped, 3);
  const abandoned = setup(async () => reply(openai), {
    observationTimeoutMs: 10,
  });
  const raw = new OpenAI({
    apiKey: "synthetic",
    fetch: abandoned.observer.fetch,
    maxRetries: 0,
  });
  const response = await raw.responses
    .create({ model: "gpt-4.1-mini", input: "x" })
    .asResponse();
  await new Promise((r) => setTimeout(r, 20));
  await abandoned.observer.close();
  assert.equal(events(abandoned.batches)[0].status, "incomplete");
  assert.equal(events(abandoned.batches)[0].input_tokens, null);
  await response.body.cancel();
  const disabled = createObserver({ fetch: async () => reply(openai) });
  assert.equal(
    (
      await new OpenAI({
        apiKey: "synthetic",
        fetch: disabled.fetch,
      }).responses.create({ model: "gpt-4.1-mini", input: "x" })
    ).output_text,
    "PRIVATE RESPONSE",
  );
  assert.equal(disabled.health().observed, 0);
  await disabled.close();
  const custom = setup(async () => reply(openai));
  await new OpenAI({
    baseURL: "https://customer.example/v1",
    apiKey: "synthetic",
    fetch: custom.observer.fetch,
  }).responses.create({ model: "gpt-4.1-mini", input: "x" });
  await custom.observer.close();
  assert.deepEqual(events(custom.batches), []);
});

test("parallel attribution is isolated, usage never invents zero, and wrong SDK versions are skipped", async () => {
  const { observer, batches } = setup(async () => {
    await new Promise((r) => setTimeout(r, 2));
    return reply(openai);
  });
  const client = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  await Promise.all(
    ["first", "second"].map((workflowId) =>
      observer.run({ workflowId }, () =>
        client.responses.create({ model: "gpt-4.1-mini", input: "x" }),
      ),
    ),
  );
  await observer.close();
  const rows = events(batches);
  assert.deepEqual(rows.map((r) => r.workflow_id).sort(), ["first", "second"]);
  assert.notEqual(rows[0].logical_request_id, rows[1].logical_request_id);
  const missing = setup(async () => reply({ ...openai, usage: null }));
  await new OpenAI({
    apiKey: "synthetic",
    fetch: missing.observer.fetch,
  }).responses.create({ model: "gpt-4.1-mini", input: "x" });
  await missing.observer.close();
  assert.equal(events(missing.batches)[0].usage_source, "unavailable");
  assert.equal(events(missing.batches)[0].input_tokens, null);
  const mismatch = setup(async () => reply(openai));
  await mismatch.observer.fetch("https://api.openai.com/v1/responses", {
    headers: { "x-stainless-package-version": "unsupported" },
    method: "POST",
    body: "{}",
  });
  await mismatch.observer.close();
  assert.deepEqual(events(mismatch.batches), []);
  assert.equal(mismatch.observer.health().normalization_failed, 1);
});

test("native chat completion stream and cancellation preserve usage and controller behavior", async () => {
  const chunks = [
    {
      id: "chat_synthetic",
      object: "chat.completion.chunk",
      model: "gpt-4.1-mini",
      created: 1,
      choices: [
        { index: 0, delta: { content: "PRIVATE" }, finish_reason: null },
      ],
    },
    {
      id: "chat_synthetic",
      object: "chat.completion.chunk",
      model: "gpt-4.1-mini",
      created: 1,
      choices: [],
      usage: {
        prompt_tokens: 12,
        completion_tokens: 3,
        total_tokens: 15,
        prompt_tokens_details: { cached_tokens: 0 },
        completion_tokens_details: { reasoning_tokens: 0 },
      },
    },
  ];
  const { observer, batches } = setup(
    async () =>
      new Response(
        chunks
          .map((chunk) => "data: " + JSON.stringify(chunk) + "\n\n")
          .join("") + "data: [DONE]\n\n",
        { headers: { "content-type": "text/event-stream" } },
      ),
  );
  const sdk = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  const stream = await sdk.chat.completions.create({
    model: "gpt-4.1-mini",
    messages: [{ role: "user", content: "PRIVATE" }],
    stream: true,
    stream_options: { include_usage: true },
  });
  const received = [];
  for await (const chunk of stream) received.push(chunk);
  assert.deepEqual(received, chunks);
  await observer.close();
  assert.equal(events(batches)[0].input_tokens, 12);
  const cancelled = setup(
    async () =>
      new Response("data: " + JSON.stringify(chunks[0]) + "\n\n", {
        headers: { "content-type": "text/event-stream" },
      }),
  );
  const client = new OpenAI({
    apiKey: "synthetic",
    fetch: cancelled.observer.fetch,
    maxRetries: 0,
  });
  const partial = await client.chat.completions.create({
    model: "gpt-4.1-mini",
    messages: [{ role: "user", content: "x" }],
    stream: true,
  });
  for await (const _ of partial) {
    break;
  }
  await cancelled.observer.close();
  const row = events(cancelled.batches)[0];
  assert.ok(["cancelled", "incomplete"].includes(row.status));
  assert.equal(row.input_tokens, null);
});

test("large native responses remain readable with bounded metadata observation and collector redirects are rejected", async () => {
  const value = {
    ...openai,
    output: [
      {
        id: "msg_synthetic",
        type: "message",
        role: "assistant",
        content: [
          { type: "output_text", text: "x".repeat(200000), annotations: [] },
        ],
      },
    ],
  };
  const { observer, batches } = setup(async () => reply(value));
  const sdk = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  assert.equal(
    (await sdk.responses.create({ model: "gpt-4.1-mini", input: "x" }))
      .output_text.length,
    200000,
  );
  await observer.close();
  assert.equal(events(batches)[0].usage_source, "unavailable");
  assert.equal(observer.health().normalization_failed, 1);
  assert.throws(
    () =>
      createObserver({
        enabled: true,
        endpoint: "http://remote.example",
        projectId: project,
        token: "x".repeat(40),
      }),
    /HTTPS/,
  );
  assert.throws(
    () =>
      createObserver({
        enabled: true,
        endpoint: "https://collector.example?secret=x",
        projectId: project,
        token: "x".repeat(40),
      }),
    /query/,
  );
});

test("a response.created event is not mistaken for terminal stream completion", async () => {
  const frame = {
    type: "response.created",
    response: { ...openai, status: "in_progress", usage: null },
  };
  const { observer, batches } = setup(
    async () =>
      new Response("data: " + JSON.stringify(frame) + "\n\n", {
        headers: { "content-type": "text/event-stream" },
      }),
  );
  const client = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  const stream = await client.responses.create({
    model: "gpt-4.1-mini",
    input: "x",
    stream: true,
  });
  const received = [];
  for await (const event of stream) received.push(event);
  assert.deepEqual(received, [frame]);
  await observer.close();
  assert.equal(events(batches)[0].status, "incomplete");
  assert.equal(events(batches)[0].input_tokens, null);
});

test("close returns within its explicit deadline during a slow collector outage", async () => {
  const { observer } = setup(async () => reply(openai), {
    exportFetch: async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      throw new Error("synthetic slow collector");
    },
  });
  const client = new OpenAI({
    apiKey: "synthetic",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  await client.responses.create({ model: "gpt-4.1-mini", input: "x" });
  const started = performance.now();
  await observer.close({ timeoutMs: 30 });
  assert.ok(performance.now() - started < 250);
  assert.equal(observer.health().enabled, false);
});
