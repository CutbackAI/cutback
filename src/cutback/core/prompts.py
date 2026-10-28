"""Bounded declarative prompt composition. No Python/Jinja, includes, tools or inference."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    model_validator,
)

from cutback.core.models import StrictModel
from cutback.core.request_profiles import RequestContract

VariableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
VariableType = Literal["string", "integer", "boolean", "json"]
MAX_RENDERED_BYTES = 64000
MAX_GRAPH_VERSIONS = 32
MAX_GRAPH_DEPTH = 8


class PromptError(ValueError):
    """Fixed content-free diagnostics, suitable for an API error boundary."""


class JSONVariableValue(StrictModel):
    value: JsonValue


class PromptSamplingOptions(StrictModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    stop: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] | None = Field(
        default=None, min_length=1, max_length=4
    )
    stop_sequences: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] | None = (
        Field(default=None, min_length=1, max_length=4)
    )


def validate_native_schema(schema, surface):
    """Qualified, nonrecursive closed-object profile; never drops constraints."""
    if schema.get("type") != "object" or "anyOf" in schema:
        raise PromptError("PROMPT_NATIVE_SCHEMA_ROOT_OBJECT_REQUIRED")
    pending, count, properties = [(schema, 0)], 0, 0
    common = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
        "title",
        "description",
    }
    numeric = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    for_openai = numeric | {"minLength", "maxLength", "minItems", "maxItems"}
    while pending:
        node, depth = pending.pop()
        count += 1
        if not isinstance(node, dict) or count > 2000 or depth > 10:
            raise PromptError("PROMPT_NATIVE_SCHEMA_COMPLEXITY_EXCEEDED")
        allowed = common | (for_openai if surface != "messages.create" else set())
        if set(node) - allowed:
            raise PromptError("PROMPT_NATIVE_SCHEMA_KEYWORD_NOT_QUALIFIED")
        kinds = node.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds] if kinds else []
        if (
            not kinds
            and "anyOf" not in node
            or any(
                not isinstance(kind, str)
                or kind not in {"object", "array", "string", "number", "integer", "boolean", "null"}
                for kind in kinds
            )
        ):
            raise PromptError("PROMPT_NATIVE_SCHEMA_TYPE_REQUIRED")
        if "object" in kinds or "properties" in node:
            fields, required = node.get("properties", {}), node.get("required", [])
            if (
                not isinstance(fields, dict)
                or node.get("additionalProperties") is not False
                or not isinstance(required, list)
                or any(not isinstance(key, str) for key in required)
                or len(set(required)) != len(required)
                or set(required) != set(fields)
            ):
                raise PromptError("PROMPT_NATIVE_SCHEMA_CLOSED_REQUIRED_PROPERTIES_EXPECTED")
            properties += len(fields)
            if properties > 1000:
                raise PromptError("PROMPT_NATIVE_SCHEMA_PROPERTY_BOUND_EXCEEDED")
            pending.extend((value, depth + 1) for value in fields.values())
        if "array" in kinds:
            if not isinstance(node.get("items"), dict):
                raise PromptError("PROMPT_NATIVE_SCHEMA_ARRAY_ITEMS_REQUIRED")
            pending.append((node["items"], depth + 1))
        if "anyOf" in node:
            variants = node["anyOf"]
            if not isinstance(variants, list) or not 1 <= len(variants) <= 20:
                raise PromptError("PROMPT_NATIVE_SCHEMA_VARIANT_BOUND_EXCEEDED")
            pending.extend((value, depth + 1) for value in variants)
        if "enum" in node:
            values = node["enum"]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 1000
                or any(isinstance(v, (dict, list)) for v in values)
            ):
                raise PromptError("PROMPT_NATIVE_SCHEMA_SCALAR_ENUM_REQUIRED")


def utf8_size(value):
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        raise PromptError("PROMPT_UTF8_INVALID") from None


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def parts(text):
    """Single linear scan; inserted values are never parsed again as template syntax."""
    if "{%" in text or "{#" in text:
        raise PromptError("EXECUTABLE_TEMPLATE_SYNTAX_UNSUPPORTED")
    position = 0
    while position < len(text):
        start = text.find("{{", position)
        if start < 0:
            yield "literal", text[position:]
            break
        if start > position:
            yield "literal", text[position:start]
        end = text.find("}}", start + 2)
        if end < 0:
            raise PromptError("PROMPT_TEMPLATE_DELIMITER_UNCLOSED")
        token = text[start + 2 : end].strip()
        kind = "component" if token.startswith(">") else "variable"
        name = token[1:].strip() if kind == "component" else token
        if len(name) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise PromptError("PROMPT_TEMPLATE_TOKEN_UNSUPPORTED")
        yield kind, name
        position = end + 2


class PromptMessage(StrictModel):
    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(min_length=1, max_length=64000)


class PromptDocument(StrictModel):
    schema_version: Literal["cutback.prompt.v1"] = "cutback.prompt.v1"
    kind: Literal["text", "chat"] = "text"
    text: str | None = Field(default=None, min_length=1, max_length=64000)
    messages: list[PromptMessage] = Field(default_factory=list, max_length=32)
    variables: dict[VariableName, VariableType] = Field(default_factory=dict, max_length=20)
    dependencies: dict[VariableName, UUID] = Field(default_factory=dict, max_length=20)
    output_schema: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def declared(self):
        if self.kind == "text":
            if self.text is None or self.messages:
                raise PromptError("TEXT_PROMPT_REQUIRES_TEXT_ONLY")
            templates = [self.text]
        else:
            if self.text is not None or not self.messages:
                raise PromptError("CHAT_PROMPT_REQUIRES_MESSAGES_ONLY")
            templates = [message.content for message in self.messages]
        refs: dict[str, set[str]] = {kind: set() for kind in ("variable", "component")}
        for text in templates:
            utf8_size(text)
            for kind, name in parts(text):
                if kind != "literal":
                    refs[kind].add(name)
        if refs["variable"] != set(self.variables):
            raise PromptError("PROMPT_VARIABLE_DECLARATIONS_MUST_MATCH_REFERENCES")
        if refs["component"] != set(self.dependencies):
            raise PromptError("PROMPT_COMPONENT_DECLARATIONS_MUST_MATCH_REFERENCES")
        try:
            encoded = json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()
        except (ValueError, RecursionError):
            raise PromptError("PROMPT_DOCUMENT_JSON_INVALID") from None
        if len(encoded) > 128000:
            raise PromptError("PROMPT_DOCUMENT_BYTES_EXCEEDED")
        # A schema is data. Optional eval/server code separately validates the supported
        # non-regex/non-reference JSON Schema profile before it can be used for checks.
        pending: list[tuple[JsonValue, int]] = (
            [(self.output_schema, 0)] if self.output_schema else []
        )
        while pending:
            value, depth = pending.pop()
            if depth > 20:
                raise PromptError("PROMPT_SCHEMA_DEPTH_EXCEEDED")
            if isinstance(value, dict):
                if set(value) & {
                    "$ref",
                    "$dynamicRef",
                    "$recursiveRef",
                    "$vocabulary",
                    "pattern",
                    "patternProperties",
                }:
                    raise PromptError("PROMPT_SCHEMA_REFERENCES_OR_REGEX_UNSUPPORTED")
                if (
                    "$schema" in value
                    and value["$schema"] != "https://json-schema.org/draft/2020-12/schema"
                ):
                    raise PromptError("PROMPT_SCHEMA_DIALECT_UNSUPPORTED")
                for key, child in value.items():
                    if key in {
                        "properties",
                        "$defs",
                        "definitions",
                        "dependentSchemas",
                    } and isinstance(child, dict):
                        pending.extend((v, depth + 1) for v in child.values())
                    else:
                        pending.append((child, depth + 1))
            elif isinstance(value, list):
                pending.extend((v, depth + 1) for v in value)
        return self


@dataclass(frozen=True)
class PromptGraph:
    root: UUID
    documents: dict[UUID, PromptDocument]

    def inspect(self):
        requirements: dict[str, str] = {}
        visited: set[UUID] = set()
        active: set[UUID] = set()
        heights: dict[UUID, int] = {}
        graph_bytes = 0

        def visit(identifier, depth):
            nonlocal graph_bytes
            if identifier in active:
                raise PromptError("PROMPT_DEPENDENCY_CYCLE")
            if depth > MAX_GRAPH_DEPTH:
                raise PromptError("PROMPT_DEPENDENCY_DEPTH_EXCEEDED")
            if identifier in visited:
                if depth + heights[identifier] > MAX_GRAPH_DEPTH:
                    raise PromptError("PROMPT_DEPENDENCY_DEPTH_EXCEEDED")
                return heights[identifier]
            document = self.documents.get(identifier)
            if document is None:
                raise PromptError("PINNED_PROMPT_DEPENDENCY_UNAVAILABLE")
            if identifier != self.root and document.output_schema is not None:
                raise PromptError("PROMPT_COMPONENT_SCHEMA_REQUIRES_SEPARATE_ROOT_CONTRACT")
            if len(visited) + len(active) >= MAX_GRAPH_VERSIONS:
                raise PromptError("PROMPT_DEPENDENCY_COUNT_EXCEEDED")
            graph_bytes += len(
                json.dumps(document.model_dump(mode="json"), allow_nan=False).encode()
            )
            if graph_bytes > 128000:
                raise PromptError("PROMPT_GRAPH_DOCUMENT_BYTES_EXCEEDED")
            active.add(identifier)
            height = 0
            for name, value_type in document.variables.items():
                if name in requirements and requirements[name] != value_type:
                    raise PromptError("PROMPT_VARIABLE_TYPE_CONFLICT")
                requirements[name] = value_type
                if len(requirements) > 20:
                    raise PromptError("PROMPT_GRAPH_VARIABLE_COUNT_EXCEEDED")
            for child in document.dependencies.values():
                if child not in self.documents or self.documents[child].kind != "text":
                    raise PromptError("PINNED_TEXT_COMPONENT_REQUIRED")
                height = max(height, visit(child, depth + 1) + 1)
            active.remove(identifier)
            visited.add(identifier)
            heights[identifier] = height
            return height

        visit(self.root, 0)
        return {"variables": requirements, "version_ids": sorted(visited, key=str)}

    def validate_values(self, values, *, dynamic_input=False):
        required = self.inspect()["variables"]
        expected = set(required) - ({"input"} if dynamic_input else set())
        if set(values) != expected:
            raise PromptError("PROMPT_VARIABLE_VALUES_MISSING_OR_EXTRA")
        if dynamic_input and required.get("input") != "string":
            raise PromptError("EXPERIMENT_PROMPT_REQUIRES_STRING_INPUT_VARIABLE")
        encoded = {}
        for name, value in values.items():
            value_type = required[name]
            if (
                value_type == "string"
                and type(value) is not str
                or value_type == "integer"
                and type(value) is not int
                or value_type == "boolean"
                and type(value) is not bool
            ):
                raise PromptError("PROMPT_VARIABLE_TYPE_INVALID")
            if value_type == "json":
                try:
                    value = JSONVariableValue.model_validate({"value": value}, strict=True).value
                except ValidationError:
                    raise PromptError("PROMPT_VARIABLE_JSON_INVALID") from None
            try:
                text = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (ValueError, TypeError, RecursionError):
                raise PromptError("PROMPT_VARIABLE_JSON_INVALID") from None
            encoded[name] = value if value_type == "string" else text
            if utf8_size(encoded[name]) > MAX_RENDERED_BYTES:
                raise PromptError("PROMPT_VARIABLE_BYTES_EXCEEDED")
        return encoded

    def render(self, values):
        encoded = self.validate_values(values)
        work_bytes = 0

        def join(chunks):
            nonlocal work_bytes
            result, output_bytes = [], 0
            for chunk in chunks:
                size = utf8_size(chunk)
                output_bytes += size
                work_bytes += size
                if output_bytes > MAX_RENDERED_BYTES or work_bytes > MAX_RENDERED_BYTES * (
                    MAX_GRAPH_DEPTH + 1
                ):
                    raise PromptError("PROMPT_RENDERED_BYTES_EXCEEDED")
                result.append(chunk)
            return "".join(result)

        def text(identifier):
            document = self.documents[identifier]
            return join(
                text(document.dependencies[value])
                if kind == "component"
                else encoded[value]
                if kind == "variable"
                else value
                for kind, value in parts(document.text or "")
            )

        root = self.documents[self.root]
        if root.kind == "text":
            return {"kind": "text", "text": text(self.root)}
        messages, final_bytes = [], 0
        for message in root.messages:
            content = join(
                text(root.dependencies[value])
                if kind == "component"
                else encoded[value]
                if kind == "variable"
                else value
                for kind, value in parts(message.content)
            )
            final_bytes += utf8_size(content)
            if final_bytes > MAX_RENDERED_BYTES:
                raise PromptError("PROMPT_RENDERED_BYTES_EXCEEDED")
            messages.append({"role": message.role, "content": content})
        return {"kind": "chat", "messages": messages}

    def native_contract(self, values, *, surface="responses.create", sampling_options=None):
        """Compile declared input to the existing qualified native request AST, not code."""
        encoded = self.validate_values(values, dynamic_input=True)
        nodes, literal_bytes = 0, 0

        def node(op, **fields):
            nonlocal nodes
            nodes += 1
            if nodes > 1900:
                raise PromptError("PROMPT_NATIVE_NODE_BOUND_EXCEEDED")
            return {"op": op, **fields}

        def literal(value):
            nonlocal literal_bytes
            literal_bytes += utf8_size(str(value))
            if literal_bytes > MAX_RENDERED_BYTES:
                raise PromptError("PROMPT_NATIVE_BYTES_EXCEEDED")
            return node("literal", value=value)

        def template(document, source):
            chunks = []
            for kind, value in parts(source):
                if kind == "component":
                    child = self.documents[document.dependencies[value]]
                    chunks.append(template(child, child.text or ""))
                elif kind == "variable" and value == "input":
                    chunks.append(node("parameter", name="input"))
                else:
                    chunks.append(literal(encoded[value] if kind == "variable" else value))
            return node("join", items=chunks)

        root = self.documents[self.root]
        system = []
        if root.kind == "text":
            payload = template(root, root.text or "")
            if surface != "responses.create":
                payload = node(
                    "list",
                    items=[node("dict", items={"role": literal("user"), "content": payload})],
                )
        else:
            messages = root.messages
            if surface == "messages.create":
                conversation_started = False
                messages = []
                for message in root.messages:
                    if message.role == "developer":
                        raise PromptError("ANTHROPIC_DEVELOPER_ROLE_UNSUPPORTED")
                    if message.role == "system":
                        if conversation_started:
                            raise PromptError("ANTHROPIC_SYSTEM_MESSAGES_MUST_PRECEDE_CONVERSATION")
                        system.append(
                            node(
                                "dict",
                                items={
                                    "type": literal("text"),
                                    "text": template(root, message.content),
                                },
                            )
                        )
                    else:
                        conversation_started = True
                        messages.append(message)
                if not messages:
                    raise PromptError("PROMPT_NATIVE_USER_MESSAGE_REQUIRED")
            payload = node(
                "list",
                items=[
                    node(
                        "dict",
                        items={
                            "role": literal(message.role),
                            "content": template(root, message.content),
                        },
                    )
                    for message in messages
                ],
            )
        try:
            fields = {"input" if surface == "responses.create" else "messages": payload}
            if system:
                fields["system"] = node("list", items=system)
            options: dict[str, Any] = PromptSamplingOptions.model_validate(
                sampling_options or {}
            ).model_dump(exclude_none=True)
            if root.output_schema is not None:
                validate_native_schema(root.output_schema, surface)
                if surface == "messages.create":
                    options["output_config"] = {
                        "format": {"type": "json_schema", "schema": root.output_schema}
                    }
                else:
                    schema = {
                        "name": "cutback_prompt",
                        "strict": True,
                        "schema": root.output_schema,
                    }
                    options.update(
                        {"text": {"format": {"type": "json_schema", **schema}}}
                        if surface == "responses.create"
                        else {"response_format": {"type": "json_schema", "json_schema": schema}}
                    )
            return RequestContract(
                version="native-prompt-v1",
                sdk_surface=surface,
                payload=node("dict", items=fields),
                parameters=["input"],
                options=options,
            )
        except ValidationError:
            raise PromptError("PROMPT_NATIVE_SURFACE_OR_OPTIONS_UNSUPPORTED") from None
