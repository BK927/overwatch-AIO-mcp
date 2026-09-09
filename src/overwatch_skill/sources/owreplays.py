"""Public OWReplays JSON adapter, verified against its website on 2026-09-09.

These are website endpoints, not a promised third-party API. Catalog identifiers
are resolved on each cached catalog fetch instead of being hard-coded.
"""

import re
import unicodedata
from datetime import UTC, datetime

from overwatch_skill.models import SourceError, SourceResult

BASE_URL = "https://owreplays.tv"
TIERS = (
    "bronze",
    "silver",
    "gold",
    "platinum",
    "emerald",
    "diamond",
    "master",
    "grandmaster",
    "champion",
)
STATUSES = {
    "unverified",
    "source_reports_expired",
    "user_reported_working",
    "client_verified",
    "needs_recheck",
}


def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text.replace("'", "").replace(".", "")).strip("-")


class OWReplaysAdapter:
    name = "owreplays"
    capabilities = ("replay_search", "replay_details")
    source_regions = ()

    def __init__(self, client):
        self.client = client

    def _error(self, message, code="PARSE_ERROR", url=None):
        return SourceError(code, message, self.name, url or BASE_URL)

    async def _catalog(self):
        fetched = await self.client.get_json(self.name, f"{BASE_URL}/api/v2/gamedata", ttl=86400)
        data = fetched.data
        if not isinstance(data, dict) or any(
            not isinstance(data.get(key), list) or not data[key]
            for key in ("heroes", "maps", "tiers")
        ):
            raise self._error("OWReplays game catalog structure changed.", url=fetched.url)
        for key in ("heroes", "maps", "tiers"):
            if any(not isinstance(row, dict) or "ID" not in row for row in data[key]):
                raise self._error(
                    f"OWReplays {key} catalog identifiers are missing.", url=fetched.url
                )
        return fetched

    def _resolve(self, catalog, kind, value):
        fields = {"heroes": ("hero",), "maps": ("map",), "tiers": ("tier", "tierId", "title")}[kind]
        wanted = slug(value)
        records = [
            row
            for row in catalog[kind]
            if any(slug(row.get(field, "")) == wanted for field in fields)
        ]
        if not records:
            raise self._error(f"Unknown OWReplays {kind} filter: {value}", "UNSUPPORTED_FILTER")
        return records

    def _normalize(self, record, catalog, fetched):
        if not isinstance(record, dict) or not re.fullmatch(
            r"[A-Z0-9]{6}", str(record.get("Code", ""))
        ):
            raise self._error(
                "OWReplays returned a missing or malformed replay code.", url=fetched.url
            )
        hero_ids = record.get("Heroes")
        if not isinstance(hero_ids, list) or not isinstance(record.get("Archived"), bool):
            raise self._error("OWReplays hero or archive fields changed.", url=fetched.url)
        lookups = {
            kind: {row["ID"]: row for row in catalog[kind]} for kind in ("heroes", "maps", "tiers")
        }
        if any(hero_id not in lookups["heroes"] for hero_id in hero_ids):
            raise self._error(
                "Replay contains hero IDs absent from the source catalog.", url=fetched.url
            )
        heroes = [slug(lookups["heroes"][hero_id]["hero"]) for hero_id in hero_ids]
        map_record = lookups["maps"].get(record.get("Map"))
        tier = lookups["tiers"].get(record.get("Tier"))
        if record.get("Map") is not None and map_record is None:
            raise self._error("Replay contains an unknown map ID.", url=fetched.url)
        if record.get("Tier") is not None and tier is None:
            raise self._error("Replay contains an unknown tier ID.", url=fetched.url)
        uploaded = record.get("Uploaded")
        try:
            uploaded_at = (
                datetime.fromtimestamp(uploaded, UTC).isoformat() if uploaded is not None else None
            )
        except (TypeError, ValueError, OSError, OverflowError) as exc:
            raise self._error("Replay upload timestamp is malformed.", url=fetched.url) from exc
        return {
            "code": record["Code"],
            "hero": heroes[0] if len(heroes) == 1 else None,
            "heroes": heroes,
            "map": slug(map_record["map"]) if map_record else None,
            "display_name": record.get("Player"),
            "source_user_handle": record.get("UserHandle"),
            "player_id": None,
            "battle_tag": None,
            "tier_claim": tier.get("title") if tier else None,
            "verified_tier": None,
            "source_reports_verified_tier": record.get("Verified"),
            "platform": record.get("Platform"),
            "uploaded_at": uploaded_at,
            "title": record.get("Title"),
            "description": record.get("Description"),
            "duration": record.get("Duration"),
            "outcome": record.get("Outcome"),
            "source": self.name,
            "source_url": f"{BASE_URL}/{record['Code']}",
            "retrieved_at": fetched.retrieved_at,
            "data_patch": record.get("PatchLevel"),
            "source_updated_at": None,
            "data_period": None,
            "replay_status": "source_reports_expired" if record["Archived"] else "unverified",
            "source_code_verification_status": record.get("CodeVerificationStatus"),
            "source_region": None,
            "player_country": None,
            "leaderboard_region": None,
            "match_server_region": None,
            "checked_at": None,
            "evidence": [],
        }

    async def search(self, filters: dict) -> SourceResult:
        requested = dict(filters)
        allowed = {
            "hero",
            "map",
            "tier",
            "tier_min",
            "player",
            "platform",
            "limit",
            "page",
            "max_pages",
            "after",
            "uploaded_after",
            "playable_status",
            "q",
            "sort",
        }
        unsupported = [
            key for key, value in filters.items() if key not in allowed and value is not None
        ]
        if unsupported:
            raise self._error(
                f"OWReplays does not support filters: {', '.join(unsupported)}",
                "UNSUPPORTED_FILTER",
            )
        try:
            limit = int(filters.get("limit", 20))
            start_page = int(filters.get("page", 1))
            max_pages = int(filters.get("max_pages", 5))
            if not 1 <= limit <= 100 or not 1 <= start_page <= 10000 or not 1 <= max_pages <= 10:
                raise ValueError
            after_value = filters.get("uploaded_after") or filters.get("after")
            after = (
                datetime.fromisoformat(after_value.replace("Z", "+00:00")) if after_value else None
            )
            if after and after.tzinfo is None:
                after = after.replace(tzinfo=UTC)
        except (ValueError, TypeError, AttributeError) as exc:
            raise self._error(
                "Use valid dates and limit 1–100, page 1–10000, max_pages 1–10.", "INVALID_ARGUMENT"
            ) from exc
        statuses = filters.get("playable_status")
        if statuses is not None and (
            not isinstance(statuses, list) or any(status not in STATUSES for status in statuses)
        ):
            raise self._error(
                "playable_status must be a list of known replay states.", "UNSUPPORTED_FILTER"
            )
        catalog_result = await self._catalog()
        catalog = catalog_result.data
        params = {"sort": "Uploaded"}
        applied = {key: value for key, value in filters.items() if value is not None}
        for field, kind, query_key in (
            ("hero", "heroes", "heroes"),
            ("map", "maps", "maps"),
            ("tier", "tiers", "tiers"),
        ):
            if filters.get(field):
                params[query_key] = ",".join(
                    str(row["ID"]) for row in self._resolve(catalog, kind, filters[field])
                )
        if filters.get("tier_min"):
            if filters.get("tier"):
                raise self._error("Use tier or tier_min, not both.", "UNSUPPORTED_FILTER")
            minimum = self._resolve(catalog, "tiers", filters["tier_min"])
            cutoff = min(
                TIERS.index(slug(row["tier"])) * 5 + 5 - int(row["division"]) for row in minimum
            )
            matches = [
                row
                for row in catalog["tiers"]
                if slug(row.get("tier", "")) in TIERS
                and TIERS.index(slug(row["tier"])) * 5 + 5 - int(row["division"]) >= cutoff
            ]
            params["tiers"] = ",".join(str(row["ID"]) for row in matches)
        if filters.get("platform"):
            platform = str(filters["platform"]).lower()
            if platform not in {"pc", "xbox", "playstation", "switch"}:
                raise self._error(
                    "Platform must be pc, xbox, playstation, or switch.", "UNSUPPORTED_FILTER"
                )
            params["platforms"] = platform
            applied["platform"] = platform
        for field in ("player", "q"):
            if filters.get(field):
                params[field] = str(filters[field])
        if filters.get("sort"):
            sorts = {
                "new": "Uploaded",
                "uploaded": "Uploaded",
                "top": "Top",
                "best": "Best",
                "relevant": "Relevant",
            }
            if str(filters["sort"]).lower() not in sorts:
                raise self._error(
                    "Supported sort values: new, top, best, relevant.", "UNSUPPORTED_FILTER"
                )
            params["sort"] = sorts[str(filters["sort"]).lower()]
        rows, seen, warnings = (
            [],
            set(),
            [
                "Public source claims do not verify nationality, match server, player identity, or current in-game playback."
            ],
        )
        all_cached, any_stale = catalog_result.cached, catalog_result.stale
        pages = 0
        fetched = None
        for page in range(start_page, start_page + max_pages):
            fetched = await self.client.get_json(
                self.name, f"{BASE_URL}/api/v2/replays", params={**params, "page": page}, ttl=1800
            )
            all_cached = all_cached and fetched.cached
            any_stale = any_stale or fetched.stale
            body = fetched.data
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("replays"), list)
                or not isinstance(body.get("pages"), int)
                or body["pages"] < 0
            ):
                raise self._error("OWReplays search response structure changed.", url=fetched.url)
            pages = body["pages"]
            for record in body["replays"]:
                row = self._normalize(record, catalog, fetched)
                for query_key, record_key in (
                    ("heroes", "Heroes"),
                    ("maps", "Map"),
                    ("tiers", "Tier"),
                    ("platforms", "Platform"),
                ):
                    if query_key in params:
                        expected = params[query_key].split(",")
                        actual = (
                            record[record_key]
                            if isinstance(record.get(record_key), list)
                            else [record.get(record_key)]
                        )
                        if not set(expected).intersection(str(item) for item in actual):
                            raise self._error(
                                f"OWReplays did not apply the {query_key} filter.", url=fetched.url
                            )
                if after and (
                    not row["uploaded_at"] or datetime.fromisoformat(row["uploaded_at"]) < after
                ):
                    continue
                if statuses is not None and row["replay_status"] not in statuses:
                    continue
                if row["code"] not in seen:
                    rows.append(row)
                    seen.add(row["code"])
            if len(rows) >= limit or page >= pages:
                break
        applied["pages_scanned"] = {"from": start_page, "to": page, "source_pages": pages}
        if page < pages:
            warnings.append(
                f"Search covers source pages {start_page}–{page} of {pages}; additional matches may exist."
            )
        if any_stale:
            warnings.append("A source response or its ID catalog came from stale cache.")
        return SourceResult(
            rows[:limit],
            self.name,
            fetched.url,
            retrieved_at=fetched.retrieved_at,
            requested_filters=requested,
            applied_filters=applied,
            warnings=warnings,
            cached=all_cached,
            stale=any_stale,
        )

    async def get(self, code: str) -> SourceResult:
        normalized = str(code).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{6}", normalized):
            raise self._error("Replay code must contain six letters or digits.", "INVALID_ARGUMENT")
        try:
            fetched = await self.client.get_json(
                self.name, f"{BASE_URL}/api/v2/replay/{normalized}", ttl=1800
            )
        except SourceError as exc:
            # This website uses 403 for a nonexistent replay as well as actual denial.
            # Only its exact observed JSON error is an empty lookup.
            if getattr(exc, "http_status", None) == 403 and getattr(exc, "response_body", None) == {
                "error": "Invalid replay"
            }:
                return SourceResult(
                    None,
                    self.name,
                    f"{BASE_URL}/{normalized}",
                    requested_filters={"code": code},
                    applied_filters={"code": normalized},
                )
            raise
        if isinstance(fetched.data, dict) and fetched.data.get("error") == "Invalid replay":
            return SourceResult(
                None,
                self.name,
                fetched.url,
                retrieved_at=fetched.retrieved_at,
                requested_filters={"code": code},
                applied_filters={"code": normalized},
            )
        catalog = await self._catalog()
        row = self._normalize(fetched.data, catalog.data, fetched)
        if row["code"] != normalized:
            raise self._error(
                "OWReplays returned a different replay than requested.", url=fetched.url
            )
        return SourceResult(
            row,
            self.name,
            row["source_url"],
            retrieved_at=fetched.retrieved_at,
            data_patch=row["data_patch"],
            requested_filters={"code": code},
            applied_filters={"code": normalized},
            cached=fetched.cached and catalog.cached,
            stale=fetched.stale or catalog.stale,
            warnings=[
                "Source verification flags do not establish current in-game playback or player identity."
            ],
        )
