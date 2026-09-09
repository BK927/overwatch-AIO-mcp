"""Run the real HTTP CLI with a portable, test-only graceful shutdown signal."""

import sys
import threading

import uvicorn

from overwatch_skill.mcp_cli import main

original_serve = uvicorn.Server.serve


async def serve_until_stdin_closes(server, *args, **kwargs):
    def stop():
        sys.stdin.read()
        server.should_exit = True

    threading.Thread(target=stop, daemon=True).start()
    return await original_serve(server, *args, **kwargs)


uvicorn.Server.serve = serve_until_stdin_closes
raise SystemExit(main(sys.argv[1:]))
