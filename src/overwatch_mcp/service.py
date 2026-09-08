"""Coordinate sources while keeping filter scope, provenance and evidence visible."""

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime

from pydantic import ValidationError

from .db import Repository
from .http import HttpClient
from .models import SourceError, SourceResult, error_envelope, utcnow
from .normalization import (
    normalize_hero,
    normalize_map,
    normalize_mode,
    normalize_platform,
    normalize_region,
    normalize_role,
    normalize_tier,
)
from .registry import Registry
from .requests import REQUESTS
from .sources.blizzard import BlizzardAdapter
from .sources.overfast import OverFastAdapter
from .sources.owreplays import OWReplaysAdapter
from .sources.patches import PatchAdapter

META_KEYS = {
    "heroes",
    "platform",
    "mode",
    "region",
    "tier",
    "map",
    "role",
    "source",
    "allow_region_fallback",
    "order_by",
}
REPLAY_SOURCE_KEYS = {
    "hero",
    "map",
    "player",
    "tier",
    "tier_min",
    "platform",
    "uploaded_after",
    "limit",
}
REPLAY_TIERS = (
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHAMPION",
)


class Service:
    def __init__(self, repository: Repository | None = None, client: HttpClient | None = None):
        self.repo = repository or Repository()
        self.client = client or HttpClient(self.repo)
        self.registry = Registry(self.repo)
        self.overfast = OverFastAdapter(self.client)
        self.blizzard = BlizzardAdapter(self.client)
        self.replays = OWReplaysAdapter(self.client)
        self.patches = PatchAdapter(self.client)
        from .sources.owcs_korea import OWCSKoreaAdapter
        from .sources.owtics import OwticsAdapter

        self.owtics = OwticsAdapter(self.client)
        self.esports = OWCSKoreaAdapter(self.client)
        self.adapters = [
            self.overfast,
            self.blizzard,
            self.owtics,
            self.replays,
            self.patches,
            self.esports,
        ]

    async def close(self):
        await self.client.close()
        self.repo.close()

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in REQUESTS:
            return error_envelope(
                SourceError("INVALID_ARGUMENT", "Unknown tool.", "server"), arguments
            )
        try:
            request = (
                REQUESTS[name].model_validate(arguments).model_dump(mode="json", exclude_none=True)
            )
            result = await getattr(self, name)(request)
            envelope = result.envelope() if isinstance(result, SourceResult) else result
            envelope["requested_filters"] = arguments
            return envelope
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            return error_envelope(SourceError("INVALID_ARGUMENT", message, "server"), arguments)
        except SourceError as exc:
            if exc.code in ("PARSE_ERROR", "SOURCE_UNAVAILABLE", "RATE_LIMITED"):
                self.repo.source_record(exc.source, f"{exc.code}: {exc.message}")
            return error_envelope(exc, arguments)

    async def ow_catalog(self, filters):
        return await self.overfast.catalog(filters)

    async def ow_players_search(self, filters):
        return await self.overfast.players_search(filters)

    async def ow_player_get(self, filters):
        return await self.overfast.player_get(filters)

    def _meta_source(self, filters):
        source = filters.get("source", "auto")
        if source == "auto":
            source = (
                "owtics" if normalize_region(filters.get("region", "ASIA")) == "KR" else "overfast"
            )
        return source

    async def _single_meta(self, filters):
        filters = self._normalize_meta_filters(filters)
        source = self._meta_source(filters)
        adapter = {"overfast": self.overfast, "owtics": self.owtics, "blizzard": self.blizzard}[
            source
        ]
        args = {k: v for k, v in filters.items() if k in META_KEYS}
        args["source"] = source
        try:
            result = await adapter.meta(args)
        except SourceError as exc:
            if exc.code in ("PARSE_ERROR", "SOURCE_UNAVAILABLE", "RATE_LIMITED"):
                self.repo.source_record(exc.source, f"{exc.code}: {exc.message}")
            if (
                source != "owtics"
                or not filters.get("allow_region_fallback")
                or normalize_region(filters.get("region", "ASIA")) != "KR"
            ):
                raise
            result = await self.overfast.meta({**args, "source": "overfast", "region": "KR"})
            result.warnings.append(
                f"OWTICS failed ({exc.code}); explicit fallback permission applied ASIA. This is not Korean-server data."
            )
        self.repo.save_meta(result)
        # A display limit must not discard observed heroes from stored history.
        return replace(
            result,
            data=result.data[: filters["limit"]],
            applied_filters={**result.applied_filters, "limit": filters["limit"]},
        )

    def _normalize_meta_filters(self, filters):
        normalized = dict(filters)
        for key, function in (
            ("platform", normalize_platform),
            ("mode", normalize_mode),
            ("region", normalize_region),
            ("map", normalize_map),
            ("role", normalize_role),
        ):
            if filters.get(key) is not None:
                normalized[key] = function(filters[key])
        if filters.get("tier") is not None:
            normalized["tier"] = (
                "GRANDMASTER_AND_CHAMPION"
                if filters["tier"].upper() == "GRANDMASTER_AND_CHAMPION"
                else normalize_tier(filters["tier"])
            )
        else:
            normalized["tier"] = "ALL"
        normalized["heroes"] = list(
            dict.fromkeys(normalize_hero(hero) for hero in filters.get("heroes", []))
        )
        if "all-heroes" in normalized["heroes"]:
            if len(normalized["heroes"]) != 1:
                raise SourceError(
                    "UNSUPPORTED_FILTER",
                    "all-heroes cannot be combined with individual heroes.",
                    "normalization",
                )
            normalized["heroes"] = []
        if not re.fullmatch(
            r"(?:pickrate|winrate|banrate):(?:asc|desc)", filters.get("order_by", "winrate:desc")
        ):
            raise SourceError(
                "UNSUPPORTED_FILTER",
                "order_by must name a rate followed by :asc or :desc.",
                "normalization",
            )
        return normalized

    async def ow_meta(self, filters):
        view = filters["view"]
        if view == "heroes":
            return await self._single_meta(filters)
        if view == "history":
            filters = self._normalize_meta_filters(filters)
            source = self._meta_source(filters)
            normalized = {
                "platform": filters["platform"],
                "mode": filters["mode"],
                "region": filters["region"],
                "tier": filters["tier"],
                "map": filters["map"],
                "role": filters.get("role"),
            }
            warnings = [
                "Stored aggregate observations are compared by retrieval time; these are not match results played during the interval. Empty history means no stored observations."
            ]
            if normalized["mode"] == "quickplay" and (
                normalized["tier"] != "ALL" or normalized["map"] != "all-maps"
            ):
                raise SourceError(
                    "UNSUPPORTED_FILTER",
                    "Stored quickplay series do not support competitive tiers or individual maps.",
                    source,
                )
            if source == "owtics":
                if normalized["platform"] != "pc":
                    raise SourceError(
                        "UNSUPPORTED_FILTER",
                        "OWTICS has no verified console history population.",
                        source,
                    )
                normalized.pop("platform")
                normalized["region"] = {"KR": "KOREA", "AMERICAS": "AMER", "EUROPE": "EU"}.get(
                    normalized["region"], normalized["region"]
                )
                if normalized["tier"] in ("GRANDMASTER", "CHAMPION"):
                    raise SourceError(
                        "UNSUPPORTED_FILTER",
                        "OWTICS stores Grandmaster and Champion only as GRANDMASTER_AND_CHAMPION.",
                        source,
                    )
                warnings.append(
                    "OWTICS platform is not verified; its source region does not establish player nationality or match server."
                )
            else:
                if normalized["tier"] in ("CHAMPION", "GRANDMASTER_AND_CHAMPION"):
                    raise SourceError(
                        "UNSUPPORTED_FILTER",
                        "This source stores its combined Grandmaster/Champion population under GRANDMASTER.",
                        source,
                    )
                if normalized["tier"] == "GRANDMASTER":
                    warnings.append("This source's GRANDMASTER history includes Champion players.")
                if normalized["region"] == "KR":
                    if not filters["allow_region_fallback"]:
                        raise SourceError(
                            "UNSUPPORTED_FILTER",
                            "This source has no Korean-only history; explicit region fallback is required for ASIA.",
                            source,
                        )
                    normalized["region"] = "ASIA"
                    warnings.append(
                        "Explicit region fallback selected stored ASIA observations, not Korean-server data."
                    )
            heroes = filters["heroes"] or None
            rows = self.repo.meta_history(
                source, normalized, heroes, filters.get("after"), filters["limit"]
            )
            previous = {}
            for row in reversed(rows):
                older = previous.get(row["hero"])
                row["difference"] = self._difference(row, older) if older else None
                row["comparison_retrieved_at"] = older["retrieved_at"] if older else None
                previous[row["hero"]] = row
            return SourceResult(
                rows,
                source,
                "local:hero_meta_snapshots",
                applied_filters={
                    **normalized,
                    "heroes": heroes or [],
                    "limit": filters["limit"],
                    **({"after": filters["after"]} if filters.get("after") else {}),
                },
                retrieved_at=rows[0]["retrieved_at"] if rows else utcnow(),
                cached=True,
                warnings=warnings,
            )
        dimension, values_key = {
            "map_comparison": ("map", "maps"),
            "tier_comparison": ("tier", "tiers"),
            "region_comparison": ("region", "regions"),
        }[view]
        normalizer = {
            "map": normalize_map,
            "tier": lambda value: (
                "GRANDMASTER_AND_CHAMPION"
                if value.upper() == "GRANDMASTER_AND_CHAMPION"
                else normalize_tier(value)
            ),
            "region": normalize_region,
        }[dimension]
        values = list(dict.fromkeys(normalizer(value) for value in filters[values_key]))
        if len(values) < 2:
            raise SourceError(
                "INVALID_ARGUMENT",
                "Comparisons require at least two distinct filter values after alias normalization.",
                "server",
            )
        outcomes = await asyncio.gather(
            *(self._single_meta({**filters, dimension: value}) for value in values),
            return_exceptions=True,
        )
        groups, statuses = [], {}
        for value, outcome in zip(values, outcomes, strict=True):
            if isinstance(outcome, SourceError):
                entry = error_envelope(outcome, {dimension: value})
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                entry = outcome.envelope()
            groups.append({"value": value, **entry})
            statuses[value] = entry["status"]
        good = [group for group in groups if group["status"] in ("ok", "empty", "stale")]
        nonempty = [group for group in good if group["data"]]
        if nonempty:
            baseline = nonempty[0]
            baseline_rows = {row["hero"]: row for row in baseline["data"]}
            for group in nonempty[1:]:
                for row in group["data"]:
                    row["difference"] = (
                        self._difference(row, baseline_rows.get(row["hero"]))
                        if group["source"] == baseline["source"]
                        else None
                    )
        failures = len(groups) - len(good)
        envelope = SourceResult(
            groups,
            "multiple",
            "local:comparison",
            retrieved_at=min((group["retrieved_at"] for group in good), default=utcnow()),
            applied_filters={
                "view": view,
                "dimension": dimension,
                "values": values,
                "limit_per_group": filters["limit"],
            },
            cached=bool(good) and all(group["cached"] for group in good),
            stale=any(group["stale"] for group in good),
            warnings=[
                "Differences are percentage points relative to the first successful nonempty group. Cross-source differences are not calculated."
            ],
        ).envelope()
        envelope["source_status"] = statuses
        if failures:
            envelope["status"] = "partial" if good else "error"
            envelope["error"] = {
                "code": "PARTIAL_RESULT" if good else "SOURCE_UNAVAILABLE",
                "message": "Some comparison groups failed."
                if good
                else "All comparison groups failed; inspect each group error.",
            }
        elif any(group["stale"] for group in good):
            envelope.update(
                status="stale",
                stale=True,
                error={
                    "code": "STALE_DATA",
                    "message": "Some comparison groups contain stale data.",
                },
            )
        elif not nonempty:
            envelope.update(
                status="empty",
                error={
                    "code": "EMPTY_RESULT",
                    "message": "All comparison groups were read successfully but contained no matching records.",
                },
            )
        return envelope

    @staticmethod
    def _difference(current, previous):
        if previous is None:
            return None
        return {
            key: round(current[key] - previous[key], 6)
            if key not in current.get("zero_value_caution", [])
            and key not in previous.get("zero_value_caution", [])
            and isinstance(current.get(key), (int, float))
            and isinstance(previous.get(key), (int, float))
            else None
            for key in ("pickrate", "winrate", "banrate")
        }

    async def ow_rankers_search(self, filters):
        return SourceResult(
            self.registry.rankers(filters),
            "local_registry",
            "local:players",
            applied_filters=filters,
            warnings=[
                "Only the stored, evidenced registry is searched. Rankings include their observation dates and are not a complete live Top 500 list."
            ],
        )

    def _normalize_replay_filters(self, filters):
        normalized = dict(filters)
        for key in (
            "hero",
            "map",
            "player",
            "tier",
            "tier_min",
            "platform",
            "player_country",
            "match_region",
        ):
            if key in filters and not filters[key].strip():
                raise SourceError(
                    "UNSUPPORTED_FILTER", f"{key} must be nonempty when supplied.", "local_registry"
                )
        for key, function in (("hero", normalize_hero), ("map", normalize_map)):
            if filters.get(key):
                normalized[key] = function(filters[key])
        if filters.get("platform"):
            normalized["platform"] = filters["platform"].lower()
            if normalized["platform"] not in ("pc", "xbox", "playstation", "switch"):
                raise SourceError(
                    "UNSUPPORTED_FILTER",
                    "Replay platform must be pc, xbox, playstation, or switch.",
                    "local_registry",
                )
        if filters.get("tier") and filters.get("tier_min"):
            raise SourceError(
                "UNSUPPORTED_FILTER", "Use tier or tier_min, not both.", "local_registry"
            )
        for key in ("tier", "tier_min"):
            if filters.get(key):
                tier, division = self._replay_tier(filters[key])
                normalized[key] = tier.lower() + (f" {division}" if division else "")
        for key in ("player_country", "match_region"):
            if filters.get(key):
                value = filters[key].upper()
                if not re.fullmatch(r"[A-Z]{2}", value) and not (
                    key == "match_region" and value == "UNKNOWN"
                ):
                    raise SourceError(
                        "UNSUPPORTED_FILTER",
                        f"{key} must be an explicit two-letter code"
                        + (" or UNKNOWN." if key == "match_region" else "."),
                        "local_registry",
                    )
                normalized[key] = value
        return normalized

    @staticmethod
    def _replay_tier(value):
        match = re.fullmatch(r"(.+?)(?:[\s_-]*([1-5]))?", value.strip())
        if not match:
            raise SourceError(
                "UNSUPPORTED_FILTER",
                "Replay tier must be a rank name with optional division 1–5.",
                "local_registry",
            )
        tier = normalize_tier(match[1])
        if tier not in REPLAY_TIERS:
            raise SourceError(
                "UNSUPPORTED_FILTER",
                "Replay tier must be a competitive rank with optional division 1–5.",
                "local_registry",
            )
        return tier, int(match[2]) if match[2] else None

    def _filter_replays(self, rows, filters):
        ranks = None
        if filters.get("player_pool") == "kr_rankers" or filters.get("ranker_status") == "verified":
            ranks = {
                row["id"]
                for row in self.registry.rankers(
                    {
                        "country": "KR" if filters.get("player_pool") == "kr_rankers" else None,
                        "verified_only": True,
                        "limit": 10000,
                    }
                )
            }
        exact = self._replay_tier(filters["tier"]) if filters.get("tier") else None
        minimum = self._replay_tier(filters["tier_min"]) if filters.get("tier_min") else None
        result = []
        for raw in rows:
            row = self.registry.enrich_replay(raw)
            if filters.get("hero") not in (None, "all-heroes") and filters["hero"] not in (
                row.get("heroes") or [row.get("hero")]
            ):
                continue
            if filters.get("map") not in (None, "all-maps") and filters["map"] != row.get("map"):
                continue
            if (
                filters.get("platform")
                and filters["platform"] != str(row.get("platform", "")).lower()
            ):
                continue
            if (
                filters.get("player")
                and filters["player"].casefold() not in str(row.get("display_name", "")).casefold()
            ):
                continue
            if filters.get("uploaded_after") and (
                not row.get("uploaded_at") or row["uploaded_at"][:10] < filters["uploaded_after"]
            ):
                continue
            if exact or minimum:
                try:
                    claimed_tier, claimed_division = self._replay_tier(
                        str(row.get("tier_claim") or "")
                    )
                except SourceError:
                    continue
                if exact and (
                    claimed_tier != exact[0]
                    or exact[1] is not None
                    and claimed_division != exact[1]
                ):
                    continue
                if minimum:
                    if REPLAY_TIERS.index(claimed_tier) < REPLAY_TIERS.index(minimum[0]):
                        continue
                    if (
                        claimed_tier == minimum[0]
                        and minimum[1] is not None
                        and (claimed_division is None or claimed_division > minimum[1])
                    ):
                        continue
            if filters.get("player_id") and row.get("player_id") != filters["player_id"]:
                continue
            if ranks is not None and row.get("player_id") not in ranks:
                continue
            if (
                filters.get("player_country")
                and row["player_country"] != filters["player_country"].upper()
            ):
                continue
            if filters.get("match_region"):
                if row["match_server_region"] != filters["match_region"].upper():
                    continue
                if (
                    filters["region_evidence"] == "verified_only"
                    and row["region_confidence"] != "verified"
                ):
                    continue
            if (
                filters.get("playable_status") is not None
                and row.get("replay_status") not in filters["playable_status"]
            ):
                continue
            result.append(row)
        return result[: filters["limit"]]

    def _save_replay_observations(self, upstream, records):
        self.repo.save_replays(
            [
                {
                    **row,
                    "retrieved_at": row.get("retrieved_at") or upstream.retrieved_at,
                    "source_url": row.get("source_url") or upstream.source_url,
                    "source": row.get("source") or upstream.source,
                    "stale": upstream.stale,
                }
                for row in records
            ]
        )

    @staticmethod
    def _observation_is_stale(row):
        if row.get("stale"):
            return True
        try:
            timestamp = datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                return True
            return (datetime.now(UTC) - timestamp).total_seconds() > 1800
        except (KeyError, ValueError, TypeError, AttributeError):
            return True

    @staticmethod
    def _oldest_observation(rows, default):
        observed = [row.get("retrieved_at") for row in rows]
        if rows and any(value is None for value in observed):
            return None
        try:
            parsed = [
                (datetime.fromisoformat(value.replace("Z", "+00:00")), value) for value in observed
            ]
            if any(timestamp.tzinfo is None for timestamp, _ in parsed):
                return None
            return min(parsed)[1] if parsed else default
        except (ValueError, TypeError, AttributeError):
            return None

    async def ow_replays_search(self, filters):
        filters = self._normalize_replay_filters(filters)
        upstream = None
        failure = None
        if filters["refresh"]:
            try:
                # Validation states belong to the local evidence store, not to public API flags.
                source_filters = {k: v for k, v in filters.items() if k in REPLAY_SOURCE_KEYS}
                if source_filters.get("hero") == "all-heroes":
                    source_filters.pop("hero")
                if source_filters.get("map") == "all-maps":
                    source_filters.pop("map")
                source_filters["limit"] = 100
                upstream = await self.replays.search(source_filters)
                self._save_replay_observations(upstream, upstream.data)
            except SourceError as exc:
                if exc.code not in ("PARSE_ERROR", "SOURCE_UNAVAILABLE", "RATE_LIMITED"):
                    raise
                failure = exc
                self.repo.source_record(exc.source, f"{exc.code}: {exc.message}")
        rows = self._filter_replays(self.repo.replay_rows(), filters)
        if failure and not rows:
            raise failure
        warnings = (
            list(upstream.warnings)
            if upstream
            else ["Search covers locally stored replay observations."]
        )
        if filters.get("player_pool") == "kr_rankers":
            warnings.append(
                "Only replays with an evidence-backed identity link to stored Korean rankers can match; equal nicknames are never linked automatically."
            )
        refreshed = {row["code"] for row in upstream.data} if upstream else set()
        retained_count = sum(row["code"] not in refreshed for row in rows)
        for row in rows:
            row["cached"] = upstream.cached if upstream and row["code"] in refreshed else True
            row["stale"] = bool(failure) or self._observation_is_stale(row)
        if retained_count:
            warnings.append(
                f"{retained_count} returned observations were retained locally and were not covered by this refresh. Each record keeps its original retrieval timestamp and freshness state."
            )
        if filters.get("tier") or filters.get("tier_min"):
            filters["tier_filter_basis"] = "source_claim"
        filters["collection_scope"] = (
            "stored_observations_after_bounded_refresh" if upstream else "stored_observations"
        )
        result = SourceResult(
            rows,
            "owreplays" if upstream else "local_registry",
            upstream.source_url if upstream else "local:replays",
            retrieved_at=self._oldest_observation(
                rows, upstream.retrieved_at if upstream else utcnow()
            ),
            applied_filters=filters,
            warnings=warnings,
            cached=all(row["cached"] for row in rows)
            if rows
            else upstream.cached
            if upstream
            else True,
            stale=bool(failure)
            or any(row["stale"] for row in rows)
            or bool(upstream and upstream.stale),
        )
        if upstream:
            result.applied_filters["source_coverage"] = upstream.applied_filters
        if failure:
            result.warnings.append(
                f"Refresh failed ({failure.code}); stored observations returned with their original retrieval dates."
            )
        return result

    async def ow_replay_get(self, filters):
        code = filters["code"].upper()
        upstream = None
        failure = None
        if filters["refresh"]:
            try:
                upstream = await self.replays.get(code)
                if upstream.data:
                    self._save_replay_observations(upstream, [upstream.data])
                elif self.repo.replay_rows(code):
                    failure = SourceError(
                        "EMPTY_RESULT",
                        "The source no longer lists this code; returning a retained observation.",
                        "owreplays",
                    )
            except SourceError as exc:
                if exc.code not in ("PARSE_ERROR", "SOURCE_UNAVAILABLE", "RATE_LIMITED"):
                    raise
                failure = exc
                self.repo.source_record(exc.source, f"{exc.code}: {exc.message}")
        rows = self.repo.replay_rows(code)
        if not rows and failure:
            raise failure
        row = self.registry.enrich_replay(rows[0]) if rows else None
        if row:
            row["cached"] = upstream.cached if upstream and upstream.data else True
            row["stale"] = bool(failure) or self._observation_is_stale(row)
        result = SourceResult(
            row,
            "owreplays" if upstream else "local_registry",
            row["source_url"] if row else upstream.source_url if upstream else "local:replays",
            retrieved_at=row.get("retrieved_at")
            if row
            else upstream.retrieved_at
            if upstream
            else utcnow(),
            applied_filters={"code": code},
            source_updated_at=row.get("source_updated_at") if row else None,
            data_period=row.get("data_period") if row else None,
            data_patch=row.get("data_patch") if row else None,
            cached=row["cached"] if row else upstream.cached if upstream else True,
            stale=bool(failure) or bool(row and row["stale"]) or bool(upstream and upstream.stale),
            warnings=list(upstream.warnings)
            if upstream
            else [
                "This is a retained observation with its original retrieval date; no source refresh was requested or completed."
            ],
        )
        if failure:
            result.warnings.append(f"{failure.code}: {failure.message}")
        return result

    async def ow_patches(self, filters):
        filters = {**filters, "locale": filters["locale"].lower()}
        if filters.get("hero"):
            filters["hero"] = normalize_hero(filters["hero"])
        result = await self.patches.fetch(filters)
        changed = self.registry.apply_patches(result.data)
        if changed:
            result.warnings.append(
                f"{changed} stored replay validations now need rechecking after an explicit replay-invalidation notice."
            )
        return result

    async def ow_esports(self, filters):
        return await self.esports.fetch(filters)

    async def ow_status(self, filters):
        if filters.get("refresh"):
            probes = [
                self.overfast.catalog({"type": "heroes", "locale": "en-us"}),
                self.blizzard.meta({"region": "ASIA", "heroes": ["reinhardt"]}),
                self.owtics.meta({"region": "KR", "platform": "pc", "mode": "competitive"}),
                self.replays.search({"limit": 1, "max_pages": 1}),
                self.patches.fetch({"view": "latest", "locale": "en-us", "max_months": 1}),
                self.esports.fetch({"view": "teams", "region": "KOREA", "limit": 1}),
            ]
            results = await asyncio.gather(*probes, return_exceptions=True)
            for adapter, result in zip(self.adapters, results, strict=True):
                if isinstance(result, SourceError):
                    self.repo.source_record(adapter.name, f"{result.code}: {result.message}")
                elif isinstance(result, BaseException):
                    raise result
                elif not result.cached and not result.stale:
                    self.repo.source_record(adapter.name)
        states = self.repo.source_states()
        sources = {}
        for adapter in self.adapters:
            capabilities = (
                adapter.capabilities
                if isinstance(adapter.capabilities, dict)
                else {"capabilities": list(adapter.capabilities)}
            )
            sources[adapter.name] = {
                **states.get(
                    adapter.name, {"status": "unknown", "last_success": None, "last_error": None}
                ),
                **capabilities,
            }
            if hasattr(adapter, "source_regions"):
                sources[adapter.name]["source_regions"] = list(adapter.source_regions)
            if getattr(adapter, "latest_release", None):
                sources[adapter.name]["latest_release"] = adapter.latest_release
        for state in sources.values():
            if state.get("last_success"):
                state["last_success_age_seconds"] = max(
                    0,
                    round(
                        (
                            datetime.now(UTC) - datetime.fromisoformat(state["last_success"])
                        ).total_seconds()
                    ),
                )
        return SourceResult(
            {
                "sources": sources,
                "stored_counts": self.repo.counts(),
                "registry_scope": "stored_evidenced_players_only",
                "live_spectating": "no_verified_public_source",
                "client_verification": "local_operator_evidence_only",
            },
            "server",
            "local:status",
        )
