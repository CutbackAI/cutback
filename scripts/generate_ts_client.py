"""Generate a small typed TypeScript request client directly from the saved OpenAPI contract."""

import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
contract = json.loads((root / "web/openapi.json").read_text())


def ts_type(schema):
    if "$ref" in schema:
        return 'components["schemas"]["' + schema["$ref"].split("/")[-1] + '"]'
    if "anyOf" in schema:
        return " | ".join(ts_type(item) for item in schema["anyOf"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(schema.get("type"), "unknown")


lines = [
    "// Generated from openapi.json. Regenerate; do not hand-edit.",
    "import type {components} from './api.generated';",
    "type Request = (path:string,options?:RequestInit)=>Promise<any>;",
    "export function createSDK(request:Request) {return {",
]
for path, methods in contract["paths"].items():
    if not path.startswith("/v1/"):
        continue
    for method, operation in methods.items():
        if method not in {"get", "post", "delete", "put", "patch"}:
            continue
        name = re.sub("[^a-zA-Z0-9_]", "_", operation["operationId"])
        params = operation.get("parameters", [])
        fields = [
            p["name"] + ("" if p.get("required") else "?") + ":" + ts_type(p["schema"])
            for p in params
        ]
        body = operation.get("requestBody", {}).get("content", {}).get("application/json")
        if body:
            fields.append("body:" + ts_type(body["schema"]))
        required = any(p.get("required") for p in params) or body
        default = "" if required else "={}"
        url = path.removeprefix("/v1")
        for param in params:
            if param["in"] == "path":
                url = url.replace(
                    "{" + param["name"] + "}",
                    "${encodeURIComponent(String(args." + param["name"] + "))}",
                )
        query = [p["name"] for p in params if p["in"] == "query"]
        lines.append(f"  {name}: (args:{{" + ";".join(fields) + "}" + default + ") => {")
        lines.append("    const query=new URLSearchParams();")
        for key in query:
            lines.append(
                f"    if(args.{key}!==undefined && args.{key}!==null) "
                f'query.set("{key}",String(args.{key}));'
            )
        options = "{method:" + json.dumps(method.upper())
        if body:
            options += ",body:JSON.stringify(args.body)"
        options += "}"
        lines.append(
            "    return request(`"
            + url
            + '`+(query.size?"?"+query.toString():""),'
            + options
            + ");},"
        )
lines.append("};}")
(root / "web/src/client.generated.ts").write_text(
    "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
)
print("TypeScript API client generated from OpenAPI paths, parameters and body schemas.")
