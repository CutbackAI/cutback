from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.core.prompts import PromptDocument, PromptError, PromptGraph


def test_typed_composition_is_pinned_exact_and_does_not_reparse_inserted_data():
    root, component = uuid4(), uuid4()
    graph = PromptGraph(
        root,
        {
            component: PromptDocument(text="Policy: {{policy}}. ", variables={"policy": "json"}),
            root: PromptDocument(
                text="{{>rules}}Classify {{input}}; {{count}} examples; strict={{strict}}.",
                variables={"input": "string", "count": "integer", "strict": "boolean"},
                dependencies={"rules": component},
            ),
        },
    )
    values = {
        "policy": {"labels": ["A", "B"]},
        "input": "{{>arbitrary_untrusted_data}}",
        "count": 2,
        "strict": True,
    }
    rendered = graph.render(values)
    assert rendered == {
        "kind": "text",
        "text": 'Policy: {"labels":["A","B"]}. Classify {{>arbitrary_untrusted_data}}; '
        "2 examples; strict=true.",
    }
    with pytest.raises(PromptError, match="TYPE_INVALID"):
        graph.render({**values, "count": True})
    with pytest.raises(PromptError, match="MISSING_OR_EXTRA"):
        graph.render({**values, "undeclared": "private-value"})
    with pytest.raises(PromptError, match="MISSING_OR_EXTRA"):
        graph.render({k: v for k, v in values.items() if k != "policy"})
    with pytest.raises(PromptError, match="JSON_INVALID"):
        graph.render({**values, "policy": {"number": float("nan")}})


@pytest.mark.parametrize(
    "template",
    [
        "{{input.attr}}",
        "{{input[0]}}",
        "{{__import__('os')}}",
        "{{>../../secret}}",
        "{{>https://example.invalid}}",
        "{% include 'file' %}",
        "{{input",
    ],
)
def test_unsupported_templates_are_data_errors_not_executable_includes(template):
    with pytest.raises(ValidationError):
        PromptDocument(text=template, variables={"input": "string"})


def test_declarations_and_qualified_schema_profile_fail_closed():
    with pytest.raises(ValidationError, match="DECLARATIONS"):
        PromptDocument(text="constant", variables={"input": "string"})
    with pytest.raises(ValidationError, match="DECLARATIONS"):
        PromptDocument(text="{{input}}")
    with pytest.raises(ValidationError, match="REFERENCES_OR_REGEX"):
        PromptDocument(text="constant", output_schema={"$ref": "https://example.invalid/schema"})
    with pytest.raises(ValidationError, match="REFERENCES_OR_REGEX"):
        PromptDocument(text="constant", output_schema={"type": "string", "pattern": "(a+)+"})
    with pytest.raises(ValidationError):
        PromptDocument(text="\ud800")


def test_graph_cycles_missing_components_and_type_conflicts_are_explicit():
    root, child = uuid4(), uuid4()
    document = PromptDocument(text="{{>child}}", dependencies={"child": child})
    with pytest.raises(PromptError, match="TEXT_COMPONENT"):
        PromptGraph(root, {root: document}).inspect()
    graph = PromptGraph(
        root, {root: document, child: PromptDocument(text="{{>root}}", dependencies={"root": root})}
    )
    with pytest.raises(PromptError, match="CYCLE"):
        graph.inspect()
    graph = PromptGraph(
        root,
        {
            root: PromptDocument(
                text="{{input}}{{>child}}",
                variables={"input": "string"},
                dependencies={"child": child},
            ),
            child: PromptDocument(text="{{input}}", variables={"input": "integer"}),
        },
    )
    with pytest.raises(PromptError, match="TYPE_CONFLICT"):
        graph.inspect()
    graph = PromptGraph(
        root,
        {root: document, child: PromptDocument(text="component", output_schema={"type": "object"})},
    )
    with pytest.raises(PromptError, match="COMPONENT_SCHEMA"):
        graph.inspect()


def test_shared_dag_cannot_bypass_longest_path_or_expansion_bounds():
    root, leaf, shared, long = uuid4(), uuid4(), uuid4(), [uuid4() for _ in range(7)]
    documents = {
        leaf: PromptDocument(text="leaf"),
        shared: PromptDocument(text="{{>leaf}}", dependencies={"leaf": leaf}),
    }
    for i, identifier in enumerate(long):
        documents[identifier] = PromptDocument(
            text="{{>child}}", dependencies={"child": long[i + 1] if i + 1 < len(long) else shared}
        )
    documents[root] = PromptDocument(
        text="{{>short}}{{>long}}", dependencies={"short": shared, "long": long[0]}
    )
    with pytest.raises(PromptError, match="DEPTH_EXCEEDED"):
        PromptGraph(root, documents).inspect()
    documents = {
        leaf: PromptDocument(text="x" * 64000),
        root: PromptDocument(text="{{>leaf}}", dependencies={"leaf": leaf}),
    }
    assert len(PromptGraph(root, documents).render({})["text"]) == 64000
    documents[root] = PromptDocument(text="{{>leaf}}{{>leaf}}", dependencies={"leaf": leaf})
    with pytest.raises(PromptError, match="RENDERED_BYTES"):
        PromptGraph(root, documents).render({})
    documents[root] = PromptDocument(
        text="{{input}}{{>leaf}}{{>leaf}}",
        variables={"input": "string"},
        dependencies={"leaf": leaf},
    )
    with pytest.raises(PromptError, match="NATIVE_BYTES"):
        PromptGraph(root, documents).native_contract({})


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_native_compilation_uses_the_existing_declared_input_contract(surface):
    root = uuid4()
    graph = PromptGraph(
        root,
        {
            root: PromptDocument(
                text="{{prefix}}: {{input}}", variables={"prefix": "string", "input": "string"}
            )
        },
    )
    contract = graph.native_contract({"prefix": "Classify"}, surface=surface)
    value = contract.render("owned synthetic case")
    expected = "Classify: owned synthetic case"
    key = "input" if surface == "responses.create" else "messages"
    assert contract.version == "native-prompt-v1"
    assert value == {
        key: expected if surface == "responses.create" else [{"role": "user", "content": expected}]
    }
    assert graph.native_contract({"prefix": "{{input}}"}, surface=surface).render("data") == {
        key: "{{input}}: data"
        if surface == "responses.create"
        else [{"role": "user", "content": "{{input}}: data"}]
    }


def test_chat_preserves_roles_and_uses_native_anthropic_system_blocks():
    root = uuid4()
    doc = PromptDocument(
        kind="chat",
        messages=[
            {"role": "system", "content": "Use {{count}} labels."},
            {"role": "user", "content": "{{input}}"},
        ],
        variables={"count": "integer", "input": "string"},
    )
    graph = PromptGraph(root, {root: doc})
    expected = [
        {"role": "system", "content": "Use 2 labels."},
        {"role": "user", "content": "synthetic"},
    ]
    assert graph.render({"count": 2, "input": "synthetic"}) == {
        "kind": "chat",
        "messages": expected,
    }
    assert graph.native_contract({"count": 2}, surface="chat.completions.create").render(
        "synthetic"
    ) == {"messages": expected}
    assert graph.native_contract({"count": 2}, surface="messages.create").render("synthetic") == {
        "system": [{"type": "text", "text": "Use 2 labels."}],
        "messages": [{"role": "user", "content": "synthetic"}],
    }
    doc.messages[0].role = "developer"
    with pytest.raises(PromptError, match="DEVELOPER_ROLE"):
        graph.native_contract({"count": 2}, surface="messages.create")
    doc.messages[0].role = "system"
    doc.messages.reverse()
    with pytest.raises(PromptError, match="MUST_PRECEDE"):
        graph.native_contract({"count": 2}, surface="messages.create")


def test_native_contract_bounds_repeated_dynamic_input_before_join():
    root = uuid4()
    graph = PromptGraph(
        root, {root: PromptDocument(text="{{input}}" * 20, variables={"input": "string"})}
    )
    with pytest.raises(ValueError, match="BYTE_BOUND"):
        graph.native_contract({}).render("x" * 64000)


def test_native_schema_profile_rejects_known_provider_errors_before_any_request():
    root = uuid4()
    base = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }
    for bad in (
        {"type": "array", "items": {"type": "string"}},
        {**base, "additionalProperties": True},
        {**base, "required": []},
        {**base, "properties": {"label": {"type": "string", "format": "email"}}},
    ):
        graph = PromptGraph(
            root,
            {
                root: PromptDocument(
                    text="{{input}}", variables={"input": "string"}, output_schema=bad
                )
            },
        )
        with pytest.raises(PromptError, match="NATIVE_SCHEMA"):
            graph.native_contract({})
    bounded_number = {**base, "properties": {"label": {"type": "number", "minimum": 0}}}
    graph = PromptGraph(
        root,
        {
            root: PromptDocument(
                text="{{input}}", variables={"input": "string"}, output_schema=bounded_number
            )
        },
    )
    assert graph.native_contract({}).options["text"]["format"]["schema"] == bounded_number
    with pytest.raises(PromptError, match="KEYWORD_NOT_QUALIFIED"):
        graph.native_contract({}, surface="messages.create")


def test_schema_property_names_are_data_not_executable_schema_keywords():
    from cutback.evals.metrics import MetricSpec, evaluate

    root = uuid4()
    schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "$ref": {"type": "string"}},
        "required": ["pattern", "$ref"],
        "additionalProperties": False,
    }
    graph = PromptGraph(
        root,
        {
            root: PromptDocument(
                text="{{input}}", variables={"input": "string"}, output_schema=schema
            )
        },
    )
    assert graph.native_contract({}).options["text"]["format"]["schema"] == schema
    metric = MetricSpec(kind="json_schema", minimum=1, schema_definition=schema)
    assert (
        evaluate(
            [{}], ['{"pattern":"literal","$ref":"literal"}'], metric, independent_labels=False
        )["value"]
        == 1
    )


def test_sampling_options_are_explicit_bounded_and_native_surface_specific():
    root = uuid4()
    graph = PromptGraph(
        root, {root: PromptDocument(text="{{input}}", variables={"input": "string"})}
    )
    assert graph.native_contract(
        {}, sampling_options={"temperature": 0.2, "top_p": 0.8}
    ).options == {"temperature": 0.2, "top_p": 0.8}
    for bad in (
        {"temperature": 3},
        {"top_p": 1.1},
        {"temperature": True},
        {"n": 10},
        {"tools": []},
        {"stop": ["end"]},
    ):
        with pytest.raises(PromptError, match="SURFACE_OR_OPTIONS"):
            graph.native_contract({}, sampling_options=bad)
    assert graph.native_contract(
        {}, surface="chat.completions.create", sampling_options={"stop": ["end"]}
    ).options == {"stop": ["end"]}


def test_legacy_schema_references_and_dialect_switches_cannot_trigger_resolution(monkeypatch):
    import urllib.request

    from cutback.evals.metrics import MetricSpec

    calls = []

    def deny_network(*args, **kwargs):
        calls.append(True)
        raise AssertionError("schema resolution must remain offline")

    monkeypatch.setattr(urllib.request, "urlopen", deny_network)
    for bad in (
        {"$recursiveRef": "https://example.invalid/never-fetch"},
        {
            "$schema": "https://json-schema.org/draft/2019-09/schema",
            "$recursiveRef": "https://example.invalid/never-fetch",
        },
        {"$schema": "https://example.invalid/custom-schema"},
        {"$vocabulary": {"https://example.invalid/vocabulary": True}},
    ):
        schema = {"type": "object", "properties": {"field": bad}}
        with pytest.raises(ValueError):
            PromptDocument(text="constant", output_schema=schema)
        with pytest.raises(ValueError):
            MetricSpec(kind="json_schema", minimum=1, schema_definition=schema)
    assert not calls
