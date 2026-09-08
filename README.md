# Overwatch AIO MCP

공개 오버워치 자료를 출처와 검증 근거를 보존하면서 조회하는 Python MCP 서버입니다.

Python 3.11 이상, SQLite, 공식 MCP Python SDK를 사용합니다. 라즈베리파이 4 (RAM 2 GB)를 고려해 HTTP 동시 요청과 원본 캐시 크기를 제한합니다.

아시아 통계, 출처의 KOREA 분류, 한국 플레이어 국적, 실제 경기 서버는 별개입니다. 알 수 없는 값은 `null`이고, 리플레이 공개 여부는 게임에서 재생 가능하다는 뜻이 아닙니다.

```powershell
uv sync --extra dev
uv run pytest
```

구현 범위와 도구 사용법은 `SPEC.md`와 아래 운영 안내에 정리합니다.
