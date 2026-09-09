"""Public output contracts, including provenance and source-specific extensions."""

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

JsonObject = dict[str, JsonValue]
Status = Literal["ok", "empty", "stale", "partial", "error"]
Data = TypeVar("Data")


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceRecord(BaseModel):
    """Known fields are validated; additional source/evidence fields are preserved."""

    model_config = ConfigDict(extra="allow", strict=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class OutputError(OutputModel):
    code: str = Field(description="Machine-readable error or result-quality code.")
    message: str = Field(description="Explanation of the failure, empty result or data limitation.")


class Response(OutputModel, Generic[Data]):
    status: Status = Field(
        description="ok: success; empty: no matches; stale: old data; partial: some groups failed; error: the operation failed. CLI exits 0 for ok/empty/stale/partial, 1 for execution failure and 2 for invalid input."
    )
    data: Data | None = Field(description="Tool-specific result; null when unavailable.")
    error: OutputError | None = Field(
        description="Failure or quality details. A non-null value alone does not mean the call failed."
    )
    source: str = Field(description="Source identifier; multiple denotes comparison groups.")
    source_url: str | None = Field(description="Source URL or local: reference; null if unknown.")
    retrieved_at: str | None = Field(
        description="Original retrieval time, not a new observation date."
    )
    source_updated_at: str | None = Field(description="Source-reported update time, if known.")
    data_period: str | None = Field(description="Period actually covered by the data, if known.")
    data_patch: str | None = Field(description="Associated game patch, if known.")
    requested_filters: JsonObject = Field(description="Original arguments supplied by the caller.")
    applied_filters: JsonObject = Field(
        description="Filters and scope actually applied by the source."
    )
    warnings: list[str] = Field(
        description="Limitations, fallback disclosures and evidence cautions."
    )
    cached: bool = Field(
        description="Whether the result was served from stored observations or cache."
    )
    stale: bool = Field(description="Whether any included data exceeded its freshness interval.")


class CatalogItem(SourceRecord):
    key: str = Field(description="Source identifier for the hero, map, mode, tier or region.")


class CatalogResponse(Response[list[CatalogItem] | JsonObject]):
    data: list[CatalogItem] | JsonObject | None = Field(
        description="Catalog items keyed by key; a single hero detail object when hero is selected; or the supported-filter object for type=filters. Additional fields follow the source catalog."
    )


Percent = Annotated[float, Field(ge=0, le=100, description="Percentage, not a 0–1 ratio.")]


class HeroRates(SourceRecord):
    hero: str
    pickrate: Percent | None
    winrate: Percent | None
    banrate: Percent | None
    rate_unit: Literal["percent"]
    source_region: str | None = Field(default=None, description="Source grouping; ASIA is not KR.")
    match_server_region: str | None = Field(
        default=None, description="Actual match server, if evidenced."
    )
    difference: JsonObject | None = Field(
        default=None, description="Percentage-point differences, if comparable."
    )


class MetaGroup(Response[list[HeroRates]]):
    value: str = Field(
        description="Compared map, tier or region. This group carries its own provenance and error."
    )


class MetaResponse(Response[list[HeroRates] | list[MetaGroup]]):
    data: list[HeroRates] | list[MetaGroup] | None = Field(
        description="Hero rate rows for heroes/history; complete per-value result envelopes for comparisons. History rows retain their retrieval dates."
    )
    source_status: dict[str, Status] | None = Field(
        default=None, description="Comparison value to group status, when comparing groups."
    )


class PlayerSearchRecord(SourceRecord):
    player_id: str
    name: str
    battle_tag: str | None
    source: str
    source_url: str


class PlayerSearchResponse(Response[list[PlayerSearchRecord]]):
    data: list[PlayerSearchRecord] | None = Field(
        description="Public search matches. Equal names do not prove identity or nationality."
    )


class PlayerData(SourceRecord):
    """Summary fields; career, hero and role views retain source-specific statistic keys."""

    player_id: str | None = None
    profile: JsonObject | None = None
    statistics: JsonObject | None = None
    profile_source_url: str | None = None
    statistics_source_url: str | None = None


class PlayerResponse(Response[PlayerData]):
    data: PlayerData | None = Field(
        description="summary: player_id, profile, statistics and their source URLs. career: statistics keyed by hero; hero: selected hero statistics; roles: role-keyed or selected-role statistics. Null denotes unavailable statistics, not proof of privacy."
    )


class RankerRecord(SourceRecord):
    id: int
    display_name: str
    country: str | None
    identity_confidence: Literal["verified", "source_claim", "inferred", "unknown"]
    evidence: list[JsonObject]
    heroes: list[JsonObject]
    leaderboard: list[JsonObject] = Field(
        description="Dated rank observations with role, region, rank, season, confidence and source."
    )


class RankersResponse(Response[list[RankerRecord]]):
    data: list[RankerRecord] | None = Field(
        description="Stored evidenced rankers only; not a complete live Top 500 leaderboard."
    )


class ReplayRecord(SourceRecord):
    code: str
    source: str
    source_url: str
    replay_status: Literal[
        "unverified",
        "source_reports_expired",
        "user_reported_working",
        "client_verified",
        "needs_recheck",
    ]
    heroes: list[str] = Field(description="Heroes reported by the replay source.")
    player_country: str | None = Field(
        default=None, description="Evidenced player nationality, separate from match server."
    )
    match_server_region: str | None = Field(
        default=None,
        description="Evidenced match server, independent of source or leaderboard region.",
    )
    evidence: list[JsonObject]
    validation: JsonObject | None = Field(
        default=None,
        description="Operator playback evidence with observed_at, status, source and evidence_ref; not a live client check.",
    )


class ReplaysResponse(Response[list[ReplayRecord]]):
    data: list[ReplayRecord] | None = Field(
        description="Replay observations with identity, region and playback evidence; public codes are not automatically playable."
    )


class ReplayResponse(Response[ReplayRecord]):
    data: ReplayRecord | None = Field(
        description="One replay observation with evidence and original retrieval dates; null when no record is available."
    )


class ReplayCompatibility(OutputModel):
    effect: Literal["invalidated", "preserved", "unknown"]
    notices: list[str]


class PatchRecord(SourceRecord):
    patch_date: str
    title: str
    description: str
    hero_changes: list[JsonObject]
    replay_compatibility: ReplayCompatibility
    source: str
    source_url: str


class PatchesResponse(Response[list[PatchRecord]]):
    data: list[PatchRecord] | None = Field(
        description="Official patch notes and hero changes, newest first, including explicit replay compatibility notices."
    )


class EsportsData(SourceRecord):
    dataset_type: Literal["esports"]
    release_tag: str
    published_at: str
    data_until: str | None
    coverage_period: str | None
    view: Literal["hero_meta", "matches", "maps", "bans", "teams"]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    records: list[JsonObject] = Field(
        description="Rows selected by view: hero metrics, matches, maps, bans or teams. Metric origins/units are carried by each row; these are esports statistics, not ranked win rates."
    )


class EsportsResponse(Response[EsportsData]):
    data: EsportsData | None = Field(
        description="Paginated OWCS Korea release data, with publication date and actual coverage period kept separate."
    )


class SourceHealth(SourceRecord):
    status: str
    last_success: str | None
    last_error: str | None
    capabilities: list[str]


class StatusData(OutputModel):
    sources: dict[str, SourceHealth]
    stored_counts: dict[str, int]
    registry_scope: str
    live_spectating: str
    client_verification: str


class StatusResponse(Response[StatusData]):
    data: StatusData | None = Field(
        description="Observed source health, capabilities and local record counts. Unknown health is not evidence of an outage."
    )


RESPONSES = {
    "ow_catalog": CatalogResponse,
    "ow_meta": MetaResponse,
    "ow_players_search": PlayerSearchResponse,
    "ow_player_get": PlayerResponse,
    "ow_rankers_search": RankersResponse,
    "ow_replays_search": ReplaysResponse,
    "ow_replay_get": ReplayResponse,
    "ow_patches": PatchesResponse,
    "ow_esports": EsportsResponse,
    "ow_status": StatusResponse,
}
