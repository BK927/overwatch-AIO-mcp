import asyncio
import time

import httpx2 as httpx
import pytest

from overwatch_mcp.db import Repository
from overwatch_mcp.http import HttpClient
from overwatch_mcp.models import SourceError, SourceResult


def test_cache_retains_original_retrieval_and_stale_failure():
    async def run():
        repo = Repository(":memory:")
        calls = []

        def handler(request):
            calls.append(request)
            return (
                httpx.Response(200, json={"answer": 42}) if len(calls) == 1 else httpx.Response(503)
            )

        client = HttpClient(repo, transport=httpx.MockTransport(handler), retries=0, min_interval=0)
        first = await client.get_json("test", "https://example.test/a", ttl=3600)
        cached = await client.get_json("test", "https://example.test/a", ttl=3600)
        assert cached.cached and first.retrieved_at == cached.retrieved_at
        assert len(calls) == 1
        repo.conn.execute("UPDATE http_cache SET expires_at=?", (time.time() - 1,))
        stale = await client.get_json("test", "https://example.test/a", ttl=3600)
        assert stale.stale and stale.retrieved_at == first.retrieved_at
        assert repo.source_states()["test"]["status"] == "degraded"
        await client.close()
        repo.close()

    asyncio.run(run())


def test_rate_limit_and_parse_are_errors_not_empty():
    async def run():
        for status, body, expected in [
            (429, "", "RATE_LIMITED"),
            (200, "<html>oops</html>", "PARSE_ERROR"),
        ]:
            repo = Repository(":memory:")
            client = HttpClient(
                repo,
                transport=httpx.MockTransport(
                    lambda req, status=status, body=body: httpx.Response(status, text=body)
                ),
                retries=0,
                min_interval=0,
            )
            with pytest.raises(SourceError) as failure:
                await client.get_json("test", "https://example.test/a")
            assert failure.value.code == expected
            await client.close()
            repo.close()

    asyncio.run(run())


def test_meta_cache_hit_does_not_create_new_historical_observation():
    repo = Repository(":memory:")
    result = SourceResult(
        [{"hero": "reinhardt", "winrate": 52.3}],
        "overfast",
        "https://example.test",
        applied_filters={"region": "ASIA"},
    )
    repo.save_meta(result)
    repo.save_meta(result)
    history = repo.meta_history("overfast", {"region": "ASIA"})
    assert len(history) == 1
    assert history[0]["source_updated_at"] is None
    assert repo.meta_history("overfast", {"region": "EUROPE"}) == []
    repo.close()
