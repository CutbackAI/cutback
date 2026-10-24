// SYNTHETIC provider fixtures; real HTTP collector/database. No provider network requests.
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import { createObserver } from "../index.mjs";

const observer = createObserver({
  enabled: true,
  endpoint: process.env.CUTBACK_ENDPOINT ?? "http://127.0.0.1:8080",
  projectId: process.env.CUTBACK_PROJECT_ID,
  serviceName: "node-synthetic-example",
  environment: "staging",
  fetch: async (input) => {
    const url = new URL(input);
    if (url.hostname === "api.openai.com")
      return new Response(
        JSON.stringify({
          id: "resp_synthetic",
          object: "response",
          created_at: 1,
          model: "gpt-4.1-mini",
          status: "completed",
          output: [],
          usage: {
            input_tokens: 20,
            output_tokens: 5,
            total_tokens: 25,
            input_tokens_details: { cached_tokens: 2 },
            output_tokens_details: { reasoning_tokens: 0 },
          },
        }),
        { headers: { "content-type": "application/json" } },
      );
    if (url.hostname === "api.anthropic.com")
      return new Response(
        JSON.stringify({
          id: "msg_synthetic",
          type: "message",
          role: "assistant",
          model: "claude-haiku-4-5",
          content: [{ type: "text", text: "synthetic" }],
          stop_reason: "end_turn",
          stop_sequence: null,
          usage: {
            input_tokens: 20,
            output_tokens: 5,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            cache_creation: {
              ephemeral_5m_input_tokens: 0,
              ephemeral_1h_input_tokens: 0,
            },
          },
        }),
        { headers: { "content-type": "application/json" } },
      );
    throw new Error("Synthetic example refuses all provider network requests.");
  },
});
try {
  const openai = new OpenAI({
    apiKey: "synthetic-not-a-key",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  const anthropic = new Anthropic({
    apiKey: "synthetic-not-a-key",
    fetch: observer.fetch,
    maxRetries: 0,
  });
  await observer.run({ workflowId: "node-synthetic" }, () =>
    openai.responses.create({
      model: "gpt-4.1-mini",
      input: "Synthetic input",
    }),
  );
  await observer.run({ workflowId: "node-synthetic" }, () =>
    anthropic.messages.create({
      model: "claude-haiku-4-5",
      max_tokens: 10,
      messages: [{ role: "user", content: "Synthetic input" }],
    }),
  );
} finally {
  const counters = await observer.close();
  console.log(
    JSON.stringify({
      disclosure:
        "SYNTHETIC provider results; real collector transport; no customer quality claim",
      counters,
    }),
  );
  if (counters.exported !== 2) process.exitCode = 1;
}
