"""Exercise both real transports in temporary local processes, then shut them down."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters


async def main():
    Path(".codex-tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="transport-", dir=".codex-tmp") as temporary:
        db = str(Path(temporary, "transport.db").resolve())
        async with Client(
            StdioServerParameters(
                command=sys.executable, args=["-m", "overwatch_mcp.server", "--db", db, "serve"]
            )
        ) as client:
            tools = await client.list_tools()
            result = await client.call_tool("ow_status", {})
            assert len(tools.tools) == 10 and result.structured_content["status"] == "ok"
            print("stdio: handshake, 10 tools, structured call passed", flush=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        args = [
            sys.executable,
            "-m",
            "overwatch_mcp.server",
            "--db",
            db,
            "serve",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ]
        with Path(temporary, "http.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                args,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                ready = False
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("HTTP server exited during startup")
                    try:
                        reader, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.close()
                        await writer.wait_closed()
                        ready = True
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
                if not ready:
                    raise RuntimeError("HTTP server did not start within ten seconds")
                async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                    tools = await client.list_tools()
                    result = await client.call_tool("ow_rankers_search", {"country": "KR"})
                    assert len(tools.tools) == 10
                    assert result.structured_content["error"]["code"] == "EMPTY_RESULT"
                print("streamable-http: handshake, 10 tools, structured call passed", flush=True)
            finally:
                # Windows venv launchers may spawn an interpreter child; terminate our own tree.
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print(json.dumps({"transports": ["stdio", "streamable-http"], "status": "passed"}))


if __name__ == "__main__":
    asyncio.run(main())
