"""Deterministic structural alignment, never an inferred semantic outcome."""


def paths(spans, side):
    by_id = {s["span_id"]: s for s in spans}
    counts: dict[tuple, int] = {}
    rows = {}
    for span in spans:
        chain: list[tuple[str, str]] = []
        visited: set[str] = set()
        current = span
        complete = True
        while current:
            if current["span_id"] in visited or len(chain) >= 32:
                complete = False
                break
            visited.add(current["span_id"])
            chain.append((current["kind"], current["operation"]))
            parent = current["parent_span_id"]
            if parent and parent not in by_id:
                complete = False
            current = by_id.get(parent)
        path = tuple(reversed(chain))
        ordinal = counts.get(path, 0)
        counts[path] = ordinal + 1
        key = ("path", path, ordinal) if complete else (side, span["span_id"])
        rows[key] = {
            "span_id": span["span_id"],
            "path": [kind + ":" + operation for kind, operation in path],
        }
    return rows


def align(left, right):
    a, b = paths(left, "left"), paths(right, "right")
    rows = []
    for key in dict.fromkeys([*a, *b]):
        one, two = a.get(key), b.get(key)
        rows.append(
            {
                "operation_path": (one or two)["path"],
                "left_span_id": one["span_id"] if one else None,
                "right_span_id": two["span_id"] if two else None,
                "alignment": "matched_path_occurrence"
                if one and two
                else "left_only"
                if one
                else "right_only",
            }
        )
    return rows
