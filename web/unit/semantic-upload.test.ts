import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeCases,
  nextChunk,
  sequenceHash,
} from "../src/semantic-upload.ts";

test("minimal flat cases have the same canonical sequence hash as validated Python Cases", async () => {
  const cases = normalizeCases([
    { id: "fixture-alpha", input: "Owned control", gold: "billing" },
  ]);
  assert.deepEqual(cases, [
    {
      id: "fixture-alpha",
      input: "Owned control",
      gold: "billing",
      reference: null,
      group: null,
      stratum: "default",
    },
  ]);
  assert.equal(
    await sequenceHash(cases),
    "a7d8119f1a099e4c713d8285728841255b03fbe8f6c05f1f1d35f3cb33ff8eba",
  );
});

test("case schema and chunk bound refuse unsupported or overlong content", () => {
  for (const input of [
    [],
    [{ id: "a", input: "x", gold: 1 }],
    [{ id: "a", input: "x", gold: "billing", episode: {} }],
    [
      { id: "a", input: "x", gold: "billing" },
      { id: "a", input: "y", gold: "access" },
    ],
  ]) {
    assert.throws(() => normalizeCases(input));
  }
  const cases = normalizeCases(
    Array.from({ length: 9 }, (_, index) => ({
      id: "case-" + index,
      input: "x".repeat(180000),
      gold: "billing",
    })),
  );
  const chunk = nextChunk(cases, 0);
  assert.ok(chunk.length < 8 && chunk.length > 0);
  assert.ok(new TextEncoder().encode(JSON.stringify(chunk)).length < 900000);
  assert.throws(() =>
    nextChunk(
      normalizeCases([
        { id: "large", input: "x".repeat(900001), gold: "billing" },
      ]),
      0,
    ),
  );
});
