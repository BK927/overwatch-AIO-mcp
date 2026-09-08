"""Official statistics request, raw-value, and filter-echo safety contracts."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from overwatch_mcp.models import SourceError
from overwatch_mcp.sources.blizzard import BlizzardAdapter

FORM = """
<select id="filter-rq-select"><option value="81">Quick Play - Role Queue</option>
<option value="97">Competitive - Role Queue</option></select>
<select id="filter-input-select"><option value="PC">Mouse &amp; Keyboard</option>
<option value="Console">Controller</option></select>
<select id="filter-region-select"><option value="Asia">Asia</option>
<option value="Europe">Europe</option><option value="Americas">Americas</option></select>
<select id="filter-tier-select"><option value="All">All Tiers</option>
<option value="Master">Master</option><option value="Grandmaster">Grandmaster and Champion</option></select>
<select id="filter-map-select"><option value="all-maps" data-rqs="81,97">All Maps</option>
<option value="kings-row" data-rqs="97">King's Row</option></select>
"""
ROW = {"id": "reinhardt", "hero": {"name": "Reinhardt", "role": "TANK"},
       "cells": {"name": "Reinhardt", "winrate": 47.8, "pickrate": 5.1, "banrate": 1.8}}


class Client:
    def __init__(self):
        self.calls = []
        self.form = FORM
        self.rows = [deepcopy(ROW)]
        self.selected_override = {}
        self.columns = ["name", "winrate", "pickrate", "banrate"]

    def result(self, data, url):
        return SimpleNamespace(data=data, url=url, retrieved_at="2026-09-09T00:00:00+00:00", headers={}, cached=False, stale=False)

    async def get_text(self, source, url, params=None, ttl=0, headers=None):
        self.calls.append((url, params))
        return self.result(self.form, url)

    async def get_json(self, source, url, params=None, ttl=0, headers=None):
        self.calls.append((url, params))
        selected = {**params, **self.selected_override}
        return self.result({"rates": {"rates": self.rows, "selected": selected}, "columns": [{"id": column} for column in self.columns]}, url)


def test_discovers_queue_value_instead_of_hardcoding_and_preserves_percentages():
    client = Client()
    result = asyncio.run(BlizzardAdapter(client).meta({"region": "ASIA", "tier": "MASTER", "heroes": ["라인하르트"]}))
    assert client.calls[-1][1]["rq"] == "97"
    assert result.data[0]["winrate"] == 47.8
    assert result.data[0]["banrate"] == 1.8
    assert result.data[0]["raw_rates"]["pickrate"] == 5.1
    assert result.applied_filters["source_selected"]["rq"] == "97"
    assert result.data_patch is None
    assert result.source_updated_at is None


@pytest.mark.parametrize("field,wrong", [("tier", "All"), ("rq", "0"), ("region", "Europe"), ("map", "kings-row"), ("input", "Console")])
def test_rejects_source_silently_changing_any_requested_filter(field, wrong):
    client = Client()
    client.selected_override = {field: wrong}
    with pytest.raises(SourceError) as error:
        asyncio.run(BlizzardAdapter(client).meta({"tier": "MASTER"}))
    assert error.value.code == "PARSE_ERROR"


def test_preserves_minus_one_sentinel_and_real_zero_distinctly():
    client = Client()
    client.rows[0]["cells"].update({"winrate": -1, "pickrate": 0})
    result = asyncio.run(BlizzardAdapter(client).meta({}))
    row = result.data[0]
    assert row["winrate"] is None
    assert row["raw_rates"]["winrate"] == -1
    assert row["pickrate"] == 0
    assert row["metric_availability"]["pickrate"] == "available"
    assert row["ranking"] is None


def test_unreported_ban_column_does_not_turn_placeholder_zero_into_statistic():
    client = Client()
    client.columns.remove("banrate")
    client.rows[0]["cells"]["banrate"] = 0
    result = asyncio.run(BlizzardAdapter(client).meta({"mode": "quickplay"}))
    assert client.calls[-1][1]["rq"] == "81"
    assert result.data[0]["banrate"] is None
    assert result.data[0]["raw_rates"]["banrate"] == 0
    assert result.data[0]["metric_availability"]["banrate"] == "not_reported"


def test_korea_requires_explicit_fallback_and_never_sets_match_server():
    client = Client()
    with pytest.raises(SourceError) as error:
        asyncio.run(BlizzardAdapter(client).meta({"region": "KR"}))
    assert error.value.code == "UNSUPPORTED_FILTER"
    assert not client.calls
    result = asyncio.run(BlizzardAdapter(client).meta({"region": "KR", "allow_region_fallback": True}))
    assert result.requested_filters["region"] == "KR"
    assert result.applied_filters["region"] == "ASIA"
    assert result.data[0]["source_region"] == "ASIA"
    assert result.data[0]["match_server_region"] is None
    assert any("한국" in warning for warning in result.warnings)


@pytest.mark.parametrize("filters", [{"mode": "quickplay", "tier": "MASTER"}, {"mode": "quickplay", "map": "kings-row"}, {"tier": "CHAMPION"}, {"rq": "2"}])
def test_unsupported_requests_are_not_silently_broadened(filters):
    client = Client()
    with pytest.raises(SourceError) as error:
        asyncio.run(BlizzardAdapter(client).meta(filters))
    assert error.value.code == "UNSUPPORTED_FILTER"
    assert not client.calls


def test_unknown_queue_label_is_unavailable_rather_than_guessed():
    client = Client()
    client.form = FORM.replace("Quick Play - Role Queue", "New Queue A").replace("Competitive - Role Queue", "New Queue B")
    with pytest.raises(SourceError) as error:
        asyncio.run(BlizzardAdapter(client).meta({}))
    assert error.value.code == "PARSE_ERROR"
    assert len(client.calls) == 1


@pytest.mark.parametrize("value", [True, "47.8", -2, 101, float("nan")])
def test_malformed_rates_are_parse_errors(value):
    client = Client()
    client.rows[0]["cells"]["winrate"] = value
    with pytest.raises(SourceError) as error:
        asyncio.run(BlizzardAdapter(client).meta({}))
    assert error.value.code == "PARSE_ERROR"
