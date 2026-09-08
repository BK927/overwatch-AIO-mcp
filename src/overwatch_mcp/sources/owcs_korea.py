"""Versioned, approved-only OWCS Korea public data releases (CC BY 4.0).

Source schema checked against release 2026.07.30. This dataset's hero-time metrics
are esports measurements, never ordinary competitive-match win/pick rates.
"""

import json
import math
import re
from collections import defaultdict
from datetime import date
from urllib.parse import urlparse

from ..models import SourceError, SourceResult
from ..normalization import normalize_hero, normalize_map

REPOSITORY = "https://github.com/IanBosworth/owcs-korea-data"
LATEST_RELEASE = "https://api.github.com/repos/IanBosworth/owcs-korea-data/releases/latest"
MAX_ASSET_BYTES = 8 * 1024 * 1024
SCHEMA_VERSION = "owcs-publication-v1"


def _key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _error(message: str, url: str = REPOSITORY) -> SourceError:
    return SourceError("PARSE_ERROR", message, "owcs_korea", url)


def _rows(data: dict, key: str, required: tuple[str, ...]) -> list[dict]:
    rows = data.get(key)
    if not isinstance(rows, list) or len(rows) > 100_000 or any(not isinstance(row, dict) or any(k not in row for k in required) for row in rows):
        raise _error(f"OWCS dataset table {key} does not match the verified schema.")
    return rows


class OWCSKoreaAdapter:
    name = "owcs_korea"
    capabilities = ("hero_meta", "matches", "maps", "bans", "teams")
    source_regions = ("KOREA",)

    def __init__(self, client):
        self.client = client
        self.latest_release = None

    async def _asset(self, release: dict, pattern: str):
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise _error("GitHub release is missing its asset list.", LATEST_RELEASE)
        matches = [a for a in assets if isinstance(a, dict) and re.fullmatch(pattern, str(a.get("name", "")))]
        if len(matches) != 1:
            raise _error("Release does not contain exactly one required JSON asset.", release.get("html_url", REPOSITORY))
        asset = matches[0]
        url = asset.get("browser_download_url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith("/IanBosworth/owcs-korea-data/releases/download/") or parsed.query or parsed.fragment:
            raise _error("Release asset URL is outside the expected public repository.", REPOSITORY)
        if type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_ASSET_BYTES:
            raise SourceError("SOURCE_UNAVAILABLE", "OWCS asset exceeds the bounded JSON download limit or has no declared size.", self.name, url)
        # HttpClient enforces an independent streamed byte limit before JSON parsing.
        result = await self.client.get_json(self.name, url, ttl=86400)
        if not isinstance(result.data, dict):
            raise _error("OWCS release asset is not a JSON object.", url)
        if len(json.dumps(result.data, separators=(",", ":"), ensure_ascii=False).encode()) > MAX_ASSET_BYTES:
            raise SourceError("SOURCE_UNAVAILABLE", "OWCS decoded JSON asset exceeds its size limit.", self.name, url)
        return result

    async def fetch(self, filters: dict) -> SourceResult:
        requested = dict(filters)
        view = filters.get("view") or "hero_meta"
        region = str(filters.get("region") or "KOREA").upper()
        allowed = {"view", "region", "hero", "heroes", "map", "map_id", "match_id", "team", "stage", "role", "after", "before", "limit", "offset"}
        extra = [key for key, value in filters.items() if key not in allowed and value is not None]
        if view not in self.capabilities or region not in ("KOREA", "KR") or extra:
            raise SourceError("UNSUPPORTED_FILTER", "OWCS Korea supports esports views and documented dataset filters only.", self.name)
        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or not 0 <= offset <= 100_000:
            raise SourceError("UNSUPPORTED_FILTER", "limit must be 1–500 and offset 0–100000.", self.name)
        for key in ("after", "before"):
            if filters.get(key):
                try:
                    date.fromisoformat(filters[key])
                except (ValueError, TypeError) as exc:
                    raise SourceError("UNSUPPORTED_FILTER", f"{key} must be an ISO calendar date.", self.name) from exc
        if filters.get("after") and filters.get("before") and filters["after"] > filters["before"]:
            raise SourceError("UNSUPPORTED_FILTER", "after must not be later than before.", self.name)
        heroes = filters.get("heroes") or ([filters["hero"]] if filters.get("hero") else [])
        if not isinstance(heroes, list) or len(heroes) > 100:
            raise SourceError("UNSUPPORTED_FILTER", "heroes must be a list with at most 100 entries.", self.name)
        hero_set = {normalize_hero(hero) for hero in heroes}
        role = str(filters.get("role") or "").lower()
        if role not in ("", "tank", "damage", "support"):
            raise SourceError("UNSUPPORTED_FILTER", "Unsupported hero role.", self.name)
        if role and view != "hero_meta":
            raise SourceError("UNSUPPORTED_FILTER", "role is supported only by hero_meta.", self.name)
        map_filter = normalize_map(filters["map"]) if filters.get("map") else None
        if map_filter == "all-maps":
            map_filter = None
        release_response = await self.client.get_json(self.name, LATEST_RELEASE, ttl=21600)
        release = release_response.data
        if not isinstance(release, dict) or not release.get("tag_name") or not isinstance(release.get("published_at"), str) or release.get("draft") or release.get("prerelease"):
            raise _error("GitHub latest-release metadata is missing or not a public stable release.", LATEST_RELEASE)
        tag = release["tag_name"]
        manifest_response = await self._asset(release, "manifest\\.json")
        manifest = manifest_response.data
        if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("release_version") != tag or not isinstance(manifest.get("safety"), dict) or manifest["safety"].get("approved_maps_only") is not True:
            raise _error("OWCS manifest schema, release identity, or approved-only guarantee changed.", manifest_response.url)
        dataset_response = await self._asset(release, r"owcs-korea-data-" + re.escape(tag) + r"\.json")
        dataset = dataset_response.data
        if dataset.get("schema_version") != SCHEMA_VERSION or dataset.get("release_version") != tag:
            raise _error("OWCS normalized dataset schema or release identity changed.", dataset_response.url)
        source_matches = _rows(dataset, "matches", ("id", "match_date", "team_a", "team_b", "stage"))
        source_maps = _rows(dataset, "maps", ("id", "match_id", "map_name", "winner_team"))
        source_bans = _rows(dataset, "hero_bans", ("map_id", "hero", "banned_by_team"))
        source_segments = _rows(dataset, "hero_time_segments", ("map_id", "hero", "team", "duration_seconds", "echo_copy_target"))
        self._validate_relations(source_matches, source_maps, source_bans, source_segments)
        counts = manifest.get("counts")
        if not isinstance(counts, dict):
            raise _error("OWCS manifest is missing its table counts.", manifest_response.url)
        for key, rows in (("matches", source_matches), ("maps", source_maps), ("hero_bans", source_bans), ("hero_time_segments", source_segments)):
            if counts.get(key) != len(rows):
                raise _error(f"OWCS {key} count differs from its release manifest.", dataset_response.url)
        match_dates = [row["match_date"] for row in source_matches]
        try:
            for value in match_dates:
                date.fromisoformat(value)
            data_until = manifest.get("data_as_of")
            if data_until:
                date.fromisoformat(data_until)
        except (ValueError, TypeError) as exc:
            raise _error("OWCS coverage dates are invalid.", dataset_response.url) from exc
        if data_until and match_dates and max(match_dates) > data_until:
            raise _error("OWCS match dates exceed the release coverage date.", dataset_response.url)
        full_period = f"{min(match_dates)}/{max(match_dates)}" if match_dates else None
        matches = [row for row in source_matches if self._match_matches(row, filters)]
        match_ids = {row["id"] for row in matches}
        maps = [row for row in source_maps if row["match_id"] in match_ids and (not map_filter or _key(row["map_name"]) == _key(map_filter)) and (not filters.get("map_id") or str(row["id"]) == str(filters["map_id"]))]
        map_ids = {row["id"] for row in maps}
        segments = [row for row in source_segments if row["map_id"] in map_ids and (not filters.get("team") or _key(row["team"]) == _key(filters["team"]))]
        if hero_set:
            if view == "bans":
                selected_map_ids = {row["map_id"] for row in source_bans if normalize_hero(row["hero"]) in hero_set}
            else:
                selected_map_ids = {row["map_id"] for row in segments if row["hero"].lower() != "unknown" and normalize_hero(row["hero"]) in hero_set}
            maps = [row for row in maps if row["id"] in selected_map_ids]
            map_ids = {row["id"] for row in maps}
            segments = [row for row in segments if row["map_id"] in map_ids]
        match_ids = {row["match_id"] for row in maps}
        matches = [row for row in matches if row["id"] in match_ids]
        map_by_id = {row["id"]: row for row in maps}
        match_by_id = {row["id"]: row for row in matches}
        warnings = ["OWCS Korea is an independent, approved-map esports dataset, not competitive ladder statistics or evidence of a KR match server."]
        responses = [release_response, manifest_response, dataset_response]
        scoped = any(filters.get(k) not in (None, "", "all") for k in ("map_id", "match_id", "team", "stage", "after", "before")) or bool(map_filter)
        if view == "hero_meta" and not scoped:
            stats_response = await self._asset(release, r"owcs-korea-hero-stats-" + re.escape(tag) + r"\.json")
            responses.append(stats_response)
            stats = stats_response.data
            if stats.get("metric_version") != manifest.get("methodology_version") or not isinstance(stats.get("coverage"), dict) or stats["coverage"].get("approved_only") is not True or not isinstance(stats.get("export_manifest"), dict) or stats["export_manifest"].get("data_as_of") != data_until:
                raise _error("OWCS aggregate statistics disagree with the release manifest.", stats_response.url)
            records = []
            for row in _rows(stats, "hero_stats", ("hero", "role", "hero_seconds", "pick_rate", "unmirrored_winrate")):
                hero = normalize_hero(row["hero"])
                if hero_set and hero not in hero_set or role and row["role"].lower() != role:
                    continue
                records.append({**row, "hero": hero, "hero_name": row["hero"], "metric_origin": "published_release_aggregate", "rate_unit": "ratio"})
            warnings.append("Published pick_rate is ban-adjusted hero-time opportunity; unmirrored_winrate uses unmirrored time. Neither is competitive-match win rate.")
        elif view == "hero_meta":
            if role:
                raise SourceError("UNSUPPORTED_FILTER", "Scoped hero-time queries cannot apply role without a verified per-segment role field.", self.name, dataset_response.url)
            records = self._hero_time(segments, map_by_id, hero_set)
            warnings.append("Scoped hero_seconds sum published segments before aggregate overlap removal. Rates remain null because the published aggregate denominators do not apply to this subset.")
            warnings.append("Echo copy-target seconds remain part of Echo playtime and are listed separately; copied heroes are not counted as ordinary hero picks.")
        elif view == "matches":
            records = [{**row, "maps_included": sum(m["match_id"] == row["id"] for m in maps)} for row in matches]
            records.sort(key=lambda row: (row["match_date"], row["id"]), reverse=True)
        elif view == "maps":
            records = [{**row, "map": normalize_map(row["map_name"]), "match_date": match_by_id[row["match_id"]]["match_date"]} for row in maps]
        elif view == "bans":
            records = [{**row, "hero": normalize_hero(row["hero"]), "hero_name": row["hero"], "map_name": map_by_id[row["map_id"]]["map_name"]} for row in source_bans if row["map_id"] in map_ids and (not hero_set or normalize_hero(row["hero"]) in hero_set) and (not filters.get("team") or _key(row["banned_by_team"]) == _key(filters["team"]))]
        else:
            names = sorted({row[k] for row in matches for k in ("team_a", "team_b")})
            records = [{"team": name, "matches_included": sum(name in (row["team_a"], row["team_b"]) for row in matches), "maps_won": sum(row["winner_team"] == name for row in maps), "hero_seconds": round(sum(row["duration_seconds"] for row in segments if row["team"] == name and row["hero"].lower() != "unknown" and (not hero_set or normalize_hero(row["hero"]) in hero_set)), 6), "metric_origin": "sum_published_segments_before_overlap_removal"} for name in names if not filters.get("team") or _key(name) == _key(filters["team"])]
        total = len(records)
        selected = records[offset:offset + limit]
        if offset + limit < total:
            warnings.append("Result is paginated; use offset to retrieve remaining records.")
        if not selected:
            warnings.append("EMPTY_RESULT: the release was read successfully but no records match this page of filters.")
        self.latest_release = tag
        return SourceResult(
            data={"dataset_type": "esports", "release_tag": tag, "published_at": release["published_at"], "data_until": data_until, "coverage_period": full_period, "license": manifest.get("license"), "attribution": "OWCS Korea Meta data project / IanBosworth", "repository_url": REPOSITORY, "methodology": manifest.get("methodology_version"), "approved_maps_only": True, "view": view, "total": total, "offset": offset, "limit": limit, "records": selected},
            source=self.name, source_url=release.get("html_url", REPOSITORY),
            retrieved_at=min(r.retrieved_at for r in responses), source_updated_at=manifest.get("generated_at_utc"),
            data_period=full_period, requested_filters=requested,
            applied_filters={**{k: v for k, v in filters.items() if v is not None}, "region": "KOREA", "view": view, "dataset_type": "esports", "release_tag": tag, "limit": limit, "offset": offset},
            warnings=warnings, cached=all(r.cached for r in responses), stale=any(r.stale for r in responses),
        )

    @staticmethod
    def _match_matches(row: dict, filters: dict) -> bool:
        return (not filters.get("match_id") or str(row["id"]) == str(filters["match_id"])) and (not filters.get("team") or _key(filters["team"]) in (_key(row["team_a"]), _key(row["team_b"]))) and (filters.get("stage") in (None, "", "all") or _key(row["stage"]) == _key(filters["stage"])) and (not filters.get("after") or row["match_date"] >= filters["after"]) and (not filters.get("before") or row["match_date"] <= filters["before"])

    @staticmethod
    def _validate_relations(matches, maps, bans, segments):
        try:
            for rows, text_fields in ((matches, ("match_date", "team_a", "team_b", "stage")), (maps, ("map_name",)), (bans, ("hero", "banned_by_team")), (segments, ("hero", "team"))):
                if any(not isinstance(row[key], str) or not row[key] for row in rows for key in text_fields):
                    raise _error("OWCS dataset contains invalid text fields.")
            match_ids = {row["id"] for row in matches}
            map_ids = {row["id"] for row in maps}
            if len(match_ids) != len(matches) or len(map_ids) != len(maps) or any(row["match_id"] not in match_ids for row in maps) or any(row["map_id"] not in map_ids for row in bans + segments):
                raise _error("OWCS dataset has duplicate or unresolved match/map identifiers.")
            for row in segments:
                value = row["duration_seconds"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise _error("OWCS segment duration is invalid.")
        except TypeError as exc:
            raise _error("OWCS dataset identifiers are invalid.") from exc

    @staticmethod
    def _hero_time(segments, maps, hero_set):
        groups = {}
        for segment in segments:
            if segment["hero"].lower() == "unknown":
                continue
            hero = normalize_hero(segment["hero"])
            if hero_set and hero not in hero_set:
                continue
            group = groups.setdefault(hero, {"hero": hero, "hero_name": segment["hero"], "hero_seconds": 0.0, "map_ids": set(), "match_ids": set(), "teams": set(), "by_map": defaultdict(float), "echo_copy_targets": defaultdict(float)})
            seconds = segment["duration_seconds"]
            map_row = maps[segment["map_id"]]
            group["hero_seconds"] += seconds
            group["map_ids"].add(segment["map_id"])
            group["match_ids"].add(map_row["match_id"])
            group["teams"].add(segment["team"])
            group["by_map"][map_row["map_name"]] += seconds
            if segment.get("echo_copy_target"):
                group["echo_copy_targets"][normalize_hero(segment["echo_copy_target"])] += seconds
        records = []
        for group in groups.values():
            records.append({"hero": group["hero"], "hero_name": group["hero_name"], "hero_seconds": round(group["hero_seconds"], 6), "maps_appeared": len(group["map_ids"]), "matches_appeared": len(group["match_ids"]), "teams": sorted(group["teams"]), "by_map": [{"map_name": key, "hero_seconds": round(value, 6)} for key, value in sorted(group["by_map"].items())], "echo_copy_targets": [{"hero": key, "seconds": round(value, 6)} for key, value in sorted(group["echo_copy_targets"].items())], "pick_rate": None, "unmirrored_winrate": None, "metric_origin": "sum_published_segments_before_overlap_removal"})
        return sorted(records, key=lambda row: (-row["hero_seconds"], row["hero"]))
