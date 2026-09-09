"""Real MCP transports, shared SQLite state and server ownership."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_registry import ranker_record, replay_record

pytest.importorskip("mcp", reason="MCP is tested in the optional-extra installation job")
from mcp import Client, StdioServerParameters

from overwatch_skill import server
from overwatch_skill.db import Repository
from overwatch_skill.execution import schemas
from overwatch_skill.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def isolated_env(home):
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONIOENCODING="utf-8")
    env.pop("OW_DB_PATH", None)
    return env


async def check_contract(client):
    tools = (await client.list_tools()).tools
    expected = schemas()
    assert {tool.name for tool in tools} == set(expected)
    for tool in tools:
        assert tool.input_schema == expected[tool.name]["inputSchema"]
        assert tool.output_schema == expected[tool.name]["outputSchema"]
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
    for name, arguments, status, code in [
        ("ow_status", {}, "ok", None),
        ("ow_rankers_search", {"country": "KR"}, "empty", "EMPTY_RESULT"),
        ("ow_catalog", {"locale": "invalid"}, "error", "UNSUPPORTED_FILTER"),
        ("ow_meta", {"matchup": "ramattra"}, "error", "INVALID_ARGUMENT"),
        ("ow_players_search", {"query": "x", "limit": 0}, "error", "INVALID_ARGUMENT"),
    ]:
        result = await client.call_tool(name, arguments)
        body = result.structured_content
        assert body["status"] == status
        assert body["requested_filters"] == arguments
        assert result.is_error is (status == "error")
        assert (body["error"]["code"] if body["error"] else None) == code
        assert json.loads(result.content[0].text) == body


@pytest.mark.parametrize("selection", ["default", "environment", "explicit"])
def test_real_stdio_entrypoint_and_shared_db_selection(tmp_path, selection):
    async def run():
        home = tmp_path / "한글 사용자"
        home.mkdir()
        cwd = tmp_path / "다른 작업 폴더"
        cwd.mkdir()
        env = isolated_env(home)
        db = home / ".overwatch-aio-skill/overwatch.db"
        args = []
        if selection in ("environment", "explicit"):
            db = tmp_path / "환경 DB.sqlite"
            env["OW_DB_PATH"] = str(db)
        if selection == "explicit":
            db = tmp_path / "명시 DB.sqlite"
            args = ["--db", str(db)]
        command = Path(sys.executable).with_name(
            "overwatch-aio-mcp" + (".exe" if os.name == "nt" else "")
        )
        async with Client(
            StdioServerParameters(
                command=str(command), args=[*args, "serve"], cwd=str(cwd), env=env
            )
        ) as client:
            await check_contract(client)
            # A separately launched skill sees the same file while MCP keeps it open.
            process = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "overwatch_skill", *args, "query", "ow_status"],
                cwd=cwd,
                env=env,
                capture_output=True,
                encoding="utf-8",
                timeout=30,
            )
            assert process.returncode == 0, process.stderr
            assert json.loads(process.stdout)["data"]["stored_counts"]["players"] == 0
        assert db.is_file()
        if selection == "explicit":
            assert not Path(env["OW_DB_PATH"]).exists()
        assert not (cwd / "data").exists()
        with sqlite3.connect(db) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    asyncio.run(run())


def test_real_http_cli_and_graceful_shutdown(tmp_path):
    async def run():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        db = tmp_path / "HTTP 한글.sqlite"
        with (tmp_path / "http.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tests/http_process.py"),
                    "--db",
                    str(db),
                    "serve",
                    "--transport",
                    "streamable-http",
                    "--port",
                    str(port),
                ],
                cwd=tmp_path,
                env=isolated_env(tmp_path),
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(f"HTTP startup failed: {log.read()}")
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
                else:
                    pytest.fail("HTTP server startup exceeded 15 seconds")
                async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                    await check_contract(client)
            finally:
                process.stdin.close()
                try:
                    await asyncio.to_thread(process.wait, timeout=15)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        process.kill()
                    process.wait(timeout=5)
            log.seek(0)
            output = log.read()
            assert process.returncode == 0, output
            assert "Application shutdown complete" in output

    asyncio.run(run())


def test_existing_evidence_survives_concurrent_skill_writes_and_mcp_reads(tmp_path):
    db = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / "tests/fixtures/legacy-schema.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO collection_jobs VALUES ('keep','old-job',10,20,'ok',5)")
    repo = Repository(db)
    registry = Registry(repo)
    registry.import_rankers([ranker_record()])
    repo.save_replays([replay_record()])
    registry.validate_replay(
        "ABC123",
        "client_verified",
        "fixture observer",
        "fixture proof",
        "2026-01-01T00:00:00+00:00",
    )
    before = repo.conn.execute("SELECT * FROM replay_validations").fetchall()
    repo.close()
    path = tmp_path / "추가 근거.json"
    path.write_text(
        json.dumps([ranker_record(f"Other{i}#5678", f"Other{i}-5678") for i in range(20)]),
        encoding="utf-8",
    )

    async def run():
        async with Client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "overwatch_skill.mcp_cli", "--db", str(db), "serve"],
                env=isolated_env(tmp_path),
            )
        ) as client:

            async def write():
                process = await asyncio.to_thread(
                    subprocess.run,
                    [
                        sys.executable,
                        "-m",
                        "overwatch_skill",
                        "--db",
                        str(db),
                        "import-rankers",
                        str(path),
                    ],
                    cwd=tmp_path,
                    capture_output=True,
                    encoding="utf-8",
                    timeout=30,
                )
                assert process.returncode == 0, process.stderr
                assert len(json.loads(process.stdout)["imported_player_ids"]) == 20

            async def read():
                for _ in range(10):
                    result = await client.call_tool("ow_rankers_search", {"limit": 50})
                    assert not result.is_error
                    assert len(result.structured_content["data"]) in (1, 21)

            await asyncio.gather(write(), read())
            result = await client.call_tool("ow_rankers_search", {"limit": 50})
            assert len(result.structured_content["data"]) == 21
        reopened = Repository(db)
        try:
            assert [
                tuple(row) for row in reopened.conn.execute("SELECT * FROM replay_validations")
            ] == [tuple(row) for row in before]
            assert reopened.conn.execute("SELECT count(*) FROM collection_jobs").fetchone()[0] == 1
            assert reopened.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            reopened.close()

    asyncio.run(run())


def test_owned_repository_closes_when_client_disconnects(tmp_path, monkeypatch):
    repositories = []

    def repository(path):
        repo = Repository(path)
        repositories.append(repo)
        return repo

    monkeypatch.setattr(server, "Repository", repository)

    async def run():
        async with Client(server.create_server(db_path=str(tmp_path / "lifecycle.db"))) as client:
            assert not (await client.call_tool("ow_status", {})).is_error
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            repositories[0].conn.execute("SELECT 1")

    asyncio.run(run())
