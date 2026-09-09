"""Exercise installed entrypoints, process IO, persistence and migration compatibility."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_registry import ranker_record, replay_record
from test_service import meta_result

from overwatch_skill.cli import schemas
from overwatch_skill.db import Repository
from overwatch_skill.db.repository import resolve_db_path
from overwatch_skill.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def environment(tmp_path):
    env = os.environ.copy()
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home), USERPROFILE=str(home), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"
    )
    env.pop("OW_DB_PATH", None)
    return env


def run_cli(args, cwd, environment, *, input=None, scenario=None, console=False):
    if scenario:
        command = [sys.executable, str(ROOT / "tests/cli_process.py"), scenario]
    elif console:
        suffix = ".exe" if os.name == "nt" else ""
        command = [str(Path(sys.executable).with_name("overwatch-aio-skill" + suffix))]
    else:
        command = [sys.executable, "-m", "overwatch_skill"]
    result = subprocess.run(
        [*command, *args],
        cwd=cwd,
        env=environment,
        input=input,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    body = json.loads(result.stdout)  # Reject banners, double JSON and incidental stdout logs.
    return result, body


def test_console_entrypoint_without_mcp_from_another_directory(tmp_path, environment):
    assert importlib.util.find_spec("mcp") is None
    process, body = run_cli(["query", "ow_status"], tmp_path, environment, console=True)
    assert process.returncode == 0
    assert process.stderr == ""
    assert body["status"] == "ok"
    assert body["data"]["stored_counts"]["players"] == 0
    assert not (tmp_path / "data").exists()
    assert (Path(environment["HOME"]) / ".overwatch-aio-skill/overwatch.db").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "3",
        '"hello"',
        "{",
        "{} {}",
        '{"refresh":NaN}',
        '{"refresh":Infinity}',
        '{"extra":1}',
    ],
)
def test_input_errors_are_one_json_and_exit_two(raw, tmp_path, environment):
    process, body = run_cli(
        ["query", "ow_status", "--input-file", "-"], tmp_path, environment, input=raw
    )
    assert process.returncode == 2
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert not (Path(environment["HOME"]) / ".overwatch-aio-skill").exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["serve"],
        ["collect"],
        ["query", "does_not_exist"],
        ["query", "ow_player_get"],
        ["query", "ow_status", "--input-file", "missing.json"],
    ],
)
def test_invalid_commands_missing_fields_and_missing_file(args, tmp_path, environment):
    process, body = run_cli(args, tmp_path, environment)
    assert process.returncode == 2
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_korean_file_name_and_content(encoding, tmp_path, environment):
    target = tmp_path / "한글 조건.json"
    target.write_text('{"hero":"라인하르트","verified_only":false}', encoding=encoding)
    process, body = run_cli(
        ["query", "ow_rankers_search", "--input-file", target.name], tmp_path, environment
    )
    assert process.returncode == 0
    assert body["status"] == "empty"
    assert body["requested_filters"]["hero"] == "라인하르트"


def test_stdin_bom_and_korean_output(tmp_path, environment):
    process, body = run_cli(
        ["query", "ow_catalog", "--input-file", "-"],
        tmp_path,
        environment,
        input='\ufeff{"locale":"ko-KR"}',
        scenario="korean",
    )
    assert process.returncode == 0
    assert body["data"][0]["name"] == "라인하르트"


@pytest.mark.parametrize(
    ("scenario", "operation", "arguments", "code", "status"),
    [
        ("unavailable", "ow_players_search", {"query": "Example"}, 1, "error"),
        ("limited", "ow_players_search", {"query": "Example"}, 1, "error"),
        ("parse", "ow_players_search", {"query": "Example"}, 1, "error"),
        ("private", "ow_player_get", {"player_id": "Example-1234"}, 1, "error"),
        ("parse", "ow_catalog", {"locale": "invalid"}, 2, "error"),
        ("empty", "ow_meta", {}, 0, "empty"),
        ("stale", "ow_meta", {}, 0, "stale"),
        (
            "partial",
            "ow_meta",
            {"view": "map_comparison", "maps": ["kings-row", "ilios"]},
            0,
            "partial",
        ),
        (
            "failed_comparison",
            "ow_meta",
            {"view": "map_comparison", "maps": ["kings-row", "ilios"]},
            1,
            "error",
        ),
        ("invalid_output", "ow_status", {}, 1, "error"),
        ("exception", "ow_status", {}, 1, "error"),
        ("log", "ow_status", {}, 0, "ok"),
    ],
)
def test_process_status_exit_contract(
    scenario, operation, arguments, code, status, tmp_path, environment
):
    process, body = run_cli(
        ["query", operation, "--input-file", "-"],
        tmp_path,
        environment,
        input=json.dumps(arguments),
        scenario=scenario,
    )
    assert process.returncode == code
    assert body["status"] == status
    Draft202012Validator(schemas()[operation]["outputSchema"]).validate(body)
    assert "private-debug-value" not in process.stdout + process.stderr
    if scenario == "log":
        assert "fixture diagnostic" in process.stderr
    if scenario in ("partial", "failed_comparison"):
        assert len(body["data"]) == 2
        assert body["source_status"]["ilios"] == "error"


def test_database_default_is_shared_and_explicit_precedence(tmp_path, environment):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    imported = first / "ranker.json"
    imported.write_text(json.dumps([ranker_record()]), encoding="utf-8")
    process, body = run_cli(["import-rankers", str(imported)], first, environment)
    assert process.returncode == 0 and len(body["imported_player_ids"]) == 1
    process, body = run_cli(["query", "ow_rankers_search"], second, environment)
    assert process.returncode == 0 and len(body["data"]) == 1
    environment["OW_DB_PATH"] = str(tmp_path / "env.db")
    process, body = run_cli(["query", "ow_rankers_search"], second, environment)
    assert process.returncode == 0 and body["status"] == "empty"
    assert Path(environment["OW_DB_PATH"]).exists()
    process, body = run_cli(
        [
            "--db",
            str(Path(environment["HOME"]) / ".overwatch-aio-skill/overwatch.db"),
            "query",
            "ow_rankers_search",
        ],
        second,
        environment,
    )
    assert process.returncode == 0 and len(body["data"]) == 1


def test_repository_default_and_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("OW_DB_PATH", raising=False)
    expected = tmp_path / ".overwatch-aio-skill/overwatch.db"
    assert resolve_db_path() == expected
    repo = Repository()
    repo.close()
    assert expected.exists()
    monkeypatch.setenv("OW_DB_PATH", "~/custom.db")
    assert resolve_db_path() == tmp_path / "custom.db"
    assert resolve_db_path(":memory:") == ":memory:"


def test_legacy_database_preserves_unused_jobs_and_all_evidence(tmp_path, environment):
    path = tmp_path / "legacy.db"
    legacy_sql = (ROOT / "tests/fixtures/legacy-schema.sql").read_text(encoding="utf-8-sig")
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy_sql)
        connection.execute("INSERT INTO collection_jobs VALUES ('keep','old-job',10,20,'ok',5)")
    repo = Repository(path)
    registry = Registry(repo)
    player_id = registry.import_rankers([ranker_record()])[0]
    repo.save_meta(meta_result())
    repo.cache_put("keep", "fixture", "https://example.test", b"{}", {}, "2026-01-01T00:00:00Z", 60)
    repo.save_replays(
        [
            {
                "code": "ABC123",
                "source": "owreplays",
                "source_url": "https://example.test/replay",
                "replay_status": "unverified",
                "heroes": [],
            }
        ]
    )
    registry.add_replay_evidence("ABC123", ranker_record()["evidence"], player_id)
    registry.validate_replay(
        "ABC123", "client_verified", "fixture observer", "fixture evidence", "2026-01-01T00:00:00Z"
    )
    tables = [
        row[0] for row in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    before = {
        name: [tuple(row) for row in repo.conn.execute(f'SELECT * FROM "{name}"')]
        for name in tables
    }
    repo.close()
    process, body = run_cli(
        ["--db", str(path), "query", "ow_replay_get", "--input-file", "-"],
        tmp_path,
        environment,
        input='{"code":"ABC123","refresh":false}',
    )
    assert process.returncode == 0
    assert body["data"]["player_id"] == player_id
    assert body["data"]["validation"]["status"] == "client_verified"
    repo = Repository(path)
    try:
        after = {
            name: [tuple(row) for row in repo.conn.execute(f'SELECT * FROM "{name}"')]
            for name in tables
        }
        assert after == before
    finally:
        repo.close()
    fresh = Repository(":memory:")
    try:
        assert (
            fresh.conn.execute(
                "SELECT name FROM sqlite_master WHERE name='collection_jobs'"
            ).fetchone()
            is None
        )
    finally:
        fresh.close()


def test_schema_export_needs_no_database(tmp_path, environment):
    process, body = run_cli(["schema"], tmp_path, environment)
    assert process.returncode == 0 and body == schemas()
    target = tmp_path / "schemas" / "queries.json"
    process = subprocess.run(
        [sys.executable, "-m", "overwatch_skill", "schema", "--output", str(target)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 0 and process.stdout == b""
    assert json.loads(target.read_text(encoding="utf-8")) == body
    assert not (Path(environment["HOME"]) / ".overwatch-aio-skill").exists()


def test_unusable_database_is_execution_failure(tmp_path, environment):
    path = tmp_path / "invalid.db"
    path.write_bytes(b"not a sqlite database")
    process, body = run_cli(["--db", str(path), "query", "ow_status"], tmp_path, environment)
    assert process.returncode == 1
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in process.stdout + process.stderr


def test_curation_commands_link_evidence_and_record_observations(tmp_path, environment):
    path = tmp_path / "evidence.db"
    repo = Repository(path)
    player_id = Registry(repo).import_rankers([ranker_record()])[0]
    repo.save_replays([replay_record()])
    repo.close()
    args = ["--db", str(path)]
    process, body = run_cli([*args, "discover-rankers"], tmp_path, environment)
    assert process.returncode == 0
    assert body["candidates"][0]["country"] is None
    assert body["candidates"][0]["verification"] == "source_claim"
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps(ranker_record()["evidence"]), encoding="utf-8-sig")
    process, body = run_cli(
        [
            *args,
            "link-replay",
            "abc123",
            "--player-id",
            str(player_id),
            "--evidence-file",
            str(evidence_file),
        ],
        tmp_path,
        environment,
    )
    assert process.returncode == 0 and body["evidence_recorded"] is True
    process, body = run_cli(
        [
            *args,
            "record-replay-check",
            "abc123",
            "--status",
            "client_verified",
            "--source",
            "fixture observer",
            "--evidence-ref",
            "fixture actual observation",
            "--observed-at",
            "2026-01-01T00:00:00Z",
        ],
        tmp_path,
        environment,
    )
    assert process.returncode == 0 and body["status"] == "client_verified"
    repo = Repository(path)
    try:
        replay = repo.replay_rows("ABC123")[0]
        assert replay["player_id"] == player_id
        assert len(replay["evidence"]) == 3
        assert replay["validation"]["evidence_ref"] == "fixture actual observation"
    finally:
        repo.close()


@pytest.mark.parametrize("raw", ["null", "{}", '["wrong"]'])
def test_curation_rejects_wrong_input_shape(raw, tmp_path, environment):
    process, body = run_cli(["import-rankers", "-"], tmp_path, environment, input=raw)
    assert process.returncode == 2 and body["error"]["code"] == "INVALID_ARGUMENT"
