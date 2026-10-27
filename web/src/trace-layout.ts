import type { components } from "./api.generated.ts";
type Span = components["schemas"]["ObservedSpan"];

export function traceRows(spans: Span[]) {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string, Span[]>();
  for (const span of spans) {
    if (span.parent_span_id && ids.has(span.parent_span_id)) {
      const list = children.get(span.parent_span_id) || [];
      list.push(span);
      children.set(span.parent_span_id, list);
    }
  }
  const visited = new Set<string>();
  const result: { span: Span; depth: number }[] = [];
  const roots = spans.filter(
    (s) => !s.parent_span_id || !ids.has(s.parent_span_id),
  );
  for (const root of [...roots, ...spans]) {
    const stack = [{ span: root, depth: 0 }];
    while (stack.length) {
      const item = stack.pop()!;
      if (visited.has(item.span.span_id)) continue;
      visited.add(item.span.span_id);
      result.push(item);
      for (const child of [
        ...(children.get(item.span.span_id) || []),
      ].reverse())
        stack.push({ span: child, depth: Math.min(item.depth + 1, 32) });
    }
  }
  return result;
}

export function traceTiming(spans: Span[]) {
  if (!spans.length) return { origin: 0n, width: 1n };
  let origin = BigInt(spans[0].started_ns),
    end = origin;
  for (const span of spans) {
    const start = BigInt(span.started_ns),
      stop = BigInt(span.ended_ns || span.started_ns);
    if (start < origin) origin = start;
    if (stop > end) end = stop;
  }
  return { origin, width: end > origin ? end - origin : 1n };
}

export function millis(nanoseconds: bigint) {
  return (Number(nanoseconds) / 1_000_000).toLocaleString("en-US", {
    maximumFractionDigits: 3,
  });
}
