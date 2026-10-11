"""Tool schemas — describe callable tools for LLM function-calling.

A :class:`ToolSchema` is a tool's name, description and typed parameters. It
renders to the JSON-schema shapes that OpenAI-style and Anthropic-style APIs
expect, and it can :func:`validate_arguments` its own calls — so a model's
proposed arguments can be checked before anything runs.

No third-party dependencies; the schemas are plain, frozen dataclasses.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any

__all__ = [
    "ToolParameter",
    "ToolRegistry",
    "ToolSchema",
    "ValidationResult",
    "validate_arguments",
]

#: The JSON-schema primitive types a parameter may declare.
_JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object"})


@dataclass(frozen=True, slots=True)
class ToolParameter:
    """One typed parameter of a :class:`ToolSchema`."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    enum: tuple[str, ...] = ()
    default: Any = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("parameter name must be non-empty")
        if self.type not in _JSON_TYPES:
            raise ValueError(
                f"parameter {self.name!r} has unknown type {self.type!r}; "
                f"expected one of {sorted(_JSON_TYPES)}"
            )

    def to_json_schema(self) -> dict[str, Any]:
        """The JSON-schema fragment for this parameter."""
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """A callable tool: name, description and parameters."""

    name: str
    description: str = ""
    parameters: tuple[ToolParameter, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name must be non-empty")
        seen: set[str] = set()
        for parameter in self.parameters:
            if parameter.name in seen:
                raise ValueError(f"duplicate parameter {parameter.name!r} on tool {self.name!r}")
            seen.add(parameter.name)

    @property
    def required(self) -> tuple[str, ...]:
        """Names of the required parameters."""
        return tuple(p.name for p in self.parameters if p.required)

    def _parameter_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {p.name: p.to_json_schema() for p in self.parameters},
        }
        if self.required:
            schema["required"] = list(self.required)
        return schema

    def to_json_schema(self) -> dict[str, Any]:
        """A generic JSON-schema description of the tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._parameter_schema(),
        }

    def to_openai(self) -> dict[str, Any]:
        """Render as an OpenAI ``tools`` entry (``{"type": "function", ...}``)."""
        return {"type": "function", "function": self.to_json_schema()}

    def to_anthropic(self) -> dict[str, Any]:
        """Render as an Anthropic ``tools`` entry (``input_schema``)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._parameter_schema(),
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The outcome of validating a tool call's arguments."""

    ok: bool
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of this result."""
        return {"ok": self.ok, "errors": list(self.errors)}


def _check_type(parameter: ToolParameter, value: Any) -> str | None:
    kind = parameter.type
    if kind == "string" and not isinstance(value, str):
        return f"parameter {parameter.name!r} must be a string"
    if kind == "boolean" and not isinstance(value, bool):
        return f"parameter {parameter.name!r} must be a boolean"
    if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return f"parameter {parameter.name!r} must be an integer"
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return f"parameter {parameter.name!r} must be a number"
    if kind == "array" and not isinstance(value, (list, tuple)):
        return f"parameter {parameter.name!r} must be an array"
    if kind == "object" and not isinstance(value, Mapping):
        return f"parameter {parameter.name!r} must be an object"
    return None


def validate_arguments(schema: ToolSchema, arguments: Mapping[str, Any]) -> ValidationResult:
    """Check ``arguments`` against ``schema``; returns every problem found."""
    errors: list[str] = []
    params = {p.name: p for p in schema.parameters}
    for parameter in schema.parameters:
        if parameter.required and parameter.name not in arguments:
            errors.append(f"missing required parameter {parameter.name!r}")
    for key, value in arguments.items():
        param = params.get(key)
        if param is None:
            errors.append(f"unknown parameter {key!r}")
            continue
        type_error = _check_type(param, value)
        if type_error is not None:
            errors.append(type_error)
        if param.enum and value not in param.enum:
            errors.append(f"parameter {key!r} must be one of {list(param.enum)}")
    return ValidationResult(ok=not errors, errors=tuple(errors))


class ToolRegistry:
    """A thread-safe catalogue of :class:`ToolSchema` objects, by name."""

    def __init__(self, schemas: Iterable[ToolSchema] | None = None) -> None:
        self._schemas: dict[str, ToolSchema] = {}
        self._lock = RLock()
        for schema in schemas or ():
            self.register(schema)

    # -- mutation -----------------------------------------------------------
    def register(self, schema: ToolSchema, *, replace: bool = False) -> None:
        """Add a schema; refuses a duplicate name unless ``replace``."""
        with self._lock:
            if schema.name in self._schemas and not replace:
                raise ValueError(f"tool {schema.name!r} is already registered")
            self._schemas[schema.name] = schema

    def unregister(self, name: str) -> bool:
        """Remove a schema by name; returns whether it existed."""
        with self._lock:
            return self._schemas.pop(name, None) is not None

    # -- reading ------------------------------------------------------------
    def get(self, name: str) -> ToolSchema:
        with self._lock:
            try:
                return self._schemas[name]
            except KeyError as exc:
                raise KeyError(f"no tool named {name!r}") from exc

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._schemas

    def all(self) -> list[ToolSchema]:
        with self._lock:
            return list(self._schemas.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self._schemas)

    def subset(self, names: Sequence[str]) -> list[ToolSchema]:
        """The schemas for ``names`` (order preserved); unknown names raise."""
        return [self.get(name) for name in names]

    # -- rendering ----------------------------------------------------------
    def openai_tools(self, names: Sequence[str] | None = None) -> list[dict[str, Any]]:
        schemas = self.all() if names is None else self.subset(names)
        return [schema.to_openai() for schema in schemas]

    def anthropic_tools(self, names: Sequence[str] | None = None) -> list[dict[str, Any]]:
        schemas = self.all() if names is None else self.subset(names)
        return [schema.to_anthropic() for schema in schemas]

    # -- validation ---------------------------------------------------------
    def validate(self, name: str, arguments: Mapping[str, Any]) -> ValidationResult:
        """Validate a call to tool ``name`` against its schema."""
        return validate_arguments(self.get(name), arguments)

    # -- dunders ------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._schemas)

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._schemas

    def __iter__(self) -> Iterator[ToolSchema]:
        with self._lock:
            return iter(list(self._schemas.values()))
