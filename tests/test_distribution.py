"""Verify the actual MCPB launch and the boundary around distributed files."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts/build_mcpb.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_bundle_contains_only_runtime_sources_and_matching_hash(tmp_path):
    bundle, metadata = builder.build(ROOT, tmp_path)
    registry = json.loads(metadata.read_text())
    checked_in_registry = json.loads((ROOT / "server.json").read_text())
    assert checked_in_registry == registry
    assert registry["packages"][0]["fileSha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    with ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {
            "manifest.json",
            "pyproject.toml",
            "uv.lock",
            "mcp_entry.py",
            "src/overwatch_skill/db/schema.sql",
        } <= names
        assert not any(
            name.endswith((".db", ".sqlite", ".pyc"))
            or name.startswith((".git", ".venv", "tests/", ".codex"))
            for name in names
        )
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == registry["version"]
        assert set(manifest["compatibility"]["platforms"]) == {"win32", "darwin", "linux"}
        assert {tool["name"] for tool in manifest["tools"]} == set(
            json.loads((ROOT / "docs/query-schemas.json").read_text())
        )
        assert manifest["server"]["entry_point"] in names


def test_bundle_is_reproducible_with_lf_text(tmp_path):
    first, _ = builder.build(ROOT, tmp_path / "first")
    second, _ = builder.build(ROOT, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()

    with ZipFile(first) as archive:
        for name in archive.namelist():
            assert b"\r" not in archive.read(name), name


def test_extracted_bundle_runs_its_declared_mcp_command(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client, StdioServerParameters

    bundle, _ = builder.build(ROOT, tmp_path)
    install = tmp_path / "설치 bundle"
    with ZipFile(bundle) as archive:
        archive.extractall(install)
    manifest = json.loads((install / "manifest.json").read_text())
    command = manifest["server"]["mcp_config"]
    env = {**os.environ, "OW_DB_PATH": str(tmp_path / "test.sqlite")}
    env.pop("UV_PROJECT_ENVIRONMENT", None)

    async def run():
        parameters = StdioServerParameters(
            command=command["command"],
            args=[value.replace("${__dirname}", str(install)) for value in command["args"]],
            env=env,
            cwd=str(tmp_path),
        )
        async with Client(parameters) as client:
            assert len((await client.list_tools()).tools) == 10
            result = await client.call_tool("ow_status", {})
            assert not result.is_error
            assert result.structured_content["data"]["stored_counts"]["players"] == 0

    asyncio.run(run())
