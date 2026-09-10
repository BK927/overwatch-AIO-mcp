"""Optional MCP adapter over the same query boundary used by the skill CLI."""

import inspect
import json
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic.fields import PydanticUndefined

from . import __version__
from .db import Repository
from .execution import failure, query
from .operations import DESCRIPTIONS
from .requests import REQUESTS
from .responses import RESPONSES
from .service import Service


def tool_result(result: dict) -> CallToolResult:
    return CallToolResult(
        is_error=result["status"] == "error",
        structured_content=result,
        content=[
            TextContent(type="text", text=json.dumps(result, ensure_ascii=False, allow_nan=False))
        ],
    )


class ValidatedMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = REQUESTS[tool.name].model_json_schema()
            tool.output_schema = RESPONSES[tool.name].model_json_schema()
        return tools


def create_server(service: Service | None = None, db_path: str | None = None) -> MCPServer:
    active = service

    @asynccontextmanager
    async def lifespan(server):
        nonlocal active
        owned = active is None
        if owned:
            active = Service(Repository(db_path))
        try:
            yield active
        finally:
            if owned and active is not None:
                await active.close()
                active = None

    async def execute(name, arguments):
        if active is None:
            return tool_result(failure("INTERNAL_ERROR", "Server lifespan has not started."))
        result, _ = await query(name, arguments, service=active)
        return tool_result(result)

    async def dispatch(ctx, call_next):
        if ctx.method == "tools/call" and isinstance(ctx.params, dict):
            name = ctx.params.get("name")
            if name in REQUESTS:
                # Validate original arguments before SDK signature coercion/default insertion.
                return await execute(name, ctx.params.get("arguments", {}))
        return await call_next(ctx)

    server = ValidatedMCPServer(
        "Overwatch 2 Data MCP Server",
        version=__version__,
        lifespan=lifespan,
        middleware=[dispatch],
        instructions="Use returned provenance, original observation dates and warnings. Never label ASIA as Korea, infer nationality from names, invent matchup rates, or describe public replay codes as playable without evidence.",
    )

    def register(name, model):
        async def invoke(**kwargs):
            return await execute(name, kwargs)

        parameters = []
        return_type = Annotated[CallToolResult, RESPONSES[name]]
        annotations = {"return": return_type}
        for field_name, field in model.model_fields.items():
            annotation = field.rebuild_annotation()
            default = (
                inspect.Parameter.empty if field.default is PydanticUndefined else field.default
            )
            parameters.append(
                inspect.Parameter(
                    field_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=annotation,
                )
            )
            annotations[field_name] = annotation
        invoke.__name__ = name
        invoke.__doc__ = DESCRIPTIONS[name]
        invoke.__annotations__ = annotations
        invoke.__signature__ = inspect.Signature(parameters, return_annotation=return_type)
        server.tool(
            name=name,
            description=DESCRIPTIONS[name],
            structured_output=True,
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
            ),
        )(invoke)

    for name, model in REQUESTS.items():
        register(name, model)
    return server
