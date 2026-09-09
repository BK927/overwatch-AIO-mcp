"""Official raw hero statistics with dynamically discovered queue identifiers.

The English site's form and JSON response were verified on 2026-09-09. The
renderer formats cells as percentages and renders negative sentinels as absent.
Unlike the wrapper source, this adapter can retain the original missing values.
"""

import math
import re

from bs4 import BeautifulSoup

from ..models import SourceError, SourceResult
from ..normalization import (
    normalize_hero,
    normalize_map,
    normalize_mode,
    normalize_platform,
    normalize_region,
    normalize_role,
    normalize_tier,
)

BASE_URL = "https://overwatch.blizzard.com/en-us/rates/"
METRICS = ("winrate", "pickrate", "banrate")


class BlizzardAdapter:
    name = "blizzard"
    capabilities = {
        "capabilities": ["hero_meta", "raw_rates", "source_filter_diagnostics"],
        "regions": ["ASIA", "AMERICAS", "EUROPE"],
        "rate_unit": "percent",
        "queue_identifiers": "discovered_from_official_form",
        "notes": ["Role Queue only. No Korean-only population or match-server data."],
    }

    def __init__(self, client):
        self.client = client

    def _error(self, message, code="PARSE_ERROR", url=BASE_URL):
        return SourceError(code, message, self.name, url)

    def discover(self, html):
        """Read current option values, including rq, without assuming numeric IDs."""
        soup = BeautifulSoup(html, "html.parser")
        options = {}
        for key in ("rq", "input", "region", "tier", "map"):
            select = soup.select_one(f"#filter-{key}-select")
            if select is None:
                raise self._error(
                    f"Official statistics {key} selector is missing; current source capabilities cannot be verified."
                )
            options[key] = [
                {
                    "value": str(option["value"]),
                    "label": option.get_text(" ", strip=True),
                    "rqs": str(option.get("data-rqs", "")).split(","),
                }
                for option in select.select("option[value]")
            ]
            if not options[key]:
                raise self._error(f"Official statistics {key} options are empty.")
        queues = {}
        for option in options["rq"]:
            label = re.sub(r"\s+", " ", option["label"]).casefold()
            mode = None
            if re.fullmatch(r"competitive\s*[-–]\s*role queue", label):
                mode = "competitive"
            elif re.fullmatch(r"quick play\s*[-–]\s*role queue", label):
                mode = "quickplay"
            if mode:
                if mode in queues and queues[mode] != option["value"]:
                    raise self._error(
                        f"Official form advertises ambiguous {mode} queue identifiers."
                    )
                queues[mode] = option["value"]
        if not queues:
            raise self._error(
                "No known Role Queue labels were found in the official form; queue IDs cannot safely be selected."
            )
        return options, queues

    async def meta(self, filters: dict) -> SourceResult:
        allowed = {
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
        }
        unsupported = [
            key for key, value in filters.items() if key not in allowed and value is not None
        ]
        if unsupported:
            raise self._error(
                f"Unsupported Blizzard statistics filters: {', '.join(unsupported)}",
                "UNSUPPORTED_FILTER",
            )
        if filters.get("source") not in (None, "auto", self.name):
            raise self._error("The selected source is not Blizzard.", "UNSUPPORTED_FILTER")
        if filters.get("view") not in (None, "heroes"):
            raise self._error(
                "This source fetches one heroes filter group; comparisons and history belong to the service.",
                "UNSUPPORTED_FILTER",
            )
        region = normalize_region(filters.get("region") or "ASIA")
        platform = normalize_platform(filters.get("platform") or "pc")
        mode = normalize_mode(filters.get("mode") or "competitive")
        tier = normalize_tier(filters.get("tier") or "ALL")
        map_key = normalize_map(filters.get("map") or "all-maps")
        role = normalize_role(filters["role"]) if filters.get("role") else None
        fallback = filters.get("allow_region_fallback", False)
        if not isinstance(fallback, bool):
            raise self._error("allow_region_fallback must be a boolean.", "UNSUPPORTED_FILTER")
        warnings = [
            "Official statistics describe source regions and Role Queue populations; they do not establish Korean-only data or a match server."
        ]
        if region == "KR":
            if not fallback:
                raise self._error(
                    "Blizzard does not provide Korean-only statistics. Explicit allow_region_fallback=true is required to use ASIA.",
                    "UNSUPPORTED_FILTER",
                )
            region = "ASIA"
            warnings.append(
                "한국 전용 데이터가 없어 아시아 데이터를 대신 반환했습니다. Requested region=KR; applied region=ASIA. This is not Korean server statistics."
            )
        if mode == "quickplay" and (tier != "ALL" or map_key != "all-maps"):
            raise self._error(
                "The official Quick Play view does not support tier or individual-map filters.",
                "UNSUPPORTED_FILTER",
            )
        if tier == "CHAMPION":
            raise self._error(
                "Blizzard groups Champion together with Grandmaster; Champion-only statistics are unsupported.",
                "UNSUPPORTED_FILTER",
            )
        if tier == "GRANDMASTER":
            warnings.append("The source GRANDMASTER tier includes Champion players.")
        heroes = filters.get("heroes") or []
        if not isinstance(heroes, list) or len(heroes) > 100:
            raise self._error(
                "heroes must be a list of at most 100 identifiers.", "UNSUPPORTED_FILTER"
            )
        heroes = list(dict.fromkeys(normalize_hero(hero) for hero in heroes))
        if "all-heroes" in heroes:
            raise self._error(
                "Omit heroes for all heroes; aggregate all-heroes is not an individual hero.",
                "UNSUPPORTED_FILTER",
            )
        order_by = filters.get("order_by") or "winrate:desc"
        if not isinstance(order_by, str) or not re.fullmatch(
            r"(?:winrate|pickrate|banrate):(asc|desc)", order_by
        ):
            raise self._error(
                "order_by must select winrate, pickrate, or banrate and asc/desc.",
                "UNSUPPORTED_FILTER",
            )
        form = await self.client.get_text(self.name, BASE_URL, ttl=21600)
        options, queues = self.discover(form.data)
        if mode not in queues:
            raise self._error(
                f"The official form currently does not advertise {mode} Role Queue.",
                "UNSUPPORTED_FILTER",
            )
        desired = {
            "input": "PC" if platform == "pc" else "Console",
            "region": region.title(),
            "tier": tier.title(),
            "map": map_key,
        }
        params = {"rq": queues[mode]}
        for key, value in desired.items():
            matched = [
                option for option in options[key] if option["value"].casefold() == value.casefold()
            ]
            if len(matched) != 1:
                raise self._error(
                    f"The official form does not advertise the requested {key}={value}.",
                    "UNSUPPORTED_FILTER",
                )
            if key == "map" and matched[0]["rqs"] != [""] and queues[mode] not in matched[0]["rqs"]:
                raise self._error(
                    f"The official form does not support map={value} for the selected queue.",
                    "UNSUPPORTED_FILTER",
                )
            params[key] = matched[0]["value"]
        fetched = await self.client.get_json(
            self.name,
            BASE_URL + "data/",
            params=params,
            ttl=3600,
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        payload = fetched.data
        if not isinstance(payload, dict) or not isinstance(payload.get("rates"), dict):
            raise self._error("Official rates object is missing.", url=fetched.url)
        body = payload["rates"]
        selected = body.get("selected")
        rows, columns = body.get("rates"), payload.get("columns")
        if (
            not isinstance(selected, dict)
            or not isinstance(rows, list)
            or not isinstance(columns, list)
        ):
            raise self._error("Official statistics response schema changed.", url=fetched.url)
        for key, expected in params.items():
            if key not in selected or str(selected[key]).casefold() != str(expected).casefold():
                raise self._error(
                    f"Official statistics did not apply {key}={expected}; selected value was {selected.get(key)!r}.",
                    url=fetched.url,
                )
        if any(
            not isinstance(column, dict) or not isinstance(column.get("id"), str)
            for column in columns
        ):
            raise self._error("Official statistics column schema changed.", url=fetched.url)
        metric_columns = {column["id"] for column in columns}
        if not {"winrate", "pickrate"} <= metric_columns:
            raise self._error(
                "Official statistics omitted win-rate or pick-rate columns.", url=fetched.url
            )
        data, seen = [], set()
        for raw in rows:
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("id"), str)
                or not isinstance(raw.get("cells"), dict)
                or not isinstance(raw.get("hero"), dict)
            ):
                raise self._error("Official hero-statistics row schema changed.", url=fetched.url)
            hero = normalize_hero(raw["id"])
            if hero in seen:
                raise self._error(
                    "Official statistics returned duplicate hero IDs.", url=fetched.url
                )
            seen.add(hero)
            if heroes and hero not in heroes:
                continue
            hero_role = str(raw["hero"].get("role", "")).lower()
            if hero_role not in ("tank", "damage", "support"):
                raise self._error(
                    "Official hero statistics omitted the hero role.", url=fetched.url
                )
            if role and hero_role != role:
                continue
            record = {
                "hero": hero,
                "role": hero_role,
                "name": raw["hero"].get("name"),
                "source": self.name,
                "source_url": fetched.url,
                "rate_unit": "percent",
                "source_region": region,
                "match_server_region": None,
                "sample_size": None,
                "raw_rates": {},
                "metric_availability": {},
            }
            for metric in METRICS:
                value = raw["cells"].get(metric)
                record["raw_rates"][metric] = value
                if metric not in metric_columns:
                    record[metric] = None
                    record["metric_availability"][metric] = "not_reported"
                    continue
                if metric not in raw["cells"]:
                    raise self._error(
                        f"Official rate column {metric} has no corresponding cell.", url=fetched.url
                    )
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or (value != -1 and not 0 <= value <= 100)
                ):
                    raise self._error(f"Invalid raw percentage in {metric}.", url=fetched.url)
                record[metric] = None if value in (None, -1) else value
                record["metric_availability"][metric] = (
                    "source_missing" if value in (None, -1) else "available"
                )
            data.append(record)
        absent = [hero for hero in heroes if hero not in seen]
        if absent:
            warnings.append(
                f"No source statistics matched: {', '.join(absent)}. No values were fabricated."
            )
        if "banrate" not in metric_columns:
            warnings.append(
                "The source does not report a ban-rate column for this mode. banrate is null; any raw placeholder is retained in raw_rates."
            )
        if any("source_missing" in record["metric_availability"].values() for record in data):
            warnings.append(
                "Raw -1 or null missing-data values remain null in normalized rates; original values are retained in raw_rates."
            )
        metric, direction = order_by.split(":")
        available = [row for row in data if row[metric] is not None]
        absent_rows = [row for row in data if row[metric] is None]
        data = (
            sorted(available, key=lambda row: row[metric], reverse=direction == "desc")
            + absent_rows
        )
        for index, row in enumerate(data, 1):
            row.update(
                {
                    "ranking": index if row[metric] is not None else None,
                    "ranking_scope": "returned_heroes",
                    "ranking_metric": metric,
                }
            )
        return SourceResult(
            data,
            self.name,
            fetched.url,
            retrieved_at=fetched.retrieved_at,
            requested_filters=dict(filters),
            applied_filters={
                "platform": platform,
                "mode": mode,
                "region": region,
                "tier": tier,
                "map": map_key,
                "role": role,
                "heroes": heroes,
                "order_by": order_by,
                "queue": "ROLE_QUEUE",
                "source_selected": dict(selected),
            },
            warnings=warnings,
            cached=form.cached and fetched.cached,
            stale=form.stale or fetched.stale,
        )
