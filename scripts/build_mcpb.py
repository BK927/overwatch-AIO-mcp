"""Build a source-only UV bundle and matching official MCP Registry metadata."""

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

REPOSITORY = "https://github.com/BK927/overwatch-aio"
REGISTRY_NAME = "io.github.BK927/overwatch-aio"


def bundle_files(root: Path) -> dict[str, bytes]:
    # Explicit distribution boundary: never sweep a checkout, home, DB or virtualenv.
    files = {
        name: (root / name).read_bytes()
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
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    for directory in ("docs", "references", "examples", "agents"):
        for path in sorted((root / directory).iterdir()):
            if path.is_file() and path.suffix in (".md", ".json", ".toml", ".yaml"):
                files[path.relative_to(root).as_posix()] = path.read_bytes()
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
        "display_name": "Overwatch AIO MCP",
        "version": version,
        "description": "Overwatch hero meta, player statistics, replays, patches and OWCS Korea with sources and evidence.",
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
        "compatibility": {"platforms": ["win32", "linux"], "runtimes": {"python": ">=3.11"}},
        "keywords": project["keywords"],
    }
    files = bundle_files(root)
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / f"overwatch-aio-{version}.mcpb"
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    registry = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": REGISTRY_NAME,
        "title": "Overwatch AIO MCP",
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
