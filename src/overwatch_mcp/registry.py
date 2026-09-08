"""Local curator inputs and evidence-aware registry searches. No nickname identity joins."""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import Repository
from .models import SourceError, utcnow

IDENTITY_TYPES = {"BATTLETAG", "PROFILE_ID", "PUBLIC_ACCOUNT_LINK", "SELF_IDENTIFICATION"}
CONFIDENCE = {"unknown": 0, "inferred": 1, "source_claim": 2, "verified": 3}


def _identity_evidence(
    evidence: list[dict], battle_tag: str | None, profile_id: str | None
) -> set[str]:
    """Evidence value names the account; source_url names the page proving the link."""
    from .normalization import normalize_player_id

    expected_tag = normalize_player_id(battle_tag) if battle_tag else None
    expected_profile = normalize_player_id(profile_id) if profile_id else None
    identifiers = {value for value in (expected_tag, expected_profile) if value}
    strong = set()
    for item in evidence:
        kind = item["evidence_type"]
        if kind not in IDENTITY_TYPES or item["confidence"] != "verified":
            continue
        try:
            value = normalize_player_id(item["value"])
        except SourceError:
            value = None
        expected = expected_tag if kind == "BATTLETAG" else expected_profile
        matches = value == expected if kind in ("BATTLETAG", "PROFILE_ID") else value in identifiers
        if not matches or value is None:
            raise ValueError(
                f"Verified {kind} evidence must match a declared target account identifier"
            )
        strong.add(kind)
    return strong


def _code(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", value):
        raise ValueError("Invalid replay code")
    return value


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    evidence_type: str = Field(min_length=1)
    value: str = Field(min_length=1)
    confidence: Literal["verified", "source_claim", "inferred", "unknown"] = "source_claim"
    source: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("evidence_type")
    @classmethod
    def evidence_key(cls, value):
        return value.upper()

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("Evidence timestamps must include a timezone")
        if value > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Evidence cannot have a future observation time")
        return value.astimezone(UTC)


class Leaderboard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    leaderboard_region: str
    role: str
    season: str | None = None
    rank: int = Field(gt=0, strict=True)
    tier: str | None = None
    observed_at: datetime
    source: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    confidence: Literal["verified", "source_claim", "inferred", "unknown"] = "source_claim"

    _timestamp = field_validator("observed_at")(Evidence.timestamp.__func__)


class HeroTag(Evidence):
    hero: str


class RankerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    battle_tag: str | None = None
    overfast_player_id: str | None = None
    display_name: str = Field(min_length=1)
    country: str | None = None
    identity_confidence: Literal["verified", "source_claim", "inferred", "unknown"] = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    leaderboard: list[Leaderboard] = Field(default_factory=list)
    heroes: list[HeroTag] = Field(default_factory=list)


class Registry:
    def __init__(self, repository: Repository):
        self.repo = repository

    def import_rankers(self, records: list[dict]) -> list[int]:
        from .normalization import (
            battle_tag_from_id,
            normalize_hero,
            normalize_player_id,
            normalize_region,
            normalize_role,
            normalize_tier,
        )

        parsed = [RankerRecord.model_validate(row) for row in records]
        result = []
        with self.repo.transaction() as db:
            for row in parsed:
                evidence = [e.model_dump(mode="json") for e in row.evidence]
                tag = (
                    battle_tag_from_id(normalize_player_id(row.battle_tag))
                    if row.battle_tag
                    else None
                )
                if row.battle_tag and not tag:
                    raise ValueError("battle_tag must include its numeric discriminator")
                pid = (
                    normalize_player_id(row.overfast_player_id) if row.overfast_player_id else None
                )
                strong = _identity_evidence(evidence, tag, pid)
                if row.identity_confidence == "verified" and len(strong) < 2:
                    raise ValueError(
                        "Verified identity requires at least two distinct verified identity evidence types matching the account"
                    )
                if row.country and not re.fullmatch(r"[A-Za-z]{2}", row.country):
                    raise ValueError("Country must be a two-letter country code such as KR")
                if row.country and not any(
                    e["evidence_type"] == "PLAYER_COUNTRY"
                    and e["value"].upper() == row.country.upper()
                    for e in evidence
                ):
                    raise ValueError("Country requires matching PLAYER_COUNTRY evidence")
                # A hexadecimal Blizzard ID may coexist with a distinct BattleTag when each has matching evidence.
                if tag and pid and battle_tag_from_id(pid) and normalize_player_id(tag) != pid:
                    raise ValueError("BattleTag and OverFast profile ID disagree")
                now = utcnow()
                existing = db.execute(
                    "SELECT * FROM players WHERE (battle_tag IS NOT NULL AND battle_tag=?) OR (overfast_player_id IS NOT NULL AND overfast_player_id=?)",
                    (tag, pid),
                ).fetchall()
                if len(existing) > 1:
                    raise ValueError("Identifiers refer to two different stored players")
                if existing:
                    previous = existing[0]
                    player_id = previous["id"]
                    if (tag and previous["battle_tag"] and tag != previous["battle_tag"]) or (
                        pid
                        and previous["overfast_player_id"]
                        and pid != previous["overfast_player_id"]
                    ):
                        raise ValueError(
                            "An import cannot silently replace an existing account identifier"
                        )
                    if previous["identity_confidence"] == "verified" and (
                        (tag and not previous["battle_tag"] and "BATTLETAG" not in strong)
                        or (
                            pid
                            and not previous["overfast_player_id"]
                            and "PROFILE_ID" not in strong
                        )
                    ):
                        raise ValueError(
                            "Adding an identifier to a verified identity requires matching verified evidence"
                        )
                    confidence = (
                        row.identity_confidence
                        if "identity_confidence" in row.model_fields_set
                        else previous["identity_confidence"]
                    )
                    country = row.country.upper() if row.country else None
                    if "country" not in row.model_fields_set:
                        country = previous["country"]
                    db.execute(
                        "UPDATE players SET battle_tag=coalesce(?,battle_tag),overfast_player_id=coalesce(?,overfast_player_id),display_name=?,country=?,identity_confidence=?,updated_at=? WHERE id=?",
                        (tag, pid, row.display_name, country, confidence, now, player_id),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO players(battle_tag,overfast_player_id,display_name,country,identity_confidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            tag,
                            pid,
                            row.display_name,
                            row.country.upper() if row.country else None,
                            row.identity_confidence,
                            now,
                            now,
                        ),
                    )
                    player_id = cursor.lastrowid
                for item in evidence:
                    previous = db.execute(
                        "SELECT * FROM player_evidence WHERE player_id=? AND evidence_type=? AND value=? AND source_url=?",
                        (player_id, item["evidence_type"], item["value"], item["source_url"]),
                    ).fetchone()
                    if previous and (
                        CONFIDENCE[previous["confidence"]] > CONFIDENCE[item["confidence"]]
                        or (
                            previous["confidence"] == item["confidence"]
                            and previous["observed_at"] > item["observed_at"]
                        )
                    ):
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO player_evidence(player_id,evidence_type,value,confidence,source,source_url,observed_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            player_id,
                            *[
                                item[k]
                                for k in (
                                    "evidence_type",
                                    "value",
                                    "confidence",
                                    "source",
                                    "source_url",
                                    "observed_at",
                                )
                            ],
                        ),
                    )
                for rank in row.leaderboard:
                    db.execute(
                        "INSERT OR REPLACE INTO leaderboard_snapshots(player_id,leaderboard_region,role,season,rank,tier,observed_at,source,source_url,confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            player_id,
                            normalize_region(rank.leaderboard_region),
                            normalize_role(rank.role),
                            rank.season,
                            rank.rank,
                            normalize_tier(rank.tier) if rank.tier else None,
                            rank.observed_at.isoformat(),
                            rank.source,
                            rank.source_url,
                            rank.confidence,
                        ),
                    )
                for tag in row.heroes:
                    previous = db.execute(
                        "SELECT * FROM player_hero_tags WHERE player_id=? AND hero=? AND source_url=?",
                        (player_id, normalize_hero(tag.hero), tag.source_url),
                    ).fetchone()
                    if previous and (
                        CONFIDENCE[previous["confidence"]] > CONFIDENCE[tag.confidence]
                        or (
                            previous["confidence"] == tag.confidence
                            and previous["observed_at"] > tag.observed_at.isoformat()
                        )
                    ):
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO player_hero_tags VALUES (?,?,?,?,?,?,?)",
                        (
                            player_id,
                            normalize_hero(tag.hero),
                            tag.evidence_type,
                            tag.confidence,
                            tag.source,
                            tag.source_url,
                            tag.observed_at.isoformat(),
                        ),
                    )
                result.append(player_id)
        return result

    def rankers(self, filters: dict) -> list[dict]:
        from .normalization import normalize_hero, normalize_region, normalize_role

        region = normalize_region(filters["region"]) if filters.get("region") else None
        role = normalize_role(filters["role"]) if filters.get("role") else None
        hero = normalize_hero(filters["hero"]) if filters.get("hero") else None
        country = filters.get("country", "") or ""
        results = []
        with self.repo._lock:
            players = self.repo.conn.execute(
                "SELECT * FROM players ORDER BY display_name"
            ).fetchall()
            for player in players:
                item = dict(player)
                evidence = [
                    dict(row)
                    for row in self.repo.conn.execute(
                        "SELECT * FROM player_evidence WHERE player_id=?", (item["id"],)
                    )
                ]
                verified = filters.get("verified_only", True)
                if verified and item["identity_confidence"] != "verified":
                    continue
                if country and item["country"] != country.upper():
                    continue
                if country and verified:
                    observed_country, country_confidence = self._strongest_evidence(
                        evidence, "PLAYER_COUNTRY"
                    )
                    if observed_country != country.upper() or country_confidence != "verified":
                        continue
                tags = [
                    dict(row)
                    for row in self.repo.conn.execute(
                        "SELECT * FROM player_hero_tags WHERE player_id=?", (item["id"],)
                    )
                ]
                if hero and not any(
                    t["hero"] == hero and (not verified or t["confidence"] == "verified")
                    for t in tags
                ):
                    continue
                ranks = self.repo.conn.execute(
                    "SELECT * FROM leaderboard_snapshots WHERE player_id=? ORDER BY observed_at DESC,id DESC",
                    (item["id"],),
                ).fetchall()
                seen = set()
                selected = []
                for rank in ranks:
                    if (
                        region
                        and rank["leaderboard_region"] != region
                        or role
                        and rank["role"] != role
                    ):
                        continue
                    if filters.get("season") and rank["season"] != filters["season"]:
                        continue
                    context = (rank["leaderboard_region"], rank["role"])
                    if context in seen:
                        continue
                    seen.add(context)
                    if (verified and rank["confidence"] != "verified") or rank[
                        "rank"
                    ] > filters.get("rank_max", 500):
                        continue
                    selected.append(dict(rank))
                if not selected:
                    continue
                item.update(
                    evidence=evidence, heroes=tags, leaderboard=selected, match_server_region=None
                )
                item["warnings"] = [
                    "Stored observed rankings are not a complete or live Top 500 leaderboard."
                ]
                results.append(item)
        results.sort(key=lambda r: min(s["rank"] for s in r["leaderboard"]))
        return results[: filters.get("limit", 20)]

    def add_replay_evidence(self, code: str, evidence: list[dict], player_id: int | None = None):
        code = _code(code)
        parsed = [Evidence.model_validate(item) for item in evidence]
        with self.repo.transaction() as db:
            replay = db.execute("SELECT * FROM replays WHERE code=?", (code,)).fetchone()
            if not replay:
                raise ValueError("Import or fetch the replay before attaching evidence")
            if player_id is not None:
                player = db.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
                if not player or player["identity_confidence"] != "verified":
                    raise ValueError("A replay can link only to a verified stored identity")
                if replay["player_id"] is not None and replay["player_id"] != player_id:
                    raise ValueError("The replay already has a different explicit player link")
                strong = _identity_evidence(
                    [e.model_dump(mode="json") for e in parsed],
                    player["battle_tag"],
                    player["overfast_player_id"],
                )
                if len(strong) < 2:
                    raise ValueError(
                        "Replay identity linking requires two distinct verified identity evidence types"
                    )
                db.execute("UPDATE replays SET player_id=? WHERE code=?", (player_id, code.upper()))
            for item in parsed:
                value = (
                    item.value.upper()
                    if item.evidence_type
                    in ("PLAYER_COUNTRY", "MATCH_SERVER_REGION", "LEADERBOARD_REGION")
                    else item.value
                )
                db.execute(
                    "INSERT OR IGNORE INTO replay_evidence(code,evidence_type,value,confidence,source,source_url,observed_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        code,
                        item.evidence_type,
                        value,
                        item.confidence,
                        item.source,
                        item.source_url,
                        item.observed_at.isoformat(),
                    ),
                )

    def validate_replay(
        self, code: str, status: str, source: str, evidence_ref: str, observed_at: str
    ):
        code = _code(code)
        if status not in ("user_reported_working", "client_verified"):
            raise ValueError("Invalid replay code or validation status")
        timestamp = Evidence.timestamp(datetime.fromisoformat(observed_at))
        if not source.strip() or not evidence_ref.strip():
            raise ValueError("A named reporter and a concrete evidence reference are required")
        with self.repo.transaction() as db:
            if not db.execute("SELECT 1 FROM replays WHERE code=?", (code,)).fetchone():
                raise ValueError("Import or fetch the replay before recording its validation")
            db.execute(
                "INSERT INTO replay_validations(code,status,observed_at,source,evidence_ref) VALUES (?,?,?,?,?)",
                (code, status, timestamp.isoformat(), source.strip(), evidence_ref.strip()),
            )
            self._recheck_known_notices(db)

    def apply_patches(self, patches: list[dict]) -> int:
        with self.repo.transaction() as db:
            for patch in patches:
                if (patch.get("replay_compatibility") or {}).get("effect") != "invalidated":
                    continue
                patch_date = date.fromisoformat(patch["patch_date"])
                if patch_date > datetime.now(UTC).date():
                    raise ValueError(
                        "A future patch notice cannot invalidate current replay evidence"
                    )
                source_url = patch.get("source_url")
                if not isinstance(source_url, str) or not source_url.strip():
                    raise ValueError("An invalidation notice requires its official source URL")
                key = source_url + "#" + patch_date.isoformat()
                db.execute(
                    "INSERT OR IGNORE INTO compatibility_notices VALUES (?,?,?,?)",
                    (key, patch_date.isoformat(), source_url, utcnow()),
                )
            return self._recheck_known_notices(db)

    @staticmethod
    def _recheck_known_notices(db) -> int:
        notice = db.execute(
            "SELECT * FROM compatibility_notices ORDER BY patch_date DESC,notice_key LIMIT 1"
        ).fetchone()
        if not notice:
            return 0
        changed = 0
        seen = set()
        for validation in db.execute(
            "SELECT * FROM replay_validations ORDER BY observed_at DESC,id DESC"
        ).fetchall():
            code = validation["code"]
            if code in seen:
                continue
            seen.add(code)
            if validation["status"] not in ("client_verified", "user_reported_working"):
                continue
            # Date-only notices cannot prove whether a same-day check preceded deployment.
            observed_day = datetime.fromisoformat(validation["observed_at"]).astimezone(UTC).date()
            if observed_day <= date.fromisoformat(notice["patch_date"]):
                # Supersede the original observation, so late notice ingestion cannot outrank a later actual check.
                db.execute(
                    "INSERT INTO replay_validations(code,status,observed_at,source,evidence_ref) VALUES (?,?,?,?,?)",
                    (
                        code,
                        "needs_recheck",
                        validation["observed_at"],
                        "blizzard_patches",
                        notice["notice_key"],
                    ),
                )
                changed += 1
        return changed

    @staticmethod
    def _strongest_evidence(evidence: list[dict], kind: str) -> tuple[str | None, str]:
        candidates = [
            item
            for item in evidence
            if item.get("evidence_type") == kind
            and item.get("value")
            and item.get("confidence") in CONFIDENCE
        ]
        if not candidates:
            return None, "unknown"
        best = max(CONFIDENCE[item["confidence"]] for item in candidates)
        candidates = [item for item in candidates if CONFIDENCE[item["confidence"]] == best]
        latest = max(item.get("observed_at", "") for item in candidates)
        candidates = [item for item in candidates if item.get("observed_at", "") == latest]
        values = {item["value"].upper() for item in candidates}
        if len(values) != 1 or best == 0:
            return None, "unknown"
        return values.pop(), candidates[0]["confidence"]

    def enrich_replay(self, replay: dict) -> dict:
        record = dict(replay)
        record.pop("player", None)
        record.setdefault("warnings", [])
        record["warnings"] = list(record["warnings"] or [])
        record["player_country"] = None
        record["player_country_confidence"] = "unknown"
        record["leaderboard_region"] = None
        record["match_server_region"], record["region_confidence"] = self._strongest_evidence(
            record.get("evidence", []), "MATCH_SERVER_REGION"
        )
        if (
            any(
                item.get("evidence_type") == "MATCH_SERVER_REGION"
                for item in record.get("evidence", [])
            )
            and record["match_server_region"] is None
        ):
            record["warnings"].append(
                "Match server region evidence is unknown or conflicts at the same confidence and observation time."
            )
        if record.get("player_id"):
            with self.repo._lock:
                player = self.repo.conn.execute(
                    "SELECT * FROM players WHERE id=?", (record["player_id"],)
                ).fetchone()
                if player and player["identity_confidence"] == "verified":
                    record["player"] = dict(player)
                    evidence = [
                        dict(row)
                        for row in self.repo.conn.execute(
                            "SELECT * FROM player_evidence WHERE player_id=?", (player["id"],)
                        )
                    ]
                    country, confidence = self._strongest_evidence(evidence, "PLAYER_COUNTRY")
                    if confidence == "verified" and country == player["country"]:
                        record["player_country"] = country
                        record["player_country_confidence"] = "verified"
                    ranks = self.repo.conn.execute(
                        "SELECT * FROM leaderboard_snapshots WHERE player_id=? ORDER BY observed_at DESC,id DESC",
                        (player["id"],),
                    ).fetchall()
                    seen = set()
                    regions = set()
                    for rank in ranks:
                        context = (rank["leaderboard_region"], rank["role"])
                        if context in seen:
                            continue
                        seen.add(context)
                        if rank["confidence"] == "verified":
                            regions.add(rank["leaderboard_region"])
                    if len(regions) == 1:
                        record["leaderboard_region"] = regions.pop()
        if record["player_country"] == "KR" and record["match_server_region"] is None:
            record["warnings"].append(
                "한국 플레이어로 확인되었지만 해당 경기가 한국 서버에서 진행됐는지는 확인되지 않았습니다."
            )
        record["warnings"].append(
            "Replay validity is recorded evidence, not a live game-client check."
        )
        if record.get("validation"):
            record["checked_at"] = record["validation"]["observed_at"]
        record["warnings"] = list(dict.fromkeys(record["warnings"]))
        return record
