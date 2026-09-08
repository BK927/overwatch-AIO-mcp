"""Read OWTICS' public server-rendered data without executing website JavaScript.

The React Router hydration and GraphQL-shaped payload were checked on 2026-09-09.
Filter echoes and per-measurement regions are checked, not inferred from the URL.
"""

import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import SourceError, SourceResult
from ..normalization import normalize_hero, normalize_map

BASE_URL = "https://owtics.gg/en-US"
_ENQUEUE = re.compile(r'window\.__reactRouterContext\.streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)')
_REGIONS = {"KR": "KOREA", "KOREA": "KOREA", "ASIA": "ASIA", "AMERICAS": "AMER", "AMER": "AMER", "EUROPE": "EU", "EU": "EU", "CHINA": "CHINA"}
_TIERS = {"ALL", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER_AND_CHAMPION"}


def _parse_error(message: str, url: str) -> SourceError:
    return SourceError("PARSE_ERROR", message, "owtics", url)


def decode_loader(html: str, route: str, url: str) -> dict[str, Any]:
    """Decode only the observed JSON-reference wire format, never eval script text.

    Turbo Stream dates contain a literal millisecond value rather than an index.
    Negative references represent null/undefined; unfamiliar tags fail closed.
    """
    if not isinstance(html, str) or len(html) > 12 * 1024 * 1024:
        raise _parse_error("OWTICS HTML is missing or exceeds the size limit.", url)
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        match = _ENQUEUE.search(script.string or "")
        if not match:
            continue
        try:
            values = json.loads(json.loads(match[1]))
            if not isinstance(values, list) or not 1 <= len(values) <= 100_000:
                raise ValueError("invalid reference table")
            memo: dict[int, Any] = {}
            active: set[int] = set()

            def ref(index: int, depth: int = 0, *, values=values, memo=memo, active=active):
                if type(index) is not int or depth > 80:
                    raise ValueError("invalid reference or nesting")
                if index in (-5, -7):
                    return None
                if not 0 <= index < len(values) or index in active:
                    raise ValueError("invalid or circular reference")
                if index in memo:
                    return memo[index]
                active.add(index)
                value = values[index]
                if isinstance(value, dict):
                    result = {}
                    for key, child in value.items():
                        if not re.fullmatch(r"_\d+", key):
                            raise ValueError("unknown key encoding")
                        name = ref(int(key[1:]), depth + 1)
                        if not isinstance(name, str):
                            raise ValueError("nonstring object key")
                        result[name] = ref(child, depth + 1)
                elif isinstance(value, list):
                    if value and isinstance(value[0], str):
                        if len(value) != 2 or value[0] != "D" or not isinstance(value[1], (int, float)):
                            raise ValueError("unknown hydration type")
                        result = datetime.fromtimestamp(value[1] / 1000, UTC).isoformat()
                    else:
                        result = [ref(child, depth + 1) for child in value]
                else:
                    result = value
                active.remove(index)
                memo[index] = result
                return result

            # Decode the specific route only; root includes unrelated translated UI.
            root = values[0]
            loader_index = next(v for k, v in root.items() if values[int(k[1:])] == "loaderData")
            route_index = next(v for k, v in values[loader_index].items() if values[int(k[1:])] == route)
            result = ref(route_index)
            if not isinstance(result, dict):
                raise ValueError("missing route object")
            return result
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, StopIteration, OverflowError, RecursionError) as exc:
            raise _parse_error("OWTICS hydration contract changed or is incomplete.", url) from exc
    raise _parse_error("OWTICS no longer exposes the verified server-rendered payload.", url)


def _rate(value, url: str):
    if value is None or value == -1:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise _parse_error("OWTICS returned an invalid percentage.", url)
    return float(value)


class OwticsAdapter:
    name = "owtics"
    capabilities = ("hero_meta", "map_meta")
    source_regions = ("KOREA", "ASIA", "AMER", "EU", "CHINA")

    def __init__(self, client):
        self.client = client

    async def meta(self, filters: dict) -> SourceResult:
        requested = dict(filters)
        region = _REGIONS.get(str(filters.get("region") or "KOREA").upper())
        tier = str(filters.get("tier") or "ALL").upper()
        mode = str(filters.get("mode") or "COMPETITIVE").upper().replace("-", "")
        map_slug = normalize_map(filters.get("map") or "all-maps")
        platform = filters.get("platform")
        allowed = {"view", "source", "heroes", "hero", "region", "tier", "mode", "map", "role", "platform", "allow_region_fallback", "order_by", "limit", "locale"}
        extra = [k for k, v in filters.items() if k not in allowed and v is not None]
        if extra or region is None or tier not in _TIERS or mode not in ("COMPETITIVE", "QUICKPLAY"):
            raise SourceError("UNSUPPORTED_FILTER", "Unsupported OWTICS filter; Grandmaster and Champion are available only as a combined tier.", self.name)
        if platform and str(platform).lower() != "pc":
            raise SourceError("UNSUPPORTED_FILTER", "OWTICS has no verified console/platform selector.", self.name)
        if mode == "QUICKPLAY" and (map_slug != "all-maps" or tier != "ALL"):
            raise SourceError("UNSUPPORTED_FILTER", "OWTICS quickplay is verified only for overall, all-tier statistics.", self.name)
        role = str(filters.get("role") or "").upper()
        if role not in ("", "TANK", "DAMAGE", "SUPPORT"):
            raise SourceError("UNSUPPORTED_FILTER", "Unsupported OWTICS role.", self.name)
        heroes = filters.get("heroes") or ([filters["hero"]] if filters.get("hero") else [])
        if not isinstance(heroes, list) or len(heroes) > 100:
            raise SourceError("UNSUPPORTED_FILTER", "heroes must be a list of at most 100 identifiers.", self.name)
        hero_set = {normalize_hero(hero) for hero in heroes}
        path = "/hero" if map_slug == "all-maps" else "/map/" + quote(map_slug, safe="-")
        response = await self.client.get_text(self.name, BASE_URL + path, params={"region": region, "tier": tier, "mode": mode}, ttl=3600)
        url = response.url
        route = decode_loader(response.data, "app/routes/hero" if map_slug == "all-maps" else "app/routes/map.$slug", url)
        warnings = ["OWTICS source-region labels describe its own population; they do not verify player country or match server location."]
        if platform:
            warnings.append("Requested PC platform cannot be verified from OWTICS' payload; no platform filter is claimed as applied.")
        try:
            if map_slug == "all-maps":
                container = route["statistics"]["heroesRatesOverview"]
                effective = container["filter"]
                if any(effective.get(k) != value for k, value in {"region": region, "tier": tier, "mode": mode}.items()):
                    raise SourceError("UNSUPPORTED_FILTER", "OWTICS did not apply the requested region, tier, or mode.", self.name, url)
                rows = [entry["measurement"] for entry in container["result"]["metaStandings"]]
                if any(any(row.get(k) != value for k, value in {"region": region, "tier": tier, "mode": mode}.items()) for row in rows):
                    raise _parse_error("OWTICS overview contains mismatched measurement filters.", url)
            else:
                map_data = route["data"]["mapBySlug"]
                if map_data["slug"] != map_slug:
                    raise SourceError("UNSUPPORTED_FILTER", "OWTICS resolved a different map than requested.", self.name, url)
                container = map_data["heroRates"]
                effective = container["filter"]
                if effective.get("tier") != tier or route.get("region") != region:
                    raise SourceError("UNSUPPORTED_FILTER", "OWTICS did not apply the requested map tier or region.", self.name, url)
                combos = route["catalog"]["combos"]
                if not any(c.get("region") == region and c.get("tier") == tier and c.get("mode") == mode and c.get("season", {}).get("id") == effective.get("season", {}).get("id") and "BY_MAP" in c.get("grains", []) for c in combos):
                    raise SourceError("UNSUPPORTED_FILTER", "OWTICS catalog does not confirm this map filter combination.", self.name, url)
                all_rows = container["result"]["measurements"]
                if not isinstance(all_rows, list) or any(not isinstance(row, dict) or "region" not in row for row in all_rows):
                    raise _parse_error("OWTICS map measurements lack explicit regions.", url)
                rows = [row for row in all_rows if row["region"] == region]
                warnings.append("OWTICS map measurements do not publish ban rate; it remains null.")
            if not isinstance(rows, list) or len(rows) > 1000:
                raise _parse_error("OWTICS returned an invalid measurement count.", url)
            season = effective.get("season")
            if not isinstance(season, dict) or not season.get("id"):
                raise _parse_error("OWTICS did not identify the measured season.", url)
            normalized = []
            seen = set()
            for row in rows:
                if "pickRate" not in row or "winRate" not in row:
                    raise _parse_error("OWTICS measurement no longer contains win/pick rates.", url)
                if map_slug == "all-maps" and row.get("season", {}).get("id") != season["id"]:
                    raise _parse_error("OWTICS measurement season does not match its applied filter.", url)
                hero = normalize_hero(row["hero"]["slug"])
                if hero in seen:
                    raise _parse_error("OWTICS returned duplicate hero measurements.", url)
                seen.add(hero)
                if hero_set and hero not in hero_set or role and row["hero"]["role"] != role:
                    continue
                normalized.append({
                    "hero": hero, "role": row["hero"]["role"].lower(),
                    "pickrate": _rate(row.get("pickRate"), url),
                    "winrate": _rate(row.get("winRate"), url),
                    "banrate": _rate(row.get("banRate"), url), "rate_unit": "percent",
                    "source_region": region, "match_server_region": None,
                    "map": map_slug, "tier": tier, "mode": mode.lower(),
                    "season": season,
                })
        except (KeyError, TypeError, AttributeError) as exc:
            raise _parse_error("OWTICS statistics schema changed or is incomplete.", url) from exc
        applied = {"region": region, "source_region": region, "tier": tier, "mode": mode.lower(), "map": map_slug, "season": season}
        if heroes:
            applied["heroes"] = sorted(hero_set)
        if role:
            applied["role"] = role.lower()
        if filters.get("order_by"):
            ordering = str(filters["order_by"]).split(":")
            if len(ordering) != 2 or ordering[0] not in ("pickrate", "winrate", "banrate") or ordering[1] not in ("asc", "desc"):
                raise SourceError("UNSUPPORTED_FILTER", "order_by must be a rate field followed by :asc or :desc.", self.name)
            metric, direction = ordering
            present = [row for row in normalized if row[metric] is not None]
            absent = [row for row in normalized if row[metric] is None]
            normalized = sorted(present, key=lambda row: row[metric], reverse=direction == "desc") + absent
            applied["order_by"] = filters["order_by"]
        if filters.get("limit") is not None:
            limit = filters["limit"]
            if type(limit) is not int or not 1 <= limit <= 500:
                raise SourceError("UNSUPPORTED_FILTER", "limit must be 1–500.", self.name)
            normalized = normalized[:limit]
            applied["limit"] = limit
        return SourceResult(
            data=normalized, source=self.name, source_url=url, retrieved_at=response.retrieved_at,
            data_period=f"OWTICS season {season.get('season')} ({season.get('era')}; display season {season.get('displaySeason')}; midseason={season.get('isMidseason')})",
            requested_filters=requested, applied_filters=applied, warnings=warnings,
            cached=response.cached, stale=response.stale,
        )
