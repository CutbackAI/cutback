import test from "node:test";
import assert from "node:assert/strict";
import { traceRows, traceTiming } from "../src/trace-layout.ts";

test("waterfall retains nanosecond identity beyond the JavaScript integer boundary", () => {
  const start = 1793131200000000001n;
  const spans = [
    {
      span_id: "one",
      parent_span_id: null,
      started_ns: String(start),
      ended_ns: String(start + 9n),
    },
    {
      span_id: "two",
      parent_span_id: "one",
      started_ns: String(start + 1n),
      ended_ns: null,
    },
  ];
  const timing = traceTiming(spans as any);
  assert.equal(timing.origin, start);
  assert.equal(timing.width, 9n);
  assert.deepEqual(
    traceRows(spans as any).map((r) => [r.span.span_id, r.depth]),
    [
      ["one", 0],
      ["two", 1],
    ],
  );
});

test("cycles and orphans stay visible once each without recursive traversal", () => {
  const spans = [
    { span_id: "one", parent_span_id: "two" },
    { span_id: "two", parent_span_id: "one" },
    { span_id: "orphan", parent_span_id: "missing" },
  ];
  const rows = traceRows(spans as any);
  assert.equal(rows.length, 3);
  assert.equal(new Set(rows.map((r) => r.span.span_id)).size, 3);
  assert.equal(rows[0].span.span_id, "orphan");
});
