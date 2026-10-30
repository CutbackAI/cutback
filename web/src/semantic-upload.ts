// Content stays in memory. Only a frozen hash and upload cursor enter metadata.
export type FlatSemanticCase = {
  id: string;
  input: string;
  gold: string;
  reference: string | null;
  group: string | null;
  stratum: string;
};

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object") {
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map(
          (key) =>
            JSON.stringify(key) +
            ":" +
            canonical((value as Record<string, unknown>)[key]),
        )
        .join(",") +
      "}"
    );
  }
  return JSON.stringify(value);
}

export function normalizeCases(value: unknown): FlatSemanticCase[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 512)
    throw new Error("SEMANTIC_UPLOAD_DATASET_CASE_BOUND");
  const seen = new Set<string>();
  return value.map((item) => {
    if (
      !item ||
      typeof item !== "object" ||
      Object.keys(item).some(
        (key) =>
          !["id", "input", "gold", "reference", "group", "stratum"].includes(
            key,
          ),
      ) ||
      typeof item.id !== "string" ||
      typeof item.input !== "string" ||
      typeof item.gold !== "string" ||
      (item.reference != null && typeof item.reference !== "string") ||
      (item.group != null && typeof item.group !== "string") ||
      (item.stratum !== undefined && typeof item.stratum !== "string") ||
      seen.has(item.id)
    )
      throw new Error("SEMANTIC_UPLOAD_FLAT_LABELED_CASES_REQUIRED");
    seen.add(item.id);
    return {
      id: item.id,
      input: item.input,
      gold: item.gold,
      reference: item.reference ?? null,
      group: item.group ?? null,
      stratum: item.stratum ?? "default",
    };
  });
}

export async function sequenceHash(cases: FlatSemanticCase[]): Promise<string> {
  const bytes = new TextEncoder().encode(canonical(cases));
  const hashed = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hashed), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}

export function nextChunk(
  cases: FlatSemanticCase[],
  offset: number,
): FlatSemanticCase[] {
  let chunk = cases.slice(offset, offset + 8);
  while (
    new TextEncoder().encode(JSON.stringify(chunk)).length > 900000 &&
    chunk.length > 1
  )
    chunk = chunk.slice(0, -1);
  if (
    !chunk.length ||
    new TextEncoder().encode(JSON.stringify(chunk)).length > 900000
  )
    throw new Error("SEMANTIC_UPLOAD_CASE_EXCEEDS_REQUEST_BOUND");
  return chunk;
}
