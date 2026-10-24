import { test } from "node:test";
import assert from "node:assert/strict";
import { csv, parseRecords } from "../src/records.ts";

test("CSV quotes, commas, embedded newlines, CRLF and escaped quotes survive import", () => {
  const rows = parseRecords(
    'input,gold,output\r\n"question, with comma","{\"\"a\"\":1}","line one\nline \"\"two\"\""\r\n',
    "fixture.csv",
  );
  assert.deepEqual(rows, [
    {
      id: "case-00001",
      input: "question, with comma",
      gold: { a: 1 },
      reference: 'line one\nline "two"',
    },
  ]);
});
test("model outputs remain references, while explicit labels retain their shape", () => {
  assert.deepEqual(
    parseRecords('{"input":"example","output":"A"}\n', "fixture.jsonl"),
    [{ id: "case-00001", input: "example", reference: "A" }],
  );
  assert.deepEqual(
    parseRecords(
      '{"cases":[{"input":"example","gold":["a","b"],"group":"g"}]}',
      "fixture.json",
    ),
    [{ id: "case-00001", input: "example", gold: ["a", "b"], group: "g" }],
  );
});
test("malformed records, oversized inputs and duplicate/prototype CSV headers are rejected", () => {
  for (const text of [
    "input,input\na,b",
    "__proto__,input\nx,y",
    "input,gold\na,b,c",
    'input\n"unterminated',
  ])
    assert.throws(() => csv(text));
  for (const text of [
    "[]",
    "null",
    '[{"input":12}]',
    '[{"input":"x","gold":3}]',
    "{invalid",
  ])
    assert.throws(() => parseRecords(text, "fixture.json"));
  assert.throws(
    () => parseRecords("x".repeat(1048577), "fixture.json"),
    /ONE_MIB/,
  );
  assert.throws(
    () =>
      parseRecords(
        JSON.stringify([{ input: "x".repeat(64001) }]),
        "fixture.json",
      ),
    /BOUNDED_TEXT/,
  );
});
