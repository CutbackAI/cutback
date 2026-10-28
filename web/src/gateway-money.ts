/** Render exact API Decimal strings without rounding small nonzero amounts to zero. */
export function gatewayModeledUSD(value: unknown): string {
  if (typeof value !== "string" || value.length > 80) return "Unknown";
  const match = /^(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if (!match) return "Unknown";
  const exponent = Number(match[3] || "0");
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 24) return "Unknown";
  const digits = match[1] + (match[2] || "");
  const point = match[1].length + exponent;
  const expanded =
    point <= 0
      ? "0." + "0".repeat(-point) + digits
      : point >= digits.length
        ? digits + "0".repeat(point - digits.length)
        : digits.slice(0, point) + "." + digits.slice(point);
  const [whole, fraction] = expanded.split(".");
  const integer = whole.replace(/^0+(?=\d)/, "");
  const remainder = (fraction || "").replace(/0+$/, "");
  return integer + (remainder ? "." + remainder : "") + " USD";
}
