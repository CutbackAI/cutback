"""Record installed dependency license metadata; compatibility still requires review."""

import importlib.metadata
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lines = [
    "# Dependency inventory",
    "",
    "Generated from the tested all-extras development environment. `uv.lock` is authoritative.",
    "No upstream implementation is vendored. Preserve dependencies' distributed licenses/notices.",
    "Metadata is an inventory, not a completed legal compatibility review. In particular, review",
    "psycopg/binary LGPL obligations and image OS packages before publishing images.",
    "",
    "| Package | Version | Declared license metadata |",
    "|---|---|---|",
]
for distribution in sorted(
    importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()
):
    metadata = distribution.metadata
    license_value = metadata.get("License-Expression") or metadata.get("License") or ""
    if not license_value:
        license_value = "; ".join(
            value.removeprefix("License :: ")
            for value in metadata.get_all("Classifier", [])
            if value.startswith("License :: ")
        )
    license_value = (
        " ".join(license_value.split()).replace("|", ";")[:120] or "Unspecified; review required"
    )
    lines.append(f"| {metadata['Name']} | {distribution.version} | {license_value} |")
(root / "docs/dependencies.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Dependency license metadata written to docs/dependencies.md")
