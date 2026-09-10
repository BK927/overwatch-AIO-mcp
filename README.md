# Overwatch 2 Data MCP Server & Agent Skill — Hero Meta, Player Stats, Replays, Patch Notes & OWCS Korea

**English** | [한국어](README.ko.md)

Query **Overwatch 2 hero meta, public player stats, evidenced rankers, replay codes, official patch notes, and OWCS Korea data** from an MCP client or a standalone agent skill. Both interfaces share ten read-only query operations and one SQLite store, with source URLs, observation dates, applied filters, cache state, and warnings preserved in every result.

Use it to compare heroes by region and rank, inspect public BattleTag profiles, retrieve curated ranker/replay evidence, connect patch changes to the available data period, and keep ranked statistics separate from esports metrics. Data is fetched only when requested; there is no scheduled collector, game-client automation, or private-profile access.

Built with **Python 3.11+ and uv**. Choose the skill, MCP server, or both.

[Download releases](https://github.com/BK927/overwatch-aio/releases) · [Distribution and registry guide](docs/distribution.md)

> The legacy `v0.3.1` MCPB keeps its original `Overwatch AIO MCP` display
> label. Version `0.3.2` and later use the `Overwatch 2 Data MCP Server` label.
> Published release assets remain immutable.

## Choose an interface

| Interface | Use it for | Installation |
|---|---|---|
| Agent skill | Let Codex select queries and interpret evidence | Root skill and basic Python dependencies |
| MCP server | Call `ow_*` tools from an MCP client | The `mcp` extra and a client registration |
| Both | Choose the interface for each environment | Both installations can share one DB |

The skill runs its CLI independently. MCP does not require a skill installation. There is no automatic switching or duplicate execution.

## Install the skill

Use the [skills CLI](https://github.com/vercel-labs/skills) for a project installation:

```text
npx skills add BK927/overwatch-aio --skill overwatch-aio-skill --agent codex
```

Add `--global` for a personal installation. Python and `uv` are needed for queries; Node.js is only needed for this installer. Use the skill directory reported by the installer.

Alternatively, ask Codex's Skill Installer:

> Install the skill at the root of BK927/overwatch-aio as overwatch-aio-skill. Use the specified commit if I provide one.

For a pinned installation, use its script with your local Codex skills path:

```text
python <Codex skills>/.system/skill-installer/scripts/install-skill-from-github.py --repo BK927/overwatch-aio --path . --name overwatch-aio-skill --ref <commit-SHA>
uv sync --frozen --no-dev --project "<installed-skill-directory>"
```

The Skill Installer's default personal directory is `~/.codex/skills/overwatch-aio-skill`. Invoke `$overwatch-aio-skill` or ask an Overwatch data question. Your database, caches, and development environment are not distributed.

Example requests:

- “Compare Reinhardt and Ramattra in Asia Master, with sources and the actual data period.”
- “Show Korea-specific data if available, and distinguish it from Asia data.”
- “Find Reinhardt replays linked to evidenced Korean players and show their playback verification.”
- “Explain Reinhardt's latest patch changes alongside OWCS Korea data.”

## Install the MCP server

MCPB-compatible clients supporting the UV runtime (manifest 0.4) can install the `.mcpb` file from a GitHub release. Dependencies are prepared at first launch. The source installation below works with clients using ordinary stdio or Streamable HTTP configuration.

Clone the repository or use an installed skill directory. Replace `<project-directory>` with its absolute path:

```text
git clone https://github.com/BK927/overwatch-aio.git
uv sync --frozen --no-dev --extra mcp --project "<project-directory>"
uv run --frozen --no-dev --extra mcp --project "<project-directory>" overwatch-aio-mcp serve
```

The default transport is **stdio**. Use the [MCP JSON example](examples/mcp-client.json) or [Codex TOML example](examples/mcp-codex.toml). Codex also accepts:

```text
codex mcp add overwatch-aio -- uv run --frozen --no-dev --extra mcp --project "<project-directory>" overwatch-aio-mcp serve
```

For **Streamable HTTP**, start the server and connect to `http://127.0.0.1:8765/mcp`:

```text
uv run --frozen --no-dev --extra mcp --project "<project-directory>" overwatch-aio-mcp serve --transport streamable-http
```

`--host` and `--port` change the listen address. The default is local-only `127.0.0.1:8765`. The server has no built-in authentication; public hosting requires separate authentication and TLS. See the [official Codex MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) for client configuration.

Keep `--extra mcp` in MCP launch commands and in `uv sync` commands for environments used by MCP. A plain `uv sync` can remove optional dependencies. The skill command stays unchanged.

## Deployment

| Environment | Status | Requirements and limits |
|---|---|---|
| Local desktop, stdio | Supported | Primary setup. The MCP client starts the process and the per-user SQLite database persists locally. |
| Local desktop, Streamable HTTP | Supported | Keep the default loopback bind unless an authenticated gateway is already in place. The server itself has no authentication. |
| Private home server | Operator-managed only | Use a service manager, one durable SQLite database, backups, and TLS plus authentication at a trusted reverse proxy or private network boundary. Do not expose the raw MCP port. |
| Raspberry Pi 4 Model B (2 GB RAM) | Tested hardware only | This records the hardware used for testing; it is not a recommendation, minimum requirement, or performance guarantee. |
| Google Compute Engine VM | Manual, unverified | A single VM with persistent disk can host the process, but no image or Terraform module is provided. Add TLS/auth, backups, single-writer DB ownership, and live upstream egress checks. |
| Cloudflare Tunnel | Possible operator-managed ingress; unverified | Tunnel can sit in front of a home server or VM after authentication is designed and tested. It is only ingress to that running host, not a Cloudflare Workers runtime. |
| Google Cloud Run | Unsupported as-is | Local SQLite is instance-local and the repository has no stateless container/storage design. External durable storage, authentication, and lifecycle changes are required. |
| Cloudflare Workers | Unsupported as-is | The current CPython MCP SDK and local SQLite design are not a Worker deployment. A port needs a stateless HTTP handler, Worker-native storage, and Worker-compatible authentication. |

Remote and home-server operation is only an operator deployment pattern—not a built-in hosted service. Binding to a non-loopback address is unsafe unless requests first pass through operator-controlled authentication and TLS and the database is stored durably.

## Available tools

| Tool | Capability |
|---|---|
| `ow_catalog` | Heroes, maps, modes, tiers, regions, and supported filters |
| `ow_meta` | Hero rates, map/tier/region comparisons, and stored observations |
| `ow_players_search` | Public name and BattleTag search |
| `ow_player_get` | Public profile and career/hero/role statistics |
| `ow_rankers_search` | Locally curated, evidenced ranker registry |
| `ow_replays_search` | Public replay discovery and stored evidence filters |
| `ow_replay_get` | Replay details and recorded playback evidence |
| `ow_patches` | Official patch notes and replay compatibility notices |
| `ow_esports` | OWCS Korea hero, match, map, ban, and team data |
| `ow_status` | Source health and stored coverage; offline by default |

Both interfaces publish the same [input/output schemas](docs/query-schemas.json). MCP returns the result as `structuredContent` and JSON text. Only `status=error` sets `isError=true`; empty, stale, and partial results retain their quality status and warnings. `overwatch-aio-mcp schema` exports the contracts. Use the [local curation CLI](docs/curation.md) for evidence writes.

## Direct CLI queries

```text
uv run --frozen --no-dev --project "<skill-directory>" overwatch-aio-skill query ow_status
uv run --frozen --no-dev --project "<skill-directory>" overwatch-aio-skill query ow_meta --input-file "<filters.json>"
uv run --frozen --no-dev --project "<skill-directory>" overwatch-aio-skill schema
```

Example `filters.json`:

```json
{"heroes":["reinhardt","ramattra"],"region":"ASIA","tier":"MASTER"}
```

Omitting the file supplies `{}`; `--input-file -` reads stdin. UTF-8 and UTF-8 BOM are supported. Stdout contains one JSON object; diagnostics go to stderr. Exit codes are `0` for ok/empty/stale/partial, `1` for execution failure, and `2` for invalid input. Inspect `status` and warnings. The [query guide](references/queries.md) provides more examples in Korean.

## Data storage and compatibility

DB selection is **`--db` → `OW_DB_PATH` → `~/.overwatch-aio-skill/overwatch.db`**. The default is shared by processes running as the same user, independently of installation directories. Use absolute paths for existing records. Place `--db` after the executable name, before `query` or `serve`.

Existing caches, meta observations, players, replays, and manual evidence remain readable. SQLite WAL and transactions support concurrent skill writes and MCP reads. The old `collection_jobs` table is preserved but unused. There is no automatic database move or deletion. History contains observations made on request; unobserved periods are not backfilled. Cached responses do not create new observations. Replay recheck flags from invalidation notices update when patches are queried. HTTP cache retention is bounded to 256 entries, 64 MiB, and seven days.

The repository is `BK927/overwatch-aio`; the Python distribution and skill remain `overwatch-aio-skill`. Existing skill commands work unchanged. Older MCP users can keep `overwatch-aio-mcp serve` after updating their path and adding `--extra mcp`. Point `--db` at the old `data/overwatch.db` explicitly if needed.

## FAQ

### Does `ASIA` mean the Korean server?

No. `ASIA` is a provider region label, not proof of a Korean match server. OWTICS `KOREA` is also a provider grouping; results keep those labels distinct from evidenced player identity.

### Does this provide a live Top 500 leaderboard?

No. `ow_rankers_search` searches the locally curated registry and returns its recorded evidence. It does not claim complete, live Top 500 coverage.

### Are returned replay codes guaranteed to work?

No. A public code alone does not prove current playability. Playback state is reported only when an operator has registered evidence; this project does not launch or control Overwatch 2.

### Does it collect data continuously or work fully offline?

There is no scheduler. Refreshing queries contact their public sources on demand. Stored observations, curated evidence, and compatible cached/status queries remain available according to each operation's offline behavior, but cannot become fresher while offline.

### Can it read private profiles or be exposed as a public MCP service?

It does not bypass private profiles. Public hosting is not turnkey: the current HTTP server has no built-in auth, so an operator must provide TLS, authentication, durable storage, access controls, and backups.

## Sources and limits

Sources include **OverFast, Blizzard hero statistics and patch notes, OWTICS, OWReplays, and OWCS Korea releases**. Requests to these services can include query filters and public player identifiers needed for the selected operation.

- ASIA does not establish a Korean match server. OWTICS KOREA is a provider grouping.
- Rankers cover the evidenced local registry, not a complete live Top 500 list.
- Public replay codes do not prove playability. Playback evidence is recorded by an operator; the game client is not controlled.
- Private profiles are not accessed. Missing statistics and explicit privacy are distinguished.
- Hero rates use percentages from 0–100. No invented matchup win rates. Esports metrics stay separate from ranked statistics.
- Source changes, rate limits, and parsing failures remain visible. Public refreshes do not overwrite manual identity or playback evidence.

This is an unofficial community project and is not affiliated with or endorsed by Blizzard Entertainment. Overwatch and related marks belong to their respective owners.

## Development and verification

```text
uv sync --locked --extra dev
uv run --frozen --extra dev ruff check src tests scripts
uv run --frozen --extra dev ruff format --check src tests scripts
uv run --frozen --extra dev python scripts/validate_skill.py
uv run --frozen --extra dev pytest -q
uv sync --locked --extra dev --extra mcp
uv run --frozen --extra dev --extra mcp pytest -q
uv build
```

CI covers Windows/Linux × Python 3.11/3.13 × skill-only/MCP installations. It checks dependency isolation, shared contracts, real stdio/HTTP connections and shutdown, and shared DB access. Live upstream checks are separate and opt-in:

```text
uv run --frozen --no-dev python scripts/live_smoke.py --output docs/live-verification.json
```

[Implementation specification](SPEC.md) · [Verification record](docs/verification.md) · [Source contracts](research/source-contracts.md) · [SQLite schema](src/overwatch_skill/db/schema.sql)
