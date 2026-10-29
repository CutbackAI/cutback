"""Conservative, non-executing Python inventory. No source content in default output."""

import ast
import copy
import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from pathspec import GitIgnoreSpec

from cutback import __version__
from cutback.core.config import load_config
from cutback.pricing import Catalog

SKIP = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cutback",
    "site-packages",
}
CONSTRUCTORS = {
    "openai.OpenAI": "openai",
    "openai.AsyncOpenAI": "openai",
    "openai.AzureOpenAI": "azure_openai",
    "openai.AsyncAzureOpenAI": "azure_openai",
    "anthropic.Anthropic": "anthropic",
    "anthropic.AsyncAnthropic": "anthropic",
}
SURFACES = {
    "openai": ("responses.create", "chat.completions.create"),
    "anthropic": ("messages.create",),
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def safe_model(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value)
        and not value.lower().startswith(("sk-", "sk_", "bearer"))
    ):
        return value
    return None


@dataclass
class Module:
    path: str
    name: str
    tree: ast.Module
    bindings: dict[str, Any] = field(default_factory=dict)
    nodes: list[tuple[ast.AST, str]] = field(default_factory=list)
    functions: set[str] = field(default_factory=set)
    definitions: dict[str, Any] = field(default_factory=dict)
    attribute_assignments: dict[str, list] = field(default_factory=dict)

    def index(self):
        def bind(key, value):
            # Multiple assignments are ambiguous: never claim execution order or branch resolution.
            self.bindings[key] = value if key not in self.bindings else None

        def visit(node, scope=""):
            self.nodes.append((node, scope))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                key = f"{scope}.{node.name}".strip(".")
                bind(key, ("symbol", f"{self.name}.{key}"))
                self.functions.add(key)
                self.definitions[key] = node
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                    parameters += [arg for arg in (node.args.vararg, node.args.kwarg) if arg]
                    for arg in parameters:
                        bind(f"{key}.{arg.arg}", None)
                for child in node.body:
                    visit(child, key)
                return
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bind(
                        f"{scope}.{alias.asname or alias.name.split('.')[0]}".strip("."),
                        ("symbol", alias.name if alias.asname else alias.name.split(".")[0]),
                    )
            elif isinstance(node, ast.ImportFrom):
                package = (
                    self.name.split(".")
                    if self.path.endswith("/__init__.py")
                    else self.name.split(".")[:-1]
                )
                parent = package[: len(package) - node.level + 1] if node.level else []
                target = ".".join(parent + ([node.module] if node.module else []))
                for alias in node.names:
                    bind(
                        f"{scope}.{alias.asname or alias.name}".strip("."),
                        ("symbol", f"{target}.{alias.name}"),
                    )
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name):
                        bind(
                            f"{scope}.{item.optional_vars.id}".strip("."),
                            ("expr", item.context_expr, scope),
                        )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for assignment_target in targets:
                    if isinstance(assignment_target, ast.Name):
                        bind(
                            f"{scope}.{assignment_target.id}".strip("."),
                            ("expr", node.value, scope),
                        )
                    elif (
                        isinstance(assignment_target, ast.Attribute)
                        and isinstance(assignment_target.value, ast.Name)
                        and assignment_target.value.id in {"self", "cls"}
                        and "." in scope
                    ):
                        class_scope = scope.rsplit(".", 1)[0]
                        bind(
                            f"{class_scope}.@{assignment_target.attr}", ("expr", node.value, scope)
                        )
                        self.attribute_assignments.setdefault(
                            f"{class_scope}.@{assignment_target.attr}", []
                        ).append((node.value, scope))
            for walk_child in ast.iter_child_nodes(node):
                visit(walk_child, scope)

        visit(self.tree)


def scan(root: str | Path, *, languages: list[str] | None = None) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("scan target must be a directory")
    config = load_config(root).scanner
    if languages is not None:
        config = type(config).model_validate({**config.model_dump(), "languages": languages})
    deadline = time.monotonic() + config.max_seconds
    external_sites = []
    javascript_available = True
    ignore_path = root / ".gitignore"
    ignore = GitIgnoreSpec.from_lines([])
    if (
        ignore_path.is_file()
        and not ignore_path.is_symlink()
        and ignore_path.stat().st_size <= 65536
    ):
        ignore = GitIgnoreSpec.from_lines(ignore_path.read_text(encoding="utf-8").splitlines())
    warnings: list[dict] = []
    modules: dict[str, Module] = {}
    ignore_specs: dict[Path, GitIgnoreSpec] = {root: ignore}

    def ignored(path: Path, directory=False):
        result = False
        for ancestor in reversed(path.parent.parents):
            if ancestor in ignore_specs:
                match = ignore_specs[ancestor].check_file(
                    path.relative_to(ancestor).as_posix() + ("/" if directory else "")
                )
                if match.include is not None:
                    result = match.include
        if path.parent in ignore_specs:
            match = ignore_specs[path.parent].check_file(path.name + ("/" if directory else ""))
            if match.include is not None:
                result = match.include
        return result

    counts = {
        "applicable_python_files": 0,
        "analyzed_files": 0,
        "excluded_entries": 0,
        "unparsed_files": 0,
        "other_source_files": 0,
        "applicable_javascript_files": 0,
        "analyzed_javascript_files": 0,
    }
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if time.monotonic() > deadline:
            warnings.append(
                {
                    "code": "SCAN_TIME_LIMIT",
                    "next_action": "raise_scanner_max_seconds_or_narrow_target",
                }
            )
            break
        nested = base / ".gitignore"
        if (
            base != root
            and nested.is_file()
            and not nested.is_symlink()
            and nested.stat().st_size <= 65536
        ):
            try:
                ignore_specs[base] = GitIgnoreSpec.from_lines(
                    nested.read_text(encoding="utf-8").splitlines()
                )
            except (OSError, UnicodeError):
                warnings.append(
                    {"code": "IGNORE_FILE_UNREADABLE", "file": nested.relative_to(root).as_posix()}
                )
        for name in list(dirs):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if (
                name in SKIP
                or path.is_symlink()
                or not path.resolve().is_relative_to(root)
                or ignored(path, True)
                or any(fnmatch(rel + "/", p) or fnmatch(rel, p) for p in config.exclude)
            ):
                dirs.remove(name)
                counts["excluded_entries"] += 1
        for name in sorted(files):
            path = base / name
            rel = path.relative_to(root).as_posix()
            external_language = (
                "javascript"
                if path.suffix in {".js", ".jsx", ".mjs", ".cjs"}
                else "typescript"
                if path.suffix in {".ts", ".tsx", ".mts", ".cts"}
                else None
            )
            if external_language:
                counts["applicable_javascript_files"] += 1
                if external_language not in config.languages:
                    counts["other_source_files"] += 1
            if path.suffix != ".py" and external_language not in config.languages:
                continue
            if path.suffix == ".py":
                counts["applicable_python_files"] += 1
                if "python" not in config.languages:
                    continue
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(root)
                or ignored(path)
                or any(fnmatch(rel, p) for p in config.exclude)
            ):
                counts["excluded_entries"] += 1
                continue
            if (
                counts["analyzed_files"] >= config.max_files
                or path.stat().st_size > config.max_file_bytes
            ):
                warnings.append({"code": "RESOURCE_LIMIT", "file": rel})
                counts["unparsed_files"] += 1
                continue
            if external_language:
                if not javascript_available:
                    counts["unparsed_files"] += 1
                    continue
                try:
                    from cutback.scanner.javascript import parse_file

                    external_sites.extend(parse_file(path.read_bytes(), rel, Catalog(), deadline))
                    counts["analyzed_files"] += 1
                    counts["analyzed_javascript_files"] += 1
                except ImportError:
                    javascript_available = False
                    warnings.append(
                        {
                            "code": "JAVASCRIPT_EXTRA_REQUIRED",
                            "next_action": "install_cutback_javascript_extra",
                        }
                    )
                    counts["unparsed_files"] += 1
                except (ValueError, OSError, UnicodeError, RecursionError):
                    warnings.append({"code": "UNPARSED_JAVASCRIPT", "file": rel})
                    counts["unparsed_files"] += 1
                continue
            try:
                source = path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=rel)
            except (OSError, UnicodeError, SyntaxError, RecursionError, ValueError):
                warnings.append({"code": "UNPARSED_FILE", "file": rel})
                counts["unparsed_files"] += 1
                continue
            module_name = rel.removeprefix("src/").removesuffix(".py").replace("/", ".")
            if module_name.endswith(".__init__"):
                module_name = module_name.removesuffix(".__init__")
            module = Module(rel, module_name, tree)
            try:
                module.index()
            except RecursionError:
                warnings.append({"code": "AST_DEPTH_LIMIT", "file": rel})
                counts["unparsed_files"] += 1
                continue
            modules[module_name] = module
            counts["analyzed_files"] += 1

    def local_target(name):
        prefix, _, rest = name.rpartition(".")
        while prefix:
            if prefix in modules:
                return modules[prefix], rest
            prefix, _, suffix = prefix.rpartition(".")
            rest = suffix + "." + rest
        return None, None

    parameter_arguments: dict[tuple, list] = {}
    heuristics: set[str] = set()

    def resolved_attribute(module, key, seen):
        marker = (module.name, "attribute:" + key)
        if marker in seen:
            return None
        values = module.attribute_assignments.get(key, [])
        candidates = []
        for value, value_scope in values:
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            candidates.append(symbol(module, value, value_scope, seen | {marker}))
        if (
            candidates
            and all(value == candidates[0] for value in candidates)
            and (candidates[0] and candidates[0].startswith("client:"))
        ):
            heuristics.add("conditional_client_attribute_requires_runtime_confirmation")
            return candidates[0]
        return None

    def symbol(module, node, scope, seen=frozenset()):
        if time.monotonic() > deadline or len(seen) > 100:
            return None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            resolved = [symbol(module, value, scope, seen) for value in node.values]
            known = [
                value for value in resolved if value and value.startswith(("client:", "object:"))
            ]
            if known and all(value == known[0] for value in known):
                heuristics.add("optional_client_override_requires_runtime_confirmation")
                return known[0]
        if isinstance(node, ast.Name):
            parts = scope.split(".") if scope else []
            for i in range(len(parts), -1, -1):
                key = ".".join(parts[:i] + [node.id])
                if key in module.bindings:
                    marker = (module.name, key)
                    if marker in seen:
                        return None
                    value = module.bindings[key]
                    if value is None:
                        arguments = parameter_arguments.get((module.name, key), [])
                        if arguments and len(arguments) <= 100:
                            resolved = [
                                symbol(other, arg, caller, seen | {marker})
                                for other, arg, caller in arguments
                            ]
                            if all(result == resolved[0] for result in resolved) and (
                                resolved[0] and resolved[0].startswith(("client:", "object:"))
                            ):
                                heuristics.add(
                                    "caller_parameter_flow_requires_runtime_confirmation"
                                )
                                return resolved[0]
                        return None
                    if value[0] == "symbol":
                        target = value[1]
                        if key in module.functions:
                            return target
                        other, rest = local_target(target)
                        if other and "." not in rest and rest in other.bindings:
                            return symbol(other, ast.Name(id=rest), "", seen | {marker})
                        return target
                    return symbol(module, value[1], value[2], seen | {marker})
            return None
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
                class_scope = scope.rsplit(".", 1)[0] if "." in scope else scope
                binding = module.bindings.get(f"{class_scope}.@{node.attr}")
                if binding and binding[0] == "expr":
                    marker = (module.name, f"{class_scope}.@{node.attr}")
                    if marker not in seen:
                        return symbol(module, binding[1], binding[2], seen | {marker})
                resolved = resolved_attribute(module, f"{class_scope}.@{node.attr}", seen)
                if resolved:
                    return resolved
                if f"{class_scope}.{node.attr}" in module.definitions:
                    return f"{module.name}.{class_scope}.{node.attr}"
            parent = symbol(module, node.value, scope, seen)
            if parent and parent.startswith("object:"):
                other, class_scope = local_target(parent.removeprefix("object:"))
                if other:
                    binding = other.bindings.get(f"{class_scope}.@{node.attr}")
                    marker = (other.name, f"{class_scope}.@{node.attr}")
                    if binding and binding[0] == "expr" and marker not in seen:
                        return symbol(other, binding[1], binding[2], seen | {marker})
                    forwarded = other.definitions.get(f"{class_scope}.__getattr__")
                    if isinstance(forwarded, ast.FunctionDef) and len(forwarded.body) >= 1:
                        returns = [stmt for stmt in forwarded.body if isinstance(stmt, ast.Return)]
                        if len(returns) == 1:
                            call = returns[0].value
                            if (
                                isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Name)
                                and call.func.id == "getattr"
                                and len(call.args) == 2
                            ):
                                delegated = symbol(
                                    other,
                                    call.args[0],
                                    f"{class_scope}.__getattr__",
                                    seen | {marker},
                                )
                                if delegated and delegated.startswith("client:"):
                                    heuristics.add(
                                        "lazy_client_forwarder_requires_runtime_confirmation"
                                    )
                                    return delegated + "." + node.attr
            return f"{parent}.{node.attr}" if parent else None
        if isinstance(node, ast.Call):
            constructor = symbol(module, node.func, scope, seen)
            if constructor in CONSTRUCTORS:
                return "client:" + CONSTRUCTORS[constructor]
            if constructor:
                other, key = local_target(constructor)
                if other:
                    definition = other.definitions.get(key)
                    marker = (other.name, "factory:" + key)
                    if marker in seen:
                        return None
                    if isinstance(definition, ast.ClassDef):
                        return "object:" + constructor
                    if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        returns = [stmt for stmt in definition.body if isinstance(stmt, ast.Return)]
                        if len(returns) == 1 and not any(
                            isinstance(stmt, (ast.For, ast.While)) for stmt in definition.body
                        ):
                            return symbol(other, returns[0].value, key, seen | {marker})
        return None

    def model_value(module, node, scope, seen=frozenset()):
        if time.monotonic() > deadline or len(seen) > 100:
            return None, "runtime_unresolved"
        if isinstance(node, ast.Constant):
            return safe_model(node.value), "literal"
        if isinstance(node, ast.Name):
            key = f"{scope}.{node.id}".strip(".")
            if key not in module.bindings:
                key = node.id
            marker = (module.name, key)
            value = module.bindings.get(key)
            if value and value[0] == "expr" and marker not in seen:
                resolved, _ = model_value(module, value[1], value[2], seen | {marker})
                return resolved, "config_symbol"
            if value and value[0] == "symbol":
                other, key = local_target(value[1])
                if other and marker not in seen:
                    return model_value(other, ast.Name(id=key), "", seen | {marker})
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id not in {"self", "cls"}
        ):
            target = symbol(module, node, scope, seen)
            namespace_module, namespace_key = local_target(target) if target else (None, None)
            marker = (module.name, "model_namespace:" + node.value.id + "." + node.attr)
            # A known local module constant is distinct from instance/class
            # attributes, descriptors, environment settings and external imports.
            if (
                namespace_module
                and namespace_key
                and "." not in namespace_key
                and marker not in seen
            ):
                value, _ = model_value(
                    namespace_module, ast.Name(id=namespace_key), "", seen | {marker}
                )
                return value, "config_symbol" if value is not None else "runtime_unresolved"
        return None, "runtime_unresolved"

    def configured_output_limit(kwargs):
        for key in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
            node = kwargs.get(key)
            if (
                isinstance(node, ast.Constant)
                and type(node.value) is int
                and 0 <= node.value <= 10000000
            ):
                return node.value
        return None

    catalog = Catalog()
    sites = []
    graph: dict[str, set[str]] = {}
    ordinal: dict[tuple, int] = {}
    # Collect simple local argument flow without executing wrappers. External/dynamic callers
    # are unknowable, so findings using this information stay explicitly heuristic.
    for module in modules.values():
        if time.monotonic() > deadline:
            break
        for node, scope in module.nodes:
            if not isinstance(node, ast.Call):
                continue
            target = symbol(module, node.func, scope)
            other, key = local_target(target) if target else (None, None)
            definition = other.definitions.get(key) if other else None
            if other is not None and isinstance(
                definition, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                params = [arg.arg for arg in (*definition.args.posonlyargs, *definition.args.args)]
                arguments = {name: arg for name, arg in zip(params, node.args, strict=False)}
                arguments.update({kw.arg: kw.value for kw in node.keywords if kw.arg})
                for name, arg in arguments.items():
                    parameter_arguments.setdefault((other.name, f"{key}.{name}"), []).append(
                        (module, arg, scope)
                    )
    counts["call_nodes_total"] = sum(
        isinstance(node, ast.Call) for module in modules.values() for node, _ in module.nodes
    )
    counts["call_nodes_analyzed"] = 0
    for module in modules.values():
        if time.monotonic() > deadline:
            warnings.append({"code": "ANALYSIS_TIME_BUDGET_EXCEEDED"})
            break
        for node, scope in module.nodes:
            if time.monotonic() > deadline:
                break
            if not isinstance(node, ast.Call):
                continue
            counts["call_nodes_analyzed"] += 1
            heuristics.clear()
            target = symbol(module, node.func, scope)
            caller = f"{module.name}.{scope}".rstrip(".")
            if target and not target.startswith("client:"):
                graph.setdefault(target, set()).add(caller)
            provider, surface = None, None
            if target:
                for candidate, methods in SURFACES.items():
                    for method in methods:
                        if target in {f"client:{candidate}.{method}", f"{candidate}.{method}"}:
                            provider, surface = candidate, method
            if not provider or not surface:
                if target and ("azure_openai" in target or "litellm" in target):
                    warnings.append(
                        {"code": "UNSUPPORTED_PROVIDER", "file": module.path, "line": node.lineno}
                    )
                # Detect direct HTTP endpoints without including URLs or payloads in output.
                if isinstance(node.func, ast.Attribute) and node.func.attr in {"post", "request"}:
                    if any(
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and re.match(r"https://api\.(openai|anthropic)\.com/", arg.value)
                        for arg in node.args
                    ):
                        warnings.append(
                            {
                                "code": "DIRECT_HTTP_NEEDS_INSTRUMENTATION",
                                "file": module.path,
                                "line": node.lineno,
                            }
                        )
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            model_node = kwargs.get("model")
            model, model_kind = model_value(module, model_node, scope)
            unresolved = ([] if model else ["model_runtime_unresolved"]) + sorted(heuristics)
            model_ref = {
                "kind": model_kind,
                "symbol": model_node.id if isinstance(model_node, ast.Name) else None,
                "environment_names": [],
            }
            if model_node:
                for child in ast.walk(model_node):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in {"getenv", "get"}
                        and child.args
                        and isinstance(child.args[0], ast.Constant)
                    ):
                        env_name = child.args[0].value
                        if isinstance(env_name, str) and re.fullmatch(
                            r"[A-Z_][A-Z0-9_]*", env_name
                        ):
                            model_ref["environment_names"].append(env_name)
            prompt_refs = []
            for key in ("input", "messages", "instructions", "system"):
                prompt = kwargs.get(key)
                if prompt is not None:
                    prompt_refs.append(
                        {
                            "argument": key,
                            "file": module.path,
                            "line": prompt.lineno,
                            "kind": type(prompt).__name__,
                            "symbol": prompt.id if isinstance(prompt, ast.Name) else None,
                            "content": None,
                            "static_characters": len(prompt.value)
                            if isinstance(prompt, ast.Constant) and isinstance(prompt.value, str)
                            else None,
                        }
                    )
                    if isinstance(prompt, ast.Name):
                        binding = module.bindings.get(
                            f"{scope}.{prompt.id}".strip(".")
                        ) or module.bindings.get(prompt.id)
                        if binding and binding[0] == "expr" and binding[1] is not None:
                            for child in ast.walk(binding[1]):
                                if (
                                    isinstance(child, ast.Call)
                                    and isinstance(child.func, ast.Name)
                                    and child.func.id == "Path"
                                    and child.args
                                    and isinstance(child.args[0], ast.Constant)
                                    and isinstance(child.args[0].value, str)
                                ):
                                    template = child.args[0].value
                                    if (
                                        not Path(template).is_absolute()
                                        and ".." not in Path(template).parts
                                        and Path(template).suffix
                                        in {".j2", ".jinja2", ".txt", ".md"}
                                    ):
                                        prompt_refs[-1]["template_path"] = template
            if not prompt_refs:
                unresolved.append("prompt_reference_unresolved")
            signature = (module.path, scope, provider, surface)
            ordinal[signature] = ordinal.get(signature, 0) + 1
            fingerprint = "callsite:" + digest("|".join(signature) + f"|{ordinal[signature]}")

            class RedactConstants(ast.NodeTransformer):
                def visit_Constant(self, node):
                    return ast.copy_location(ast.Constant(value="<redacted>"), node)

            expression = (
                ast.unparse(RedactConstants().visit(copy.deepcopy(model_node)))
                if model_node is not None
                else None
            )
            sites.append(
                {
                    "call_site_id": fingerprint,
                    "semantic_fingerprint": "semantic:"
                    + digest("|".join((scope, provider, surface, expression or "unknown"))),
                    "provider": provider,
                    "sdk_surface": surface,
                    "requested_model": model,
                    "model_reference": model_ref,
                    "model_expression": expression,
                    "prompt_template_refs": prompt_refs,
                    "source_file": module.path,
                    "source_symbol": scope or "<module>",
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                    "workflow_hint": caller,
                    "workflow_attribution": "heuristic",
                    "confidence": "medium" if heuristics else "high",
                    "unresolved": unresolved,
                    "evidence": [f"{module.path}:{node.lineno}", "python_ast"],
                    "price": catalog.rate_info(provider, model),
                    "upstream_callers": [],
                    "entry_points": [],
                    "configured_max_output_tokens": configured_output_limit(kwargs),
                    "evidence_status": "discovered",
                    "actual_cost": None,
                    "actual_tokens": None,
                    "actual_latency": None,
                }
            )
    for site in sites:
        pending = list(graph.get(site["workflow_hint"], set()))
        visited: set[str] = set()
        while pending and len(visited) < 1000:
            caller = pending.pop()
            if caller not in visited:
                visited.add(caller)
                pending.extend(graph.get(caller, set()) - visited)
        site["upstream_callers"] = sorted(visited) if config.follow_local_call_graph else []
        for qualified in sorted(visited | {site["workflow_hint"]}):
            for module in modules.values():
                prefix = module.name + "."
                if not qualified.startswith(prefix):
                    continue
                definition = module.definitions.get(qualified[len(prefix) :])
                if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not graph.get(qualified):
                        site["entry_points"].append(
                            {
                                "symbol": qualified,
                                "file": module.path,
                                "line": definition.lineno,
                                "kind": "function_root",
                                "confidence": "heuristic",
                            }
                        )
                    for decorator in definition.decorator_list:
                        if (
                            isinstance(decorator, ast.Call)
                            and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr
                            in {"get", "post", "put", "delete", "patch", "route"}
                        ):
                            site["entry_points"].append(
                                {
                                    "symbol": qualified,
                                    "file": module.path,
                                    "line": definition.lineno,
                                    "kind": "http_route",
                                    "confidence": "heuristic",
                                }
                            )
    sites.extend(external_sites)
    for module in modules.values():
        imports_sdk = any(
            isinstance(node, ast.ImportFrom)
            and node.module in {"openai", "anthropic"}
            or isinstance(node, ast.Import)
            and any(alias.name in {"openai", "anthropic"} for alias in node.names)
            for node, _ in module.nodes
        )
        if imports_sdk and not any(site["source_file"] == module.path for site in sites):
            warnings.append(
                {
                    "code": "SDK_IMPORT_WITHOUT_RESOLVED_CALL_SITE",
                    "file": module.path,
                    "meaning": (
                        "no local resolved calls; dynamic or shared-wrapper "
                        "coverage remains unknown"
                    ),
                }
            )
    sites.sort(key=lambda site: (site["source_file"], site["line"]))
    if counts["other_source_files"]:
        warnings.append({"code": "UNSUPPORTED_LANGUAGES", "count": counts["other_source_files"]})
    try:
        git_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        commit = git_result.stdout.strip() if git_result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    result = {
        "version": "1.0",
        "scanner_version": __version__,
        "project": {"root": str(root), "commit": commit},
        "coverage": counts,
        "summary": {
            "llm_call_sites_detected": len(sites),
            "runtime_observations": 0,
            "actual_spend_usd": None,
            "actual_spend_reason": "no_runtime_telemetry",
        },
        "call_sites": sites,
        "warnings": warnings,
        "unknowns": [
            {
                "field": "historical_usage_and_quality",
                "reason": "static_code_only",
                "next_action": "instrument_supported_sdk_and_import_authorized_examples",
            }
        ],
        "limitations": [
            "Python AST and opt-in JS/TS tree-sitter; no exhaustive recall claim",
            "JS/TS imports across files, destructuring and dynamic factories remain unresolved",
            "Ambiguous assignments, dynamic factories and object mutation may be unresolved",
            "Call sites are static candidates, not runtime coverage",
            "Fingerprint changes on moves or same-symbol sink reordering",
        ],
    }
    from cutback.core.inventory import Inventory

    return Inventory.model_validate(result).model_dump(mode="json")


def reconcile_inventory(previous: dict, current: dict) -> dict:
    """Link unique semantic identities across file moves, preserving ambiguity."""
    before: dict[str, list] = {}
    after: dict[str, list] = {}
    for site in previous.get("call_sites", []):
        before.setdefault(site.get("semantic_fingerprint", site["call_site_id"]), []).append(site)
    for site in current.get("call_sites", []):
        after.setdefault(site.get("semantic_fingerprint", site["call_site_id"]), []).append(site)
    links, ambiguous = [], []
    for key in before.keys() & after.keys():
        if len(before[key]) == len(after[key]) == 1:
            links.append(
                {
                    "previous": before[key][0]["call_site_id"],
                    "current": after[key][0]["call_site_id"],
                    "method": "unique_semantic_fingerprint",
                }
            )
        else:
            ambiguous.append(key)
    return {"links": links, "ambiguous": ambiguous, "inferred_runtime_workflows": None}
