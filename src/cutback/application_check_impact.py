"""Bounded, parse-only impact planning for reviewed application check declarations.

Import reachability is evidence about source dependencies, never proof that a
command tests them. Unknown impact falls back to every discovered command.
"""

import ast
import tomllib
from pathlib import Path, PurePosixPath

from pydantic import Field, StrictInt, model_validator

from cutback.application_check_discovery import read_bound, table
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import safe_relative

MAX_PYTHON_BYTES = 8388608
MAX_SYNTAX_NODES = 500000
MAX_IMPORT_EDGES = 50000
MAX_WORKFLOW_PATHS = 1000
OPAQUE_NAMES = {
    "__import__",
    "exec",
    "eval",
    "getattr",
    "setattr",
    "globals",
    "locals",
    "vars",
    "__builtins__",
}
OPAQUE_ATTRIBUTES = {
    "import_module",
    "spec_from_file_location",
    "module_from_spec",
    "run_path",
    "run_module",
    "__import__",
    "exec",
    "eval",
    "__getattribute__",
    "__dict__",
}


class WorkflowChecks(StrictModel):
    key: Identifier
    entrypoints: list[str] = Field(min_length=1, max_length=20)
    resources: list[str] = Field(default_factory=list, max_length=100)
    commands: list[Identifier] = Field(min_length=1, max_length=20)
    regression_commands: list[Identifier] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def reviewed_scope(self):
        for names in (self.entrypoints, self.resources, self.commands, self.regression_commands):
            if len(set(names)) != len(names):
                raise ValueError("APPLICATION_CHECK_IMPACT_DUPLICATE_DECLARATION")
        for name in self.entrypoints + self.resources:
            safe_relative(name)
        if not set(self.regression_commands) <= set(self.commands):
            raise ValueError("APPLICATION_CHECK_IMPACT_REGRESSION_COMMAND_NOT_IN_WORKFLOW")
        return self


class ImpactDeclaration(StrictModel):
    version: StrictInt = Field(ge=1, le=1)
    workflows: list[WorkflowChecks] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({w.key for w in self.workflows}) != len(self.workflows):
            raise ValueError("APPLICATION_CHECK_IMPACT_DUPLICATE_WORKFLOW")
        return self


def declaration(root, tree):
    raw = read_bound(root, tree, "pyproject.toml")
    config = tomllib.loads(raw) if raw is not None else {}
    value = table(table(config.get("tool", {})).get("cutback", {})).get("workflow_checks")
    return ImpactDeclaration.model_validate(value) if value is not None else None


def module_name(name):
    parts = list(PurePosixPath(name).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and all(part.isidentifier() for part in parts):
        return ".".join(parts)
    return None


def source_graph(root, tree):
    """Root/src layouts only; ambiguity and opaque imports prevent selective plans."""
    modules, names, unknowns = {}, {}, set()
    graph: dict[str, set[str]] = {name: set() for name in tree.files if name.endswith(".py")}
    for name in sorted(graph):
        module = module_name(name)
        if module is None:
            unknowns.add("unsupported_python_module_layout")
        elif module in modules:
            unknowns.add("ambiguous_python_module")
        else:
            modules[module] = name
            names[name] = module

    # Importing an entrypoint module also executes its package initializers,
    # even when that module contains no explicit import statements of its own.
    for name, module in names.items():
        parts = module.split(".")
        for index in range(1, len(parts)):
            ancestor = modules.get(".".join(parts[:index]))
            if ancestor:
                graph[name].add(ancestor)

    byte_count, node_count, edge_count = 0, 0, 0
    for name in sorted(graph):
        try:
            raw = read_bound(root, tree, name)
            byte_count += len((raw or "").encode("utf-8"))
            if byte_count > MAX_PYTHON_BYTES:
                unknowns.add("python_analysis_byte_bound_exceeded")
                break
            parsed = ast.parse(raw or "")
        except (ValueError, SyntaxError, UnicodeError, RecursionError):
            unknowns.add("python_source_not_statically_analyzable")
            continue
        module = names.get(name, "")
        package = module if PurePosixPath(name).name == "__init__.py" else module.rpartition(".")[0]
        sys_aliases = {
            alias.asname or alias.name
            for node in ast.walk(parsed)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "sys"
        }
        references: set[str] = set()
        for node in ast.walk(parsed):
            node_count += 1
            if node_count > MAX_SYNTAX_NODES:
                unknowns.add("python_analysis_node_bound_exceeded")
                break
            if isinstance(node, ast.Import):
                references.update(alias.name for alias in node.names)
                if any(alias.name.split(".")[0] in {"importlib", "runpy"} for alias in node.names):
                    unknowns.add("dynamic_python_execution_or_import")
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if not node.level and prefix.split(".")[0] in {"importlib", "runpy"}:
                    unknowns.add("dynamic_python_execution_or_import")
                if any(alias.name in OPAQUE_NAMES for alias in node.names):
                    unknowns.add("dynamic_python_execution_or_import")
                if prefix == "sys" and any(
                    alias.name in {"path", "meta_path", "path_hooks", "modules"}
                    for alias in node.names
                ):
                    unknowns.add("runtime_python_import_resolution")
                if node.level:
                    parts = package.split(".") if package else []
                    if node.level > len(parts):
                        unknowns.add("unresolved_relative_import")
                        continue
                    prefix = ".".join(
                        parts[: len(parts) - node.level + 1] + ([prefix] if prefix else [])
                    )
                if prefix:
                    references.add(prefix)
                    references.update(prefix + "." + alias.name for alias in node.names)
                if any(alias.name == "*" for alias in node.names):
                    unknowns.add("wildcard_python_import")
            elif (
                isinstance(node, ast.Name)
                and node.id in OPAQUE_NAMES
                or isinstance(node, ast.Attribute)
                and node.attr in OPAQUE_ATTRIBUTES
            ):
                # Inspect references, not just direct calls: assigning an opaque
                # callable to an alias does not make its imports statically known.
                unknowns.add("dynamic_python_execution_or_import")
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in sys_aliases
                and node.attr in {"path", "meta_path", "path_hooks", "modules"}
                or isinstance(node, ast.Name)
                and node.id == "__path__"
            ):
                unknowns.add("runtime_python_import_resolution")
        if node_count > MAX_SYNTAX_NODES:
            break
        for reference in references:
            parts = reference.split(".")
            for index in range(1, len(parts) + 1):
                dependency = modules.get(".".join(parts[:index]))
                if dependency:
                    graph[name].add(dependency)
        edge_count += len(graph[name])
        if edge_count > MAX_IMPORT_EDGES:
            unknowns.add("python_analysis_edge_bound_exceeded")
            break
    return graph, unknowns


def closure(graph, entrypoints):
    pending, found = list(entrypoints), set()
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            pending.extend(graph.get(name, ()))
    return found


def impact_plan(base_root: Path, proposed_root: Path, before, after, commands, *, selective=False):
    left, right = declaration(base_root, before), declaration(proposed_root, after)
    if left != right:
        raise ValueError("APPLICATION_CHECK_IMPACT_DECLARATION_CHANGED")
    keys = [command.key for command in commands]
    changed = sorted(
        name
        for name in before.files.keys() | after.files.keys()
        if before.files.get(name) != after.files.get(name)
    )
    result = {
        "schema_version": "cutback.application-check-impact.v1",
        "requested_selection": "affected_with_regression_guards" if selective else "all_commands",
        "mode": "all_commands",
        "changed_paths": changed,
        "workflows": [],
        "selected_commands": keys,
        "omitted_commands": [],
        "global_commands": keys,
        "fallback_reasons": [],
        "coverage": "reviewed_declarations_and_static_reachability_not_semantic_test_coverage",
        "external_consumers": "unknown",
        "application_imported": False,
        "application_commands_executed": 0,
        "provider_calls_authorized": 0,
    }
    if left is None:
        result["fallback_reasons"] = ["no_reviewed_workflow_declaration"]
        return commands, result

    for workflow in left.workflows:
        if not set(workflow.commands) <= set(keys):
            raise ValueError("APPLICATION_CHECK_IMPACT_UNKNOWN_COMMAND")
        for name in workflow.entrypoints + workflow.resources:
            if name not in before.files or name not in after.files:
                raise ValueError("APPLICATION_CHECK_IMPACT_DECLARED_PATH_MISSING")
        if any(not name.endswith(".py") for name in workflow.entrypoints):
            raise ValueError("APPLICATION_CHECK_IMPACT_PYTHON_ENTRYPOINT_REQUIRED")

    graph_before, unknowns_before = source_graph(base_root, before)
    graph_after, unknowns_after = source_graph(proposed_root, after)
    reasons = unknowns_before | unknowns_after
    covered, affected_commands, guard_commands, declared_commands = set(), set(), set(), set()
    workflows = []
    for workflow in left.workflows:
        dependencies = (
            closure(graph_before, workflow.entrypoints)
            | closure(graph_after, workflow.entrypoints)
            | set(workflow.resources)
        )
        if len(dependencies) > MAX_WORKFLOW_PATHS:
            reasons.add("workflow_dependency_report_bound_exceeded")
        affected = sorted(set(changed) & dependencies)
        covered.update(dependencies)
        declared_commands.update(workflow.commands)
        if affected:
            affected_commands.update(workflow.commands)
        else:
            guard_commands.update(workflow.regression_commands)
        workflows.append(
            {
                "key": workflow.key,
                "status": "affected" if affected else "unchanged_declared_scope",
                "changed_dependencies": affected,
                "dependency_paths": sorted(dependencies)[:MAX_WORKFLOW_PATHS],
                "dependency_count": len(dependencies),
                "dependency_paths_truncated": len(dependencies) > MAX_WORKFLOW_PATHS,
                "commands": workflow.commands,
                "regression_commands": workflow.regression_commands,
            }
        )
    if set(changed) - covered:
        reasons.add("changed_paths_outside_declared_workflows")
    if not changed:
        reasons.add("no_source_changes")
    global_commands = set(keys) - declared_commands
    selected = affected_commands | guard_commands | global_commands
    # Full-suite mode is the default. Selective mode is explicitly requested and
    # every unchanged declared workflow still contributes its reviewed guard.
    if not selective or reasons:
        selected = set(keys)
    priority = affected_commands | global_commands
    selected_keys = [key for key in keys if key in selected and key in priority] + [
        key for key in keys if key in selected and key not in priority
    ]
    by_key = {command.key: command for command in commands}
    result.update(
        {
            "mode": "all_commands_fallback"
            if reasons
            else (
                "affected_with_regression_guards" if selective else "all_commands_affected_first"
            ),
            "workflows": workflows,
            "selected_commands": selected_keys,
            "omitted_commands": [key for key in keys if key not in selected],
            "global_commands": [key for key in keys if key in global_commands],
            "fallback_reasons": sorted(reasons),
        }
    )
    return [by_key[key] for key in selected_keys], result
