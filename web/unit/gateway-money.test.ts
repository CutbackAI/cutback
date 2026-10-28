import { test } from "node:test";
import assert from "node:assert/strict";
import { gatewayModeledUSD } from "../src/gateway-money.ts";

test("gateway budgets display exact tiny, held and zero Decimal amounts", () => {
  assert.equal(gatewayModeledUSD("1E-12"), "0.000000000001 USD");
  assert.equal(gatewayModeledUSD("0.000002000000"), "0.000002 USD");
  assert.equal(gatewayModeledUSD("1.051536000000"), "1.051536 USD");
  assert.equal(gatewayModeledUSD("1.234567890123"), "1.234567890123 USD");
  assert.equal(gatewayModeledUSD("0E-12"), "0 USD");
  assert.equal(gatewayModeledUSD("10"), "10 USD");
});

test("missing and unqualified money is never represented as zero spend", () => {
  for (const value of [
    null,
    undefined,
    false,
    0,
    "NaN",
    "Infinity",
    "-1",
    "1E99999",
    "",
    "private",
  ]) {
    assert.equal(gatewayModeledUSD(value), "Unknown");
  }
});
