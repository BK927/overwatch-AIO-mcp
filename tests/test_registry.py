from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from overwatch_mcp.db import Repository
from overwatch_mcp.registry import Evidence, Leaderboard, Registry


def evidence(kind, value, *, confidence="verified", day="2026-01-01", source_url=None):
    return {
        "evidence_type": kind,
        "value": value,
        "confidence": confidence,
        "source": "public-proof",
        "source_url": source_url or f"https://example.org/{kind}/{value}",
        "observed_at": day + "T12:00:00+00:00",
    }


def identity_evidence(tag="Player#1234", pid="Player-1234"):
    return [evidence("BATTLETAG", tag), evidence("PROFILE_ID", pid)]


def rank_record(rank=127, *, day="2026-01-01", confidence="verified", season="S20"):
    return {
        "leaderboard_region": "ASIA",
        "role": "TANK",
        "season": season,
        "rank": rank,
        "tier": "GRANDMASTER",
        "observed_at": day + "T12:00:00+00:00",
        "source": "leaderboard",
        "source_url": f"https://example.org/ranking/{day}",
        "confidence": confidence,
    }


def ranker_record(tag="Player#1234", pid="Player-1234"):
    return {
        "battle_tag": tag,
        "overfast_player_id": pid,
        "display_name": "같은닉네임",
        "country": "KR",
        "identity_confidence": "verified",
        "evidence": identity_evidence(tag, pid) + [evidence("PLAYER_COUNTRY", "KR")],
        "leaderboard": [rank_record()],
        "heroes": [{**evidence("HERO_MAIN", "reinhardt"), "hero": "라인"}],
    }


def replay_record(code="ABC123"):
    return {
        "code": code,
        "display_name": "같은닉네임",
        "heroes": ["reinhardt"],
        "tier_claim": "Grandmaster 2",
        "source": "owreplays",
        "source_url": f"https://owreplays.tv/{code}",
        "replay_status": "unverified",
        "match_server_region": None,
        "player_id": None,
    }


def notice(day="2026-01-03", effect="invalidated"):
    return {
        "patch_date": day,
        "source_url": "https://overwatch.blizzard.com/en-us/news/patch-notes/live/" + day,
        "replay_compatibility": {"effect": effect},
    }


@pytest.fixture
def registry(tmp_path):
    repository = Repository(tmp_path / "overwatch.db")
    yield Registry(repository)
    repository.close()


def test_import_search_and_separate_region_meanings(registry):
    player_id = registry.import_rankers([ranker_record()])[0]
    results = registry.rankers(
        {"country": "KR", "hero": "라인", "role": "탱커", "region": "아시아"}
    )
    assert len(results) == 1 and results[0]["id"] == player_id
    assert results[0]["country"] == "KR"
    assert results[0]["leaderboard"][0]["leaderboard_region"] == "ASIA"
    assert results[0]["leaderboard"][0]["role"] == "tank"
    assert results[0]["match_server_region"] is None


@pytest.mark.parametrize(
    "replacement",
    [
        [evidence("BATTLETAG", "Player#1234"), evidence("PROFILE_ID", "Other-5678")],
        [
            evidence("BATTLETAG", "Player#1234"),
            evidence("BATTLETAG", "Player#1234", source_url="https://other.example/proof"),
        ],
        [
            evidence("PUBLIC_ACCOUNT_LINK", "Unrelated-1234"),
            evidence("SELF_IDENTIFICATION", "Unrelated-1234"),
        ],
        [
            evidence("BATTLETAG", "Player#1234"),
            evidence("PROFILE_ID", "Player-1234", confidence="source_claim"),
        ],
    ],
)
def test_verified_identity_requires_two_distinct_matching_proofs(registry, replacement):
    record = ranker_record()
    record["evidence"] = replacement + [evidence("PLAYER_COUNTRY", "KR")]
    with pytest.raises(ValueError):
        registry.import_rankers([record])
    assert registry.repo.counts()["players"] == 0


def test_battletag_and_hex_profile_can_describe_one_verified_account(registry):
    player_id = registry.import_rankers([ranker_record(pid="ABCDEF%7C123ABC")])[0]
    stored = registry.rankers({})[0]
    assert stored["id"] == player_id
    assert stored["battle_tag"] == "Player#1234"
    assert stored["overfast_player_id"] == "ABCDEF%7C123ABC"


def test_battletag_separator_is_canonical_without_lowercasing(registry):
    first = registry.import_rankers([ranker_record(tag="Player-1234")])[0]
    second = registry.import_rankers([ranker_record()])[0]
    assert first == second
    assert registry.rankers({})[0]["battle_tag"] == "Player#1234"


def test_country_needs_matching_proof_and_verified_search_needs_verified_country(registry):
    record = ranker_record()
    record["evidence"] = identity_evidence()
    with pytest.raises(ValueError, match="PLAYER_COUNTRY"):
        registry.import_rankers([record])
    record["evidence"].append(evidence("PLAYER_COUNTRY", "KR", confidence="source_claim"))
    registry.import_rankers([record])
    assert registry.rankers({"country": "KR"}) == []
    assert len(registry.rankers({"country": "KR", "verified_only": False})) == 1


def test_batch_import_rolls_back_if_later_identity_is_invalid(registry):
    invalid = ranker_record("Other#5678", "Other-5678")
    invalid["evidence"][1]["value"] = "Wrong-8765"
    with pytest.raises(ValueError):
        registry.import_rankers([ranker_record(), invalid])
    assert registry.repo.counts()["players"] == 0


def test_equal_nicknames_never_merge_or_link(registry):
    players = registry.import_rankers([ranker_record(), ranker_record("Other#5678", "Other-5678")])
    assert len(set(players)) == 2
    registry.repo.save_replays([replay_record()])
    replay = registry.enrich_replay(registry.repo.replay_rows()[0])
    assert replay["player_id"] is None
    assert replay["player_country"] is None
    assert replay["match_server_region"] is None


def test_latest_rank_precedes_threshold_and_confidence_filters(registry):
    record = ranker_record()
    record["leaderboard"] += [rank_record(600, day="2026-01-02")]
    registry.import_rankers([record])
    assert registry.rankers({"rank_max": 500}) == []
    assert registry.rankers({"rank_max": 1000})[0]["leaderboard"][0]["rank"] == 600
    record["leaderboard"] += [rank_record(100, day="2026-01-03", confidence="source_claim")]
    registry.import_rankers([record])
    assert registry.rankers({"rank_max": 1000}) == []
    assert registry.rankers({"verified_only": False})[0]["leaderboard"][0]["rank"] == 100


def test_explicit_historical_season_remains_queryable(registry):
    record = ranker_record()
    record["leaderboard"] += [rank_record(600, day="2026-01-02", season="S21")]
    registry.import_rankers([record])
    assert registry.rankers({}) == []
    assert registry.rankers({"season": "S20"})[0]["leaderboard"][0]["rank"] == 127


def test_timestamp_validation_and_timezone_normalization():
    value = evidence("BATTLETAG", "Player#1234")
    value["observed_at"] = "2026-01-01T21:00:00+09:00"
    assert Evidence.model_validate(value).observed_at.isoformat() == "2026-01-01T12:00:00+00:00"
    for observed in ("2026-01-01T12:00:00", (datetime.now(UTC) + timedelta(days=1)).isoformat()):
        with pytest.raises(ValidationError):
            Evidence.model_validate({**value, "observed_at": observed})
        with pytest.raises(ValidationError):
            Leaderboard.model_validate({**rank_record(), "observed_at": observed})


def test_partial_import_preserves_omitted_existing_country_and_identity(registry):
    player_id = registry.import_rankers([ranker_record()])[0]
    registry.import_rankers(
        [
            {
                "battle_tag": "Player#1234",
                "display_name": "Updated name",
                "leaderboard": [rank_record(99, day="2026-01-02")],
            }
        ]
    )
    found = registry.rankers({"country": "KR"})[0]
    assert found["id"] == player_id
    assert found["identity_confidence"] == "verified"
    assert found["leaderboard"][0]["rank"] == 99


def test_replay_link_requires_matching_proofs_and_is_transactional(registry):
    player_id = registry.import_rankers([ranker_record()])[0]
    registry.repo.save_replays([replay_record()])
    bad = identity_evidence()
    bad[1]["value"] = "Other-5678"
    with pytest.raises(ValueError):
        registry.add_replay_evidence("ABC123", bad, player_id)
    replay = registry.repo.replay_rows()[0]
    assert replay["player_id"] is None and replay["evidence"] == []
    registry.add_replay_evidence("abc123", identity_evidence(), player_id)
    linked = registry.enrich_replay(registry.repo.replay_rows()[0])
    assert linked["player_id"] == player_id
    assert linked["player_country"] == "KR"
    assert linked["leaderboard_region"] == "ASIA"
    assert linked["match_server_region"] is None


def test_conflicting_explicit_replay_link_is_not_replaced(registry):
    first, second = registry.import_rankers(
        [ranker_record(), ranker_record("Other#5678", "Other-5678")]
    )
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence("ABC123", identity_evidence(), first)
    with pytest.raises(ValueError, match="different explicit"):
        registry.add_replay_evidence(
            "ABC123", identity_evidence("Other#5678", "Other-5678"), second
        )
    assert registry.repo.replay_rows()[0]["player_id"] == first


def test_region_weak_claim_cannot_override_verified_observation(registry):
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence(
        "ABC123",
        [
            evidence("MATCH_SERVER_REGION", "kr"),
            evidence("MATCH_SERVER_REGION", "JP", confidence="source_claim", day="2026-01-02"),
        ],
    )
    result = registry.enrich_replay(registry.repo.replay_rows()[0])
    assert result["match_server_region"] == "KR" and result["region_confidence"] == "verified"
    assert result["player_country"] is None


def test_conflicting_equal_region_proof_remains_unknown(registry):
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence(
        "ABC123", [evidence("MATCH_SERVER_REGION", "KR"), evidence("MATCH_SERVER_REGION", "JP")]
    )
    result = registry.enrich_replay(registry.repo.replay_rows()[0])
    assert result["match_server_region"] is None and result["region_confidence"] == "unknown"
    assert any("conflicts" in warning for warning in result["warnings"])


def test_manual_validation_requires_existing_replay_and_real_observation(registry):
    with pytest.raises(ValueError, match="fetch the replay"):
        registry.validate_replay(
            "ABC123", "client_verified", "operator", "file:proof", "2026-01-01T12:00:00Z"
        )
    registry.repo.save_replays([replay_record()])
    for status, source, reference, timestamp in [
        ("unverified", "operator", "proof", "2026-01-01T12:00:00Z"),
        ("client_verified", " ", "proof", "2026-01-01T12:00:00Z"),
        ("client_verified", "operator", " ", "2026-01-01T12:00:00Z"),
        ("client_verified", "operator", "proof", "2026-01-01T12:00:00"),
    ]:
        with pytest.raises(ValueError):
            registry.validate_replay("ABC123", status, source, reference, timestamp)


def test_full_link_validation_refresh_and_patch_flow_preserves_manual_evidence(registry):
    player_id = registry.import_rankers([ranker_record()])[0]
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence(
        "ABC123", identity_evidence() + [evidence("MATCH_SERVER_REGION", "KR")], player_id
    )
    registry.validate_replay(
        "ABC123",
        "client_verified",
        "local operator",
        "file:successful-import.png",
        "2026-01-02T12:00:00Z",
    )
    registry.repo.save_replays(
        [
            {
                **replay_record(),
                "replay_status": "source_reports_expired",
                "description": "source refreshed",
            }
        ]
    )
    row = registry.enrich_replay(registry.repo.replay_rows("ABC123")[0])
    assert row["player_id"] == player_id
    assert row["replay_status"] == "client_verified"
    assert row["validation"]["source"] == "local operator"
    assert row["match_server_region"] == "KR"
    assert len(row["evidence"]) == 3
    assert registry.apply_patches([notice(effect="unknown")]) == 0
    assert registry.apply_patches([notice()]) == 1
    assert registry.apply_patches([notice()]) == 0
    assert registry.repo.replay_rows()[0]["replay_status"] == "needs_recheck"
    # A post-patch check imported later must beat the recheck status regardless of ingestion date.
    registry.validate_replay(
        "ABC123", "user_reported_working", "player", "report:after-patch", "2026-01-04T12:00:00Z"
    )
    registry.repo.save_replays([replay_record()])
    assert registry.repo.replay_rows()[0]["replay_status"] == "user_reported_working"


def test_late_old_validation_cannot_bypass_known_notice(registry):
    registry.repo.save_replays([replay_record()])
    assert registry.apply_patches([notice()]) == 0
    registry.validate_replay(
        "ABC123", "client_verified", "operator", "proof", "2026-01-01T12:00:00Z"
    )
    assert registry.repo.replay_rows()[0]["replay_status"] == "needs_recheck"
    registry.validate_replay(
        "ABC123", "client_verified", "operator", "newer-proof", "2026-01-05T12:00:00Z"
    )
    registry.validate_replay(
        "ABC123", "user_reported_working", "player", "old-proof", "2026-01-02T12:00:00Z"
    )
    assert registry.repo.replay_rows()[0]["replay_status"] == "client_verified"


def test_same_day_notice_is_conservative_but_older_notice_does_not_invalidate_newer_check(registry):
    registry.repo.save_replays([replay_record()])
    registry.validate_replay(
        "ABC123", "client_verified", "operator", "proof", "2026-01-03T23:59:00Z"
    )
    assert registry.apply_patches([notice("2026-01-02")]) == 0
    assert registry.apply_patches([notice("2026-01-03")]) == 1
    assert registry.repo.replay_rows()[0]["replay_status"] == "needs_recheck"


def test_future_notice_and_missing_source_do_not_mutate_validations(registry):
    registry.repo.save_replays([replay_record()])
    registry.validate_replay(
        "ABC123", "client_verified", "operator", "proof", "2026-01-01T12:00:00Z"
    )
    invalid = notice()
    invalid["source_url"] = None
    for item in [invalid, notice((datetime.now(UTC) + timedelta(days=2)).date().isoformat())]:
        with pytest.raises(ValueError):
            registry.apply_patches([item])
    assert registry.repo.replay_rows()[0]["replay_status"] == "client_verified"


def test_weaker_reimport_does_not_replace_verified_country_evidence(registry):
    record = ranker_record()
    registry.import_rankers([record])
    weaker = deepcopy(record)
    weaker["evidence"][-1]["confidence"] = "source_claim"
    weaker["evidence"][-1]["observed_at"] = "2026-01-02T12:00:00Z"
    registry.import_rankers([weaker])
    assert len(registry.rankers({"country": "KR"})) == 1


def test_attaching_identity_evidence_does_not_implicitly_link_or_verify_replay(registry):
    registry.import_rankers([ranker_record()])
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence(
        "ABC123", identity_evidence() + [evidence("CLIENT_VERIFIED", "ABC123")]
    )
    result = registry.repo.replay_rows()[0]
    assert result["player_id"] is None
    assert result["replay_status"] == "unverified"
    assert "validation" not in result


def test_unverified_player_or_single_proof_cannot_establish_link(registry):
    record = ranker_record()
    record["identity_confidence"] = "source_claim"
    player_id = registry.import_rankers([record])[0]
    registry.repo.save_replays([replay_record()])
    with pytest.raises(ValueError, match="verified stored identity"):
        registry.add_replay_evidence("ABC123", identity_evidence(), player_id)
    registry.import_rankers([ranker_record()])
    with pytest.raises(ValueError, match="two distinct"):
        registry.add_replay_evidence("ABC123", identity_evidence()[:1], player_id)


def test_identifier_conflict_is_rejected_without_rewriting_stored_player(registry):
    registry.import_rankers([ranker_record(pid="ABCDEF%7C123ABC")])
    replacement = ranker_record(pid="999999%7C123ABC")
    with pytest.raises(ValueError, match="replace an existing account"):
        registry.import_rankers([replacement])
    assert registry.rankers({})[0]["overfast_player_id"] == "ABCDEF%7C123ABC"


def test_downgraded_identity_stops_enrichment_from_reusing_earlier_player_data(registry):
    player_id = registry.import_rankers([ranker_record()])[0]
    registry.repo.save_replays([replay_record()])
    registry.add_replay_evidence("ABC123", identity_evidence(), player_id)
    old_enriched = registry.enrich_replay(registry.repo.replay_rows()[0])
    assert old_enriched["player"]["identity_confidence"] == "verified"
    registry.import_rankers(
        [
            {
                "battle_tag": "Player#1234",
                "display_name": "Revoked identity",
                "identity_confidence": "unknown",
            }
        ]
    )
    current = registry.enrich_replay(old_enriched)
    assert "player" not in current
    assert current["player_country"] is None
