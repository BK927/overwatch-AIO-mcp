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
