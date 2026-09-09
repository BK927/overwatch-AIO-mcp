"""Validate advertised output contracts against real dispatch and adapter fixtures."""

import asyncio
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from test_korea_sources import JsonClient
from test_overfast import FakeClient, hero_stats
from test_registry import ranker_record
from test_replays_patches import PATCH
from test_replays_patches import FakeClient as ReplayClient
from test_service import meta_result

from overwatch_mcp.db import Repository
from overwatch_mcp.http import HttpClient
from overwatch_mcp.models import SourceError, utcnow
from overwatch_mcp.requests import REQUESTS
from overwatch_mcp.server import create_server, main
from overwatch_mcp.service import Service
from overwatch_mcp.sources.overfast import OverFastAdapter
from overwatch_mcp.sources.owcs_korea import OWCSKoreaAdapter
from overwatch_mcp.sources.owreplays import OWReplaysAdapter
from overwatch_mcp.sources.patches import PatchAdapter


def service_with_http(status=503, payload=None):
    repo = Repository(":memory:")
    return Service(
        repo,
        HttpClient(
            repo,
            transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)),
            retries=0,
            min_interval=0,
        ),
    )


def validate_result(schema, result, *, status, is_error):
    payload = result.structured_content
    assert payload["status"] == status
    assert result.is_error is is_error
    Draft202012Validator(schema).validate(payload)
    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == payload
    return payload


def test_advertised_contracts_reject_missing_fields_and_match_export(monkeypatch, capsys):
    async def run():
        async with Client(create_server(db_path=":memory:")) as client:
            tools = (await client.list_tools()).tools
        required = {
            "status",
            "data",
            "error",
            "source",
            "source_url",
            "retrieved_at",
            "source_updated_at",
            "data_period",
            "data_patch",
            "requested_filters",
            "applied_filters",
            "warnings",
            "cached",
            "stale",
        }
        assert {tool.name for tool in tools} == set(REQUESTS)
        for tool in tools:
            wire = tool.model_dump(by_alias=True)
            schema = wire["outputSchema"]
            Draft202012Validator.check_schema(schema)
            assert schema["type"] == "object"
            assert required <= set(schema["required"])
            assert required <= schema["properties"].keys()
            assert schema["properties"]["data"]["description"]
            assert not Draft202012Validator(schema).is_valid({})
            assert wire["annotations"]["readOnlyHint"] is True
            assert wire["annotations"]["destructiveHint"] is False
        return {
            tool.name: tool.model_dump(
                mode="json", by_alias=True, exclude_none=True, exclude={"name"}
            )
            for tool in tools
        }

    expected = asyncio.run(run())
    monkeypatch.setattr("sys.argv", ["overwatch-aio-mcp", "schema"])
    main()
    assert json.loads(capsys.readouterr().out) == expected
    checked_in = Path(__file__).resolve().parents[1] / "docs/tool-schemas.json"
    assert json.loads(checked_in.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize(
    ("tool", "arguments", "status", "payload", "code"),
    [
        ("ow_players_search", {"query": "Example"}, 503, None, "SOURCE_UNAVAILABLE"),
        ("ow_players_search", {"query": "Example"}, 429, None, "RATE_LIMITED"),
        ("ow_players_search", {"query": "Example"}, 200, {}, "PARSE_ERROR"),
        (
            "ow_player_get",
            {"player_id": "Example-1234"},
            200,
            {"is_public": False},
            "PRIVATE_PROFILE",
        ),
        ("ow_catalog", {"locale": "invalid"}, 503, None, "UNSUPPORTED_FILTER"),
        ("ow_meta", {"matchup": "ramattra"}, 503, None, "INVALID_ARGUMENT"),
    ],
)
def test_failures_have_mcp_error_flag_and_valid_structured_content(
    tool, arguments, status, payload, code
):
    async def run():
        service = service_with_http(status, payload)
        try:
            async with Client(create_server(service)) as client:
                schemas = {t.name: t.output_schema for t in (await client.list_tools()).tools}
                body = validate_result(
                    schemas[tool],
                    await client.call_tool(tool, arguments),
                    status="error",
                    is_error=True,
                )
                assert body["error"]["code"] == code
                assert body["requested_filters"] == arguments
        finally:
            await service.close()

    asyncio.run(run())


STATS = {
    "general": {"winrate": 50},
    "roles": {"tank": {"winrate": 55}},
    "heroes": {"reinhardt": {"winrate": 55}},
}


class FreshReplayClient(ReplayClient):
    def result(self, data, url):
        result = super().result(data, url)
        result.retrieved_at = utcnow()
        return result


@pytest.mark.parametrize(
    ("tool", "arguments", "upstream", "expected"),
    [
        ("ow_catalog", {}, [[{"key": "reinhardt", "name": "라인하르트"}]], "ok"),
        ("ow_catalog", {"hero": "라인"}, [{"key": "reinhardt", "name": "라인하르트"}], "ok"),
        ("ow_catalog", {"type": "filters"}, [], "ok"),
        ("ow_meta", {}, [[hero_stats()]], "ok"),
        ("ow_meta", {"view": "history"}, [[hero_stats()]], "ok"),
        (
            "ow_players_search",
            {"query": "Example"},
            [
                {
                    "total": 1,
                    "results": [
                        {
                            "player_id": "Example-1234",
                            "name": "Example",
                            "custom": {"keep": [None, 1]},
                        }
                    ],
                }
            ],
            "ok",
        ),
        ("ow_player_get", {"player_id": "Example-1234"}, [STATS, {"username": "Example"}], "ok"),
        (
            "ow_player_get",
            {"player_id": "Example-1234", "view": "career"},
            [{"reinhardt": {"game": {"win_percentage": 55}}}],
            "ok",
        ),
        (
            "ow_player_get",
            {"player_id": "Example-1234", "view": "hero", "hero": "라인"},
            [STATS],
            "ok",
        ),
        ("ow_player_get", {"player_id": "Example-1234", "view": "roles"}, [STATS], "ok"),
        ("ow_rankers_search", {}, [], "ok"),
        ("ow_replays_search", {}, [], "ok"),
        ("ow_replay_get", {"code": "8D8VRC"}, [], "ok"),
        ("ow_replay_get", {"code": "ABC123", "refresh": False}, [], "empty"),
        ("ow_patches", {"before": "2026-08-31"}, [], "ok"),
        *(
            ("ow_esports", {"view": view}, [], "ok")
            for view in ("hero_meta", "matches", "maps", "bans", "teams")
        ),
        ("ow_esports", {"team": "NotPresent"}, [], "empty"),
        ("ow_status", {}, [], "ok"),
    ],
)
def test_all_tools_and_views_preserve_adapter_data(tool, arguments, upstream, expected):
    async def run():
        service = service_with_http()
        service.overfast = OverFastAdapter(FakeClient(*upstream))
        service.replays = OWReplaysAdapter(FreshReplayClient())
        service.patches = PatchAdapter(ReplayClient(html=PATCH))
        service.esports = OWCSKoreaAdapter(JsonClient())
        service.registry.import_rankers([ranker_record()])
        if tool == "ow_meta" and arguments.get("view") == "history":
            await service.call("ow_meta", {})
        original_call = service.call
        observed = []

        async def capture(name, args):
            result = await original_call(name, args)
            observed.append(result)
            return result

        service.call = capture
        try:
            async with Client(create_server(service)) as client:
                schema = next(
                    t.output_schema for t in (await client.list_tools()).tools if t.name == tool
                )
                body = validate_result(
                    schema, await client.call_tool(tool, arguments), status=expected, is_error=False
                )
                assert (
                    body == observed[0]
                )  # No inserted defaults, coercion or dropped source fields.
                assert body["requested_filters"] == arguments
                damaged = {**body, "cached": "false"}
                assert not Draft202012Validator(schema).is_valid(damaged)
                if tool == "ow_meta" and expected == "ok":
                    damaged = {**body, "data": [{"hero": "reinhardt"}]}
                    assert not Draft202012Validator(schema).is_valid(damaged)
        finally:
            await service.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("mode", "expected", "is_error"),
    [
        ("ok", "ok", False),
        ("empty", "empty", False),
        ("stale", "stale", False),
        ("partial", "partial", False),
        ("error", "error", True),
    ],
)
def test_comparison_quality_and_group_failures_preserve_their_meaning(mode, expected, is_error):
    async def run():
        service = service_with_http()

        async def meta(filters):
            if mode == "error" or mode == "partial" and filters["map"] == "ilios":
                raise SourceError("SOURCE_UNAVAILABLE", "offline", "overfast")
            return meta_result(stale=mode == "stale", data=[] if mode == "empty" else None)

        service.overfast.meta = meta
        arguments = {"view": "map_comparison", "maps": ["kings-row", "ilios"]}
        try:
            async with Client(create_server(service)) as client:
                schema = next(
                    t.output_schema
                    for t in (await client.list_tools()).tools
                    if t.name == "ow_meta"
                )
                body = validate_result(
                    schema,
                    await client.call_tool("ow_meta", arguments),
                    status=expected,
                    is_error=is_error,
                )
                assert len(body["data"]) == 2
                assert body["source_status"]["ilios"] == (
                    "error" if mode in ("partial", "error") else mode
                )
        finally:
            await service.close()

    asyncio.run(run())


def test_invalid_server_output_becomes_a_structured_internal_error():
    async def run():
        service = service_with_http()

        async def broken(name, arguments):
            return {"status": "ok", "data": "wrong shape", "private_debug": "do not expose"}

        service.call = broken
        try:
            async with Client(create_server(service)) as client:
                schema = next(
                    t.output_schema
                    for t in (await client.list_tools()).tools
                    if t.name == "ow_status"
                )
                body = validate_result(
                    schema, await client.call_tool("ow_status", {}), status="error", is_error=True
                )
                assert body["error"]["code"] == "INTERNAL_ERROR"
                assert "private_debug" not in json.dumps(body)
        finally:
            await service.close()

    asyncio.run(run())


def test_empty_esports_release_preserves_unknown_coverage():
    async def run():
        service = service_with_http()
        upstream = JsonClient()
        manifest = upstream.table("manifest.json")
        manifest["data_as_of"] = None
        dataset = upstream.table("owcs-korea-data-test-release.json")
        for key in ("matches", "maps", "hero_bans", "hero_time_segments"):
            dataset[key] = []
            manifest["counts"][key] = 0
        service.esports = OWCSKoreaAdapter(upstream)
        try:
            async with Client(create_server(service)) as client:
                schema = next(
                    t.output_schema
                    for t in (await client.list_tools()).tools
                    if t.name == "ow_esports"
                )
                body = validate_result(
                    schema,
                    await client.call_tool("ow_esports", {"view": "matches"}),
                    status="empty",
                    is_error=False,
                )
                assert body["data"]["coverage_period"] is None
                assert body["data"]["data_until"] is None
        finally:
            await service.close()

    asyncio.run(run())
