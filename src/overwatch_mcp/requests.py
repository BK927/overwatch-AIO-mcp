"""Public tool arguments. Unknown arguments are rejected instead of silently ignored."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CatalogRequest(Request):
    type: Literal["heroes", "maps", "modes", "tiers", "regions", "filters"] = "heroes"
    locale: str = "ko-KR"
    hero: str | None = None
    mode: str | None = None
    role: str | None = None


class MetaRequest(Request):
    view: Literal["heroes", "map_comparison", "tier_comparison", "region_comparison", "history"] = (
        "heroes"
    )
    heroes: list[str] | None = Field(default=None, max_length=60)
    platform: str = "pc"
    mode: str = "competitive"
    region: str = "ASIA"
    tier: str | None = None
    map: str = "all-maps"
    role: str | None = None
    source: Literal["auto", "overfast", "owtics", "blizzard"] = "auto"
    allow_region_fallback: bool = False
    maps: list[str] | None = Field(default=None, max_length=6)
    tiers: list[str] | None = Field(default=None, max_length=6)
    regions: list[str] | None = Field(default=None, max_length=6)
    order_by: str = "winrate:desc"
    after: date | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def check_comparison(self):
        dimensions = {
            "map_comparison": "maps",
            "tier_comparison": "tiers",
            "region_comparison": "regions",
        }
        for view, dimension in dimensions.items():
            values = getattr(self, dimension)
            if self.view == view and (not values or len(values) < 2):
                raise ValueError(f"{view} requires 2–6 values in {dimension}")
            if self.view != view and values is not None:
                raise ValueError(f"{dimension} is only supported for {view}")
        if self.after is not None and self.view != "history":
            raise ValueError("after is only supported for stored history")
        return self


class PlayersSearchRequest(Request):
    query: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)


class PlayerGetRequest(Request):
    player_id: str = Field(min_length=1, max_length=100)
    view: Literal["summary", "career", "hero", "roles"] = "summary"
    platform: str = "pc"
    mode: str = "competitive"
    hero: str | None = None
    role: str | None = None


class RankersSearchRequest(Request):
    region: str | None = None
    country: str | None = None
    role: str | None = None
    hero: str | None = None
    season: str | None = None
    rank_max: int = Field(default=500, ge=1, le=10000)
    verified_only: bool = True
    limit: int = Field(default=20, ge=1, le=100)


class ReplaysSearchRequest(Request):
    hero: str | None = None
    map: str | None = None
    player: str | None = None
    player_id: int | None = Field(default=None, ge=1)
    player_pool: Literal["all", "kr_rankers"] = "all"
    tier: str | None = None
    tier_min: str | None = None
    platform: str | None = None
    uploaded_after: date | None = None
    player_country: str | None = None
    match_region: str | None = None
    region_evidence: Literal["verified_only", "include_claims"] = "verified_only"
    ranker_status: Literal["any", "verified"] = "any"
    playable_status: (
        list[
            Literal[
                "unverified",
                "source_reports_expired",
                "user_reported_working",
                "client_verified",
                "needs_recheck",
            ]
        ]
        | None
    ) = Field(default=None, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)
    refresh: bool = True


class ReplayGetRequest(Request):
    code: str = Field(pattern=r"^[A-Za-z0-9]{6}$")
    refresh: bool = True


class PatchesRequest(Request):
    view: Literal["latest", "history"] = "latest"
    hero: str | None = None
    after: date | None = None
    before: date | None = None
    locale: str = "en-US"
    limit: int = Field(default=20, ge=1, le=100)


class EsportsRequest(Request):
    region: Literal["KOREA"] = "KOREA"
    view: Literal["hero_meta", "matches", "maps", "bans", "teams"] = "hero_meta"
    hero: str | None = None
    map: str | None = None
    team: str | None = None
    stage: str | None = None
    after: date | None = None
    before: date | None = None
    limit: int = Field(default=50, ge=1, le=500)


class StatusRequest(Request):
    refresh: bool = False


REQUESTS = {
    "ow_catalog": CatalogRequest,
    "ow_meta": MetaRequest,
    "ow_players_search": PlayersSearchRequest,
    "ow_player_get": PlayerGetRequest,
    "ow_rankers_search": RankersSearchRequest,
    "ow_replays_search": ReplaysSearchRequest,
    "ow_replay_get": ReplayGetRequest,
    "ow_patches": PatchesRequest,
    "ow_esports": EsportsRequest,
    "ow_status": StatusRequest,
}
