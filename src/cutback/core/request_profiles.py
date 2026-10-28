"""Parse-only request contracts. Public profiles contain hashes, never prompt content."""

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from cutback.core.models import Identifier, StrictModel


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class SourceBinding(StrictModel):
    version: Literal["request-profile-v1"] = "request-profile-v1"
    workflow_id: Identifier
    call_site_id: Identifier
    source_file: str = Field(max_length=500)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{7,40}$")
    dependencies: dict[str, str] = Field(max_length=64)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_binding: dict[str, Any] = Field(max_length=8)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def integrity(self):
        if (
            fingerprint(self.model_dump(mode="json", exclude={"profile_sha256"}))
            != self.profile_sha256
        ):
            raise ValueError("REQUEST_PROFILE_INTEGRITY_FAILURE")
        for name, digest in self.dependencies.items():
            safe_relative(name)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("INVALID_DEPENDENCY_HASH")
        safe_relative(self.source_file)
        if self.dependencies.get(self.source_file) != self.source_sha256:
            raise ValueError("SOURCE_DEPENDENCY_MISMATCH")
        edit = self.model_binding
        if set(edit) != {"file", "line", "column", "end_line", "end_column"}:
            raise ValueError("INVALID_MODEL_BINDING")
        if not isinstance(edit["file"], str):
            raise ValueError("INVALID_MODEL_BINDING")
        safe_relative(edit["file"])
        if (
            edit["file"] not in self.dependencies
            or any(
                type(edit[key]) is not int or not 0 <= edit[key] <= 1048576
                for key in ("line", "column", "end_line", "end_column")
            )
            or edit["line"] < 1
            or edit["end_line"] < edit["line"]
        ):
            raise ValueError("INVALID_MODEL_BINDING")
        return self


def safe_relative(name: str):
    from pathlib import PurePosixPath

    path = PurePosixPath(name)
    if (
        not path.parts
        or path.is_absolute()
        or any(p in {".", ".."} or ":" in p or "\\" in p for p in path.parts)
    ):
        raise ValueError("UNSAFE_PROFILE_PATH")


class RequestContract(StrictModel):
    version: Literal["native-text-v1", "native-prompt-v1"] = "native-text-v1"
    sdk_surface: Literal["responses.create", "chat.completions.create", "messages.create"]
    payload: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict, max_length=8)
    parameters: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(), allow_nan=False).encode()) > 128000:
            raise ValueError("REQUEST_CONTRACT_TOO_LARGE")
        allowed = {
            "responses.create": {"temperature", "top_p", "text"},
            "chat.completions.create": {"temperature", "top_p", "stop", "response_format"},
            "messages.create": {"stop_sequences"}
            | ({"output_config"} if self.version == "native-prompt-v1" else set()),
        }[self.sdk_surface]
        if set(self.options) - allowed:
            raise ValueError("UNQUALIFIED_REQUEST_OPTIONS")
        if "text" in self.options and self.sdk_surface != "responses.create":
            raise ValueError("STRUCTURED_OUTPUT_SURFACE_MISMATCH")
        if "response_format" in self.options and self.sdk_surface != "chat.completions.create":
            raise ValueError("STRUCTURED_OUTPUT_SURFACE_MISMATCH")
        count = 0

        def check(node, depth=0):
            nonlocal count
            count += 1
            if count > 2000 or depth > 24 or not isinstance(node, dict):
                raise ValueError("INVALID_REQUEST_TEMPLATE")
            op = node.get("op")
            if op == "parameter":
                if set(node) != {"op", "name"} or node["name"] not in self.parameters:
                    raise ValueError("UNDECLARED_REQUEST_PARAMETER")
            elif op == "literal":
                if set(node) != {"op", "value"}:
                    raise ValueError("INVALID_REQUEST_LITERAL")
            elif op in {"list", "join"}:
                if set(node) != {"op", "items"} or not isinstance(node["items"], list):
                    raise ValueError("INVALID_REQUEST_SEQUENCE")
                for child in node["items"]:
                    check(child, depth + 1)
            elif op == "dict":
                if set(node) != {"op", "items"} or not isinstance(node["items"], dict):
                    raise ValueError("INVALID_REQUEST_MAPPING")
                for child in node["items"].values():
                    check(child, depth + 1)
            elif op == "lookup":
                if set(node) != {"op", "value", "key"} or type(node["key"]) not in {str, int}:
                    raise ValueError("INVALID_REQUEST_LOOKUP")
                check(node["value"], depth + 1)
            else:
                raise ValueError("UNQUALIFIED_REQUEST_TEMPLATE")

        check(self.payload)
        return self

    def render(self, text: str):
        rendered_bytes = 0

        def bounded_value(value):
            nonlocal rendered_bytes
            rendered_bytes += len(json.dumps(value, allow_nan=False).encode())
            if rendered_bytes > 128000:
                raise ValueError("RENDERED_REQUEST_EXCEEDS_BYTE_BOUND")
            return value

        def walk(node, depth=0):
            if depth > 24 or not isinstance(node, dict):
                raise ValueError("INVALID_REQUEST_TEMPLATE")
            op = node.get("op")
            if op == "literal":
                return bounded_value(node["value"])
            if op == "parameter":
                if len(self.parameters) == 1:
                    return bounded_value(text)
                value = json.loads(text)
                if not isinstance(value, dict) or node["name"] not in value:
                    raise ValueError("CASE_PARAMETER_MAPPING_REQUIRED")
                return bounded_value(value[node["name"]])
            if op == "lookup":
                value = walk(node["value"], depth + 1)
                if isinstance(value, str):
                    value = json.loads(value)
                return value[node["key"]]
            if op == "join":
                return "".join(str(walk(item, depth + 1)) for item in node["items"])
            if op == "list":
                return [walk(item, depth + 1) for item in node["items"]]
            if op == "dict":
                return {key: walk(item, depth + 1) for key, item in node["items"].items()}
            raise ValueError("UNQUALIFIED_REQUEST_TEMPLATE")

        value = walk(self.payload)
        bundle = value if self.version == "native-prompt-v1" else None
        if bundle is not None:
            required = "input" if self.sdk_surface == "responses.create" else "messages"
            optional = (
                {"instructions"}
                if self.sdk_surface == "responses.create"
                else {"system"}
                if self.sdk_surface == "messages.create"
                else set()
            )
            if (
                not isinstance(bundle, dict)
                or required not in bundle
                or set(bundle) - {required} - optional
            ):
                raise ValueError("QUALIFIED_NATIVE_PROMPT_ARGUMENTS_REQUIRED")
            if "instructions" in bundle and not isinstance(bundle["instructions"], str):
                raise ValueError("QUALIFIED_NATIVE_PROMPT_INSTRUCTIONS_REQUIRED")
            if "system" in bundle:
                system = bundle["system"]
                if (
                    not isinstance(system, list)
                    or not 1 <= len(system) <= 32
                    or any(
                        not isinstance(block, dict)
                        or set(block) != {"type", "text"}
                        or block.get("type") != "text"
                        or not isinstance(block.get("text"), str)
                        for block in system
                    )
                ):
                    raise ValueError("QUALIFIED_ANTHROPIC_SYSTEM_BLOCKS_REQUIRED")
            value = bundle[required]
        if self.sdk_surface == "responses.create" and isinstance(value, str):
            return bundle if bundle is not None else value
        if not isinstance(value, list) or not 1 <= len(value) <= 100:
            raise ValueError("QUALIFIED_TEXT_MESSAGES_REQUIRED")
        for message in value:
            if not isinstance(message, dict) or set(message) - {"role", "content"}:
                raise ValueError("QUALIFIED_TEXT_MESSAGES_REQUIRED")
            roles = (
                {"user", "assistant"}
                if self.sdk_surface == "messages.create"
                else {"user", "assistant", "system", "developer"}
            )
            if message.get("role") not in roles or not isinstance(message.get("content"), str):
                raise ValueError("QUALIFIED_TEXT_MESSAGES_REQUIRED")
        return bundle if bundle is not None else value


@dataclass
class PreparedProfile:
    binding: SourceBinding
    request: RequestContract
    provider: str
    model: str
    instruction: str
    max_output_tokens: int
    label: str

    def public(self):
        return {
            "binding": self.binding.model_dump(mode="json"),
            "provider": self.provider,
            "model": self.model,
            "sdk_surface": self.request.sdk_surface,
            "instruction_sha256": fingerprint(self.instruction),
            "max_output_tokens": self.max_output_tokens,
            "label": self.label,
            "status": "REQUEST_CONTRACT_PREPARED",
        }

    def baseline(self):
        # Call only after explicit permission to prepare/import request content.
        return {
            "key": "baseline",
            "provider": self.provider,
            "model": self.model,
            "instruction": self.instruction,
            "max_output_tokens": self.max_output_tokens,
            "request_contract": self.request.model_dump(mode="json"),
        }


class Resolver:
    def __init__(self, root: Path, relative: str):
        self.root, self.relative = root, relative
        self.dependencies: dict[str, str] = {}
        self.modules: dict[str, ast.Module] = {}

    def module(self, relative):
        safe_relative(relative)
        path = self.root / relative
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(self.root)
            or any(p.is_symlink() for p in path.parents if p.is_relative_to(self.root))
            or path.stat().st_size > 1048576
            or len(self.dependencies) >= 64
        ):
            raise ValueError("UNSAFE_PROFILE_DEPENDENCY")
        if relative not in self.modules:
            raw = path.read_bytes()
            self.dependencies[relative] = hashlib.sha256(raw).hexdigest()
            self.modules[relative] = ast.parse(raw)
        return self.modules[relative]

    def assignment(self, node, relative, before):
        tree = self.module(relative)
        name = node.id if isinstance(node, ast.Name) else None
        if name:
            functions = [
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.lineno <= before <= (n.end_lineno or n.lineno)
            ]
            scope = max(functions, key=lambda n: n.lineno).body if functions else []
            # Conditional writes and mutations cannot be reconstructed by selecting a literal.
            direct = {id(n) for n in [*tree.body, *scope]}
            for parent in [*tree.body, *scope]:
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for write in ast.walk(parent):
                    if getattr(write, "lineno", before) >= before:
                        continue
                    targets = (
                        write.targets
                        if isinstance(write, ast.Assign)
                        else (
                            [write.target]
                            if isinstance(write, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                            else []
                        )
                    )
                    if any(
                        any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(t))
                        for t in targets
                    ) and (
                        id(write) not in direct
                        or isinstance(write, (ast.AugAssign, ast.NamedExpr))
                        or any(not isinstance(t, ast.Name) for t in targets)
                    ):
                        raise ValueError("CONDITIONAL_REQUEST_VALUE_REQUIRES_REVIEW")
                    if (
                        isinstance(write, ast.Call)
                        and isinstance(write.func, ast.Attribute)
                        and (isinstance(write.func.value, ast.Name) and write.func.value.id == name)
                    ):
                        raise ValueError("MUTATED_REQUEST_VALUE_REQUIRES_REVIEW")
            values = [
                n.value
                for n in [*tree.body, *scope]
                if isinstance(n, (ast.Assign, ast.AnnAssign))
                and n.lineno < before
                and any(
                    isinstance(t, ast.Name) and t.id == name
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                )
            ]
            if values:
                return values[-1], relative
            for item in tree.body:
                if isinstance(item, ast.ImportFrom) and item.module:
                    for alias in item.names:
                        if (alias.asname or alias.name) == name:
                            module = item.module.replace(".", "/") + ".py"
                            if item.level:
                                module = str(Path(relative).parent / module).replace("\\", "/")
                            for candidate in (module, "src/" + module):
                                if (self.root / candidate).is_file():
                                    return self.assignment(
                                        ast.Name(id=alias.name), candidate, 10**9
                                    )
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            for item in tree.body:
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        if (alias.asname or alias.name) == node.value.id:
                            module = alias.name.replace(".", "/") + ".py"
                            for candidate in (module, "src/" + module):
                                if (self.root / candidate).is_file():
                                    return self.assignment(ast.Name(id=node.attr), candidate, 10**9)
        raise ValueError("DYNAMIC_REQUEST_VALUE_REQUIRES_REVIEW")

    def literal(self, node, relative, before, depth=0):
        if depth > 16:
            raise ValueError("PROFILE_RESOLUTION_LIMIT")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_text"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "Path"
            and len(node.func.value.args) == 1
            and not node.args
        ):
            if node.func.value.keywords or any(k.arg != "encoding" for k in node.keywords):
                raise ValueError("PROMPT_FILE_OPTIONS_REQUIRE_REVIEW")
            if node.keywords and ast.literal_eval(node.keywords[0].value) not in {"utf-8", "utf8"}:
                raise ValueError("PROMPT_FILE_ENCODING_REQUIRES_REVIEW")
            if not any(
                isinstance(item, ast.ImportFrom)
                and item.module == "pathlib"
                and any(alias.name == "Path" and alias.asname is None for alias in item.names)
                for item in self.module(relative).body
            ):
                raise ValueError("PROMPT_PATH_BINDING_REQUIRES_REVIEW")
            name = ast.literal_eval(node.func.value.args[0])
            if not isinstance(name, str):
                raise ValueError("DYNAMIC_PROMPT_PATH")
            safe_relative(name)
            if Path(name).suffix not in {".txt", ".md", ".j2", ".jinja2"} or any(
                p.lower() in {"secrets", ".aws", ".ssh", ".env"} for p in Path(name).parts
            ):
                raise ValueError("UNQUALIFIED_PROMPT_FILE")
            path = self.root / name
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(self.root)
                or any(p.is_symlink() for p in path.parents if p.is_relative_to(self.root))
                or path.stat().st_size > 64000
            ):
                raise ValueError("UNSAFE_PROMPT_FILE")
            raw = path.read_bytes()
            self.dependencies[name] = hashlib.sha256(raw).hexdigest()
            return raw.decode("utf-8")
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError):
            value_node, origin = self.assignment(node, relative, before)
            return self.literal(
                value_node, origin, getattr(value_node, "lineno", before), depth + 1
            )
        if len(json.dumps(value).encode()) > 64000:
            raise ValueError("REQUEST_VALUE_TOO_LARGE")
        return value

    def template(self, node, relative, before, parameters, depth=0):
        if depth > 20:
            raise ValueError("PROFILE_RESOLUTION_LIMIT")
        if isinstance(node, ast.Name) and node.id in parameters:
            return {"op": "parameter", "name": node.id}
        if isinstance(node, ast.JoinedStr):
            if any(
                isinstance(n, ast.FormattedValue) and (n.format_spec or n.conversion != -1)
                for n in node.values
            ):
                raise ValueError("FORMATTED_REQUEST_REQUIRES_REVIEW")
            return {
                "op": "join",
                "items": [
                    self.template(
                        n.value if isinstance(n, ast.FormattedValue) else n,
                        relative,
                        before,
                        parameters,
                        depth + 1,
                    )
                    for n in node.values
                ],
            }
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            items = [
                self.template(n, relative, before, parameters, depth + 1)
                for n in (node.left, node.right)
            ]
            if any(
                item["op"] not in {"join", "parameter"}
                and not (item["op"] == "literal" and isinstance(item["value"], str))
                for item in items
            ):
                raise ValueError("NON_TEXT_CONCATENATION_REQUIRES_REVIEW")
            return {
                "op": "join",
                "items": items,
            }
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in parameters
        ):
            return {
                "op": "lookup",
                "value": {"op": "parameter", "name": node.value.id},
                "key": ast.literal_eval(node.slice),
            }
        if isinstance(node, (ast.List, ast.Tuple)):
            return {
                "op": "list",
                "items": [
                    self.template(n, relative, before, parameters, depth + 1) for n in node.elts
                ],
            }
        if isinstance(node, ast.Dict):
            if any(
                not isinstance(k, ast.Constant) or not isinstance(k.value, str) for k in node.keys
            ):
                raise ValueError("DYNAMIC_REQUEST_KEY")
            return {
                "op": "dict",
                "items": {
                    k.value: self.template(v, relative, before, parameters, depth + 1)
                    for k, v in zip(node.keys, node.values, strict=True)
                    if isinstance(k, ast.Constant)
                },
            }
        try:
            return {"op": "literal", "value": self.literal(node, relative, before)}
        except ValueError:
            if isinstance(node, ast.Name):
                value, origin = self.assignment(node, relative, before)
                return self.template(value, origin, value.lineno, parameters, depth + 1)
            raise ValueError("DYNAMIC_REQUEST_TEMPLATE_REQUIRES_REVIEW") from None


def prepare_profile(
    root: Path, site: dict, *, workflow_id: str | None = None, commit: str | None = None
) -> PreparedProfile:
    from cutback.core.inventory import workflow_identity

    root = root.resolve(strict=True)
    relative = site["source_file"]
    resolver = Resolver(root, relative)
    tree = resolver.module(relative)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and n.lineno == site["line"]
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create"
    ]
    if len(calls) != 1:
        raise ValueError("REQUEST_CALL_NOT_UNIQUE")
    call = calls[0]
    if call.args or any(k.arg is None for k in call.keywords):
        raise ValueError("EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW")
    values = {k.arg: k.value for k in call.keywords if k.arg is not None}
    payload_key = "input" if site["sdk_surface"] == "responses.create" else "messages"
    output_key = "max_output_tokens" if site["sdk_surface"] == "responses.create" else "max_tokens"
    option_names = {
        "responses.create": {"temperature", "top_p", "text"},
        "chat.completions.create": {"temperature", "top_p", "stop", "response_format"},
        "messages.create": {"stop_sequences"},
    }[site["sdk_surface"]]
    allowed = {"model", payload_key, output_key, "instructions", "system", "store", *option_names}
    if set(values) - allowed:
        raise ValueError("UNQUALIFIED_REQUEST_OPTIONS:" + ",".join(sorted(set(values) - allowed)))
    if payload_key not in values or output_key not in values:
        raise ValueError("REQUEST_PAYLOAD_AND_EXPLICIT_OUTPUT_BOUND_REQUIRED")
    if "store" in values and resolver.literal(values["store"], relative, call.lineno) is not False:
        raise ValueError("REQUEST_STORAGE_POLICY_REQUIRES_REVIEW")
    model_node = values.get("model")
    model = resolver.literal(model_node, relative, call.lineno)
    if model != site["requested_model"]:
        raise ValueError("MODEL_REFERENCE_UNRESOLVED")
    binding_node, origin = model_node, relative
    if not isinstance(model_node, ast.Constant):
        binding_node, origin = resolver.assignment(model_node, relative, call.lineno)
    if not isinstance(binding_node, ast.Constant) or not isinstance(binding_node.value, str):
        raise ValueError("MODEL_EDIT_REQUIRES_REVIEW")
    enclosing = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.lineno <= call.lineno <= (n.end_lineno or n.lineno)
    ]
    fn = max(enclosing, key=lambda n: n.lineno) if enclosing else None
    parameters = (
        [
            a.arg
            for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
            if a.arg not in {"self", "cls", "client", "api_key", "http_client"}
        ]
        if fn
        else []
    )
    if fn:
        for node in ast.walk(fn):
            if getattr(node, "lineno", call.lineno) >= call.lineno:
                continue
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.id in parameters
            ):
                raise ValueError("TRANSFORMED_REQUEST_PARAMETER_REQUIRES_REVIEW")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(k.arg == "base_url" for k in node.keywords):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "OpenAI",
                "AsyncOpenAI",
                "Anthropic",
                "AsyncAnthropic",
            }:
                raise ValueError("CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW")
    payload = resolver.template(values[payload_key], relative, call.lineno, parameters)
    used = sorted({n["name"] for n in _nodes(payload) if n.get("op") == "parameter"})
    instruction = ""
    for key in ("instructions", "system"):
        if key in values:
            instruction = resolver.literal(values[key], relative, call.lineno)
            if not isinstance(instruction, str):
                raise ValueError("DYNAMIC_INSTRUCTION_REQUIRES_REVIEW")
    if "instructions" in values and site["sdk_surface"] != "responses.create":
        raise ValueError("INSTRUCTION_SURFACE_MISMATCH")
    if "system" in values and site["sdk_surface"] != "messages.create":
        raise ValueError("INSTRUCTION_SURFACE_MISMATCH")
    bound = resolver.literal(values[output_key], relative, call.lineno)
    if type(bound) is not int or not 16 <= bound <= 16384:
        raise ValueError("OUTPUT_BOUND_NOT_QUALIFIED")
    request = RequestContract(
        sdk_surface=site["sdk_surface"],
        payload=payload,
        parameters=used,
        options={
            k: resolver.literal(v, relative, call.lineno)
            for k, v in values.items()
            if k in option_names
        },
    )
    entries = site["entry_points"] or [{"file": relative, "symbol": site["source_symbol"]}]
    identities = [workflow_identity(e["file"], e["symbol"])[0] for e in entries]
    workflow_id = workflow_id or identities[0]
    if workflow_id not in identities:
        raise ValueError("WORKFLOW_SOURCE_BINDING_MISMATCH")
    value = {
        "version": "request-profile-v1",
        "workflow_id": workflow_id,
        "call_site_id": site["call_site_id"],
        "source_file": relative,
        "source_sha256": resolver.dependencies[relative],
        "source_commit": commit,
        "dependencies": resolver.dependencies,
        "request_sha256": fingerprint(
            {
                "request": request.model_dump(mode="json"),
                "instruction": instruction,
                "max_output_tokens": bound,
                "provider": site["provider"],
                "model": model,
            }
        ),
        "model_binding": {
            "file": origin,
            "line": binding_node.lineno,
            "column": binding_node.col_offset,
            "end_line": binding_node.end_lineno,
            "end_column": binding_node.end_col_offset,
        },
    }
    binding = SourceBinding(**value, profile_sha256=fingerprint(value))
    return PreparedProfile(
        binding,
        request,
        site["provider"],
        model,
        instruction,
        bound,
        f"{relative} · {site['source_symbol']}",
    )


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nodes(nested)
