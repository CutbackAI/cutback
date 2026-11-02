/** Numeric conversion is for a relative plot only; exact decimal strings stay in the table. */
export type ValuePoint = { key: string; value: unknown };
const decimal = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

function numeric(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  if (!decimal.test(String(value))) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

export function exactValue(value: unknown): string {
  return numeric(value) === null ? "Unknown" : String(value);
}

export function chartGeometry(points: ValuePoint[]) {
  const numbers = points.map((point) => numeric(point.value));
  const maximum = Math.max(
    0,
    ...numbers.filter((value): value is number => value !== null),
  );
  const segments: { x: number; y: number; key: string }[][] = [];
  let current: { x: number; y: number; key: string }[] = [];
  numbers.forEach((value, index) => {
    if (value === null) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push({
        x: points.length > 1 ? 30 + (index / (points.length - 1)) * 600 : 330,
        y: 170 - (maximum ? value / maximum : 0) * 140,
        key: points[index].key,
      });
    }
  });
  if (current.length) segments.push(current);
  return {
    maximum,
    segments,
    known: numbers.filter((value) => value !== null).length,
  };
}
