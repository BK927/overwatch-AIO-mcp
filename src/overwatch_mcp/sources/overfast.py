"""OverFast 4.x public catalog, percentage statistics, and player adapter.

Contract: https://overfast-api.tekrop.fr/openapi.json (verified 2026-09-09).
The API already returns percentages; a value such as 0.5 means 0.5%, not 50%.
"""

import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

from ..models import SourceError, SourceResult
from ..normalization import (
    TIERS,
    battle_tag_from_id,
    normalize_hero,
    normalize_map,
    normalize_mode,
    normalize_platform,
    normalize_player_id,
    normalize_region,
    normalize_role,
    normalize_tier,
)

BASE_URL = "https://overfast-api.tekrop.fr"
LOCALES = {
    "de-de",
    "en-gb",
    "en-us",
    "es-es",
    "es-mx",
    "fr-fr",
    "it-it",
    "ja-jp",
    "ko-kr",
    "pl-pl",
    "pt-br",
    "ru-ru",
    "zh-tw",
}
MAP_MODES = {
    "assault",
    "capture-the-flag",
    "clash",
    "control",
    "deathmatch",
    "elimination",
    "escort",
    "flashpoint",
    "hybrid",
    "payload-race",
    "practice-range",
    "push",
    "team-deathmatch",
    "workshop",
}


class OverFastAdapter:
    name = "overfast"
    capabilities = {
        "capabilities": ["heroes", "maps", "modes", "player_search", "player_stats", "hero_meta"],
        "regions": ["ASIA", "AMERICAS", "EUROPE"],
        "source_regions": ["ASIA", "AMERICAS", "EUROPE"],
        "tiers": list(TIERS[:-1]),
        "rate_unit": "percent",
        "notes": [
            "GRANDMASTER statistics include CHAMPION.",
            "Statistics are Role Queue aggregates; ASIA does not identify a Korean match server.",
        ],
    }

    def __init__(self, client):
        self.client = client

    def _error(self, code: str, message: str, url: str | None = None):
        return SourceError(code, message, self.name, url or BASE_URL)

    def _filters(self, filters: dict, allowed: set[str]):
        unsupported = sorted(k for k, v in filters.items() if v is not None and k not in allowed)
        if unsupported:
            raise self._error(
                "UNSUPPORTED_FILTER", f"Unsupported filters: {', '.join(unsupported)}"
            )
        if filters.get("source") not in (None, "auto", self.name):
            raise self._error("UNSUPPORTED_FILTER", "The selected source is not OverFast.")

    def _result(self, fetched, data, requested, applied, warnings=None, source_updated_at=None):
        return SourceResult(
            data=data,
            source=self.name,
            source_url=fetched.url,
            retrieved_at=fetched.retrieved_at,
            source_updated_at=source_updated_at,
            requested_filters=dict(requested),
            applied_filters=applied,
            warnings=warnings or [],
            cached=fetched.cached,
            stale=fetched.stale,
        )

    def _object(self, value: Any, url: str) -> dict:
        if not isinstance(value, dict):
            raise self._error("PARSE_ERROR", "Expected a JSON object from OverFast.", url)
        return value

    def _list(self, value: Any, url: str) -> list[dict]:
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise self._error("PARSE_ERROR", "Expected a JSON array of objects from OverFast.", url)
        return value

    async def health_check(self) -> SourceResult:
        return await self.catalog({"type": "heroes", "locale": "en-us"})

    async def catalog(self, filters: dict) -> SourceResult:
        self._filters(filters, {"type", "hero", "locale", "mode", "role", "source"})
        kind = str(filters.get("type") or "heroes").lower()
        locale = str(filters.get("locale") or "ko-KR").lower()
        if locale not in LOCALES:
            raise self._error("UNSUPPORTED_FILTER", f"Unsupported locale: {locale}")
        applied = {"type": kind}
        warnings: list[str] = []
        params: dict[str, Any] = {}
        if kind == "heroes":
            params["locale"] = locale
            applied["locale"] = locale
            if filters.get("hero"):
                if filters.get("role") or filters.get("mode"):
                    raise self._error(
                        "UNSUPPORTED_FILTER", "Hero detail does not accept role or mode filters."
                    )
                hero = normalize_hero(filters["hero"])
                if hero == "all-heroes":
                    raise self._error("UNSUPPORTED_FILTER", "Hero detail requires a single hero.")
                applied["hero"] = hero
                path = f"/heroes/{quote(hero, safe='')}"
            else:
                path = "/heroes"
                if filters.get("role"):
                    params["role"] = applied["role"] = normalize_role(filters["role"])
                if filters.get("mode"):
                    mode = str(filters["mode"]).lower()
                    if mode != "stadium":
                        mode = normalize_mode(mode)
                    if mode not in ("quickplay", "stadium"):
                        raise self._error(
                            "UNSUPPORTED_FILTER",
                            "Hero catalog supports quickplay and stadium, not a competitive season roster.",
                        )
                    params["gamemode"] = mode
                    applied["mode"] = mode
        elif kind == "maps":
            if filters.get("hero") or filters.get("role"):
                raise self._error(
                    "UNSUPPORTED_FILTER", "Map catalog cannot filter by hero or role."
                )
            path = "/maps"
            applied["locale"] = "en-us"
            if locale != "en-us":
                warnings.append(
                    "OverFast map names are provided in English; the requested locale is unavailable for this catalog."
                )
            if filters.get("mode"):
                mode = str(filters["mode"]).lower().replace("_", "-")
                if mode not in MAP_MODES:
                    raise self._error(
                        "UNSUPPORTED_FILTER",
                        "Map catalog requires a map mode such as escort or control; competitive map-pool filtering is unavailable.",
                    )
                params["gamemode"] = mode
                applied["mode"] = mode
        elif kind == "modes":
            if any(filters.get(k) for k in ("hero", "role", "mode")):
                raise self._error(
                    "UNSUPPORTED_FILTER",
                    "Mode catalog does not support hero, role, or mode filters.",
                )
            path = "/gamemodes"
            applied["locale"] = "en-us"
            if locale != "en-us":
                warnings.append("OverFast map-mode names are provided in English.")
        elif kind in ("tiers", "regions", "filters"):
            if any(filters.get(k) for k in ("hero", "role", "mode")):
                raise self._error(
                    "UNSUPPORTED_FILTER",
                    "This filter catalog does not accept hero, role, or mode filters.",
                )
            available = {
                "tiers": [
                    {
                        "key": t,
                        "supported": t != "CHAMPION",
                        "includes": ["GRANDMASTER", "CHAMPION"] if t == "GRANDMASTER" else [t],
                    }
                    for t in TIERS
                ],
                "regions": [
                    {"key": r, "source_region": r, "match_server_region": None}
                    for r in self.capabilities["regions"]
                ],
                "platforms": ["pc", "console"],
                "modes": ["competitive", "quickplay"],
                "roles": ["tank", "damage", "support"],
                "map_modes": sorted(MAP_MODES),
                "locales": sorted(LOCALES),
                "rate_unit": "percent",
                "queue": "ROLE_QUEUE",
                "allow_region_fallback": {"default": False, "KR": "ASIA"},
                "champion_filter": "Unavailable separately; GRANDMASTER includes CHAMPION.",
                "scope": "Adapter-supported filters, verified against OverFast 4.12.0 on 2026-09-09.",
            }
            return SourceResult(
                data=available if kind == "filters" else available[kind],
                source=self.name,
                source_url=f"{BASE_URL}/openapi.json",
                requested_filters=dict(filters),
                applied_filters=applied,
            )
        else:
            raise self._error("UNSUPPORTED_FILTER", f"Unsupported catalog type: {kind}")
        fetched = await self.client.get_json(self.name, BASE_URL + path, params=params, ttl=86400)
        data = (
            self._object(fetched.data, fetched.url)
            if kind == "heroes" and filters.get("hero")
            else self._list(fetched.data, fetched.url)
        )
        if isinstance(data, list) and any(not isinstance(row.get("key"), str) for row in data):
            raise self._error("PARSE_ERROR", "Catalog item is missing its identifier.", fetched.url)
        return self._result(fetched, data, filters, applied, warnings)

    async def meta(self, filters: dict) -> SourceResult:
        self._filters(
            filters,
            {
                "view",
                "platform",
                "mode",
                "region",
                "tier",
                "map",
                "role",
                "heroes",
                "source",
                "allow_region_fallback",
                "order_by",
            },
        )
        if filters.get("view") not in (None, "heroes"):
            raise self._error(
                "UNSUPPORTED_FILTER",
                "This adapter fetches one heroes filter group; comparisons and history belong to the service.",
            )
        platform = normalize_platform(filters.get("platform") or "pc")
        mode = normalize_mode(filters.get("mode") or "competitive")
        region = normalize_region(filters.get("region") or "ASIA")
        tier = normalize_tier(filters.get("tier") or "ALL")
        map_key = normalize_map(filters.get("map") or "all-maps")
        warnings = [
            "Only Role Queue aggregates are available. Source regions do not identify the server of any particular match."
        ]
        fallback = filters.get("allow_region_fallback", False)
        if not isinstance(fallback, bool):
            raise self._error("UNSUPPORTED_FILTER", "allow_region_fallback must be a boolean.")
        if region == "KR":
            if not fallback:
                raise self._error(
                    "UNSUPPORTED_FILTER",
                    "OverFast has no Korean-only statistics. Set allow_region_fallback=true to request explicitly labeled ASIA data.",
                )
            region = "ASIA"
            warnings.append(
                "한국 전용 데이터가 없어 아시아 데이터를 대신 반환했습니다. Requested region=KR; applied region=ASIA. This is not Korean server statistics."
            )
        if tier == "CHAMPION":
            raise self._error(
                "UNSUPPORTED_FILTER",
                "OverFast cannot isolate CHAMPION. Request GRANDMASTER explicitly to retrieve its combined GRANDMASTER and CHAMPION group.",
            )
        if tier != "ALL" and mode != "competitive":
            raise self._error(
                "UNSUPPORTED_FILTER", "Competitive tier cannot be applied to quickplay statistics."
            )
        if tier == "GRANDMASTER":
            warnings.append(
                "OverFast GRANDMASTER statistics include CHAMPION; the ranks cannot be separated."
            )
        applied: dict[str, Any] = {
            "platform": platform,
            "mode": mode,
            "region": region,
            "tier": tier,
            "map": map_key,
            "queue": "ROLE_QUEUE",
        }
        params: dict[str, Any] = {"platform": platform, "gamemode": mode, "region": region.lower()}
        if tier != "ALL":
            params["competitive_division"] = tier.lower()
        if map_key != "all-maps":
            params["map"] = map_key
        if filters.get("role"):
            params["role"] = applied["role"] = normalize_role(filters["role"])
        hero_values = filters.get("heroes")
        if hero_values is not None and (
            not isinstance(hero_values, list) or len(hero_values) > 100
        ):
            raise self._error(
                "UNSUPPORTED_FILTER", "heroes must be an array with at most 100 hero identifiers."
            )
        heroes = list(dict.fromkeys(normalize_hero(h) for h in hero_values)) if hero_values else []
        if "all-heroes" in heroes:
            raise self._error("UNSUPPORTED_FILTER", "Use an omitted heroes filter for all heroes.")
        if heroes:
            applied["heroes"] = heroes
        order_by = filters.get("order_by") or "winrate:desc"
        if not isinstance(order_by, str) or not re.fullmatch(
            r"(hero|winrate|pickrate|banrate):(asc|desc)", order_by
        ):
            raise self._error(
                "UNSUPPORTED_FILTER", "order_by must be hero/winrate/pickrate/banrate:asc/desc."
            )
        params["order_by"] = order_by
        applied["order_by"] = order_by
        fetched = await self.client.get_json(
            self.name, BASE_URL + "/heroes/stats", params=params, ttl=3600
        )
        raw = self._list(fetched.data, fetched.url)
        data = []
        seen = set()
        for item in raw:
            if not isinstance(item.get("hero"), str):
                raise self._error(
                    "PARSE_ERROR", "Hero statistics lack a hero identifier.", fetched.url
                )
            hero = normalize_hero(item["hero"])
            if hero in seen:
                raise self._error(
                    "PARSE_ERROR", "OverFast returned duplicate hero statistics.", fetched.url
                )
            seen.add(hero)
            if heroes and hero not in heroes:
                continue
            record = {"hero": hero}
            for metric in ("pickrate", "winrate", "banrate"):
                value = item.get(metric)
                if metric != "banrate" and metric not in item:
                    raise self._error(
                        "PARSE_ERROR", f"Missing {metric} in hero statistics.", fetched.url
                    )
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0 <= value <= 100
                ):
                    raise self._error(
                        "PARSE_ERROR",
                        f"Invalid percent value for {metric}: expected 0–100 or null.",
                        fetched.url,
                    )
                record[metric] = value
            zeros = [metric for metric in ("pickrate", "winrate", "banrate") if record[metric] == 0]
            record.update(
                {
                    "rate_unit": "percent",
                    "source_region": region,
                    "match_server_region": None,
                    "sample_size": None,
                    "zero_value_caution": zeros,
                }
            )
            data.append(record)
        missing = [hero for hero in heroes if hero not in seen]
        if missing:
            warnings.append(
                f"No source statistics matched: {', '.join(missing)}. No values were fabricated."
            )
        if any(row["zero_value_caution"] for row in data):
            warnings.append(
                "OverFast may encode unavailable Blizzard rates as 0.0. Zero values are source values, not proof of a true 0% rate; sample sizes are unknown."
            )
        # Sort ourselves as well, so local hero subsets and recorded ranks are deterministic.
        metric, direction = order_by.split(":")
        available_rows = [row for row in data if row.get(metric) is not None]
        absent_rows = [row for row in data if row.get(metric) is None]
        data = (
            sorted(available_rows, key=lambda row: row[metric], reverse=direction == "desc")
            + absent_rows
        )
        for index, row in enumerate(data, 1):
            row["ranking"] = index if row.get(metric) is not None else None
            row["ranking_scope"] = "returned_heroes"
            row["ranking_metric"] = metric
        return self._result(fetched, data, filters, applied, warnings)

    async def players_search(self, filters: dict) -> SourceResult:
        self._filters(filters, {"query", "limit", "offset", "order_by", "source"})
        query = normalize_player_id(filters.get("query", ""))
        limit, offset = filters.get("limit", 10), filters.get("offset", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise self._error("UNSUPPORTED_FILTER", "limit must be an integer from 1 to 100.")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise self._error("UNSUPPORTED_FILTER", "offset must be an integer from 0 to 10000.")
        order_by = filters.get("order_by") or "name:asc"
        if not isinstance(order_by, str) or not re.fullmatch(
            r"(player_id|name|last_updated_at):(asc|desc)", order_by
        ):
            raise self._error("UNSUPPORTED_FILTER", "Unsupported player search ordering.")
        params = {"name": query, "limit": limit, "offset": offset, "order_by": order_by}
        fetched = await self.client.get_json(
            self.name, BASE_URL + "/players", params=params, ttl=600
        )
        raw = self._object(fetched.data, fetched.url)
        rows = self._list(raw.get("results"), fetched.url)
        if not isinstance(raw.get("total"), int) or raw["total"] < len(rows):
            raise self._error("PARSE_ERROR", "Invalid player search result total.", fetched.url)
        data = []
        for row in rows[:limit]:
            if not isinstance(row.get("player_id"), str) or not isinstance(row.get("name"), str):
                raise self._error(
                    "PARSE_ERROR", "Player search record lacks its name or identifier.", fetched.url
                )
            data.append(
                {
                    **row,
                    "battle_tag": battle_tag_from_id(row["player_id"]),
                    "source": self.name,
                    "source_url": row.get("career_url") or fetched.url,
                }
            )
        return self._result(
            fetched,
            data,
            filters,
            {"query": query, "limit": limit, "offset": offset, "order_by": order_by},
        )

    async def _private_profile(self, player_id: str) -> bool:
        """An exact public search record is evidence; an empty stats object is not."""
        try:
            result = await self.players_search({"query": player_id, "limit": 100})
        except SourceError:
            return False
        return any(
            row["player_id"] == player_id and row.get("is_public") is False for row in result.data
        )

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(value, UTC).isoformat()
        except (OverflowError, ValueError, OSError):
            return None

    @staticmethod
    def _has_stats(value: Any) -> bool:
        if isinstance(value, dict):
            return any(OverFastAdapter._has_stats(v) for v in value.values())
        if isinstance(value, list):
            return any(OverFastAdapter._has_stats(v) for v in value)
        return value is not None

    def _validate_player_rates(self, value: Any, url: str):
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if key in ("winrate", "win_percentage") and child is not None:
                if (
                    isinstance(child, bool)
                    or not isinstance(child, (int, float))
                    or not math.isfinite(child)
                    or not 0 <= child <= 100
                ):
                    raise self._error("PARSE_ERROR", f"Invalid player percentage: {key}.", url)
            elif isinstance(child, dict):
                self._validate_player_rates(child, url)

    async def player_get(self, filters: dict) -> SourceResult:
        self._filters(filters, {"player_id", "view", "platform", "mode", "hero", "role", "source"})
        player_id = normalize_player_id(filters.get("player_id", ""))
        view = filters.get("view") or "summary"
        if view not in ("summary", "career", "hero", "roles"):
            raise self._error("UNSUPPORTED_FILTER", f"Unsupported player view: {view}")
        platform = normalize_platform(filters.get("platform") or "pc")
        mode = normalize_mode(filters.get("mode") or "competitive")
        hero = normalize_hero(filters["hero"]) if filters.get("hero") else None
        role = normalize_role(filters["role"]) if filters.get("role") else None
        if view == "hero" and not hero:
            raise self._error("UNSUPPORTED_FILTER", "The hero view requires a hero.")
        if hero == "all-heroes" and view != "career":
            raise self._error(
                "UNSUPPORTED_FILTER",
                "all-heroes is only a career filter; omit hero for summary totals.",
            )
        if role and view != "roles":
            raise self._error("UNSUPPORTED_FILTER", "The role filter requires view=roles.")
        if hero and view == "roles":
            raise self._error(
                "UNSUPPORTED_FILTER", "A hero filter cannot be applied to role aggregates."
            )
        applied = {"player_id": player_id, "view": view, "platform": platform, "mode": mode}
        if hero:
            applied["hero"] = hero
        if role:
            applied["role"] = role
        # Preserve encoded hexadecimal Blizzard IDs without double-encoding their %7C.
        player_url = BASE_URL + "/players/" + quote(unquote(player_id), safe="")
        params = {"platform": platform, "gamemode": mode}
        path = "/stats/career" if view == "career" else "/stats/summary"
        if view == "career" and hero:
            params["hero"] = hero
        try:
            fetched = await self.client.get_json(
                self.name, player_url + path, params=params, ttl=600
            )
        except SourceError as exc:
            if getattr(exc, "http_status", None) in (403, 404) and await self._private_profile(
                player_id
            ):
                raise self._error(
                    "PRIVATE_PROFILE",
                    "An exact OverFast search record marks this profile private.",
                    player_url,
                ) from exc
            raise
        raw = self._object(fetched.data, fetched.url)
        if raw.get("is_public") is False:
            raise self._error(
                "PRIVATE_PROFILE", "OverFast explicitly marks this profile private.", fetched.url
            )
        self._validate_player_rates(raw, fetched.url)
        warnings = []
        if view == "career":
            data = {hero: raw[hero]} if hero and raw.get(hero) is not None else {} if hero else raw
        else:
            if not all(key in raw for key in ("general", "roles", "heroes")):
                raise self._error(
                    "PARSE_ERROR",
                    "Player stats summary lacks the documented general/roles/heroes fields.",
                    fetched.url,
                )
            if raw["general"] is not None and not isinstance(raw["general"], dict):
                raise self._error("PARSE_ERROR", "Invalid player general statistics.", fetched.url)
            if raw["roles"] is not None and not isinstance(raw["roles"], dict):
                raise self._error("PARSE_ERROR", "Invalid player role statistics.", fetched.url)
            if raw["heroes"] is not None and not isinstance(raw["heroes"], dict):
                raise self._error("PARSE_ERROR", "Invalid player hero statistics.", fetched.url)
            for section in ("roles", "heroes"):
                if any(
                    value is not None and not isinstance(value, dict)
                    for value in (raw[section] or {}).values()
                ):
                    raise self._error(
                        "PARSE_ERROR", f"Invalid player {section} statistic entry.", fetched.url
                    )
            if hero:
                data = (raw.get("heroes") or {}).get(hero)
            elif view == "roles":
                data = (raw.get("roles") or {}).get(role) if role else raw.get("roles")
            else:
                data = raw
        if not self._has_stats(data):
            if await self._private_profile(player_id):
                raise self._error(
                    "PRIVATE_PROFILE",
                    "An exact OverFast search record marks this profile private.",
                    player_url,
                )
            data = None
            warnings.append(
                "No statistics are available for these filters. This alone does not establish that the profile is private."
            )
        updated_at = None
        if view == "summary":
            summary = await self.client.get_json(self.name, player_url + "/summary", ttl=600)
            profile = dict(self._object(summary.data, summary.url))
            if not isinstance(profile.get("username"), str):
                raise self._error("PARSE_ERROR", "Player profile lacks its username.", summary.url)
            if profile.get("is_public") is False:
                raise self._error(
                    "PRIVATE_PROFILE",
                    "OverFast explicitly marks this profile private.",
                    summary.url,
                )
            competitive = profile.get("competitive")
            if isinstance(competitive, dict):
                profile["competitive"] = {platform: competitive.get(platform)}
            updated_at = self._timestamp(profile.get("last_updated_at"))
            data = {
                "player_id": player_id,
                "profile": profile,
                "statistics": data,
                "profile_source_url": summary.url,
                "statistics_source_url": fetched.url,
            }
            applied["mode_filter_scope"] = "statistics"
            if hero:
                applied["hero_filter_scope"] = "statistics"
            result = self._result(fetched, data, filters, applied, warnings, updated_at)
            result.stale = fetched.stale or summary.stale
            result.cached = fetched.cached and summary.cached
            return result
        return self._result(fetched, data, filters, applied, warnings, updated_at)
