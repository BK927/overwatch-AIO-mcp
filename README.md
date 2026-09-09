# Overwatch AIO Skill

**`overwatch-aio-skill`**은 오버워치 공개 자료를 출처와 근거를 보존하면서 조회하는 Codex 스킬입니다. 영웅 메타, 플레이어 통계, 랭커 명부, 리플레이, 공식 패치, OWCS Korea 질문을 하나의 스킬로 처리합니다.

필요할 때 Python 코드를 실행합니다. MCP 서버나 정기 수집기, 백그라운드 프로세스는 없습니다. Python 3.11 이상과 `uv`를 사용하며 조회 결과와 근거는 SQLite에 저장합니다.

## 설치

Codex에서 Skill Installer에 아래와 같이 요청할 수 있습니다.

> BK927/overwatch-aio-skill 저장소 루트의 스킬을 overwatch-aio-skill 이름으로 설치해 줘. 원하는 커밋을 지정했다면 그 커밋을 사용해 줘.

Skill Installer를 직접 사용하는 경우(경로는 사용자의 Codex 설치에 맞게 조정):

```text
python <Codex skills>/.system/skill-installer/scripts/install-skill-from-github.py --repo BK927/overwatch-aio-skill --path . --name overwatch-aio-skill --ref <커밋 SHA>
uv sync --frozen --no-dev --project "<설치된 스킬 폴더>"
```

기본 설치 위치는 `~/.codex/skills/overwatch-aio-skill`입니다. 설치한 다음 턴부터 `$overwatch-aio-skill`로 부르거나 오버워치 데이터 질문을 통해 자동 선택할 수 있습니다. 설치 시 배포된 소스만 복사하며 개발 환경·캐시·사용자 DB는 포함하지 않습니다. 이후 준비하는 `.venv`는 실행 의존성만 담는 런타임입니다.

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

DB 선택 순서는 **`--db` → `OW_DB_PATH` → `~/.overwatch-aio-skill/overwatch.db`**입니다. 기본 DB는 현재 작업 폴더나 스킬 설치 경로와 무관하게 같습니다. 상대 경로를 직접 지정하면 현재 작업 폴더 기준이므로 기존 DB에는 절대 경로를 권장합니다.

```text
uv run --frozen --no-dev --project "<스킬 폴더>" overwatch-aio-skill --db "D:/기존자료/overwatch.db" query ow_status
```

기존 SQLite DB의 캐시·메타·랭커·리플레이·근거를 그대로 읽습니다. 기존 `collection_jobs` 테이블은 사용하지 않고 남겨두며, 새 DB에는 만들지 않습니다. 데이터 삭제나 자동 이동은 하지 않습니다. 이 전환 당시 개발 체크아웃에는 사용자 DB가 없었습니다.

메타 이력은 **요청 시 관측한 기록**만 쌓입니다. 조회하지 않은 기간의 데이터를 소급해서 채우지 않으며 동일한 캐시를 다시 읽어도 관측을 중복 저장하지 않습니다. 패치 무효화 공지에 따른 리플레이 `needs_recheck`는 **패치를 조회할 때** 반영됩니다. 자동 감시나 알림은 제공하지 않습니다.

HTTP 캐시는 최대 256개·64 MiB·7일 범위로 보관합니다. 명부·리플레이 연결·재생 검증 근거는 별도 테이블에 두어 공개 출처 새로고침으로 덮어쓰지 않습니다. [근거 관리 명령](docs/curation.md)은 사용자가 제공한 실제 근거를 등록할 때 사용합니다.

GitHub 저장소는 기존 커밋 이력을 유지하면서 이름을 변경했습니다. 로컬 개발 폴더 `D:\repo\overwatch-AIO-mcp`는 기존 작업 연결을 위해 유지합니다. 예전 MCP 실행 설정은 이 패키지에서 더 이상 동작하지 않으므로 사용 중인 클라이언트에서 해당 등록을 제거해야 합니다.

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
uv build
```

CI는 Windows/Linux × Python 3.11/3.13에서 실행합니다. 실제 외부 출처 검사는 자동 테스트와 분리되어 있습니다.

```text
uv run --frozen --no-dev python scripts/live_smoke.py --output docs/live-verification.json
```

[구현 명세](SPEC.md) · [검증 기록](docs/verification.md) · [출처 조사](research/source-contracts.md) · [SQLite 구조](src/overwatch_skill/db/schema.sql)
