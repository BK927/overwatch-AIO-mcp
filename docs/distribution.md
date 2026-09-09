# Discovery and distribution

The public repository is [BK927/overwatch-aio](https://github.com/BK927/overwatch-aio). The Python distribution and standalone skill remain `overwatch-aio-skill`. The GitHub description and topics include both MCP and agent skills; the main README is English with a complete Korean guide alongside it.

## Skill installation and discovery

```text
npx skills add BK927/overwatch-aio --skill overwatch-aio-skill --agent codex
```

This installs into the current project; add `--global` for a personal installation. The repository root contains the skill and all its Python sources. Python 3.11+ and `uv` are still needed for queries. The skills installer is an alternative to Codex's Skill Installer, not an additional runtime dependency.

The [skills.sh FAQ](https://www.skills.sh/docs/faq) describes automatic listing through installation telemetry. A successful install does not guarantee immediate search indexing or ranking. Do not treat a guessed directory URL as evidence that a listing is live.

## MCP bundle

The release bundle uses the [MCPB UV runtime](https://github.com/anthropics/mcpb/blob/main/MANIFEST.md). A compatible host must support manifest version 0.4 and UV server configuration. Source-based stdio/HTTP installation remains available for other clients.

The manifest declares the exact `uv` command, the `mcp` extra, and ten tools. It uses the same DB selection and server entrypoint as the source install. Dependencies are prepared on first launch; the bundle does not include a virtual environment or user's data. Windows and Linux are covered by CI.

Build from the intended release checkout:

```text
uv run --frozen --no-dev python scripts/build_mcpb.py
```

This creates `dist/overwatch-aio-<version>.mcpb` and `dist/server.json`. The builder includes only selected source and documentation files and computes the registry's SHA-256 from the actual bundle. Development tests and build helpers remain in the GitHub checkout rather than the runtime bundle.

Run `uv run --frozen --extra dev --extra mcp pytest tests/test_distribution.py -q` to check packaging and launch the extracted bundle's declared command. The release additionally uses the official MCPB CLI to validate the manifest.

## Official MCP Registry publication

Registry identity: **`io.github.BK927/overwatch-aio`**. The [official registry](https://registry.modelcontextprotocol.io/) accepts MCPB artifacts on GitHub Releases, with their SHA-256 recorded in `server.json`. See [supported package types](https://modelcontextprotocol.io/registry/package-types).

1. Update the Python package version, refresh `uv.lock`, and run validation.
2. Build the bundle and validate it. Copy the generated registry metadata to the repository's `server.json` before committing the release.
3. Create a GitHub release tagged `v<version>` at that exact commit and upload the bundle and metadata. Keep already published artifacts unchanged.
4. Log in with the official `mcp-publisher`, then publish `server.json`.
5. Query the registry for the exact name and version; check that the package URL and hash match the release asset. Search index propagation can lag publication.

Registry credentials belong in the publisher's credential storage, never in this repository. No PyPI upload is needed for this MCPB distribution path.
