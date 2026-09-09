"""MCP stdio / Streamable HTTP entrypoint and local curation commands."""

import argparse
import asyncio
import inspect
import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError
from pydantic.fields import PydanticUndefined

from . import __version__
from .db import Repository
from .models import SourceError, error_envelope
from .requests import REQUESTS
from .responses import RESPONSES
from .service import Service

DESCRIPTIONS = {
    "ow_catalog": "List public hero details, maps and supported filters. Map modes are map types, not competitive seasonal pools.",
    "ow_meta": "Compare hero aggregate percentages, maps, tiers or source regions; history uses stored retrieval snapshots. ASIA is not KR. No matchup winrates.",
    "ow_players_search": "Search public BattleTags or names. Equal names do not establish identity or nationality.",
    "ow_player_get": "Read a public player profile and filtered career/hero/role statistics; report private and missing data distinctly.",
    "ow_rankers_search": "Search the locally curated, evidenced ranker registry only, including dated rank observations. Not the complete live Top 500.",
    "ow_replays_search": "Find public replay codes and filter stored identity/region/validation evidence. A Korean player does not prove a Korean match server.",
    "ow_replay_get": "Get replay details and independently recorded playback evidence. Public source flags never imply client verification.",
    "ow_patches": "Read official English patch notes and hero changes. Explicit replay invalidation notices mark older checks needs_recheck.",
    "ow_esports": "Read the released OWCS Korea dataset with match coverage and release dates. Esports metrics are separate from ranked ladder statistics.",
    "ow_status": "Show observed source health, capabilities and stored coverage. refresh performs bounded cached probes; default performs no network access.",
}

RAW_ARGUMENTS: ContextVar[dict | None] = ContextVar("overwatch_raw_arguments", default=None)


def tool_result(name: str, result: dict) -> CallToolResult:
    # Validate without reserializing the model: preserve omitted fields and source values.
    RESPONSES[name].model_validate(result)
    return CallToolResult(
        is_error=result["status"] == "error",
        structured_content=result,
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
    )


async def validate_arguments(ctx, call_next):
    if ctx.method != "tools/call" or not isinstance(ctx.params, dict):
        return await call_next(ctx)
    name = ctx.params.get("name")
    arguments = ctx.params.get("arguments") or {}
    if name not in REQUESTS:
        return await call_next(ctx)
    try:
        REQUESTS[name].model_validate(arguments)
    except ValidationError as exc:
        message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        result = error_envelope(SourceError("INVALID_ARGUMENT", message, "server"), arguments)
        return tool_result(name, result)
    token = RAW_ARGUMENTS.set(arguments)
    try:
        return await call_next(ctx)
    finally:
        RAW_ARGUMENTS.reset(token)


class ValidatedMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = REQUESTS[tool.name].model_json_schema()
        return tools


def create_server(service: Service | None = None, db_path: str | None = None) -> MCPServer:
    active = service

    @asynccontextmanager
    async def lifespan(server):
        nonlocal active
        owned = active is None
        if owned:
            active = Service(Repository(db_path or os.getenv("OW_DB_PATH", "data/overwatch.db")))
        try:
            yield active
        finally:
            if owned and active:
                await active.close()
                active = None

    server = ValidatedMCPServer(
        "Overwatch AIO MCP",
        version=__version__,
        lifespan=lifespan,
        middleware=[validate_arguments],
        instructions="Use returned provenance, original observation dates and warnings. Never label ASIA as Korea, infer nationality from names, invent matchup rates, or describe public replay codes as playable without evidence.",
    )

    def register(name, model):
        async def invoke(**kwargs):
            if active is None:
                raise RuntimeError("Server lifespan has not started")
            raw = RAW_ARGUMENTS.get()
            arguments = raw if raw is not None else kwargs
            result = await active.call(name, arguments)
            try:
                return tool_result(name, result)
            except ValidationError:
                return tool_result(
                    name,
                    error_envelope(
                        SourceError(
                            "INTERNAL_ERROR", "Tool returned an invalid response.", "server"
                        ),
                        arguments,
                    ),
                )

        # Derive the flat public signature from the same validated model used by dispatch.
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


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description="Overwatch public-data MCP and local curation")
    parser.add_argument("--db", default=os.getenv("OW_DB_PATH", "data/overwatch.db"))
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Start the MCP server (default command)")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    schema = sub.add_parser("schema", help="Export tool input/output JSON Schemas and annotations")
    schema.add_argument("--output")
    importer = sub.add_parser(
        "import-rankers", help="Import local curator-authored ranker evidence JSON"
    )
    importer.add_argument("path")
    linker = sub.add_parser(
        "link-replay", help="Attach explicit evidence and optional verified player identity"
    )
    linker.add_argument("code")
    linker.add_argument("--player-id", type=int)
    linker.add_argument("--evidence-file", required=True)
    check = sub.add_parser(
        "record-replay-check", help="Record an actual operator playback observation"
    )
    check.add_argument("code")
    check.add_argument(
        "--status", required=True, choices=["user_reported_working", "client_verified"]
    )
    check.add_argument("--source", required=True)
    check.add_argument("--evidence-ref", required=True)
    check.add_argument("--observed-at", required=True)
    collector = sub.add_parser("collect", help="Run bounded configured collection jobs")
    collector.add_argument("--config", required=True)
    collector.add_argument("--once", action="store_true")
    sub.add_parser(
        "discover-rankers", help="List unverified high-tier replay authors for curator review"
    )
    args = parser.parse_args()
    if args.command in (None, "serve"):
        server = create_server(db_path=args.db)
        transport = getattr(args, "transport", "stdio")
        kwargs = {} if transport == "stdio" else {"host": args.host, "port": args.port}
        server.run(transport=transport, **kwargs)
        return
    if args.command == "schema":
        text = json.dumps(
            {
                tool.name: tool.model_dump(
                    mode="json", by_alias=True, exclude_none=True, exclude={"name"}
                )
                for tool in asyncio.run(create_server().list_tools())
            },
            ensure_ascii=False,
            indent=2,
        )
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return
    if args.command == "collect":
        from .collector import run_collector

        raise SystemExit(
            asyncio.run(run_collector(args.db, read_json(args.config), once=args.once))
        )
    from .registry import Registry

    repository = Repository(args.db)
    registry = Registry(repository)
    try:
        if args.command == "import-rankers":
            result = {"imported_player_ids": registry.import_rankers(read_json(args.path))}
        elif args.command == "link-replay":
            registry.add_replay_evidence(args.code, read_json(args.evidence_file), args.player_id)
            result = {"code": args.code.upper(), "evidence_recorded": True}
        elif args.command == "record-replay-check":
            registry.validate_replay(
                args.code, args.status, args.source, args.evidence_ref, args.observed_at
            )
            result = {"code": args.code.upper(), "status": args.status, "source": args.source}
        else:
            candidates = []
            for replay in repository.replay_rows():
                if replay.get("player_id") is None and str(
                    replay.get("tier_claim", "")
                ).lower().startswith(("grandmaster", "champion")):
                    candidates.append(
                        {
                            "display_name": replay.get("display_name"),
                            "tier_claim": replay["tier_claim"],
                            "source_url": replay["source_url"],
                            "replay_code": replay["code"],
                            "country": None,
                            "verification": "source_claim",
                        }
                    )
            result = {
                "candidates": candidates[:100],
                "warnings": [
                    "Unverified high-tier replay authors only. No Top 500 rank, country or identity is inferred; candidates require independent curator evidence."
                ],
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
