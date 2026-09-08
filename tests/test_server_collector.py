import asyncio

import httpx2 as httpx
from mcp import Client
from pydantic import ValidationError

from overwatch_mcp.collector import CollectionConfig, Collector
from overwatch_mcp.db import Repository
from overwatch_mcp.http import HttpClient
from overwatch_mcp.server import create_server
from overwatch_mcp.service import Service


def test_mcp_handshake_lists_flat_ten_tools_and_dispatches():
    async def run():
        repo = Repository(":memory:")
        http = HttpClient(
            repo, transport=httpx.MockTransport(lambda req: httpx.Response(503)), retries=0
        )
        service = Service(repo, http)
        server = create_server(service)
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 10
            search = next(tool for tool in tools.tools if tool.name == "ow_players_search")
            assert "query" in search.input_schema["properties"]
            assert search.input_schema["properties"]["limit"]["maximum"] == 50
            assert search.input_schema["additionalProperties"] is False
            assert search.annotations.read_only_hint
            response = await client.call_tool("ow_status", {})
            assert response.structured_content["data"]["stored_counts"]["players"] == 0
            assert response.structured_content["requested_filters"] == {}
            unknown = await client.call_tool("ow_meta", {"matchup": "ramattra"})
            assert unknown.is_error
            assert unknown.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            invalid = await client.call_tool("ow_players_search", {"query": "x", "limit": 0})
            assert invalid.is_error
            result = await client.call_tool("ow_rankers_search", {"country": "KR"})
            assert result.structured_content["error"]["code"] == "EMPTY_RESULT"
        await service.close()

    asyncio.run(run())


def test_collector_due_state_and_lease_survive_instances():
    class FakeService:
        def __init__(self):
            self.repo = Repository(":memory:")
            self.calls = []

        async def call(self, name, arguments):
            self.calls.append(name)
            return {"status": "ok"}

    async def run():
        service = FakeService()
        config = {
            "jobs": [
                {
                    "name": "heroes",
                    "tool": "ow_catalog",
                    "arguments": {"type": "heroes"},
                    "interval_seconds": 86400,
                }
            ]
        }
        first = Collector(service, config)
        assert len(await first.collect_due(now=100000)) == 1
        second = Collector(service, config)
        assert await second.collect_due(now=100001) == []
        assert len(await second.collect_due(now=186400)) == 1
        assert len(service.calls) == 2
        service.repo.close()

    asyncio.run(run())


def test_collector_rejects_invalid_unbounded_or_duplicate_jobs():
    import pytest

    with pytest.raises(ValidationError):
        CollectionConfig.model_validate(
            {
                "jobs": [
                    {
                        "name": "unsafe",
                        "tool": "ow_meta",
                        "arguments": {"matchup": "dva"},
                        "interval_seconds": 1,
                    }
                ]
            }
        )
    job = {"name": "same", "tool": "ow_catalog", "interval_seconds": 86400}
    with pytest.raises(ValidationError):
        CollectionConfig.model_validate({"jobs": [job, job]})
