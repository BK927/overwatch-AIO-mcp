"""MCP launch command; help and schemas also work in skill-only installations."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .execution import schemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Overwatch 2 Data MCP query server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", help="SQLite path (then OW_DB_PATH, then per-user default)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Start MCP (default command); no scheduled collection")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    schema = sub.add_parser("schema", help="Export the shared operation input/output schemas")
    schema.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command == "schema":
        text = json.dumps(schemas(), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(text, end="")
        return 0
    try:
        from .server import create_server
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        print(
            "MCP support is not installed. From the project root run: uv sync --frozen --no-dev --extra mcp. "
            "Launch with: uv run --frozen --no-dev --extra mcp overwatch-aio-mcp serve",
            file=sys.stderr,
        )
        return 1
    transport = getattr(args, "transport", "stdio")
    kwargs = {} if transport == "stdio" else {"host": args.host, "port": args.port}
    create_server(db_path=args.db).run(transport=transport, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
