# Overwatch AIO MCP

공개 오버워치 자료를 출처와 검증 근거를 보존하면서 조회하는 Python MCP 서버입니다.

Python 3.11 이상, SQLite, 공식 MCP Python SDK를 사용합니다. 라즈베리파이 4 (RAM 2 GB)를 고려해 HTTP 동시 요청과 원본 캐시 크기를 제한합니다.

아시아 통계, 출처의 KOREA 분류, 한국 플레이어 국적, 실제 경기 서버는 별개입니다. 알 수 없는 값은 `null`이고, 리플레이 공개 여부는 게임에서 재생 가능하다는 뜻이 아닙니다.

```powershell
uv sync --extra dev
uv run pytest
```

## 실행

```powershell
uv sync --locked --extra dev
uv run --frozen overwatch-aio-mcp serve
```

기본값은 **stdio**이다. MCP 클라이언트에서 `examples/mcp-client.json`의 실행 설정을 사용한다. 다른 위치에 복사하면 프로젝트/DB 절대 경로를 수정한다. `uv`가 클라이언트의 PATH에 없으면 설치된 `uv.exe` 절대 경로를 지정한다.

로컬 HTTP:

```powershell
uv run --frozen overwatch-aio-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765
```

연결 주소는 `http://127.0.0.1:8765/mcp`. DB는 기본 `data/overwatch.db`, 환경변수 `OW_DB_PATH` 또는 명령 앞의 `--db`로 변경한다. 외부 공개용 인증은 내장하지 않았으므로 원격 사용은 인증된 HTTPS 프록시/터널 뒤에 둔다. 기존 MCP 등록이나 전역 설정은 이 프로젝트가 자동 변경하지 않는다.

## 도구 10개

| 도구 | 사용 예 |
|---|---|
| `ow_catalog` | `{"type":"heroes","hero":"라인하르트","locale":"ko-KR"}` |
| `ow_meta` | `{"heroes":["라인","라마","디바"],"region":"ASIA","tier":"MASTER"}` |
| `ow_players_search` | `{"query":"BattleTag#12345"}` |
| `ow_player_get` | `{"player_id":"BattleTag#12345","view":"hero","hero":"reinhardt"}` |
| `ow_rankers_search` | `{"country":"KR","hero":"reinhardt","verified_only":true}` |
| `ow_replays_search` | `{"hero":"reinhardt","player_pool":"kr_rankers"}` |
| `ow_replay_get` | `{"code":"실제6자리코드"}` |
| `ow_patches` | `{"view":"history","hero":"reinhardt","after":"2026-01-01"}` |
| `ow_esports` | `{"view":"hero_meta","hero":"reinhardt"}` |
| `ow_status` | `{}` 또는 `{"refresh":true}` |

표의 BattleTag와 코드는 형식 설명용이다. 실제 계정이나 재생 가능한 코드를 보장하지 않는다. `ow_meta`는 맵·티어·지역 비교와 저장된 history도 지원한다. 도구의 정확한 필드는 [JSON Schema](docs/tool-schemas.json)와 [SPEC.md](SPEC.md)에 있다.

## 자료를 해석하는 규칙

- `source=auto, region=KR`는 OWTICS의 KOREA 분류를 사용한다. ASIA 대체에는 `allow_region_fallback=true`가 필요하다.
- OverFast/Blizzard 메타는 0–100 백분율이다. 원본의 -1은 누락이며 OverFast의 0은 실제 0%인지 확정할 수 없다.
- 한국 플레이어라는 증거는 한국 서버 경기라는 증거가 아니다. 리플레이의 신원·국적·실제 서버를 따로 표시한다.
- 공개된 코드, 사이트의 valid 플래그, 사용자 제보, 클라이언트 확인을 구분한다.
- 명부와 이력은 처음에는 비어 있다. 과거 수치나 한국 랭커를 추정해 채우지 않는다.
- OWCS Korea는 프로 대회 자료이며 일반 경쟁전과 섞지 않는다. 릴리스 발행일과 실제 포함 경기 날짜를 따로 제공한다.

## 명부와 검증 관리

[근거 관리 안내](docs/curation.md)에 따라 로컬 JSON을 import하고 리플레이 신원·지역 근거를 연결한다. 확인 없는 기록이 `client_verified`가 되는 경로는 없다. `discover-rankers`는 미확인 고티어 작성자 후보를 보여 준다. 전체 한국 Top 500 실시간 수집이나 게임 내 자동 관전은 제공하지 않는다.

## 주기 수집

```powershell
uv run --frozen overwatch-aio-mcp collect --config examples/collector.json --once
uv run --frozen overwatch-aio-mcp collect --config examples/collector.json
```

첫 명령은 도래한 작업을 한 번 실행하고, 두 번째는 계속 실행한다. 동일 DB에 실행 시점과 lease를 저장하므로 반복 실행해도 주기 이전에 불필요하게 수집하지 않는다. 샘플의 관심 영웅·티어·지역만 수집하며, 관심 플레이어는 `ow_player_get` 작업을 43200초 주기로 추가할 수 있다. 예제 설정을 수정한 뒤 사용한다.

## Raspberry Pi 운영

64-bit Raspberry Pi OS, Python 3.11 이상을 권장한다. `deploy/`에 로컬 HTTP 서버·수집기·30분 timer 예제를 제공한다. 아직 장치에 설치된 서비스는 없다.

1. 프로젝트를 `/opt/overwatch-aio-mcp`에 놓고 `overwatch` 서비스 계정을 준비한다.
2. 해당 계정이 사용할 가상환경을 **서비스 시작 전에** `uv sync --frozen --no-dev`로 준비한다. 보호된 홈의 Python을 사용할 경우 서비스가 접근 가능한 경로로 배치하거나 시스템 Python을 지정한다.
3. unit의 사용자, 프로젝트 위치, `/usr/local/bin/uv` 경로를 실제 환경에 맞춘다. 서비스는 읽기 전용 프로젝트에서 `uv run --no-sync`로 기존 환경을 사용한다.
4. DB·캐시 경로는 systemd StateDirectory/CacheDirectory가 관리한다. 외부 접근은 별도 인증된 HTTPS 구성을 사용한다.
5. unit/timer를 설치한 뒤 systemd에서 활성화한다. 전용 Redis, 브라우저 상시 실행, 별도 LLM 서버는 필요 없다.

## 검증과 명세

```powershell
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check src tests scripts
uv run --frozen overwatch-aio-mcp schema --output docs/tool-schemas.json
uv build
```

외부 서비스에 실제 GET을 하는 검증은 `uv run --frozen python scripts/live_smoke.py`로 따로 실행한다. 일반 테스트는 외부 네트워크 없이 동작한다. Windows/Linux와 Python 3.11/3.13의 GitHub Actions 설정도 포함한다.

- [구현 명세](SPEC.md)
- [도구 JSON Schema](docs/tool-schemas.json)
- [SQLite 전체 스키마](src/overwatch_mcp/db/schema.sql)
- [랭커와 리플레이 근거 관리](docs/curation.md)
- [출처 계약 확인 기록](research/source-contracts.md)
