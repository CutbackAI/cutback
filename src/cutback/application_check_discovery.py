"""Parse-only discovery of existing checks in two exact application source trees.

Produces a reviewable ordinary check contract. It imports no application, runs no
test collection/package script and cannot prove that any test exercises a workflow.
"""

import ast
import hashlib
import json
import tomllib
from pathlib import Path

from cutback.application_checks import (
    ApplicationCheckContract,
    ApplicationCommand,
    snapshot,
    source_root,
)
from cutback.core.request_profiles import fingerprint

CONFIG_BYTES = 262144


def table(value):
    if not isinstance(value, dict):
        raise ValueError("APPLICATION_CHECK_DISCOVERY_CONFIGURATION_SHAPE_INVALID")
    return value


def check_definitions(tree):
    configuration = {
        "pyproject.toml",
        "pytest.ini",
        ".pytest.ini",
        "setup.cfg",
        "tox.ini",
        "package.json",
    }
    return {
        name: digest
        for name, digest in tree.files.items()
        if (
            Path(name).name in configuration | {"conftest.py", "check_application.py"}
            or Path(name).name.startswith("test_")
            and name.endswith(".py")
            or Path(name).name.endswith(("_test.py", ".test.js", ".test.mjs", ".test.cjs"))
        )
    }


def read_bound(root, tree, name):
    """Read only a previously frozen tracked file and recheck its exact bytes."""
    if name not in tree.files:
        return None
    path = root / name
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("APPLICATION_CHECK_DISCOVERY_UNSAFE_SOURCE")
    raw = path.read_bytes()
    if len(raw) > CONFIG_BYTES:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_CONFIGURATION_BOUND_EXCEEDED")
    if hashlib.sha256(raw).hexdigest() != tree.files[name]:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_SOURCE_CHANGED")
    return raw.decode("utf-8")


def candidates(root, tree):
    commands, origins, unknowns = [], {}, []
    raw = read_bound(root, tree, "pyproject.toml")
    config = tomllib.loads(raw) if raw is not None else {}
    tools = table(config.get("tool", {}))
    explicit = table(tools.get("cutback", {})).get("application_checks")
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) != {"commands"}:
            raise ValueError("APPLICATION_CHECK_DISCOVERY_DECLARATION_INVALID")
        definitions = explicit["commands"]
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= 20:
            raise ValueError("APPLICATION_CHECK_DISCOVERY_COMMAND_BOUND_EXCEEDED")
        for value in definitions:
            command = ApplicationCommand.model_validate(value)
            commands.append(command)
            origins[command.key] = "tracked_pyproject_explicit_declaration"
        return commands, origins, ["declared_commands_need_review_not_independent_coverage"]

    # Run the full suite rather than claim a selective import graph is complete.
    # Pytest's own configuration/fixtures are interpreted only after explicit execution.
    tests = sorted(
        name
        for name in tree.files
        if name.endswith(".py")
        and (Path(name).name.startswith("test_") or Path(name).name.endswith("_test.py"))
    )
    pytest_config = table(tools.get("pytest", {})).get("ini_options")
    if pytest_config is not None:
        table(pytest_config)
    if (
        tests
        or pytest_config is not None
        or any(name in tree.files for name in ("pytest.ini", ".pytest.ini"))
    ):
        commands.append(
            ApplicationCommand(key="discovered-python-suite", arguments=["-m", "pytest"])
        )
        origins[commands[-1].key] = "full_pytest_suite_candidate_not_selective_coverage"
        unknowns += [
            "pytest_dependency_and_plugins_not_executed_or_verified",
            "test_collection_and_fixture_effects_require_local_review",
        ]
    else:
        # A conventional independent script is useful for applications with no test runner.
        # Its name and guarded entry point identify a candidate, never authority to run it.
        scripts = sorted(name for name in tree.files if Path(name).name == "check_application.py")
        if len(scripts) > 20:
            raise ValueError("APPLICATION_CHECK_DISCOVERY_COMMAND_BOUND_EXCEEDED")
        for name in scripts:
            source = read_bound(root, tree, name)
            parsed = ast.parse(source or "")
            guarded = any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.Eq)
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"
                for node in parsed.body
            )
            if guarded:
                command = ApplicationCommand(
                    key="discovered-script-" + hashlib.sha256(name.encode()).hexdigest()[:12],
                    arguments=[name],
                )
                commands.append(command)
                origins[command.key] = "guarded_conventional_application_script_candidate"
            else:
                unknowns.append("unguarded_application_check_script_requires_explicit_declaration")

    package = read_bound(root, tree, "package.json")
    if package is not None:
        value = json.loads(package)
        script = table(table(value).get("scripts", {})).get("test")
        if script == "node --test":
            command = ApplicationCommand(
                key="discovered-node-suite", runtime="node", arguments=["--test"]
            )
            commands.append(command)
            origins[command.key] = "literal_native_node_test_suite"
        elif script:
            unknowns.append("package_test_script_requires_explicit_command_no_shell_inference")
    if len(commands) > 20:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_COMMAND_BOUND_EXCEEDED")
    return commands, origins, sorted(set(unknowns))


def discover(base: Path, proposed: Path):
    a, b = source_root(base), source_root(proposed)
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("APPLICATION_CHECK_INDEPENDENT_WORKSPACES_REQUIRED")
    before, after = snapshot(a), snapshot(b)
    if check_definitions(before) != check_definitions(after):
        raise ValueError("APPLICATION_CHECK_DISCOVERY_RUNNER_CHANGED_REVIEW_EXPLICIT_COMMANDS")
    left, left_origins, left_unknowns = candidates(a, before)
    right, right_origins, right_unknowns = candidates(b, after)
    if [c.model_dump(mode="json") for c in left] != [c.model_dump(mode="json") for c in right]:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_RUNNER_CHANGED_REVIEW_EXPLICIT_COMMANDS")
    if not left:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_NO_DISCOVERED_COMMAND_CANDIDATES")
    if snapshot(a) != before or snapshot(b) != after:
        raise ValueError("APPLICATION_CHECK_DISCOVERY_SOURCE_CHANGED")
    contract = ApplicationCheckContract(base=before, proposed=after, commands=left)
    result = {
        "schema_version": "cutback.application-check-discovery.v1",
        "status": "REVIEW_REQUIRED_BEFORE_EXPLICIT_LOCAL_EXECUTION",
        "contract": contract.model_dump(mode="json"),
        "contract_hash": fingerprint(contract.model_dump(mode="json")),
        "origins": {"base": left_origins, "proposed": right_origins},
        "unknowns": sorted(
            set(
                left_unknowns
                + right_unknowns
                + [
                    "workflow_test_coverage_not_independently_established",
                    "dependencies_and_ignored_runtime_state_not_qualified",
                    "external_consumers_not_discovered",
                ]
            )
        ),
        "application_imported": False,
        "application_commands_executed": 0,
        "provider_calls_authorized": 0,
        "coverage": "candidate_full_suites_or_explicit_declarations_not_verified_workflow_coverage",
    }
    return {**result, "discovery_hash": fingerprint(result)}
