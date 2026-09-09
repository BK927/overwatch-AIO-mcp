import asyncio
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from overwatch_skill.models import SourceError
from overwatch_skill.normalization import (
    battle_tag_from_id,
    normalize_hero,
    normalize_map,
    normalize_player_id,
    normalize_region,
    normalize_tier,
)
from overwatch_skill.sources.overfast import BASE_URL, OverFastAdapter


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def get_json(self, source, url, params=None, ttl=3600):
        self.calls.append({"source": source, "url": url, "params": params or {}, "ttl": ttl})
        data = self.responses.pop(0)
        if isinstance(data, Exception):
            raise data
        return SimpleNamespace(
            data=data,
            url=url + ("?" + urlencode(params) if params else ""),
            retrieved_at="2026-09-09T00:00:00+00:00",
            headers={},
            cached=True,
            stale=False,
        )


def run(coro):
    return asyncio.run(coro)


def hero_stats(**overrides):
    return {"hero": "reinhardt", "pickrate": 6.8, "winrate": 52.3, "banrate": 1.2, **overrides}


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("라인하르트", "reinhardt"),
        ("라인", "reinhardt"),
        ("D.Va", "dva"),
        ("디바", "dva"),
        ("라마", "ramattra"),
        ("Soldier: 76", "soldier-76"),
        ("Lúcio", "lucio"),
        ("Torbjörn", "torbjorn"),
        ("레킹볼", "wrecking-ball"),
    ],
)
def test_hero_aliases(input_value, expected):
    assert normalize_hero(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("왕의 길", "kings-row"),
        ("King’s Row", "kings-row"),
        ("Esperança", "esperanca"),
        ("에스페란사", "esperanca"),
        ("Temple of Anubis", "anubis"),
        ("감시 기지: 지브롤터", "watchpoint-gibraltar"),
        ("all", "all-maps"),
    ],
)
def test_map_aliases(input_value, expected):
    assert normalize_map(input_value) == expected


def test_region_and_battletag_are_not_conflated():
    assert normalize_region("KOREA") == "KR"
    assert normalize_region("아시아") == "ASIA"
    assert normalize_player_id("  TeKrop#2217  ") == "TeKrop-2217"
    assert battle_tag_from_id("TeKrop-2217") == "TeKrop#2217"
    assert battle_tag_from_id("deadbeef%7Cfeedface") is None
    assert normalize_tier("에메랄드") == "EMERALD"


def test_rates_remain_percent_and_unknown_metadata_null():
    client = FakeClient([hero_stats(pickrate=0.068, winrate=0.523)])
    result = run(
        OverFastAdapter(client).meta({"region": "ASIA", "tier": "MASTER", "heroes": ["라인"]})
    )
    row = result.data[0]
    assert row["winrate"] == 0.523
    assert row["pickrate"] == 0.068
    assert row["rate_unit"] == "percent"
    assert row["match_server_region"] is None
    assert row["source_region"] == "ASIA"
    assert (
        result.source_updated_at is None
        and result.data_patch is None
        and result.data_period is None
    )
    assert client.calls[0]["params"]["competitive_division"] == "master"
    assert client.calls[0]["ttl"] == 3600
    assert result.cached


def test_korean_region_requires_opt_in_before_network():
    client = FakeClient()
    with pytest.raises(SourceError, match="no Korean-only") as error:
        run(OverFastAdapter(client).meta({"region": "KR"}))
    assert error.value.code == "UNSUPPORTED_FILTER"
    assert client.calls == []


def test_region_fallback_keeps_requested_and_applied():
    client = FakeClient([hero_stats()])
    filters = {"region": "KR", "allow_region_fallback": True}
    result = run(OverFastAdapter(client).meta(filters))
    assert result.requested_filters == filters
    assert result.applied_filters["region"] == "ASIA"
    assert result.data[0]["match_server_region"] is None
    assert any("Requested region=KR" in warning for warning in result.warnings)
    assert client.calls[0]["params"]["region"] == "asia"


def test_champion_cannot_silently_become_grandmaster():
    with pytest.raises(SourceError, match="cannot isolate CHAMPION"):
        run(OverFastAdapter(FakeClient()).meta({"tier": "CHAMPION"}))
    client = FakeClient([hero_stats()])
    result = run(OverFastAdapter(client).meta({"tier": "GRANDMASTER"}))
    assert result.applied_filters["tier"] == "GRANDMASTER"
    assert any("include CHAMPION" in warning for warning in result.warnings)


def test_zero_rate_is_retained_with_missing_data_caution():
    result = run(OverFastAdapter(FakeClient([hero_stats(winrate=0)])).meta({}))
    assert result.data[0]["winrate"] == 0
    assert result.data[0]["zero_value_caution"] == ["winrate"]
    assert any("not proof of a true 0%" in warning for warning in result.warnings)


@pytest.mark.parametrize("invalid", [-1, 100.1, True, "52.3", float("nan"), float("inf")])
def test_invalid_percentage_is_a_parse_error(invalid):
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(FakeClient([hero_stats(winrate=invalid)])).meta({}))
    assert error.value.code == "PARSE_ERROR"


def test_meta_empty_result_is_not_a_source_failure():
    result = run(OverFastAdapter(FakeClient([])).meta({"heroes": ["라마"]}))
    assert result.envelope()["status"] == "empty"
    assert result.envelope()["error"]["code"] == "EMPTY_RESULT"
    failure = SourceError("SOURCE_UNAVAILABLE", "upstream down", "overfast")
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(FakeClient(failure)).meta({}))
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_ranking_scope_and_hero_filter():
    client = FakeClient(
        [
            hero_stats(),
            hero_stats(hero="ramattra", winrate=53),
            hero_stats(hero="dva", winrate=60),
        ]
    )
    result = run(OverFastAdapter(client).meta({"heroes": ["라인", "라마"]}))
    assert [row["hero"] for row in result.data] == ["ramattra", "reinhardt"]
    assert [row["ranking"] for row in result.data] == [1, 2]
    assert all(row["ranking_scope"] == "returned_heroes" for row in result.data)


@pytest.mark.parametrize(
    "filters",
    [
        {"mode": "quickplay", "tier": "MASTER"},
        {"mode": "stadium"},
        {"matchup": "ramattra"},
        {"allow_region_fallback": "true"},
        {"heroes": "reinhardt"},
        {"platform": "switch"},
    ],
)
def test_unrepresentable_meta_filters_fail_explicitly(filters):
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(FakeClient()).meta(filters))
    assert error.value.code == "UNSUPPORTED_FILTER"


def test_catalog_hero_detail_and_map_mode():
    client = FakeClient(
        {"key": "reinhardt", "name": "라인하르트"}, [{"key": "rialto", "name": "Rialto"}]
    )
    adapter = OverFastAdapter(client)
    detail = run(adapter.catalog({"type": "heroes", "hero": "라인"}))
    assert detail.data["name"] == "라인하르트"
    assert client.calls[0]["url"] == BASE_URL + "/heroes/reinhardt"
    assert client.calls[0]["params"] == {"locale": "ko-kr"}
    maps = run(adapter.catalog({"type": "maps", "mode": "escort", "locale": "ko-KR"}))
    assert client.calls[1]["params"] == {"gamemode": "escort"}
    assert maps.applied_filters["locale"] == "en-us"
    assert maps.warnings
    with pytest.raises(SourceError, match="competitive map-pool"):
        run(adapter.catalog({"type": "maps", "mode": "competitive"}))


def test_player_search_preserves_battletag_and_distinguishes_hex_id():
    client = FakeClient(
        {
            "total": 2,
            "results": [
                {"player_id": "TeKrop-2217", "name": "TeKrop", "is_public": True},
                {"player_id": "abcd%7Cef01", "name": "Player", "is_public": None},
            ],
        }
    )
    result = run(OverFastAdapter(client).players_search({"query": "TeKrop#2217"}))
    assert client.calls[0]["params"]["name"] == "TeKrop-2217"
    assert result.data[0]["battle_tag"] == "TeKrop#2217"
    assert result.data[1]["battle_tag"] is None


def test_hero_player_view_applies_platform_mode_and_hero():
    client = FakeClient(
        {
            "general": {"winrate": 50},
            "roles": {},
            "heroes": {"reinhardt": {"winrate": 55}, "dva": {"winrate": 60}},
        }
    )
    result = run(
        OverFastAdapter(client).player_get(
            {
                "player_id": "AbCd#1234",
                "view": "hero",
                "hero": "라인",
                "mode": "quickplay",
                "platform": "console",
            }
        )
    )
    assert client.calls[0]["url"].endswith("/players/AbCd-1234/stats/summary")
    assert client.calls[0]["params"] == {"gamemode": "quickplay", "platform": "console"}
    assert result.data == {"winrate": 55}
    assert result.applied_filters["hero"] == "reinhardt"


def test_missing_stats_are_not_assumed_private():
    client = FakeClient(
        {"general": None, "roles": None, "heroes": None}, {"total": 0, "results": []}
    )
    result = run(OverFastAdapter(client).player_get({"player_id": "Person-1234", "view": "roles"}))
    assert result.data is None
    assert result.envelope()["error"]["code"] == "EMPTY_RESULT"
    assert any("does not establish" in warning for warning in result.warnings)


def test_only_exact_identity_establishes_private_profile():
    client = FakeClient(
        {"general": None, "roles": None, "heroes": None},
        {
            "total": 1,
            "results": [{"player_id": "Person-1234", "name": "Person", "is_public": False}],
        },
    )
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(client).player_get({"player_id": "Person#1234", "view": "roles"}))
    assert error.value.code == "PRIVATE_PROFILE"
    client = FakeClient(
        {"general": None, "roles": None, "heroes": None},
        {
            "total": 1,
            "results": [{"player_id": "Other-5678", "name": "Person", "is_public": False}],
        },
    )
    result = run(OverFastAdapter(client).player_get({"player_id": "Person#1234", "view": "roles"}))
    assert result.data is None


def test_summary_filters_statistics_and_platform_rank():
    client = FakeClient(
        {"general": {"winrate": 50}, "heroes": {"reinhardt": {"winrate": 55}}, "roles": {}},
        {
            "username": "AbCd",
            "last_updated_at": 1704209332,
            "competitive": {"pc": {"tank": "master"}, "console": {"tank": "gold"}},
        },
    )
    result = run(OverFastAdapter(client).player_get({"player_id": "AbCd-1234", "hero": "라인"}))
    assert result.data["statistics"] == {"winrate": 55}
    assert result.data["profile"]["competitive"] == {"pc": {"tank": "master"}}
    assert result.applied_filters["hero_filter_scope"] == "statistics"
    assert result.source_updated_at == "2024-01-02T15:28:52+00:00"


def test_career_filtered_endpoint_and_encoded_identifier():
    client = FakeClient(
        {
            "reinhardt": {"game": {"games_played": 0, "win_percentage": 0}},
            "ana": {"game": {"games_played": 20}},
        }
    )
    result = run(
        OverFastAdapter(client).player_get(
            {"player_id": "ABC%7CDEF", "view": "career", "hero": "라인"}
        )
    )
    assert "%257C" not in client.calls[0]["url"]
    assert "ABC%7CDEF" in client.calls[0]["url"]
    assert client.calls[0]["params"]["hero"] == "reinhardt"
    assert list(result.data) == ["reinhardt"]


@pytest.mark.parametrize(
    "filters",
    [
        {"view": "hero"},
        {"view": "roles", "hero": "라인"},
        {"view": "career", "role": "tank"},
        {"view": "summary", "region": "KR"},
        {"view": "hero", "hero": "all-heroes"},
    ],
)
def test_player_filters_cannot_be_ignored(filters):
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(FakeClient()).player_get({"player_id": "Person-1234", **filters}))
    assert error.value.code == "UNSUPPORTED_FILTER"


def test_player_percentages_are_validated_without_scaling():
    client = FakeClient({"general": None, "roles": {}, "heroes": {"ana": {"winrate": 150}}})
    with pytest.raises(SourceError) as error:
        run(
            OverFastAdapter(client).player_get(
                {"player_id": "Player-1234", "view": "hero", "hero": "ana"}
            )
        )
    assert error.value.code == "PARSE_ERROR"


def test_player_http_forbidden_without_private_evidence_is_source_failure():
    failure = SourceError("SOURCE_UNAVAILABLE", "Forbidden", "overfast")
    failure.http_status = 403
    client = FakeClient(failure, {"total": 0, "results": []})
    with pytest.raises(SourceError) as error:
        run(OverFastAdapter(client).player_get({"player_id": "Player-1234", "view": "roles"}))
    assert error.value.code == "SOURCE_UNAVAILABLE"
