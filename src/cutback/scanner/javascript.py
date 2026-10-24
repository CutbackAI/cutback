"""Optional tree-sitter JS/TS discovery. Source is parsed, never evaluated or imported."""

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from cutback.scanner.python import SURFACES, digest, safe_model


@dataclass
class Scope:
    parent: Any = None
    symbol: str = "<module>"
    bindings: dict = field(default_factory=dict)

    def bind(self, name, value):
        self.bindings[name] = value if name not in self.bindings else None

    def defined(self, name):
        return name in self.bindings or bool(self.parent and self.parent.defined(name))

    def lookup(self, name):
        if name in self.bindings:
            return self.bindings[name]
        return self.parent.lookup(name) if self.parent else None


def parse_file(source: bytes, path: str, catalog, deadline: float) -> list[dict]:
    from tree_sitter import Language, Parser

    if path.endswith((".ts", ".tsx", ".mts", ".cts")):
        import tree_sitter_typescript as grammar

        language = (
            grammar.language_tsx() if path.endswith(".tsx") else grammar.language_typescript()
        )
    else:
        import tree_sitter_javascript as js_grammar

        language = js_grammar.language()
    parser = Parser(Language(language))
    tree = parser.parse(source)
    if tree.root_node.has_error:
        raise ValueError("UNPARSED_JAVASCRIPT")

    def text(node):
        return source[node.start_byte : node.end_byte].decode("utf-8") if node else ""

    def literal(node):
        if node and node.type == "string":
            value = text(node)
            # Decode ordinary quoted literals only. Escape-heavy strings remain unresolved.
            if "\\" not in value:
                return value[1:-1]
            if value.startswith('"'):
                try:
                    return json.loads(value)
                except ValueError:
                    return None
        return None

    root_scope = Scope()
    nodes: list[Any] = []
    scopes: dict[int, Scope] = {}
    pending: list[tuple[Any, Scope, int]] = [(tree.root_node, root_scope, 0)]
    parameter: Any
    target: Any
    key: Any
    prop: Any
    while pending:
        node, scope, depth = pending.pop()
        if depth > 300 or len(nodes) > 100000 or time.monotonic() > deadline:
            raise ValueError("JAVASCRIPT_AST_RESOURCE_LIMIT")
        if node.type in {
            "function_declaration",
            "function_expression",
            "arrow_function",
            "method_definition",
        }:
            name = node.child_by_field_name("name")
            symbol = text(name) or (
                text(node.parent.child_by_field_name("name"))
                if node.parent and node.parent.type == "variable_declarator"
                else "<anonymous>"
            )
            if node.type == "function_declaration" and name:
                scope.bind(text(name), None)
            scope = Scope(
                scope, symbol if scope.symbol == "<module>" else scope.symbol + "." + symbol
            )
            parameters = node.child_by_field_name("parameters")
            if parameters:
                for parameter in parameters.named_children:
                    target = parameter.child_by_field_name("pattern") or parameter
                    if target.type == "identifier":
                        scope.bind(text(target), None)
            single = node.child_by_field_name("parameter")
            if single:
                scope.bind(text(single), None)
        elif node.type in {"statement_block", "catch_clause"}:
            scope = Scope(scope, scope.symbol)
            parameter = node.child_by_field_name("parameter")
            if parameter:
                scope.bind(text(parameter), None)
        scopes[node.id] = scope
        nodes.append(node)
        pending.extend((child, scope, depth + 1) for child in reversed(node.named_children))

    for node in nodes:
        scope = scopes[node.id]
        if node.type == "import_statement":
            package = literal(node.child_by_field_name("source"))
            if package not in {"openai", "@anthropic-ai/sdk"}:
                # Local/third-party imported names shadow any outer bindings as well.
                package = None
            provider = "openai" if package == "openai" else "anthropic"
            clause = next((c for c in node.named_children if c.type == "import_clause"), None)
            if clause:
                for part in clause.named_children:
                    if part.type == "identifier":
                        scope.bind(text(part), ("constructor", provider) if package else None)
                    elif part.type == "namespace_import":
                        scope.bind(
                            text(part.named_children[-1]),
                            ("namespace", provider) if package else None,
                        )
                    elif part.type == "named_imports":
                        for spec in part.named_children:
                            original = text(spec.child_by_field_name("name"))
                            alias = text(spec.child_by_field_name("alias")) or original
                            valid = original in (
                                {"OpenAI", "default"}
                                if provider == "openai"
                                else {"Anthropic", "default"}
                            )
                            scope.bind(
                                alias, ("constructor", provider) if package and valid else None
                            )
        elif node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            if name and name.type == "identifier":
                scope.bind(text(name), ("expression", value, scope))
            elif name:
                # Destructuring is intentionally conservative; do not reuse outer names.
                for child in name.named_children:
                    target = child.child_by_field_name("value") or child
                    scope.bind(text(target), None)
        elif node.type in {"assignment_expression", "augmented_assignment_expression"}:
            target = node.child_by_field_name("left")
            if target and target.type == "identifier":
                # Mutation may happen in any branch; invalidate the binding at its definition.
                owner = scope
                while owner.parent and text(target) not in owner.bindings:
                    owner = owner.parent
                owner.bindings[text(target)] = None

    def resolve(node, scope, seen=frozenset()):
        if node is None or node.id in seen or len(seen) > 40:
            return None
        seen = seen | {node.id}
        if node.type == "identifier":
            value = scope.lookup(text(node))
            return (
                resolve(value[1], value[2], seen) if value and value[0] == "expression" else value
            )
        if node.type == "string":
            return ("literal", literal(node))
        if node.type == "new_expression":
            value = resolve(node.child_by_field_name("constructor"), scope, seen)
            return ("client", value[1]) if value and value[0] == "constructor" else None
        if node.type == "member_expression":
            value = resolve(node.child_by_field_name("object"), scope, seen)
            prop = text(node.child_by_field_name("property"))
            if (
                value
                and value[0] == "namespace"
                and prop
                in ({"OpenAI", "default"} if value[1] == "openai" else {"Anthropic", "default"})
            ):
                return ("constructor", value[1])
            if value and value[0] in {"client", "surface"}:
                return ("surface", value[1], (value[2] + "." if len(value) > 2 else "") + prop)
        if (
            node.type == "call_expression"
            and text(node.child_by_field_name("function")) == "require"
        ):
            args = node.child_by_field_name("arguments")
            package = literal(args.named_children[0]) if args and args.named_children else None
            if package in {"openai", "@anthropic-ai/sdk"} and not scope.defined("require"):
                return ("constructor", "openai" if package == "openai" else "anthropic")
        if node.type in {"as_expression", "parenthesized_expression", "non_null_expression"}:
            return resolve(node.named_children[0], scope, seen)
        return None

    sites: list[dict] = []
    ordinal: Counter[str] = Counter()
    for node in nodes:
        if node.type != "call_expression":
            continue
        scope = scopes[node.id]
        target = resolve(node.child_by_field_name("function"), scope)
        if not target or target[0] != "surface" or target[2] not in SURFACES[target[1]]:
            continue
        _, provider, surface = target
        args = node.child_by_field_name("arguments")
        obj = args.named_children[0] if args and args.named_children else None
        props = {}
        spread = False
        if obj and obj.type == "object":
            for prop in obj.named_children:
                if prop.type == "pair":
                    key = prop.child_by_field_name("key")
                    props[literal(key) or text(key)] = prop.child_by_field_name("value")
                elif prop.type == "shorthand_property_identifier":
                    props[text(prop)] = prop
                else:
                    spread = True
        model_node = props.get("model")
        value = resolve(model_node, scope)
        # Tree-sitter gives shorthand properties a distinct type.
        if model_node and model_node.type == "shorthand_property_identifier":
            binding = scope.lookup(text(model_node))
            value = (
                resolve(binding[1], binding[2]) if binding and binding[0] == "expression" else None
            )
        model = safe_model(value[1]) if value and value[0] == "literal" and not spread else None
        signature = "|".join((path, scope.symbol, provider, surface))
        ordinal[signature] += 1
        refs = []
        for key in {"input", "messages", "system", "instructions"} & props.keys():
            prop = props[key]
            refs.append(
                {
                    "argument": key,
                    "file": path,
                    "line": prop.start_point.row + 1,
                    "kind": prop.type,
                    "symbol": text(prop) if prop.type == "identifier" else None,
                    "content": None,
                }
            )
        limit = props.get("max_output_tokens") or props.get("max_tokens")
        output_limit = (
            int(text(limit)) if limit and limit.type == "number" and text(limit).isdigit() else None
        )
        sites.append(
            {
                "call_site_id": "callsite:" + digest(signature + "|" + str(ordinal[signature])),
                "semantic_fingerprint": "semantic:"
                + digest("|".join((scope.symbol, provider, surface, model or "unknown"))),
                "language": "typescript"
                if path.endswith((".ts", ".tsx", ".mts", ".cts"))
                else "javascript",
                "provider": provider,
                "sdk_surface": surface,
                "requested_model": model,
                "model_reference": {
                    "kind": "literal_or_constant" if model else "runtime_unresolved",
                    "name": text(model_node)
                    if model_node
                    and model_node.type in {"identifier", "shorthand_property_identifier"}
                    else None,
                },
                "model_expression": None,
                "prompt_template_refs": refs,
                "source_file": path,
                "source_symbol": scope.symbol,
                "line": node.start_point.row + 1,
                "workflow_hint": scope.symbol,
                "workflow_attribution": "heuristic",
                "confidence": "medium",
                "unresolved": (["runtime_model"] if not model else [])
                + ["javascript_call_graph_not_resolved"],
                "evidence": [path + ":" + str(node.start_point.row + 1), "tree_sitter_parse_only"],
                "price": catalog.rate_info(provider, model),
                "upstream_callers": [],
                "entry_points": [],
                "configured_max_output_tokens": output_limit
                if output_limit and output_limit < 10000000
                else None,
            }
        )
    return sites
