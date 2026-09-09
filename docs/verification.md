# 구현 검증 기록

2026-09-09 KST 로컬 검증. 공개 출처의 응답은 시간에 따라 달라질 수 있다.

- Windows, uv 관리 Python 3.13.11: 회귀 테스트 218개 통과.
- Windows, 별도 uv 환경 Python 3.11.14: 동일 테스트 218개 통과.
- Ruff 검사와 포맷 검사 통과.
- 실제 별도 프로세스의 stdio: MCP handshake, 10개 도구 열거, 구조화 응답 호출 통과.
- 임시 loopback Streamable HTTP: MCP handshake, 10개 도구 열거, 구조화 응답 호출 통과. 시험 후 생성한 프로세스를 종료했다.
- `uv build`: source distribution과 Python wheel 생성 성공. 테스트 DB, 다운로드한 HTML/JS, uv cache, 가상환경이 배포물에 포함되지 않음을 확인했다. SQLite `schema.sql`은 wheel에 포함된다.
- 실제 공개 GET으로 OverFast, Blizzard 원본 통계, OWTICS, OWReplays, Blizzard 패치, OWCS Korea를 검증했다. 요약은 [live-verification.json](live-verification.json)에 있다. 개인정보·전체 원본 응답은 이 기록에 저장하지 않았다.
- 조회 당시 선택한 공개 플레이어의 해당 영웅 경쟁전 통계는 없었다. 이를 0 승률이나 비공개 프로필로 바꾸지 않고 `EMPTY_RESULT`로 반환했다.
- 새 DB의 한국 랭커 명부 조회는 `EMPTY_RESULT`. 예시 선수·가짜 순위를 자동 등록하지 않는다.

GitHub Actions는 Windows/Linux × Python 3.11/3.13로 구성했다. 아직 원격 저장소에 push하지 않았으므로 원격 CI 실행 완료를 주장하지 않는다. 라즈베리파이 실기기 배포·메모리 측정과 외부 HTTPS 구성도 수행하지 않았다.

## 출력 계약 보완 검증 (2026-09-09 KST)

- Windows, Python 3.13.14: 기존 218개와 출력 계약 검증 36개를 포함한 테스트 254개 통과. 이 보완분은 Python 3.11에서 재실행하지 않았다.
- 10개 도구의 실제 `tools/list` 출력 스키마, 필수 응답 필드, 읽기 전용·비파괴 annotations를 확인했다. CLI 및 저장된 스키마 파일이 실제 도구 정의와 일치한다.
- 출처 응답 fixture를 실제 어댑터와 MCP 호출에 통과시켜 도구별 데이터·조회 view·원본 필드 보존을 검증했다. 오류·빈 결과·오래된 캐시·일부/전체 비교 실패와 알 수 없는 OWCS 집계 기간을 구분한다.
- 모의 HTTP 503/429, 파싱 실패, 비공개 프로필, 지원하지 않는 필터, 입력 오류에서 `isError=true`와 스키마에 맞는 구조화 응답을 확인했다. JSON 텍스트와 `structuredContent`도 일치한다.
- 실제 stdio와 loopback Streamable HTTP에서 상세 스키마 및 정상/빈 결과/오류 응답 검증 통과. 제한된 실행 환경의 첫 정리 시도는 로그 파일 잠금으로 실패했으며, 재검증은 테스트 서버 종료·임시 파일 정리까지 완료했다.
- Ruff 검사·포맷 검사와 패키지 빌드 통과. 보완 검증에는 실제 외부 데이터 서비스 요청을 사용하지 않았다.
