"""The no-extra CI environment must keep the skill fully independent of MCP."""

import importlib.util
import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is not None,
    reason="Exercised in the separate skill-only installation job",
)


def test_skill_imports_and_schema_without_mcp(tmp_path):
    code = """
import importlib.util, json, sys
from overwatch_skill import cli
assert importlib.util.find_spec('mcp') is None
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
assert cli.main(['schema']) == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 10


def test_missing_mcp_extra_has_install_guidance_without_protocol_output(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "overwatch_skill.mcp_cli", "serve"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "--extra mcp" in result.stderr
    assert "Traceback" not in result.stderr
