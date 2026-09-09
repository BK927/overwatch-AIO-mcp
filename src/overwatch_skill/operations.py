"""Descriptions shared by the CLI schema and skill reference."""

DESCRIPTIONS = {
    "ow_catalog": "List public hero details, maps and supported filters. Map modes are map types, not competitive seasonal pools.",
    "ow_meta": "Compare hero aggregate percentages, maps, tiers or source regions; history uses stored retrieval snapshots. ASIA is not KR. No matchup winrates.",
    "ow_players_search": "Search public BattleTags or names. Equal names do not establish identity or nationality.",
    "ow_player_get": "Read a public player profile and filtered career/hero/role statistics; report private and missing data distinctly.",
    "ow_rankers_search": "Search the locally curated, evidenced ranker registry only, including dated rank observations. Not the complete live Top 500.",
    "ow_replays_search": "Find public replay codes and filter stored identity/region/validation evidence. A Korean player does not prove a Korean match server.",
    "ow_replay_get": "Get replay details and independently recorded playback evidence. Public source flags never imply client verification.",
    "ow_patches": "Read official English patch notes and hero changes. Explicit replay invalidation notices mark older checks needs_recheck.",
    "ow_esports": "Read the released OWCS Korea dataset with match coverage and release dates. Esports metrics are separate from ranked ladder statistics.",
    "ow_status": "Show observed source health, capabilities and stored coverage. refresh performs bounded cached probes; default performs no network access.",
}
