import test from "node:test";
import assert from "node:assert/strict";
import { chartGeometry, exactValue } from "../src/analytics-chart.ts";

test("missing observations break a trend rather than becoming zero or connected evidence", () => {
  const plot = chartGeometry([
    { key: "one", value: "3" },
    { key: "two", value: "0" },
    { key: "missing", value: null },
    { key: "four", value: "2" },
  ]);
  assert.deepEqual(
    plot.segments.map((segment) => segment.map((point) => point.key)),
    [["one", "two"], ["four"]],
  );
  assert.equal(plot.known, 3);
  assert.equal(plot.maximum, 3);
  assert.equal(plot.segments[0][1].y, 170);
});

test("a financial or token string remains exact outside the approximate plot scale", () => {
  const value = "9007199254740993.0000001";
  assert.equal(exactValue(value), value);
  assert.equal(exactValue("0.000000000001"), "0.000000000001");
  assert.equal(exactValue("0"), "0");
  assert.equal(chartGeometry([{ key: "one", value }]).segments[0][0].x, 330);
});

test("invalid, absent and non-finite values never produce a plotted zero", () => {
  for (const value of [
    null,
    undefined,
    "",
    " ",
    "NaN",
    "Infinity",
    -1,
    false,
    "1e10000",
  ]) {
    assert.equal(exactValue(value), "Unknown");
    assert.equal(chartGeometry([{ key: "unknown", value }]).known, 0);
  }
  assert.equal(chartGeometry([]).segments.length, 0);
});
