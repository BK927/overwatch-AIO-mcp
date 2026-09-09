PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'unknown',
    last_attempt TEXT, last_success TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL,
    body BLOB NOT NULL, headers TEXT NOT NULL, retrieved_at TEXT NOT NULL,
    expires_at REAL NOT NULL, created_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY, battle_tag TEXT UNIQUE, overfast_player_id TEXT UNIQUE,
    display_name TEXT NOT NULL, country TEXT, identity_confidence TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK (identity_confidence IN ('verified','source_claim','inferred','unknown'))
);
CREATE TABLE IF NOT EXISTS player_evidence (
    id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES players(id),
    evidence_type TEXT NOT NULL, value TEXT NOT NULL, confidence TEXT NOT NULL,
    source TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
    UNIQUE(player_id,evidence_type,value,source_url)
);
CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
    id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES players(id),
    leaderboard_region TEXT NOT NULL, role TEXT NOT NULL, season TEXT,
    rank INTEGER CHECK(rank > 0), tier TEXT, observed_at TEXT NOT NULL,
    source TEXT NOT NULL, source_url TEXT NOT NULL, confidence TEXT NOT NULL,
    UNIQUE(player_id,leaderboard_region,role,observed_at,source_url)
);
CREATE TABLE IF NOT EXISTS player_hero_tags (
    player_id INTEGER NOT NULL REFERENCES players(id), hero TEXT NOT NULL,
    evidence_type TEXT NOT NULL, confidence TEXT NOT NULL,
    source TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
    PRIMARY KEY(player_id,hero,source_url)
);
CREATE TABLE IF NOT EXISTS replays (
    code TEXT PRIMARY KEY, player_id INTEGER REFERENCES players(id),
    payload TEXT NOT NULL, discovered_at TEXT NOT NULL, refreshed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replay_evidence (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL REFERENCES replays(code),
    evidence_type TEXT NOT NULL, value TEXT NOT NULL, confidence TEXT NOT NULL,
    source TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
    UNIQUE(code,evidence_type,value,source_url,observed_at)
);
CREATE TABLE IF NOT EXISTS replay_validations (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL REFERENCES replays(code),
    status TEXT NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    CHECK(status IN ('user_reported_working','client_verified','needs_recheck'))
);
CREATE TABLE IF NOT EXISTS compatibility_notices (
    notice_key TEXT PRIMARY KEY, patch_date TEXT NOT NULL, source_url TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hero_meta_snapshots (
    id INTEGER PRIMARY KEY, series_key TEXT NOT NULL, source TEXT NOT NULL,
    hero TEXT NOT NULL, filters TEXT NOT NULL, payload TEXT NOT NULL,
    retrieved_at TEXT NOT NULL, source_updated_at TEXT, data_period TEXT, data_patch TEXT,
    UNIQUE(series_key,hero,retrieved_at)
);
CREATE INDEX IF NOT EXISTS meta_history ON hero_meta_snapshots(series_key,retrieved_at);
CREATE INDEX IF NOT EXISTS leaderboard_player ON leaderboard_snapshots(player_id,observed_at);
