from pydantic import BaseModel, ValidationError
from dataclasses import dataclass, field
from typing import Any
from abc import ABC
import asyncio
import logging

EXCLUDED_PROPERTIES = ["title"]

MAX_TOOL_OUTPUT_LENGTH = 10000

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool = False
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success_result(cls, output: str, metadata: dict[str, Any] = None) -> "ToolResult":
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def error_result(cls, error: str, metadata: dict[str, Any] = None) -> "ToolResult":
        return cls(success=False, error=error, metadata=metadata)


class Tool(ABC):
    def __init__(self, name: str, description: str = None, model: BaseModel = None):
        self.name = name
        self.description = description
        self.model = model
        self.function = None

    @property
    def json_schema(self) -> dict:
        schema = self.model.model_json_schema(mode="serialization")
        defs = schema.get("$defs", {})

        def resolve_refs(obj):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref = obj["$ref"]
                    if ref.startswith("#/$defs/"):
                        def_name = ref[len("#/$defs/") :]
                        resolved = defs.get(def_name, {})
                        return resolve_refs(
                            {k: v for k, v in resolved.items() if k not in EXCLUDED_PROPERTIES}
                        )
                return {k: resolve_refs(v) for k, v in obj.items() if k not in EXCLUDED_PROPERTIES}
            elif isinstance(obj, list):
                return [resolve_refs(item) for item in obj]
            return obj

        parameters = {
            "type": "object",
            "properties": resolve_refs(schema.get("properties", {})),
            "required": schema.get("required", []),
        }

        return {
            "name": self.name,
            "description": self.description,
            "parameters": parameters,
        }

    def validate_params(self, args: dict[str, Any]) -> list[str]:
        try:
            self.model(**args)
            return []
        except ValidationError as e:
            errors = []
            for error in e.errors():
                field = ".".join(str(loc) for loc in error["loc"])
                err_type = error.get("type", "")
                ctx = error.get("ctx", {})
                inp = error.get("input")

                if err_type == "missing":
                    msg = f"'{field}' is required but was not provided"
                elif err_type == "literal_error":
                    expected = ctx.get("expected", "")
                    msg = f"'{field}' must be one of {expected}, got {inp!r}"
                elif err_type in (
                    "greater_than",
                    "greater_than_equal",
                    "less_than",
                    "less_than_equal",
                ):
                    op = {
                        "greater_than": ">",
                        "greater_than_equal": ">=",
                        "less_than": "<",
                        "less_than_equal": "<=",
                    }[err_type]
                    bound = ctx.get("gt") or ctx.get("ge") or ctx.get("lt") or ctx.get("le")
                    msg = f"'{field}' must be {op} {bound}, got {inp!r}"
                elif err_type == "string_too_short":
                    msg = (
                        f"'{field}' is too short (min length: {ctx.get('min_length')}), got {inp!r}"
                    )
                elif err_type == "string_too_long":
                    msg = (
                        f"'{field}' is too long (max length: {ctx.get('max_length')}), got {inp!r}"
                    )
                elif err_type in ("int_type", "float_type", "str_type", "bool_type"):
                    expected_type = err_type.replace("_type", "")
                    msg = f"'{field}' must be a {expected_type}, got {type(inp).__name__} {inp!r}"
                else:
                    msg = f"'{field}': {error['msg']}"
                    if inp is not None:
                        msg += f" (got {inp!r})"

                errors.append(msg)
            return errors
        except Exception as e:
            return [str(e)]

    def __call__(self, function):
        if self.name is None:
            self.name = function.__name__
        if self.description is None:
            self.description = function.__doc__
        self.function = function
        return self

    def invoke(self, *args, **kwargs):
        """Synchronous invocation. Use ainvoke for async tools."""
        try:
            result = self.function(*args, **kwargs)
            if isinstance(result, ToolResult):
                return result
            return ToolResult.success_result(str(result))
        except Exception as e:
            logger.error(f"Error invoking tool {self.name}: {e}")
            return ToolResult.error_result(f"Error executing tool '{self.name}': {e}")

    async def ainvoke(self, *args, **kwargs):
        """Asynchronous invocation. Awaits if the tool function is a coroutine."""
        try:
            if asyncio.iscoroutinefunction(self.function):
                result = await self.function(*args, **kwargs)
            else:
                result = self.function(*args, **kwargs)
            if isinstance(result, ToolResult):
                return result
            return ToolResult.success_result(str(result))
        except Exception as e:
            logger.error(f"Error invoking tool {self.name}: {e}")
            return ToolResult.error_result(f"Error executing tool '{self.name}': {e}")
