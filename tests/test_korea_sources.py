"""Synthetic minimal fixtures using public source schemas verified 2026-09-09.

The fixture numbers and teams are artificial, not historical esports statistics.
Live contract probes are described in the adapters; unit tests need no network.
"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from overwatch_skill.models import SourceError
from overwatch_skill.sources.owcs_korea import LATEST_RELEASE, MAX_ASSET_BYTES, OWCSKoreaAdapter
from overwatch_skill.sources.owtics import OwticsAdapter, decode_loader

FETCHED = "2026-09-09T00:00:00+00:00"
SEASON = {"id": "season-test", "season": 24, "displaySeason": 4, "era": "OW", "isMidseason": False}


def hydration(route, payload):
    """Synthetic Turbo Stream references with the website's observed encoding."""
    values = []

    def ref(value):
        if value is None:
            return -5
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            values[index] = {f"_{ref(key)}": ref(child) for key, child in value.items()}
        elif isinstance(value, list):
            values[index] = [ref(child) for child in value]
        else:
            values[index] = value
        return index

    ref({"loaderData": {route: payload}})
    literal = json.dumps(json.dumps(values))
    return f"<html><script>window.__reactRouterContext.streamController.enqueue({literal})</script></html>"


def overview():
    measurement = {
        "hero": {"slug": "reinhardt", "role": "TANK"},
        "region": "KOREA",
        "tier": "MASTER",
        "mode": "COMPETITIVE",
        "season": SEASON,
        "winRate": 51.2,
        "pickRate": 4.3,
        "banRate": 0.5,
    }
    return {
        "statistics": {
            "heroesRatesOverview": {
                "filter": {
                    "region": "KOREA",
                    "tier": "MASTER",
                    "mode": "COMPETITIVE",
                    "season": SEASON,
                },
                "result": {"metaStandings": [{"measurement": measurement}]},
            }
        }
    }


def map_payload():
    return {
        "region": "KOREA",
        "data": {
            "mapBySlug": {
                "slug": "kings-row",
                "heroRates": {
                    "filter": {"region": None, "tier": "MASTER", "season": SEASON},
                    "result": {
                        "measurements": [
                            {
                                "hero": {"slug": "reinhardt", "role": "TANK"},
                                "region": "ASIA",
                                "winRate": 90,
                                "pickRate": 20,
                            },
                            {
                                "hero": {"slug": "reinhardt", "role": "TANK"},
                                "region": "KOREA",
                                "winRate": 45,
                                "pickRate": 5,
                            },
                        ]
                    },
                },
            }
        },
        "catalog": {
            "combos": [
                {
                    "region": "KOREA",
                    "tier": "MASTER",
                    "mode": "COMPETITIVE",
                    "season": SEASON,
                    "grains": ["BY_MAP"],
                }
            ]
        },
    }


class TextClient:
    def __init__(self, payload=None, *, map_view=False, html=None):
        self.html = (
            html
            if html is not None
            else hydration(
                "app/routes/map.$slug" if map_view else "app/routes/hero",
                payload if payload is not None else overview(),
            )
        )
        self.calls = []

    async def get_text(self, source, url, params=None, ttl=0):
        self.calls.append((source, url, params, ttl))
        return SimpleNamespace(
            data=self.html, url=url, retrieved_at=FETCHED, cached=False, stale=False
        )


def run_meta(client=None, **filters):
    return asyncio.run(
        OwticsAdapter(client or TextClient()).meta({"region": "KR", "tier": "MASTER", **filters})
    )


def test_owtics_verified_korea_and_percent_without_match_server_inference():
    client = TextClient()
    result = run_meta(client, heroes=["라인"], platform="pc")
    assert result.data[0]["winrate"] == 51.2
    assert result.data[0]["rate_unit"] == "percent"
    assert result.data[0]["source_region"] == "KOREA"
    assert result.data[0]["match_server_region"] is None
    assert result.requested_filters["region"] == "KR"
    assert result.applied_filters["region"] == "KOREA"
    assert "platform" not in result.applied_filters
    assert any("platform cannot be verified" in warning for warning in result.warnings)
    assert result.source_updated_at is None and result.data_patch is None
    assert client.calls[0][2]["region"] == "KOREA"


def test_owtics_map_selects_explicit_region_in_mixed_payload():
    result = run_meta(TextClient(map_payload(), map_view=True), map="kings-row")
    assert len(result.data) == 1 and result.data[0]["winrate"] == 45
    assert result.data[0]["banrate"] is None
    assert result.applied_filters["season"] == SEASON


@pytest.mark.parametrize(
    "filters",
    [
        {"tier": "GRANDMASTER"},
        {"tier": "CHAMPION"},
        {"platform": "console"},
        {"match_server_region": "KR"},
        {"region": "INVALID"},
        {"mode": "quickplay", "map": "kings-row"},
    ],
)
def test_owtics_rejects_unverified_filters_before_request(filters):
    client = TextClient()
    with pytest.raises(SourceError) as caught:
        run_meta(client, **filters)
    assert caught.value.code == "UNSUPPORTED_FILTER"
    assert client.calls == []


def test_owtics_filter_echo_mismatch_does_not_return_wrong_population():
    payload = overview()
    payload["statistics"]["heroesRatesOverview"]["filter"]["region"] = "ASIA"
    with pytest.raises(SourceError) as caught:
        run_meta(TextClient(payload))
    assert caught.value.code == "UNSUPPORTED_FILTER"


def test_owtics_measurement_mismatch_is_parse_failure():
    payload = overview()
    payload["statistics"]["heroesRatesOverview"]["result"]["metaStandings"][0]["measurement"][
        "tier"
    ] = "ALL"
    with pytest.raises(SourceError) as caught:
        run_meta(TextClient(payload))
    assert caught.value.code == "PARSE_ERROR"


@pytest.mark.parametrize("value", [101, float("nan"), True, "55%"])
def test_owtics_malformed_percent_is_not_zero(value):
    payload = overview()
    payload["statistics"]["heroesRatesOverview"]["result"]["metaStandings"][0]["measurement"][
        "winRate"
    ] = value
    with pytest.raises(SourceError) as caught:
        run_meta(TextClient(payload))
    assert caught.value.code == "PARSE_ERROR"


def test_owtics_missing_measurement_and_unknown_hero_are_different():
    assert run_meta(heroes=["ana"]).data == []
    with pytest.raises(SourceError) as caught:
        run_meta(TextClient(html="<html><p>Site changed</p></html>"))
    assert caught.value.code == "PARSE_ERROR"


def test_owtics_does_not_execute_javascript_or_follow_circular_references():
    html = '<script>window.__reactRouterContext.streamController.enqueue("[{\\"_1\\":2},\\"loaderData\\",{\\"_3\\":4},\\"app/routes/hero\\",{\\"_5\\":4},\\"loop\\"]")</script>'
    with pytest.raises(SourceError) as caught:
        decode_loader(html, "app/routes/hero", "https://owtics.gg/en-US/hero")
    assert caught.value.code == "PARSE_ERROR"


def release_fixture():
    tag = "test-release"
    base = f"https://github.com/IanBosworth/owcs-korea-data/releases/download/{tag}/"
    manifest = {
        "schema_version": "owcs-publication-v1",
        "release_version": tag,
        "safety": {"approved_maps_only": True},
        "data_as_of": "2026-07-11",
        "generated_at_utc": "2026-07-30T01:00:00Z",
        "methodology_version": "hero-intervals-v3-echo-copy",
        "license": "CC BY 4.0",
        "counts": {"matches": 2, "maps": 2, "hero_bans": 2, "hero_time_segments": 4},
    }
    dataset = {
        "schema_version": "owcs-publication-v1",
        "release_version": tag,
        "matches": [
            {
                "id": 1,
                "match_date": "2026-06-12",
                "team_a": "Alpha",
                "team_b": "Beta",
                "stage": "Regular Season",
            },
            {
                "id": 2,
                "match_date": "2026-07-11",
                "team_a": "Alpha",
                "team_b": "Gamma",
                "stage": "Playoffs",
            },
        ],
        "maps": [
            {"id": 10, "match_id": 1, "map_name": "King's Row", "winner_team": "Alpha"},
            {"id": 20, "match_id": 2, "map_name": "Ilios", "winner_team": "Gamma"},
        ],
        "hero_bans": [
            {"map_id": 10, "hero": "Ana", "banned_by_team": "Alpha"},
            {"map_id": 20, "hero": "Reinhardt", "banned_by_team": "Gamma"},
        ],
        "hero_time_segments": [
            {
                "map_id": 10,
                "hero": "Reinhardt",
                "team": "Alpha",
                "duration_seconds": 100,
                "echo_copy_target": None,
            },
            {
                "map_id": 10,
                "hero": "Echo",
                "team": "Alpha",
                "duration_seconds": 10,
                "echo_copy_target": "Reinhardt",
            },
            {
                "map_id": 10,
                "hero": "unknown",
                "team": "Alpha",
                "duration_seconds": 8,
                "echo_copy_target": None,
            },
            {
                "map_id": 20,
                "hero": "Ana",
                "team": "Gamma",
                "duration_seconds": 50,
                "echo_copy_target": None,
            },
        ],
    }
    stats = {
        "metric_version": manifest["methodology_version"],
        "coverage": {"approved_only": True},
        "export_manifest": {"data_as_of": "2026-07-11"},
        "hero_stats": [
            {
                "hero": "Reinhardt",
                "role": "tank",
                "hero_seconds": 100,
                "pick_rate": 0.9,
                "pick_rate_is_ban_adjusted": True,
                "unmirrored_winrate": 0.75,
            }
        ],
    }
    payloads = {
        base + "manifest.json": manifest,
        base + f"owcs-korea-data-{tag}.json": dataset,
        base + f"owcs-korea-hero-stats-{tag}.json": stats,
    }
    release = {
        "tag_name": tag,
        "published_at": "2026-07-31T03:00:58Z",
        "html_url": f"https://github.com/IanBosworth/owcs-korea-data/releases/tag/{tag}",
        "assets": [
            {"name": url.rsplit("/", 1)[1], "size": 3000, "browser_download_url": url}
            for url in payloads
        ],
    }
    payloads[LATEST_RELEASE] = release
    return payloads


class JsonClient:
    def __init__(self):
        self.payloads = release_fixture()
        self.calls = []
        self.stale_url = None

    async def get_json(self, source, url, params=None, ttl=0):
        self.calls.append(url)
        return SimpleNamespace(
            data=copy.deepcopy(self.payloads[url]),
            url=url,
            retrieved_at=FETCHED,
            cached=False,
            stale=url == self.stale_url,
        )

    def table(self, suffix):
        return next(value for url, value in self.payloads.items() if url.endswith(suffix))


def run_esports(client=None, **filters):
    return asyncio.run(OWCSKoreaAdapter(client or JsonClient()).fetch(filters))


def test_owcs_keeps_coverage_publish_generation_times_distinct():
    result = run_esports()
    assert result.data["dataset_type"] == "esports"
    assert result.data["data_until"] == "2026-07-11"
    assert result.data["published_at"] == "2026-07-31T03:00:58Z"
    assert result.data_period == "2026-06-12/2026-07-11"
    assert result.source_updated_at == "2026-07-30T01:00:00Z"
    assert result.data["records"][0]["pick_rate"] == 0.9
    assert result.data["records"][0]["rate_unit"] == "ratio"
    assert "winrate" not in result.data["records"][0]
    assert result.data["license"] == "CC BY 4.0"


@pytest.mark.parametrize(
    ("view", "count"), [("matches", 2), ("maps", 2), ("bans", 2), ("teams", 3)]
)
def test_owcs_reads_each_normalized_view(view, count):
    result = run_esports(view=view)
    assert result.data["total"] == count


def test_owcs_scoped_hero_time_does_not_reuse_global_rates_or_echo_target():
    result = run_esports(view="hero_meta", map="kings-row", hero="라인")
    row = result.data["records"][0]
    assert row["hero_seconds"] == 100
    assert row["pick_rate"] is None and row["unmirrored_winrate"] is None
    assert row["by_map"] == [{"map_name": "King's Row", "hero_seconds": 100.0}]
    result = run_esports(view="hero_meta", map="kings-row", hero="echo")
    assert result.data["records"][0]["echo_copy_targets"] == [
        {"hero": "reinhardt", "seconds": 10.0}
    ]


def test_owcs_hero_filter_for_bans_means_banned_hero_not_hero_played():
    result = run_esports(view="bans", hero="reinhardt")
    assert result.data["records"][0]["map_id"] == 20


def test_owcs_filters_matches_teams_and_pages():
    result = run_esports(view="matches", after="2026-07-01", team="Gamma")
    assert result.data["total"] == 1 and result.data["records"][0]["id"] == 2
    result = run_esports(view="teams", hero="reinhardt", team="Alpha")
    assert result.data["records"][0]["hero_seconds"] == 100
    result = run_esports(view="bans", limit=1, offset=1)
    assert result.data["total"] == 2 and len(result.data["records"]) == 1


@pytest.mark.parametrize(
    "filters",
    [
        {"region": "ASIA"},
        {"view": "matches", "role": "tank"},
        {"view": "hero_meta", "role": "tank", "team": "Alpha"},
        {"tier": "MASTER"},
        {"limit": 1000},
        {"after": "2026-99-99"},
    ],
)
def test_owcs_rejects_unapplied_or_invalid_filters(filters):
    with pytest.raises(SourceError) as caught:
        run_esports(**filters)
    assert caught.value.code == "UNSUPPORTED_FILTER"


@pytest.mark.parametrize("mutation", ["count", "schema", "reference", "negative_duration"])
def test_owcs_corrupt_release_is_source_failure(mutation):
    client = JsonClient()
    if mutation == "count":
        client.table("manifest.json")["counts"]["maps"] = 9
    elif mutation == "schema":
        client.table("manifest.json")["schema_version"] = "unknown"
    elif mutation == "reference":
        client.table("owcs-korea-data-test-release.json")["maps"][0]["match_id"] = 99
    else:
        client.table("owcs-korea-data-test-release.json")["hero_time_segments"][0][
            "duration_seconds"
        ] = -1
    with pytest.raises(SourceError) as caught:
        run_esports(client, view="matches")
    assert caught.value.code == "PARSE_ERROR"


@pytest.mark.parametrize("mutation", ["url", "size"])
def test_owcs_asset_boundary_checks_before_download(mutation):
    client = JsonClient()
    asset = client.payloads[LATEST_RELEASE]["assets"][0]
    if mutation == "url":
        asset["browser_download_url"] = "http://127.0.0.1/internal.json"
    else:
        asset["size"] = MAX_ASSET_BYTES + 1
    with pytest.raises(SourceError):
        run_esports(client)
    assert client.calls == [LATEST_RELEASE]


def test_owcs_marks_any_stale_constituent_and_reports_empty_records():
    client = JsonClient()
    client.stale_url = next(url for url in client.payloads if url.endswith("manifest.json"))
    result = run_esports(client, view="matches", team="Absent")
    assert result.stale
    assert result.data["records"] == []
    assert any("EMPTY_RESULT" in warning for warning in result.warnings)
