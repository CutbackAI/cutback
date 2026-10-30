export type ImportCase = {
  id: string;
  input: string;
  gold?: string | unknown[] | Record<string, unknown>;
  reference?: string;
  group?: string;
  stratum?: string;
};

export function csv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (!cell || quoted) {
        quoted = !quoted;
      } else throw new Error("INVALID_CSV");
    } else if (char === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      if (row.some((v) => v.length)) rows.push(row);
      row = [];
      cell = "";
    } else cell += char;
  }
  if (quoted) throw new Error("INVALID_CSV");
  row.push(cell);
  if (row.some((v) => v.length)) rows.push(row);
  if (rows.length < 2) throw new Error("NO_EXAMPLES");
  const headers = rows.shift()!.map((v) => v.trim().replace(/^\uFEFF/, ""));
  if (
    new Set(headers).size !== headers.length ||
    headers.some((v) => ["__proto__", "constructor", "prototype"].includes(v))
  )
    throw new Error("INVALID_CSV_HEADERS");
  return rows.map((row) => {
    if (row.length !== headers.length) throw new Error("INVALID_CSV_COLUMNS");
    return Object.fromEntries(headers.map((key, i) => [key, row[i]]));
  });
}

export function parseRecords(text: string, name: string): ImportCase[] {
  if (new TextEncoder().encode(text).length > 1048576)
    throw new Error("FILE_EXCEEDS_ONE_MIB");
  let rows: unknown;
  try {
    rows = name.toLowerCase().endsWith(".csv")
      ? csv(text)
      : name.toLowerCase().endsWith(".jsonl")
        ? text
            .split(/\r?\n/)
            .filter((v) => v.trim())
            .map((line) => JSON.parse(line))
        : JSON.parse(text);
  } catch {
    throw new Error("CANNOT_PARSE_EXAMPLE_FILE");
  }
  if (
    !Array.isArray(rows) &&
    rows &&
    typeof rows === "object" &&
    "cases" in rows
  )
    rows = (rows as { cases: unknown }).cases;
  if (!Array.isArray(rows) || !rows.length || rows.length > 10000)
    throw new Error("ONE_TO_TEN_THOUSAND_EXAMPLES_REQUIRED");
  return rows.map((row, index) => {
    if (!row || typeof row !== "object") throw new Error("TEXT_INPUT_REQUIRED");
    if (row.security || row.episode)
      throw new Error(
        "USE_THE_COMPLETE_TYPED_SUITE_IMPORT_TO_PRESERVE_EXECUTION_AND_SECURITY_CONTRACTS",
      );
    const input = row.input ?? row.prompt ?? row.text;
    if (typeof input !== "string" || !input.length || input.length > 64000)
      throw new Error("BOUNDED_TEXT_INPUT_REQUIRED");
    const item: ImportCase = {
      id: String(row.id || "case-" + String(index + 1).padStart(5, "0")),
      input,
    };
    const gold = row.gold ?? row.label ?? row.expected;
    if (gold !== undefined && gold !== null && gold !== "") {
      let parsed = gold;
      if (typeof gold === "string" && /^[\[{]/.test(gold.trim())) {
        try {
          parsed = JSON.parse(gold);
        } catch {
          throw new Error("INVALID_STRUCTURED_LABEL");
        }
      }
      if (typeof parsed !== "string" && !(parsed && typeof parsed === "object"))
        throw new Error("LABEL_MUST_BE_TEXT_OR_STRUCTURED");
      item.gold = parsed;
    }
    const reference = row.reference ?? row.output ?? row.response;
    if (reference !== undefined && reference !== null) {
      if (typeof reference !== "string")
        throw new Error("TEXT_REFERENCE_REQUIRED");
      item.reference = reference;
    }
    if (row.group) item.group = String(row.group);
    if (row.stratum) item.stratum = String(row.stratum);
    return item;
  });
}
