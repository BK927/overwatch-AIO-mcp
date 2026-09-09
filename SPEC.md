# Overwatch AIO Skill 구현 명세

## 범위와 계약

저장소 루트를 하나의 설치 가능한 Codex 스킬로 구성한다. Python 패키지는 `overwatch_skill`, 배포 패키지와 CLI는 `overwatch-aio-skill`, 버전은 `0.2.0`이다. 10개 조회와 명시적인 근거 관리 명령을 요청 시 실행한다. MCP·정기 수집·예약 실행은 포함하지 않는다. 공개 출처에 없는 정보는 추정하지 않는다.

정확한 조회 입력·출력 JSON Schema는 `docs/query-schemas.json`, SQLite DDL은 `src/overwatch_skill/db/schema.sql`, 랭커 입력 형식은 `Registry`의 Pydantic 모델 및 `docs/curation.md`를 따른다. 조회 스키마는 `uv run overwatch-aio-skill schema --output docs/query-schemas.json`으로 REQUESTS/RESPONSES 모델에서 재생성한다.

모든 도구는 `status`, `data`, `error`, `source`, `source_url`, `retrieved_at`, `source_updated_at`, `data_period`, `data_patch`, `requested_filters`, `applied_filters`, `warnings`, `cached`, `stale`를 반환한다. `requested_filters`는 입력 조건, `applied_filters`는 실제 적용 범위이다. 여러 소스 또는 페이지를 사용한 결과는 개별 행/그룹의 출처와 관측 시점도 확인해야 한다.

- 모르는 수정 시점·경기 집계 기간·패치·표본수·지역은 `null`.
- 메타 수치는 **백분율 0–100**, 차이는 **퍼센트포인트**. OWCS 데이터의 자체 지표는 별도 단위와 방법론을 유지한다.
- 캐시 적중 시 원래 `retrieved_at`을 보존한다. 오래된 캐시를 반환하면 `STALE_DATA` 및 출처 실패를 표시한다.
- `EMPTY_RESULT`는 정상 조회의 결과 없음이다. API 장애나 파싱 실패를 빈 배열로 숨기지 않는다.
- `PRIVATE_PROFILE`, `UNSUPPORTED_FILTER`, `SOURCE_UNAVAILABLE`, `PARSE_ERROR`, `RATE_LIMITED`, `STALE_DATA`, `PARTIAL_RESULT`, 입력 오류 `INVALID_ARGUMENT`를 구분한다.
- 지역 비교 일부 실패는 개별 오류와 성공 그룹을 함께 반환한다. 다른 출처 간 수치 차이는 자동 계산하지 않는다.
- 모든 도구는 `responses.py`의 공통 필수 필드·도구별 데이터 구조를 `schema` 명령의 `outputSchema`로 제공하고, 반환 전에 검증한다. 출처별 확장 필드와 기존 값·필드 생략 여부는 보존한다. 표준출력에 검증된 JSON 하나를 반환하며 로그는 표준오류로 분리한다.
- 종료 코드는 정상·빈 결과·오래된 캐시·일부 성공 `0`, 실행 실패 `1`, 입력 오류(`INVALID_ARGUMENT`, `UNSUPPORTED_FILTER`) `2`다. 비교 전체 실패는 그룹별 오류를 보존하며 `1`을 반환한다. 내부 출력 계약 위반은 구조화된 `INTERNAL_ERROR`로 반환한다.

## 도구

| 도구 | 구현 기능 | 주요 한계 |
|---|---|---|
| `ow_catalog` | heroes·영웅 상세, maps, modes, tiers, regions, filters | maps의 mode는 escort/control 등 맵 유형; 현 시즌 경쟁전 맵 풀과 다름 |
| `ow_meta` | heroes, map_comparison, tier_comparison, region_comparison, history; 3개 통계 소스 | 비교 축 2–6개, 상대 영웅별 실제 matchup 미제공 |
| `ow_players_search` | 이름/BattleTag 검색, 페이지 offset | 동일 이름만으로 동일인 연결 불가 |
| `ow_player_get` | summary, career, hero, roles; 플랫폼·모드·영웅·역할 필터 | 비공개 증거가 없는 통계 부재를 비공개로 단정하지 않음 |
| `ow_rankers_search` | 저장 명부의 지역·국적·영웅·역할·순위·시즌·검증 필터 | 전체 한국 랭커/현재 Top 500이 아님 |
| `ow_replays_search` | 공개 게시물 검색 및 저장된 국적·신원·실제 경기 지역·재생 근거 필터 | 공개 페이지 범위가 제한되며 신원 연결은 독립 근거 필요 |
| `ow_replay_get` | 코드 상세, 원출처 주장, 검증 이력, 지역 근거 | 웹 플래그를 클라이언트 재생 확인으로 승격하지 않음 |
| `ow_patches` | 공식 패치 최신/이력, 영웅별 변경, 리플레이 호환성 공지 | 검증된 영문 구조 사용, 월별 탐색 범위 표시 |
| `ow_esports` | hero_meta, matches, maps, bans, teams | 한국 프로씬 대회 자료; 일반 경쟁전 통계와 분리 |
| `ow_status` | 출처별 관측 상태·capability·저장량·마지막 성공 시점 | 기본 호출은 외부 소스에 새 요청하지 않음 |

## 지역과 출처 선택

`source_region`, `player_country`, `leaderboard_region`, `match_server_region`은 서로 다른 필드다. `ASIA`는 한국 서버 통계가 아니다. OWTICS의 `KOREA`는 제공자가 붙인 분류이며 계정/서버/데이터센터 중 무엇인지는 확정하지 않는다.

`ow_meta(source=auto)`는 일반 지역에 OverFast, `region=KR`에 OWTICS를 선택한다. 한국 조건이 없거나 실패하면 기본적으로 오류를 반환한다. `allow_region_fallback=true`일 때만 OverFast ASIA로 대체하며 요청·적용 지역 및 경고를 보존한다. `source=blizzard`는 원본 sentinel과 필터 문제 확인을 위한 직접 통계 조회다.

OverFast는 GRANDMASTER 집계에 CHAMPION이 포함됨을 표시한다. OWTICS는 `GRANDMASTER_AND_CHAMPION`만 제공하므로 개별 GRANDMASTER/CHAMPION 조건은 거절한다. OWTICS에서 검증되지 않은 플랫폼을 적용했다고 표시하지 않으며 console은 거절한다. 영웅/전장 별칭은 출처의 표준 slug로 정규화하고 BattleTag 대소문자는 보존한다.

## 출처 어댑터

공통 생성자는 HTTP 클라이언트를 받으며 결과는 `SourceResult`, 실패는 `SourceError`다. 어댑터마다 `name`, `capabilities`를 선언한다. 모든 출처가 의미 없는 빈 공통 메서드를 구현하도록 강요하지 않는다.

| 출처 | 사용 경로 | 검증 방식 / TTL |
|---|---|---|
| OverFast | `/heroes`, `/heroes/{hero}`, `/maps`, `/gamemodes`, `/heroes/stats`, `/players`, 개인 summary/stats | 현재 OpenAPI 대조, percent 범위·필수 키 확인; catalog 24h, meta 1h, player 10m |
| Blizzard stats | 영웅 통계 폼과 `/en-us/rates/data/` | 폼 라벨에서 rq 동적 발견, selected 필터 대조, 음수 sentinel 보존/정규값 null |
| OWTICS | `/en-US/hero`, `/en-US/map/{slug}`의 SSR hydration | filter 및 각 measurement의 지역·모드·티어 확인; 1h |
| OWReplays | `/api/v2/gamedata`, `/api/v2/replays`, `/api/v2/replay/{code}` | 동적 ID 매핑, 필터 결과 교차 확인, archive 상태 보존; 24h/30m |
| Blizzard patches | `/en-us/news/patch-body/live/{year}/{month}` | 월·패치 날짜·영웅 블록 검증, 모드 구역 제목 보존; 6h |
| OWCS Korea | GitHub releases/latest + manifest 및 JSON 자산 | 릴리스 스키마·관계·기간·집계 방법 확인, 실제 데이터 기간과 발행 시점 분리 |

공개 웹 내부 경로는 변경될 수 있다. 파싱 실패 시 `PARSE_ERROR`로 출처 상태가 저하된다. 브라우저 상시 실행, 로그인 세션, 비공개 프로필 접근, 게임 클라이언트 자동 조작은 하지 않는다. 출처 조사 근거는 `research/source-contracts.md`에 기록한다.

## 저장과 이력

SQLite WAL·foreign key·트랜잭션을 사용한다. 주요 테이블은 `players`, `player_evidence`, `leaderboard_snapshots`, `player_hero_tags`, `replays`, `replay_evidence`, `replay_validations`, `compatibility_notices`, `hero_meta_snapshots`, `http_cache`, `source_status`다. 새 DB에는 `collection_jobs`를 만들지 않는다. 기존 DB의 해당 테이블은 그대로 두고 사용하지 않는다. DB는 `--db` → `OW_DB_PATH` → `~/.overwatch-aio-skill/overwatch.db` 순서로 결정하며 자동 데이터 이동이나 삭제 마이그레이션은 없다.

메타는 요청 시에만 관측·저장한다. 실제 적용된 출처·지역·모드·플랫폼·맵·티어·역할을 기준으로 시계열을 분리한다. 영웅 선택/출력 순서는 시계열을 분할하지 않는다. 동일 응답의 캐시 재사용은 새 스냅샷을 만들지 않는다. `history`는 저장된 집계값 사이의 차이이며 해당 주에 플레이한 실제 매치들의 승률이 아니다. 오래된 데이터가 없으면 소급 생성하지 않는다.

원본 HTTP 응답과 헤더는 SQLite cache에 함께 보존한다. 최대 256개·64 MiB·7일의 원본 캐시 제한이 있다. 단일 응답은 12 MiB, HTTP 동시 요청은 4개, 출처별 요청은 직렬화한다. 429/일부 5xx는 최대 두 번 재시도하며 4xx·파싱 오류에 오래된 성공을 대신 표시하지 않는다.

## 랭커·리플레이 증거

초기 실명/국적/랭커 데이터는 비어 있다. 근거 없는 예시 인물을 실제 데이터로 넣지 않는다. 랭커는 curator JSON import로 관리하며 verified identity에는 두 종류 이상의 독립적인 검증 근거가 필요하다. PLAYER_COUNTRY 근거 없이 국적을 설정하지 않는다. 가장 최근 지역/역할별 순위 관측을 먼저 선택한 다음 순위 한도를 적용한다.

리플레이 새로고침은 출처 관측만 갱신한다. 로컬 player link·검증·지역 증거는 별도 테이블에 남는다. 같은 표시 이름으로 자동 연결하지 않는다. `discover-rankers`는 저장된 공개 고티어 리플레이에서 검토할 미확인 후보를 제시할 뿐, 한국인이나 Top 500으로 등록하지 않는다.

재생 상태는 `unverified`, `source_reports_expired`, `user_reported_working`, `client_verified`, `needs_recheck`다. `client_verified`는 로컬 운영자가 실제 확인의 시점·보고자·증거 참조를 기록할 때만 생성한다. `ow_patches` 조회에서 확인한 명확한 코드 무효화 패치는 이전 검증을 `needs_recheck`로 바꾸며, 불명확한 공지나 단순 패치 발행만으로 만료를 확정하지 않는다.

## 실행과 설치

`overwatch-aio-skill query <operation> --input-file <경로 또는 ->`로 한 작업을 실행한다. 파일 생략은 빈 객체, `-`는 표준입력이다. UTF-8 BOM을 허용하고 잘못된 JSON이나 객체가 아닌 입력은 `2`로 반환한다. 작업 폴더를 바꾸지 않는 `uv run --frozen --no-dev --project <skill-root>`를 사용할 수 있다.

스킬 자동 선택을 허용한다. 핵심 분기·근거 규칙은 `SKILL.md`, 상세 조회는 `references/queries.md`, 근거 등록은 `docs/curation.md`에서 설명한다. 개인 설치는 GitHub에 게시한 커밋의 루트를 Skill Installer로 복사한다. 사용자 DB·캐시·개발 환경은 배포하지 않으며 설치 후 실행 의존성을 별도로 준비한다.

## 완료 판정

기존 출처·필터·비율·캐시·근거 보존 테스트와 CLI 입력/출력 계약을 검증한다. 별도 프로세스의 JSON·종료 코드, 다른 작업 폴더, 한글/BOM 입력, 기본 DB 공유, 기존 DB 호환성을 검사한다. MCP 없이 조회·스키마 생성·패키지 빌드가 성공해야 한다. Windows/Linux × Python 3.11/3.13 CI에 스킬 형식 검증을 포함한다. 공개 소스 live smoke는 별도 opt-in으로 수행한다.
