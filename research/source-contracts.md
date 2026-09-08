# Public replay and official patch source contracts

Verified by anonymous HTTP GET on 2026-09-09. This describes observed website
behavior, not a guaranteed API contract. Source failures must not become empty
results. No browser, login, cookies, or game-client interaction is required.

## OWReplays

- Site: <https://owreplays.tv/>
- Website JavaScript: <https://owreplays.tv/app.js?v=6a3919b9> at capture time.
- Catalog: `GET https://owreplays.tv/api/v2/gamedata` returns arrays `heroes`,
  `maps`, `tiers`, `leagues`, `gamemodes`, `gametypes`. Entries have numeric `ID`.
  Heroes have `hero`; maps have `map`; tiers have `tier`, `tierId`, `title`, and
  `division`. Resolve filters from the live cached catalog, never fixed IDs.
- Search: `GET https://owreplays.tv/api/v2/replays` returns an object with
  `replays` and integer `pages`. Normal empty search is `{"replays":[],"pages":0}`.
- The site's own JavaScript constructs `heroes`, `maps`, `tiers`, `leagues`,
  `modes`, `gametypes`, `platforms`, `player`, `title`, `participants`,
  `composition`, `playlist`, `q`, `page`, `sort`, `sortasc`, `vro`, `bookmarked`.
  Array selectors are comma-separated IDs; platforms are `pc`, `xbox`,
  `playstation`, `switch`. Observed sort options include `Top`, `Best`,
  `Uploaded`, `Relevant`, `Playlist`. The adapter exposes a tested subset.
- Actual tested filter request:
  <https://owreplays.tv/api/v2/replays?heroes=19&platforms=pc&sort=Uploaded>
  returned Reinhardt / PC records. Page size observed: 10.
- Details: <https://owreplays.tv/api/v2/replay/8D8VRC> returns one raw record.
  Its canonical public page is <https://owreplays.tv/8D8VRC>.
- Fields include `Code`, `Heroes` (IDs), `Map` (ID), `Tier` (ID or null),
  `Player`, `UserHandle`, `Uploaded` (Unix seconds), `Platform`, `Archived`,
  `PatchLevel`, `Verified`, and `CodeVerificationStatus`.
- The same record can have `Archived=true`, `Verified=true`, and
  `CodeVerificationStatus="valid"`. Archive status therefore takes precedence;
  no website verification field establishes current client playback. The
  site detail page explicitly reports expired archived codes.
- Unknown code `ZZZZZZ` returned HTTP 403 with exactly
  `{"error":"Invalid replay"}`. This specific response is an empty lookup;
  arbitrary HTTP 403 remains source failure.
- HTML also embeds JSON in `script#__INITIAL_STATE__` with `overwatch-store`,
  `replay-table-store`, and `detail-replay-store`. This is a potential future
  fallback; the current adapter uses verified anonymous JSON endpoints.
- These records do not establish BattleTag, country, ranker identity, or match
  server. All such fields remain null. Matching display names is not identity.

## Blizzard official patch notes

- Landing page: <https://overwatch.blizzard.com/en-us/news/patch-notes/>
- Its own page JavaScript loads month fragments from
  `/{locale}/news/patch-body/live/{year}/{month}` using
  `X-Requested-With: XMLHttpRequest`.
- Actual tested fragment:
  <https://overwatch.blizzard.com/en-us/news/patch-body/live/2026/8>.
- HTML is server rendered. Parse `.PatchNotes-body`, `.PatchNotes-patch`,
  anchor `id="patch-YYYY-MM-DD"`, `.PatchNotes-patchTitle`.
- Hero blocks: `.PatchNotesHeroUpdate`, `.PatchNotesHeroUpdate-name`,
  `.PatchNotesHeroUpdate-body`. Preserve preceding section titles, because
  core-game and Stadium hero changes can occur in the same monthly document.
- Source link format:
  <https://overwatch.blizzard.com/en-us/news/patch-notes/live/2026/8/#patch-2026-08-19>.
- Empty future month <https://overwatch.blizzard.com/en-us/news/patch-body/live/2026/10>
  returns `.patch-notes-error` text `No Patch Notes Found` within `.PatchNotes-body`.
  Unexpected body markup is a parse error.
- August 19, 2026 explicitly reports wiped replay codes; August 20 explicitly
  preserves codes from the August 19 update. The adapter records an effect and
  the exact notice, and leaves ambiguous wording `unknown`.
- Dates are available but game version often is not: use null rather than
  inferring a version from dates or neighboring source data.
- English parsing is verified. Other locales are explicitly unsupported until
  notice classification and source contracts are tested for them.
- Traversal is bounded to at most 24 requested months and 100 returned patches.
  Actual checked months and incomplete coverage appear in response metadata.

## Validation

Read-only live smoke with the shared HTTP client and in-memory repository:

- Replay `8D8VRC`: three heroes, `source_reports_expired`.
- Reinhardt / PC query: results returned and filter IDs checked.
- Nonexistent `ZZZZZZ`: `EMPTY_RESULT`.
- August 2026 patch retrieval: explicit preserved / invalidated effects parsed.

Offline contract tests additionally cover source schema drift, filter mismatch,
bounded pagination, empty results, forbidden identity inference, replay status
precedence, section context, and ambiguous compatibility statements.

## Blizzard direct raw hero statistics

- Official form: <https://overwatch.blizzard.com/en-us/rates/>.
- Data: <https://overwatch.blizzard.com/en-us/rates/data/> with the public
  `Accept: application/json` and `X-Requested-With: XMLHttpRequest` headers.
- Discover selectors `#filter-rq-select`, `#filter-input-select`,
  `#filter-region-select`, `#filter-tier-select`, `#filter-map-select` from HTML.
- Queue values are taken from the current form's English Role Queue labels.
  Current captured values were 0 for Quick Play and 2 for Competitive; these
  values are not fixed in production code. Tests deliberately use different IDs.
- The data object contains `rates.rates`, `rates.selected`, and `columns`.
  `rates.rates` items have `id`, `hero.role`, `hero.name`, and `cells` metrics.
- Require exact selected echoes for input, region, tier, map, and discovered rq.
  A request for Master without rq actually returned selected tier All. This
  demonstrates why accepted HTTP status alone does not verify applied filters.
- A verified competitive request:
  <https://overwatch.blizzard.com/en-us/rates/data/?input=PC&region=Asia&map=all-maps&tier=Master&rq=2>.
- The official renderer
  <https://static.playoverwatch.com/js/pages/rates/rates.52faffd1f83e27c450dd.js>
  divides numeric cell values by 100 only for a percentage formatter. Thus raw
  values are already percentage points, e.g. 47.8 denotes 47.8%, not 4,780%.
- Its renderer displays negative cells as missing. Preserve original raw
  values, mapping the expected `-1` sentinel to normalized null. The live sets
  sampled here contained no negative values; sentinel handling is covered by
  an offline contract test grounded in the renderer behavior.
- Quick Play's form disables tier and individual-map filtering. Its columns
  omit banrate although raw rows contain banrate=0. Normalized banrate is null
  when the column is unreported; retain the placeholder in `raw_rates`.
- Current tier choices include Emerald. The Grandmaster option explicitly
  includes Champion. Reject a Champion-only request.
- KR has no source option. Default rejection, or explicitly authorized ASIA
  fallback with both requested/applied filters and a warning, is required.
