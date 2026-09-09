"""Service contracts, persistence and mixed-source evidence behavior, without network."""

import asyncio
import copy
import time
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest

from overwatch_skill.db import Repository
from overwatch_skill.http import HttpClient
from overwatch_skill.models import SourceError, SourceResult, utcnow
from overwatch_skill.service import Service


def run(coroutine):
    return asyncio.run(coroutine)


def meta_result(
    source="overfast",
    *,
    timestamp="2026-08-01T00:00:00+00:00",
    cached=False,
    stale=False,
    filters=None,
    data=None,
):
    return SourceResult(
        data
        if data is not None
        else [
            {
                "hero": "reinhardt",
                "winrate": 52.0,
                "pickrate": 5.0,
                "banrate": 1.0,
                "rate_unit": "percent",
                "match_server_region": None,
            }
        ],
        source,
        f"https://{source}.example/observed",
        retrieved_at=timestamp,
        applied_filters=filters
        or {
            "region": "KOREA" if source == "owtics" else "ASIA",
            "tier": "ALL",
            "map": "all-maps",
            "mode": "competitive",
        },
        cached=cached,
        stale=stale,
    )


def replay(code="ABC123", *, timestamp=None, **extra):
    return {
        "code": code,
        "hero": "reinhardt",
        "heroes": ["reinhardt"],
        "map": "kings-row",
        "tier_claim": "Grandmaster 2",
        "platform": "pc",
        "display_name": "Example",
        "replay_status": "unverified",
        "source": "owreplays",
        "source_url": f"https://owreplays.tv/{code}",
        "retrieved_at": timestamp or utcnow(),
        **extra,
    }


class FakeAdapter:
    def __init__(self, name, handler=None, **methods):
        self.name = name
        self.capabilities = ("example",)
        self.handler = handler
        self.methods = methods
        self.calls = []

    async def _call(self, method, filters):
        self.calls.append((method, copy.deepcopy(filters)))
        result = self.handler(method, filters) if self.handler else self.methods.get(method)
        if isinstance(result, Exception):
            raise result
        if result is None:
            result = SourceResult([], self.name, f"https://{self.name}.example")
        return copy.deepcopy(result)

    async def meta(self, filters):
        return await self._call("meta", filters)

    async def catalog(self, filters):
        return await self._call("catalog", filters)

    async def players_search(self, filters):
        return await self._call("players_search", filters)

    async def player_get(self, filters):
        return await self._call("player_get", filters)

    async def search(self, filters):
        return await self._call("search", filters)

    async def get(self, code):
        return await self._call("get", code)

    async def fetch(self, filters):
        return await self._call("fetch", filters)


@pytest.fixture
def service():
    repository = Repository(":memory:")

    def reject_network(request):
        raise AssertionError(f"Unexpected HTTP request: {request.url}")

    client = HttpClient(
        repository, transport=httpx.MockTransport(reject_network), retries=0, min_interval=0
    )
    service = Service(repository, client)
    yield service
    run(service.close())


def attach_fakes(service):
    service.overfast = FakeAdapter(
        "overfast",
        meta=meta_result(),
        catalog=SourceResult([{"key": "reinhardt"}], "overfast", "https://example.test/heroes"),
        players_search=SourceResult(
            [{"player_id": "Example-1234"}], "overfast", "https://example.test/players"
        ),
        player_get=SourceResult({"name": "Example"}, "overfast", "https://example.test/profile"),
    )
    service.blizzard = FakeAdapter("blizzard", meta=meta_result("blizzard"))
    service.owtics = FakeAdapter("owtics", meta=meta_result("owtics"))
    service.replays = FakeAdapter(
        "owreplays",
        search=SourceResult([replay()], "owreplays", "https://owreplays.tv"),
        get=SourceResult(replay(), "owreplays", "https://owreplays.tv/ABC123"),
    )
    service.patches = FakeAdapter(
        "blizzard_patches",
        fetch=SourceResult([], "blizzard_patches", "https://example.test/patches"),
    )
    service.esports = FakeAdapter(
        "owcs_korea",
        fetch=SourceResult(
            {"records": [], "dataset_type": "esports"},
            "owcs_korea",
            "https://example.test/releases",
        ),
    )
    service.adapters = [
        service.overfast,
        service.blizzard,
        service.owtics,
        service.replays,
        service.patches,
        service.esports,
    ]


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("ow_catalog", {}, "ok"),
        ("ow_meta", {}, "ok"),
        ("ow_players_search", {"query": "Example"}, "ok"),
        ("ow_player_get", {"player_id": "Example#1234"}, "ok"),
        ("ow_rankers_search", {}, "empty"),
        ("ow_replays_search", {}, "ok"),
        ("ow_replay_get", {"code": "abc123"}, "ok"),
        ("ow_patches", {}, "empty"),
        ("ow_esports", {}, "empty"),
        ("ow_status", {}, "ok"),
    ],
)
def test_all_ten_tool_dispatches_keep_original_user_filters(service, tool, arguments, expected):
    attach_fakes(service)
    result = run(service.call(tool, arguments))
    assert result["status"] == expected
    assert result["requested_filters"] == arguments
    assert "retrieved_at" in result and "applied_filters" in result


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("not_a_tool", {}),
        ("ow_meta", {"limit": 0}),
        ("ow_meta", {"mapz": []}),
        ("ow_meta", {"view": "map_comparison", "maps": ["oasis"]}),
        ("ow_meta", {"after": "2026-01-01"}),
        ("ow_meta", {"maps": ["oasis", "ilios"]}),
        ("ow_players_search", {}),
        ("ow_replay_get", {"code": "bad"}),
        ("ow_replays_search", {"playable_status": ["probably_valid"]}),
    ],
)
def test_model_errors_are_invalid_arguments_not_empty_results(service, tool, arguments):
    result = run(service.call(tool, arguments))
    assert result["status"] == "error" and result["error"]["code"] == "INVALID_ARGUMENT"


def test_auto_korea_prefers_owtics_and_failure_does_not_implicitly_fallback(service):
    attach_fakes(service)
    result = run(service.call("ow_meta", {"region": "한국", "tier": "마스터"}))
    assert result["source"] == "owtics"
    assert service.owtics.calls[0][1]["tier"] == "MASTER"
    assert not service.overfast.calls
    service.owtics.methods["meta"] = SourceError("SOURCE_UNAVAILABLE", "offline", "owtics")
    failure = run(service.call("ow_meta", {"region": "KR"}))
    assert failure["status"] == "error" and failure["source"] == "owtics"
    assert not service.overfast.calls


def test_explicit_korea_fallback_reports_asia_and_records_owtics_failure(service):
    attach_fakes(service)
    service.owtics.methods["meta"] = SourceError("RATE_LIMITED", "slow down", "owtics")
    result = run(service.call("ow_meta", {"region": "KR", "allow_region_fallback": True}))
    assert result["source"] == "overfast" and result["applied_filters"]["region"] == "ASIA"
    assert service.overfast.calls[0][1]["region"] == "KR"
    assert service.overfast.calls[0][1]["allow_region_fallback"] is True
    assert any("not Korean-server" in warning for warning in result["warnings"])
    assert service.repo.source_states()["owtics"]["status"] == "degraded"


def test_direct_blizzard_dispatch_and_status_capabilities(service):
    attach_fakes(service)
    service.overfast.capabilities = {"capabilities": ["hero_meta"], "regions": ["ASIA"]}
    service.esports.latest_release = "test-release"
    assert run(service.call("ow_meta", {"source": "blizzard"}))["source"] == "blizzard"
    status = run(service.call("ow_status", {}))["data"]["sources"]
    assert "blizzard" in status
    assert status["overfast"]["capabilities"] == ["hero_meta"]
    assert status["overfast"]["regions"] == ["ASIA"]
    assert status["owcs_korea"]["latest_release"] == "test-release"


def test_meta_limit_is_applied_after_preserving_all_observed_heroes(service):
    attach_fakes(service)
    service.overfast.methods["meta"] = meta_result(
        data=[{"hero": "reinhardt", "winrate": 55}, {"hero": "ana", "winrate": 54}]
    )
    result = run(service.call("ow_meta", {"limit": 1}))
    assert len(result["data"]) == 1 and result["applied_filters"]["limit"] == 1
    assert len(service.repo.meta_history("overfast", {"region": "ASIA"})) == 2


def test_history_uses_original_observation_timestamp_and_normalized_context(service):
    attach_fakes(service)
    first = meta_result(
        timestamp="2026-08-01T00:00:00+00:00",
        filters={"region": "ASIA", "role": "tank", "tier": "MASTER"},
    )
    second = meta_result(
        timestamp="2026-08-08T00:00:00+00:00",
        filters=first.applied_filters,
        data=[{"hero": "reinhardt", "winrate": 54, "pickrate": 6, "banrate": 1}],
    )
    service.repo.save_meta(first)
    service.repo.save_meta(second)
    service.repo.save_meta(second)
    result = run(
        service.call(
            "ow_meta",
            {
                "view": "history",
                "region": "아시아",
                "tier": "마스터",
                "role": "돌격",
                "heroes": ["라인"],
            },
        )
    )
    assert len(result["data"]) == 2
    assert result["data"][0]["difference"]["winrate"] == 2
    assert result["data"][0]["comparison_retrieved_at"] == first.retrieved_at
    assert result["retrieved_at"] == second.retrieved_at
    assert result["cached"]


def test_owtics_history_supports_combined_tier_and_does_not_claim_platform(service):
    result = meta_result(
        "owtics",
        filters={
            "region": "KOREA",
            "tier": "GRANDMASTER_AND_CHAMPION",
            "mode": "competitive",
            "map": "all-maps",
        },
    )
    service.repo.save_meta(result)
    history = run(
        service.call(
            "ow_meta", {"view": "history", "region": "KR", "tier": "GRANDMASTER_AND_CHAMPION"}
        )
    )
    assert len(history["data"]) == 1
    assert history["applied_filters"]["region"] == "KOREA"
    assert "platform" not in history["applied_filters"]


@pytest.mark.parametrize(
    "fields",
    [
        {"platform": "steam"},
        {"mode": "arcade"},
        {"tier": "Unranked"},
        {"role": "mage"},
        {"map": "없는 전장"},
        {"heroes": ["없는영웅"]},
        {"source": "overfast", "region": "KR"},
    ],
)
def test_history_validates_filters_even_with_empty_store(service, fields):
    result = run(service.call("ow_meta", {"view": "history", **fields}))
    assert result["status"] == "error" and result["error"]["code"] == "UNSUPPORTED_FILTER"


@pytest.mark.parametrize(
    ("other", "expected_status"), [("failure", "partial"), ("stale", "stale"), ("empty", "ok")]
)
def test_comparisons_distinguish_failure_stale_and_valid_empty(service, other, expected_status):
    attach_fakes(service)

    def handler(method, filters):
        if filters["map"] == "oasis":
            if other == "failure":
                return SourceError("SOURCE_UNAVAILABLE", "offline", "overfast")
            return meta_result(
                stale=other == "stale",
                data=[]
                if other == "empty"
                else [{"hero": "reinhardt", "winrate": 53, "pickrate": 7, "banrate": 2}],
            )
        return meta_result()

    service.overfast.handler = handler
    result = run(service.call("ow_meta", {"view": "map_comparison", "maps": ["왕의 길", "oasis"]}))
    assert result["status"] == expected_status
    assert result["source_status"]["kings-row"] == "ok"
    if other == "stale":
        assert result["stale"] and result["data"][1]["data"][0]["difference"]["winrate"] == 1
    if other == "failure":
        assert result["error"]["code"] == "PARTIAL_RESULT"


def test_all_empty_comparison_and_duplicate_aliases(service):
    attach_fakes(service)
    service.overfast.methods["meta"] = meta_result(data=[])
    result = run(service.call("ow_meta", {"view": "map_comparison", "maps": ["oasis", "ilios"]}))
    assert result["status"] == "empty" and result["error"]["code"] == "EMPTY_RESULT"
    duplicate = run(
        service.call("ow_meta", {"view": "map_comparison", "maps": ["왕의 길", "kings-row"]})
    )
    assert duplicate["error"]["code"] == "INVALID_ARGUMENT"


def test_cross_source_comparison_does_not_compute_difference(service):
    attach_fakes(service)
    result = run(service.call("ow_meta", {"view": "region_comparison", "regions": ["ASIA", "KR"]}))
    assert result["status"] == "ok"
    assert result["data"][1]["data"][0]["difference"] is None
    assert (
        Service._difference({"winrate": 0, "zero_value_caution": ["winrate"]}, {"winrate": 50})[
            "winrate"
        ]
        is None
    )


@pytest.mark.parametrize(
    "filters",
    [
        {"tier_min": "banana"},
        {"tier": "Grandmaster 9"},
        {"platform": "console"},
        {"player_country": "KOREA"},
        {"match_region": "ASIA"},
        {"hero": "없는영웅"},
        {"map": "없는 전장"},
        {"tier": "MASTER", "tier_min": "GOLD"},
        {"tier": " "},
    ],
)
def test_local_replay_filters_validate_before_scanning_empty_store(service, filters):
    result = run(service.call("ow_replays_search", {"refresh": False, **filters}))
    assert result["status"] == "error" and result["error"]["code"] == "UNSUPPORTED_FILTER"


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"tier": "GM2"}, {"CCC333"}),
        ({"tier": "Grandmaster"}, {"BBB222", "CCC333", "DDD444", "FFF666"}),
        ({"tier_min": "GM2"}, {"CCC333", "DDD444", "EEE555"}),
        ({"tier_min": "Grandmaster"}, {"BBB222", "CCC333", "DDD444", "EEE555", "FFF666"}),
    ],
)
def test_replay_minimum_tier_honors_divisions_and_unknown_division(service, filters, expected):
    service.repo.save_replays(
        [
            replay(code, tier_claim=tier)
            for code, tier in [
                ("AAA111", "Master 1"),
                ("BBB222", "Grandmaster 5"),
                ("CCC333", "Grandmaster 2"),
                ("DDD444", "Grandmaster 1"),
                ("EEE555", "Champion 5"),
                ("FFF666", "Grandmaster"),
            ]
        ]
    )
    result = run(service.call("ow_replays_search", {"refresh": False, **filters}))
    assert {row["code"] for row in result["data"]} == expected
    assert result["applied_filters"]["tier_filter_basis"] == "source_claim"


def test_bounded_refresh_does_not_rejuvenate_unseen_local_observations(service):
    attach_fakes(service)
    old_time = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    service.repo.save_replays([replay("OLD123", timestamp=old_time)])
    result = run(service.call("ow_replays_search", {}))
    by_code = {row["code"]: row for row in result["data"]}
    assert by_code["OLD123"]["retrieved_at"] == old_time and by_code["OLD123"]["stale"]
    assert not by_code["ABC123"]["stale"] and not by_code["ABC123"]["cached"]
    assert result["retrieved_at"] == old_time and result["status"] == "stale"
    assert any("not covered by this refresh" in warning for warning in result["warnings"])


def test_refresh_failure_returns_only_marked_retained_data_and_unknown_filter_stays_error(service):
    attach_fakes(service)
    service.repo.save_replays([replay()])
    service.replays.methods["search"] = SourceError("RATE_LIMITED", "limited", "owreplays")
    result = run(service.call("ow_replays_search", {}))
    assert result["status"] == "stale" and result["data"][0]["stale"]
    assert service.repo.source_states()["owreplays"]["status"] == "degraded"
    service.replays.methods["search"] = SourceError(
        "UNSUPPORTED_FILTER", "unknown tier", "owreplays"
    )
    assert run(service.call("ow_replays_search", {}))["status"] == "error"


def test_missing_detail_retains_observation_with_stale_status(service):
    attach_fakes(service)
    old = replay(timestamp="2026-01-01T00:00:00+00:00")
    service.repo.save_replays([old])
    service.replays.methods["get"] = SourceResult(None, "owreplays", "https://owreplays.tv/ABC123")
    result = run(service.call("ow_replay_get", {"code": "ABC123"}))
    assert result["status"] == "stale" and result["retrieved_at"] == old["retrieved_at"]
    assert result["data"]["replay_status"] == "unverified"
    assert any("no longer lists" in warning for warning in result["warnings"])


def test_local_match_server_filter_never_uses_source_region_or_nickname(service):
    service.repo.save_replays(
        [replay(source_region="KOREA", match_server_region="KR", player_country="KR")]
    )
    result = run(service.call("ow_replays_search", {"refresh": False, "match_region": "KR"}))
    assert result["status"] == "empty"
    plain = run(service.call("ow_replay_get", {"code": "ABC123", "refresh": False}))
    assert plain["data"]["player_country"] is None and plain["data"]["match_server_region"] is None


def test_programmer_errors_are_not_misreported_as_source_outages(service):
    attach_fakes(service)
    service.overfast.methods["meta"] = RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        run(service.call("ow_meta", {}))


def test_http_fixture_to_service_to_history_preserves_cache_timestamp_and_stale_flags():
    async def scenario():
        repository = Repository(":memory:")
        fail = False
        calls = []

        def handler(request):
            calls.append(request)
            if fail:
                return httpx.Response(503)
            return httpx.Response(
                200, json=[{"hero": "reinhardt", "winrate": 52.3, "pickrate": 5.0, "banrate": 1.0}]
            )

        client = HttpClient(
            repository, transport=httpx.MockTransport(handler), retries=0, min_interval=0
        )
        service = Service(repository, client)
        try:
            first = await service.call("ow_meta", {"heroes": ["라인"], "tier": "MASTER"})
            cached = await service.call("ow_meta", {"heroes": ["reinhardt"], "tier": "MASTER"})
            assert first["status"] == "ok" and cached["cached"]
            assert first["retrieved_at"] == cached["retrieved_at"] and len(calls) == 1
            history = await service.call(
                "ow_meta", {"view": "history", "heroes": ["라인"], "tier": "마스터"}
            )
            assert (
                len(history["data"]) == 1
                and history["data"][0]["retrieved_at"] == first["retrieved_at"]
            )
            repository.conn.execute("UPDATE http_cache SET expires_at=?", (time.time() - 1,))
            fail = True
            stale = await service.call("ow_meta", {"heroes": ["라인"], "tier": "MASTER"})
            assert stale["status"] == "stale" and stale["retrieved_at"] == first["retrieved_at"]
            assert repository.counts()["hero_meta_snapshots"] == 1
        finally:
            await service.close()

    run(scenario())
