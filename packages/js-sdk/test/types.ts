import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import { createObserver } from "@cutbackai/sdk";
const observer = createObserver();
const openai = new OpenAI({ fetch: observer.fetch });
const anthropic = new Anthropic({ fetch: observer.fetch });
observer
  .run({ workflowId: "typed" }, () =>
    openai.responses.create({ model: "gpt-4.1-mini", input: "example" }),
  )
  .withResponse();
observer
  .run({ workflowId: "typed" }, () =>
    anthropic.messages.create({
      model: "claude-haiku-4-5",
      max_tokens: 10,
      messages: [],
    }),
  )
  .asResponse();
