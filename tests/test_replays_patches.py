"""Contract and evidence-safety tests for public replay and patch sources."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from overwatch_mcp.models import SourceError
from overwatch_mcp.sources.owreplays import OWReplaysAdapter
from overwatch_mcp.sources.patches import PatchAdapter

CATALOG = {
    "heroes": [{"ID": 19, "hero": "Reinhardt"}, {"ID": 8, "hero": "Genji"}],
    "maps": [{"ID": 34, "map": "Colosseo"}],
    "tiers": [
        {"ID": 30, "tier": "Master", "tierId": "master-1", "title": "Master 1", "division": "1"},
        {
            "ID": 31,
            "tier": "Grandmaster",
            "tierId": "grandmaster-5",
            "title": "Grandmaster 5",
            "division": "5",
        },
        {
            "ID": 35,
            "tier": "Grandmaster",
            "tierId": "grandmaster-1",
            "title": "Grandmaster 1",
            "division": "1",
        },
        {
            "ID": 40,
            "tier": "Champion",
            "tierId": "champion-1",
            "title": "Champion 1",
            "division": "1",
        },
    ],
}
REPLAY = {
    "Code": "8D8VRC",
    "Heroes": [19],
    "Map": 34,
    "Tier": 35,
    "Player": "WHORU",
    "UserHandle": "WHORU",
    "Verified": True,
    "Archived": True,
    "CodeVerificationStatus": "valid",
    "Uploaded": 1777611129,
    "Platform": "pc",
    "PatchLevel": "2.22.0.1",
}


class FakeClient:
    def __init__(self, replay=None, pages=1, html=None):
        self.calls = []
        self.replay = deepcopy(REPLAY if replay is None else replay)
        self.pages = pages
        self.html = html
        self.catalog = deepcopy(CATALOG)

    def result(self, data, url):
        return SimpleNamespace(
            data=data,
            url=url,
            retrieved_at="2026-09-09T00:00:00+00:00",
            headers={},
            cached=False,
            stale=False,
        )

    async def get_json(self, source, url, params=None, ttl=0):
        self.calls.append((url, params))
        if url.endswith("gamedata"):
            return self.result(self.catalog, url)
        if url.endswith("replays"):
            return self.result(
                {
                    "replays": self.replay if isinstance(self.replay, list) else [self.replay],
                    "pages": self.pages,
                },
                url,
            )
        return self.result(self.replay, url)

    async def get_text(self, source, url, params=None, ttl=0, headers=None):
        self.calls.append((url, params))
        return self.result(self.html, url)


def test_archive_takes_precedence_over_source_valid_and_verified_flags():
    result = asyncio.run(OWReplaysAdapter(FakeClient()).get("8d8vrc"))
    assert result.data["replay_status"] == "source_reports_expired"
    assert result.data["source_code_verification_status"] == "valid"
    assert result.data["verified_tier"] is None
    assert result.data["battle_tag"] is None
    assert result.data["player_country"] is None
    assert result.data["match_server_region"] is None
    assert result.data["checked_at"] is None


def test_unarchived_source_claim_is_only_unverified_and_multiple_heroes_are_not_primary():
    record = {**REPLAY, "Archived": False, "Heroes": [19, 8]}
    result = asyncio.run(OWReplaysAdapter(FakeClient(record)).get("8D8VRC"))
    assert result.data["replay_status"] == "unverified"
    assert result.data["hero"] is None
    assert result.data["heroes"] == ["reinhardt", "genji"]


def test_search_resolves_catalog_filters_and_expands_minimum_tier():
    client = FakeClient()
    result = asyncio.run(
        OWReplaysAdapter(client).search(
            {"hero": "reinhardt", "tier_min": "GRANDMASTER", "platform": "PC"}
        )
    )
    params = client.calls[-1][1]
    assert params["heroes"] == "19"
    assert params["tiers"] == "31,35,40"
    assert params["platforms"] == "pc"
    assert result.applied_filters["platform"] == "pc"


@pytest.mark.parametrize(
    "field,value", [("match_region", "KR"), ("country", "KR"), ("player_pool", "kr_rankers")]
)
def test_source_does_not_silently_ignore_region_or_identity_filters(field, value):
    client = FakeClient()
    with pytest.raises(SourceError) as error:
        asyncio.run(OWReplaysAdapter(client).search({field: value}))
    assert error.value.code == "UNSUPPORTED_FILTER"
    assert not client.calls


def test_upstream_ignored_hero_filter_is_a_parse_error():
    client = FakeClient({**REPLAY, "Heroes": [8]})
    with pytest.raises(SourceError) as error:
        asyncio.run(OWReplaysAdapter(client).search({"hero": "reinhardt"}))
    assert error.value.code == "PARSE_ERROR"


def test_search_is_bounded_and_never_promotes_working_state():
    client = FakeClient(pages=1000)
    result = asyncio.run(
        OWReplaysAdapter(client).search({"max_pages": 2, "playable_status": ["client_verified"]})
    )
    assert result.data == []
    assert len(client.calls) == 3
    assert result.applied_filters["pages_scanned"]["to"] == 2
    assert any("additional matches" in warning for warning in result.warnings)


def test_empty_results_and_corrupt_records_have_different_outcomes():
    empty = asyncio.run(OWReplaysAdapter(FakeClient([], pages=0)).search({}))
    assert empty.envelope()["error"]["code"] == "EMPTY_RESULT"
    with pytest.raises(SourceError) as error:
        asyncio.run(OWReplaysAdapter(FakeClient({"error": "backend failed"})).search({}))
    assert error.value.code == "PARSE_ERROR"


def test_detail_response_must_match_requested_code():
    with pytest.raises(SourceError) as error:
        asyncio.run(OWReplaysAdapter(FakeClient()).get("ABC123"))
    assert error.value.code == "PARSE_ERROR"


@pytest.mark.parametrize(
    "body,is_empty", [({"error": "Invalid replay"}, True), ({"error": "Access denied"}, False)]
)
def test_missing_replay_403_is_distinguished_from_access_denial(body, is_empty):
    class ErrorClient(FakeClient):
        async def get_json(self, source, url, params=None, ttl=0):
            error = SourceError("SOURCE_UNAVAILABLE", "HTTP 403", source, url)
            error.http_status = 403
            error.response_body = body
            raise error

    adapter = OWReplaysAdapter(ErrorClient())
    if is_empty:
        assert asyncio.run(adapter.get("ZZZZZZ")).envelope()["error"]["code"] == "EMPTY_RESULT"
    else:
        with pytest.raises(SourceError) as error:
            asyncio.run(adapter.get("ZZZZZZ"))
        assert error.value.code == "SOURCE_UNAVAILABLE"


PATCH = """
<div class="PatchNotes-body"><div class="PatchNotes-patch PatchNotes-live">
<div class="anchor" id="patch-2026-08-19"></div>
<h3 class="PatchNotes-patchTitle">Overwatch Retail Patch Notes – August 19, 2026</h3>
<div class="PatchNotes-sectionDescription"><p>Replay codes have been wiped.</p></div>
<div class="PatchNotesHeroUpdate"><h5 class="PatchNotesHeroUpdate-name">Reinhardt</h5>
<div class="PatchNotesHeroUpdate-body"><p>Barrier Field health increased.</p></div></div>
<div class="PatchNotesHeroUpdate"><h5 class="PatchNotesHeroUpdate-name">Lúcio</h5>
<div class="PatchNotesHeroUpdate-body"><p>Healing increased.</p></div></div>
</div></div>
"""


def test_patch_hero_filter_preserves_replay_notice_and_does_not_invent_version():
    records = PatchAdapter(None).parse(
        PATCH, "https://overwatch.blizzard.com/en-us/news/patch-body/live/2026/8", "reinhardt"
    )
    assert len(records) == 1
    record = records[0]
    assert record["hero_changes"] == [
        {
            "hero": "reinhardt",
            "description": "Barrier Field health increased.",
            "context_headings": [],
        }
    ]
    assert record["game_version"] is None
    assert record["replay_compatibility"]["effect"] == "invalidated"
    assert record["source_url"].endswith("/news/patch-notes/live/2026/8/#patch-2026-08-19")
    assert "Healing" not in record["description"]


def test_patch_accented_name_normalizes_and_nonmatching_hero_is_empty():
    adapter = PatchAdapter(None)
    assert (
        adapter.parse(PATCH, "https://overwatch.blizzard.com/en-us/news/patch-notes/", "lucio")[0][
            "hero"
        ]
        == "lucio"
    )
    assert (
        adapter.parse(PATCH, "https://overwatch.blizzard.com/en-us/news/patch-notes/", "ana") == []
    )


@pytest.mark.parametrize(
    "notice,effect",
    [
        ("Replay codes from the August 11 patch are still available.", "preserved"),
        ("Replay codes have been wiped.", "invalidated"),
        ("Replay codes may be affected by this update.", "unknown"),
        ("Replay codes have not been wiped.", "unknown"),
    ],
)
def test_only_explicit_replay_compatibility_changes_are_classified(notice, effect):
    html = PATCH.replace("Replay codes have been wiped.", notice)
    record = PatchAdapter(None).parse(
        html, "https://overwatch.blizzard.com/en-us/news/patch-notes/"
    )[0]
    assert record["replay_compatibility"]["effect"] == effect


def test_archive_months_are_bounded_and_coverage_is_reported():
    client = FakeClient(html='<div class="PatchNotes-body"></div>')
    result = asyncio.run(
        PatchAdapter(client).fetch({"before": "2026-08-31", "after": "2025-01-01", "max_months": 2})
    )
    assert len(client.calls) == 2
    assert result.applied_filters["months_checked"] == ["2026-08", "2026-07"]
    assert any("older matching notes" in warning for warning in result.warnings)


def test_patch_wrong_month_or_missing_container_is_not_empty():
    with pytest.raises(SourceError) as error:
        asyncio.run(
            PatchAdapter(FakeClient(html=PATCH)).fetch({"before": "2026-07-31", "max_months": 1})
        )
    assert error.value.code == "PARSE_ERROR"
    with pytest.raises(SourceError):
        PatchAdapter(None).parse("<html>Access denied</html>", "https://overwatch.blizzard.com")


def test_source_empty_month_marker_is_distinct_from_changed_structure():
    adapter = PatchAdapter(None)
    assert (
        adapter.parse(
            '<div class="PatchNotes-body"><p class="patch-notes-error">No Patch Notes Found</p></div>',
            "https://overwatch.blizzard.com",
        )
        == []
    )
    with pytest.raises(SourceError):
        adapter.parse(
            '<div class="PatchNotes-body"><article>New patch markup</article></div>',
            "https://overwatch.blizzard.com",
        )


def test_hero_change_preserves_stadium_section_context():
    html = PATCH.replace(
        '<div class="PatchNotesHeroUpdate">',
        '<h4 class="PatchNotes-sectionTitle">Stadium Updates</h4><h4 class="PatchNotes-sectionTitle">Tank</h4><div class="PatchNotesHeroUpdate">',
        1,
    )
    record = PatchAdapter(None).parse(html, "https://overwatch.blizzard.com", "reinhardt")[0]
    assert record["hero_changes"][0]["context_headings"] == ["Stadium Updates", "Tank"]
    assert record["description"].startswith("Stadium Updates / Tank:")
