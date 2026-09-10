"""Build a source-only UV bundle and matching official MCP Registry metadata."""

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

REPOSITORY = "https://github.com/BK927/overwatch-aio"
REGISTRY_NAME = "io.github.BK927/overwatch-aio"


def normalized_text_bytes(path: Path) -> bytes:
    """Return platform-independent text bytes for a reproducible MCPB."""
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def bundle_files(root: Path) -> dict[str, bytes]:
    # Explicit distribution boundary: never sweep a checkout, home, DB or virtualenv.
    files = {
        name: normalized_text_bytes(root / name)
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "README.ko.md",
            "SKILL.md",
            "SPEC.md",
            "research/source-contracts.md",
        )
    }
    for suffix in ("*.py", "*.sql"):
        for path in sorted((root / "src/overwatch_skill").rglob(suffix)):
            files[path.relative_to(root).as_posix()] = normalized_text_bytes(path)
    for directory in ("docs", "references", "examples", "agents"):
        for path in sorted((root / directory).iterdir()):
            if path.is_file() and path.suffix in (".md", ".json", ".toml", ".yaml"):
                files[path.relative_to(root).as_posix()] = normalized_text_bytes(path)
    files["mcp_entry.py"] = (
        b'"""MCPB entrypoint; dependencies are prepared by the UV runtime."""\n'
        b"from overwatch_skill.mcp_cli import main\n\n"
        b'if __name__ == "__main__":\n    raise SystemExit(main())\n'
    )
    return files


def build(root: Path, output: Path) -> tuple[Path, Path]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    queries = json.loads((root / "docs/query-schemas.json").read_text(encoding="utf-8"))
    manifest = {
        "manifest_version": "0.4",
        "name": "overwatch-aio",
        "display_name": "Overwatch 2 Data MCP Server",
        "version": version,
        "description": "Overwatch 2 MCP for hero meta, public player stats, replays, patch notes, and OWCS Korea.",
        "author": {"name": "BK927", "url": "https://github.com/BK927"},
        "repository": {"type": "git", "url": REPOSITORY},
        "homepage": REPOSITORY,
        "documentation": REPOSITORY + "#readme",
        "server": {
            "type": "uv",
            "entry_point": "mcp_entry.py",
            "mcp_config": {
                "command": "uv",
                "args": [
                    "run",
                    "--frozen",
                    "--no-dev",
                    "--no-editable",
                    "--extra",
                    "mcp",
                    "--project",
                    "${__dirname}",
                    "${__dirname}/mcp_entry.py",
                    "serve",
                ],
            },
        },
        "tools": [
            {"name": name, "description": value["description"]} for name, value in queries.items()
        ],
        "compatibility": {
            "platforms": ["win32", "darwin", "linux"],
            "runtimes": {"python": ">=3.11"},
        },
        "keywords": project["keywords"],
    }
    files = bundle_files(root)
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / f"overwatch-aio-{version}.mcpb"
    # Stored entries plus fixed metadata make the bundle identical across zlib
    # versions and operating systems. The source-only bundle is small enough
    # that cross-platform reproducibility is more valuable than compression.
    with ZipFile(bundle, "w", compression=ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    registry = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": REGISTRY_NAME,
        "title": "Overwatch 2 Data MCP Server",
        "description": manifest["description"],
        "repository": {"url": REPOSITORY, "source": "github"},
        "version": version,
        "websiteUrl": REPOSITORY,
        "packages": [
            {
                "registryType": "mcpb",
                "identifier": f"{REPOSITORY}/releases/download/v{version}/{bundle.name}",
                "fileSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "transport": {"type": "stdio"},
            }
        ],
    }
    metadata = output / "server.json"
    metadata.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return bundle, metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    for path in build(Path(__file__).resolve().parents[1], args.output_dir):
        print(path)
