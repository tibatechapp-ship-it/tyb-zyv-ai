"""Tests for tool schemas — JSON-schema rendering, argument validation, registry."""

from __future__ import annotations

import pytest

from src.tools import (
    ToolParameter,
    ToolRegistry,
    ToolSchema,
    ValidationResult,
    validate_arguments,
)

WRITE = ToolSchema(
    "write_file",
    "Write a file to disk.",
    (
        ToolParameter("path", "string", "Absolute path.", required=True),
        ToolParameter("content", "string", "File contents.", required=True),
        ToolParameter("mode", "string", "Open mode.", enum=("w", "a"), default="w"),
        ToolParameter("retries", "integer", "How many times to retry.", default=3),
    ),
)


# -- construction -----------------------------------------------------------
def test_parameter_rejects_blank_name_and_unknown_type() -> None:
    with pytest.raises(ValueError):
        ToolParameter("", "string")
    with pytest.raises(ValueError):
        ToolParameter("x", "gadget")


def test_schema_rejects_blank_name_and_duplicate_params() -> None:
    with pytest.raises(ValueError):
        ToolSchema("")
    with pytest.raises(ValueError):
        ToolSchema("tool", parameters=(ToolParameter("a"), ToolParameter("a")))


def test_required_names() -> None:
    assert WRITE.required == ("path", "content")


# -- rendering --------------------------------------------------------------
def test_to_json_schema_shape() -> None:
    data = WRITE.to_json_schema()
    assert data["name"] == "write_file"
    assert data["parameters"]["type"] == "object"
    assert set(data["parameters"]["properties"]) == {"path", "content", "mode", "retries"}
    assert data["parameters"]["required"] == ["path", "content"]


def test_to_openai_shape() -> None:
    data = WRITE.to_openai()
    assert data == {"type": "function", "function": WRITE.to_json_schema()}


def test_to_anthropic_shape() -> None:
    data = WRITE.to_anthropic()
    assert data["name"] == "write_file"
    assert data["input_schema"]["type"] == "object"
    assert data["input_schema"]["required"] == ["path", "content"]


# -- validation -------------------------------------------------------------
def test_validate_ok() -> None:
    result = validate_arguments(WRITE, {"path": "/tmp/x", "content": "hi", "retries": 2})
    assert isinstance(result, ValidationResult)
    assert result.ok is True
    assert result.errors == ()


def test_validate_missing_required() -> None:
    result = validate_arguments(WRITE, {"path": "/tmp/x"})
    assert result.ok is False
    assert "content" in result.errors[0]


def test_validate_unknown_parameter() -> None:
    result = validate_arguments(WRITE, {"path": "/tmp/x", "content": "hi", "nope": 1})
    assert result.ok is False
    assert any("nope" in error for error in result.errors)


def test_validate_wrong_type() -> None:
    result = validate_arguments(WRITE, {"path": "/tmp/x", "content": "hi", "retries": "three"})
    assert result.ok is False
    assert any("retries" in error for error in result.errors)
    string_error = validate_arguments(WRITE, {"path": 5, "content": "hi"})
    assert string_error.ok is False
    assert any("path" in error for error in string_error.errors)


def test_validate_integer_excludes_bool() -> None:
    schema = ToolSchema("t", parameters=(ToolParameter("n", "integer", required=True),))
    assert validate_arguments(schema, {"n": True}).ok is False
    assert validate_arguments(schema, {"n": 2}).ok is True


def test_validate_boolean_number_array_object_types() -> None:
    schema = ToolSchema(
        "t",
        parameters=(
            ToolParameter("flag", "boolean", required=True),
            ToolParameter("ratio", "number", required=True),
            ToolParameter("items", "array", required=True),
            ToolParameter("meta", "object", required=True),
        ),
    )
    good = {"flag": True, "ratio": 1.5, "items": ["a"], "meta": {"k": 1}}
    assert validate_arguments(schema, good).ok is True
    assert validate_arguments(schema, {**good, "flag": "yes"}).ok is False
    assert validate_arguments(schema, {**good, "ratio": "fast"}).ok is False
    assert validate_arguments(schema, {**good, "items": "a"}).ok is False
    assert validate_arguments(schema, {**good, "meta": [1]}).ok is False


def test_parameter_enum_default_and_description_rendered() -> None:
    parameter = ToolParameter("mode", "string", "The mode.", enum=("w", "a"), default="w")
    schema = parameter.to_json_schema()
    assert schema["enum"] == ["w", "a"]
    assert schema["default"] == "w"
    assert schema["description"] == "The mode."
    # a parameter without optional bits renders only its type
    assert ToolParameter("bare", "string").to_json_schema() == {"type": "string"}


def test_schema_without_required_omits_required_key() -> None:
    schema = ToolSchema("t", parameters=(ToolParameter("opt", "string"),))
    assert "required" not in schema.to_json_schema()["parameters"]
    assert "required" not in schema.to_anthropic()["input_schema"]


def test_validate_enum_violation() -> None:
    result = validate_arguments(WRITE, {"path": "/tmp/x", "content": "hi", "mode": "r"})
    assert result.ok is False
    assert any("mode" in error for error in result.errors)


def test_validation_result_to_dict() -> None:
    result = validate_arguments(WRITE, {})
    assert result.to_dict() == {
        "ok": False,
        "errors": ["missing required parameter 'path'", "missing required parameter 'content'"],
    }


# -- registry ---------------------------------------------------------------
def test_registry_register_get_has_len() -> None:
    registry = ToolRegistry([WRITE])
    assert len(registry) == 1
    assert registry.has("write_file") is True
    assert registry.get("write_file") is WRITE
    assert "write_file" in registry
    assert list(registry) == [WRITE]


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError):
        ToolRegistry().get("missing")


def test_registry_duplicate_refused_unless_replace() -> None:
    registry = ToolRegistry([WRITE])
    with pytest.raises(ValueError):
        registry.register(WRITE)
    other = ToolSchema("write_file", "v2")
    registry.register(other, replace=True)
    assert registry.get("write_file") is other


def test_registry_unregister_names_subset() -> None:
    registry = ToolRegistry([WRITE])
    assert registry.names() == ["write_file"]
    assert registry.subset(["write_file"]) == [WRITE]
    assert registry.unregister("write_file") is True
    assert registry.unregister("write_file") is False
    assert len(registry) == 0


def test_registry_rendering_and_validation() -> None:
    registry = ToolRegistry([WRITE])
    openai = registry.openai_tools()
    assert openai[0]["type"] == "function"
    anthropic = registry.anthropic_tools(["write_file"])
    assert anthropic[0]["name"] == "write_file"
    assert registry.validate("write_file", {"path": "/x"}).ok is False
    assert registry.validate("write_file", {"path": "/x", "content": "y"}).ok is True