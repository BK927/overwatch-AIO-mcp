# Overwatch AIO — Skill & MCP

[English](README.md) | **한국어**

오버워치 공개 자료를 출처와 근거를 보존하면서 조회합니다. 영웅 메타, 플레이어 통계, 랭커 명부, 리플레이, 공식 패치, OWCS Korea를 **스킬 또는 MCP**로 사용할 수 있습니다. 두 방식은 같은 조회 기능 10개와 데이터 처리 코드를 공유합니다.

Python 3.11 이상과 `uv`를 사용하며 조회 결과와 근거는 SQLite에 저장합니다. 두 방식 모두 요청할 때 데이터를 가져옵니다. 정기 수집·예약 실행은 제공하지 않습니다.

## 사용 방식 선택

| 방식 | 적합한 사용 | 필요한 설치 |
|---|---|---|
| 스킬만 | Codex가 질문에 맞는 조회를 조합하고 근거를 해석 | 스킬 설치와 기본 Python 의존성 |
| MCP만 | MCP 클라이언트에서 `ow_*` 도구를 직접 호출 | 저장소와 `mcp` 선택 의존성, 클라이언트 등록 |
| 둘 다 | 사용 환경에 따라 두 경로를 선택 | 위 두 설치를 함께 사용 |

스킬은 MCP 없이 CLI로 실행합니다. MCP만 사용하면 스킬 설치가 필요하지 않습니다. 둘 다 설치해도 자동 전환하거나 같은 조회를 두 경로로 중복 실행하지 않습니다. MCP 서버는 클라이언트가 시작하는 stdio 또는 직접 실행하는 HTTP 프로세스입니다.

## 스킬 설치

skills CLI로 현재 프로젝트에 설치할 수도 있습니다. 개인 설치에는 `--global`을 추가합니다. 설치기는 Node.js를 사용하며, 조회 실행에는 Python과 `uv`가 필요합니다.

```text
npx skills add BK927/overwatch-aio --skill overwatch-aio-skill --agent codex
```

Codex에서 Skill Installer에 아래와 같이 요청할 수 있습니다.

> BK927/overwatch-aio 저장소 루트의 스킬을 overwatch-aio-skill 이름으로 설치해 줘. 원하는 커밋을 지정했다면 그 커밋을 사용해 줘.

Skill Installer를 직접 사용하는 경우(경로는 사용자의 Codex 설치에 맞게 조정):

```text
python <Codex skills>/.system/skill-installer/scripts/install-skill-from-github.py --repo BK927/overwatch-aio --path . --name overwatch-aio-skill --ref <커밋 SHA>
uv sync --frozen --no-dev --project "<설치된 스킬 폴더>"
```

기본 설치 위치는 `~/.codex/skills/overwatch-aio-skill`입니다. 설치한 다음 턴부터 `$overwatch-aio-skill`로 부르거나 오버워치 데이터 질문을 통해 자동 선택할 수 있습니다. 설치 시 배포된 소스만 복사하며 개발 환경·캐시·사용자 DB는 포함하지 않습니다. 이후 준비하는 `.venv`는 실행 의존성만 담는 런타임입니다.

## MCP 설치와 연결

저장소를 복제하거나 이미 설치한 스킬 폴더를 사용합니다. 아래 `<프로젝트 폴더>`는 `pyproject.toml`이 있는 폴더의 절대 경로로 바꿉니다.

```text
git clone https://github.com/BK927/overwatch-aio.git
uv sync --frozen --no-dev --extra mcp --project "<프로젝트 폴더>"
uv run --frozen --no-dev --extra mcp --project "<프로젝트 폴더>" overwatch-aio-mcp serve
```

기본 전송 방식은 stdio입니다. [MCP 클라이언트 JSON 예제](examples/mcp-client.json) 또는 [Codex TOML 예제](examples/mcp-codex.toml)의 경로를 수정해 등록합니다. Codex에서는 다음 명령으로도 등록할 수 있습니다.

```text
codex mcp add overwatch-aio -- uv run --frozen --no-dev --extra mcp --project "<프로젝트 폴더>" overwatch-aio-mcp serve
```

HTTP로 연결하려면 서버를 실행한 다음 클라이언트에 `http://127.0.0.1:8765/mcp`를 등록합니다.

```text
uv run --frozen --no-dev --extra mcp --project "<프로젝트 폴더>" overwatch-aio-mcp serve --transport streamable-http
```

`--host`와 `--port`로 수신 주소를 지정할 수 있습니다. 기본값은 로컬 접근용 `127.0.0.1:8765`이며 인증 기능을 자체 제공하지 않습니다. 외부 공개가 필요하면 인증과 TLS를 제공하는 별도 배포 구성을 사용합니다. Codex 연결 방식은 [공식 MCP 안내](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)를 참고합니다.

스킬과 같은 필터·스키마·결과 본문을 사용하며, MCP는 JSON 본문을 `structuredContent`와 텍스트 응답으로 제공합니다. `status=error`일 때만 `isError=true`입니다. 빈 결과·오래된 캐시·부분 성공은 오류 플래그 대신 본문의 상태와 경고로 구분합니다. 스키마는 `overwatch-aio-mcp schema`로도 확인할 수 있습니다. 근거 등록은 [기존 CLI 명령](docs/curation.md)을 사용합니다.

같은 폴더에서 두 방식을 사용하면 MCP 실행 명령에 항상 `--extra mcp`를 포함합니다. `uv sync`는 선택한 의존성에 맞춰 환경을 정리하므로 MCP를 계속 사용할 폴더에서는 동기화할 때도 해당 옵션을 포함합니다. 스킬의 실행 명령과 이름은 그대로 유지합니다.

## 자연어 사용 예시

- “$overwatch-aio-skill 아시아 마스터에서 라인하르트와 라마트라 메타를 비교해 줘.”
- “한국 기준 자료가 있으면 보여 줘. 아시아 자료와 구분해 줘.”
- “저장된 지난 관측과 지금 메타가 얼마나 달라졌어?”
- “이 BattleTag의 공개 경쟁전 탱커 통계를 확인해 줘.”
- “한국 랭커로 확인된 라인하르트 리플레이를 찾아서 신원과 재생 확인 근거도 보여 줘.”
- “최신 패치의 라인하르트 변경과 OWCS Korea 자료를 함께 설명해 줘.”

스킬은 질문에 필요한 조회를 조합합니다. 처음 사용하는 DB의 랭커 명부나 메타 이력은 비어 있을 수 있습니다.

## 직접 조회

```text
uv run --frozen --no-dev --project "<스킬 폴더>" overwatch-aio-skill query ow_status
uv run --frozen --no-dev --project "<스킬 폴더>" overwatch-aio-skill query ow_meta --input-file "<조건.json>"
uv run --frozen --no-dev --project "<스킬 폴더>" overwatch-aio-skill schema
```

조건 파일 예시:

```json
{"heroes":["라인하르트","라마트라"],"region":"ASIA","tier":"MASTER"}
```

입력 파일 생략 시 `{}`, `--input-file -`는 표준입력 JSON입니다. UTF-8과 UTF-8 BOM을 지원합니다. 표준출력은 JSON 하나이고 로그는 표준오류에 나옵니다. 정상·빈 결과·오래된 캐시·일부 성공은 종료 코드 `0`, 실행 실패는 `1`, 입력 오류는 `2`입니다. 결과의 `status`와 경고를 함께 확인해야 합니다.

조회 작업은 `ow_catalog`, `ow_meta`, `ow_players_search`, `ow_player_get`, `ow_rankers_search`, `ow_replays_search`, `ow_replay_get`, `ow_patches`, `ow_esports`, `ow_status`입니다. 입력·출력 설명은 [조회 가이드](references/queries.md), 정확한 형식은 [JSON Schema](docs/query-schemas.json)에 있습니다.

## 데이터 저장과 기존 버전 전환

두 방식 모두 DB 선택 순서는 **`--db` → `OW_DB_PATH` → `~/.overwatch-aio-skill/overwatch.db`**입니다. 같은 사용자로 실행하면 설치 폴더가 달라도 기본 DB를 공유합니다. 상대 경로를 직접 지정하면 현재 작업 폴더 기준이므로 기존 DB에는 절대 경로를 권장합니다. `--db`는 명령 이름 바로 뒤, `query` 또는 `serve` 앞에 지정합니다.

```text
uv run --frozen --no-dev --project "<스킬 폴더>" overwatch-aio-skill --db "D:/기존자료/overwatch.db" query ow_status
```

기존 SQLite DB의 캐시·메타·랭커·리플레이·근거를 그대로 읽습니다. 기존 `collection_jobs` 테이블은 사용하지 않고 남겨두며, 새 DB에는 만들지 않습니다. 데이터 삭제나 자동 이동은 하지 않습니다. SQLite WAL과 트랜잭션으로 스킬의 근거 저장과 MCP의 조회가 같은 DB를 사용할 수 있습니다.

메타 이력은 **요청 시 관측한 기록**만 쌓입니다. 조회하지 않은 기간의 데이터를 소급해서 채우지 않으며 동일한 캐시를 다시 읽어도 관측을 중복 저장하지 않습니다. 패치 무효화 공지에 따른 리플레이 `needs_recheck`는 **패치를 조회할 때** 반영됩니다. 자동 감시나 알림은 제공하지 않습니다.

HTTP 캐시는 최대 256개·64 MiB·7일 범위로 보관합니다. 명부·리플레이 연결·재생 검증 근거는 별도 테이블에 두어 공개 출처 새로고침으로 덮어쓰지 않습니다. [근거 관리 명령](docs/curation.md)은 사용자가 제공한 실제 근거를 등록할 때 사용합니다.

GitHub 주소를 `BK927/overwatch-aio`로 변경했습니다. 배포 패키지와 스킬 이름은 `overwatch-aio-skill`로 유지합니다. 0.2.0 스킬 사용자는 기존 명령을 계속 사용합니다. 0.1.x MCP 사용자는 위 예제처럼 프로젝트 경로와 `--extra mcp`를 갱신하고 `overwatch-aio-mcp serve`를 사용합니다. 이전 `data/overwatch.db`를 계속 쓰려면 절대 경로로 지정합니다. 제거된 정기 수집 명령은 복원하지 않았습니다.

## 자료의 한계

- ASIA는 한국 경기 서버가 아닙니다. OWTICS의 KOREA도 제공자 분류이며 실제 서버 증거와 다릅니다.
- 랭커는 근거를 등록한 명부 범위입니다. 전체 한국 랭커나 실시간 Top 500 목록을 자동 확보하지 않습니다.
- 공개 코드만으로 재생 가능성을 단정하지 않습니다. 게임 내 실제 재생과 관전 자동화는 없습니다.
- 비공개 프로필에 접근하지 않으며 통계 부재와 비공개를 구분합니다.
- 메타 비율은 0–100 백분율입니다. 상대 영웅별 실제 상성 승률은 제공하지 않습니다. OWCS 대회 지표와 경쟁전 통계는 분리합니다.
- 출처의 구조 변경과 장애는 오류로 표시합니다. 출처 URL·집계 기간·조회 시각·캐시 상태를 확인해야 합니다.

## 개발과 검증

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

CI는 Windows/Linux × Python 3.11/3.13 × 스킬 단독/MCP 포함 환경에서 실행합니다. 스킬 단독 환경에는 MCP SDK가 없는지 확인하고, MCP 환경에서는 두 경로의 결과와 실제 stdio·HTTP 연결, 종료, DB 공유를 검사합니다. 실제 외부 출처 검사는 자동 테스트와 분리되어 있습니다.

```text
uv run --frozen --no-dev python scripts/live_smoke.py --output docs/live-verification.json
```

[구현 명세](SPEC.md) · [검증 기록](docs/verification.md) · [출처 조사](research/source-contracts.md) · [SQLite 구조](src/overwatch_skill/db/schema.sql)
