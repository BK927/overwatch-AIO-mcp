# 조회 입력과 결과

## 실행과 공통 계약

`uv run --frozen --no-dev --project "<skill-root>" overwatch-aio-skill [--db "<DB>"] query <operation> [--input-file "<파일 또는 ->"]`

파일 생략 시 빈 객체다. 파일/표준입력 모두 UTF-8 JSON이며 BOM을 허용한다. 배열·null·잘못된 JSON·알 수 없는 입력 필드·잘못된 허용값은 입력 오류다. Windows에서는 한글 JSON을 기본 코드페이지의 파이프로 보내기보다 UTF-8 파일로 준비하는 편이 확실하다.

스키마 조회는 `... overwatch-aio-skill schema`, 파일 저장은 `... schema --output "<path>"`다. 각 작업은 `description`, `inputSchema`, `outputSchema`를 제공한다. 아래는 자주 필요한 선택이며 전체 필드는 [JSON Schema](../docs/query-schemas.json)가 기준이다.

공통 결과: `status`, `data`, `error`, `source`, `source_url`, `retrieved_at`, `source_updated_at`, `data_period`, `data_patch`, `requested_filters`, `applied_filters`, `warnings`, `cached`, `stale`.

| 상태 | 뜻 | 종료 코드 |
|---|---|---|
| `ok` | 조회 성공 | 0 |
| `empty` | 정상 조회, 조건에 맞는 결과 없음 | 0 |
| `stale` | 원래 관측 시각을 보존한 오래된 데이터 | 0 |
| `partial` | 비교 일부 성공, 그룹별 오류 포함 | 0 |
| `error` + `INVALID_ARGUMENT` / `UNSUPPORTED_FILTER` | 입력을 고쳐야 함 | 2 |
| 그 밖의 `error` | 외부 장애·비공개·파싱·내부 계약 위반 등 | 1 |

오류의 `data`는 보통 null이며 비교 전체 실패는 실패 그룹 배열을 보존한다. `PRIVATE_PROFILE`과 통계 없음은 다르다. `INTERNAL_ERROR`는 유효하지 않은 출력을 숨기고 실패를 알린다. 실패를 빈 조회로 바꾸거나 제한된 요청을 무한 재시도하지 않는다.

## 메타와 카탈로그

`ow_catalog`: `type` = heroes(기본)/maps/modes/tiers/regions/filters, `locale` 기본 ko-KR. 영웅 상세는 `hero`, 맵 유형은 `mode`, 역할은 `role`이다. 시즌 경쟁전 맵 풀 조회가 아니다.

`ow_meta`: `view` = heroes(기본)/map_comparison/tier_comparison/region_comparison/history. 기본 pc·competitive·ASIA·all-maps, `source` = auto/overfast/owtics/blizzard. `heroes`, `tier`, `role`, `map`, `order_by`, `limit`로 좁힌다. 한국어 영웅 별칭을 정규화한다.

```json
{"heroes":["라인하르트","라마트라"],"region":"ASIA","tier":"MASTER"}
```

비교는 해당 축 `maps` / `tiers` / `regions`에 2–6개를 지정한다. 예:

```json
{"view":"map_comparison","maps":["kings-row","ilios"],"heroes":["reinhardt"],"tier":"MASTER"}
```

같은 출처·조건의 비율 차이는 퍼센트포인트다. `source_status`와 각 그룹의 출처/시각도 확인한다. 출처가 다르면 자동 차이 계산을 하지 않는다.

`region=KR`의 auto는 OWTICS, 일반 지역은 OverFast를 선택한다. 한국 범위 실패 시 기본은 오류다. `allow_region_fallback=true`만 OverFast ASIA 대체를 허용한다. OWTICS의 티어는 `GRANDMASTER_AND_CHAMPION`처럼 합쳐진 값을 쓰며 console은 지원하지 않는다. 상세 지원 조건은 `ow_catalog`의 filters를 확인한다.

history는 같은 출처·플랫폼·모드·지역·티어·맵·역할의 저장된 관측을 조회한다. `after`는 history에서만 사용한다. 영웅 선택과 출력 순서는 시계열을 나누지 않는다. 오늘 처음 썼다면 지난주의 기록이 없을 수 있다. 캐시 재사용을 새로운 관측으로 중복 저장하지 않는다.

## 플레이어와 랭커

`ow_players_search`: 필수 `query`, `limit` 기본 10(최대 50), `offset` 기본 0. 결과의 `player_id`로 계정을 선택한다. 동명이인은 자동 동일인 판정을 하지 않는다.

`ow_player_get`: 필수 `player_id`, `view` = summary(기본)/career/hero/roles. pc·competitive가 기본, `hero` / `role`로 필터한다. BattleTag의 대소문자를 보존한다.

`ow_rankers_search`: `region`, `country`, `role`, `hero`, `season`, `rank_max`(기본 500), `verified_only`(기본 true), `limit`(기본 20). 저장된 최근 지역·역할별 순위 관측과 신원 근거를 검색한다. 새 DB는 빈 명부다. 전체 한국 랭커 목록이 필요해도 공개 근거 없이 채우지 않는다.

## 리플레이와 패치

`ow_replays_search`: `hero`, `map`, `player`, `tier`, `tier_min`, `platform`, `uploaded_after`, `limit`(기본 20)로 공개 게시물 범위를 좁힌다. `refresh` 기본 true, false이면 저장된 관측만 사용한다. 업스트림 탐색은 제한된 페이지 범위다.

저장 근거 필터: `player_id`, `player_pool`(all/kr_rankers), `player_country`, `match_region`, `region_evidence`(verified_only/include_claims), `ranker_status`(any/verified), `playable_status`(상태 배열). 국가와 경기 서버 조건을 서로 대체하지 않는다.

`ow_replay_get`: 필수 영숫자 6자리 `code`, `refresh` 기본 true. `source_replay_status`, `validation`, `evidence`와 검증 날짜를 확인한다. 상태: unverified/source_reports_expired/user_reported_working/client_verified/needs_recheck.

`ow_patches`: `view` latest(기본)/history, `hero`, `after`, `before`, `limit`(기본 20). 검증된 `locale=en-US`만 지원한다. 패치 조회는 명확한 코드 무효화 공지를 저장하고 공지 이전의 재생 확인을 needs_recheck로 표시한다. 패치가 나왔다는 사실만으로 모두 만료 처리하지 않는다. 월별 조회 범위는 응답 경고를 확인한다.

## 대회와 상태

`ow_esports`: `region=KOREA`, `view` hero_meta(기본)/matches/maps/bans/teams. `hero`, `map`, `team`, `stage`, `after`, `before`, `limit`(기본 50) 지원. `release_tag`, `published_at`, `data_until`, `coverage_period`, 행별 방법론과 단위를 보존한다. 최근 발행됐더라도 경기 집계 기간은 과거일 수 있다.

`ow_status`: `refresh=false`가 기본이며 외부 요청 없이 저장된 상태와 기능·저장량을 보여 준다. `refresh=true`는 제한된 출처 점검을 한다. 성공 시각이 없거나 오래된 상태를 지금의 정상 운영으로 단정하지 않는다.
