"""
Local variant of the Modal-backed MCP server for Open WebUI.

Same tool as sandbox_mcp.py (run_script -> Modal Sandbox), but this file
has no Modal App/deploy plumbing: it runs as a plain Python process on the
Open WebUI server itself (like the mcpo-hosted servers), and only calls
out to Modal's API when a sandbox is actually created. The heavy compute
still happens in Modal's cloud -- this wrapper is just the glue.

Modal SDK >= 1.5 requires an App reference for client-side Sandbox.create,
so we lazily create one via App.lookup(create_if_missing=True) and pass
it to every sandbox.

Requirements on the host:
    pip install modal fastmcp uvicorn
    modal token new          # or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET env vars

Run (HTTP / streamable, for Open WebUI's native MCP connection):
    python3 sandbox_mcp_local.py --http --host 172.17.0.1 --port 8020
    -> Open WebUI MCP URL: http://<host-reachable-address>:8020/mcp

Run (stdio, for mcpo like your other servers):
    uvx mcpo --port 8010 -- python3 sandbox_mcp_local.py
    -> register the mcpo endpoint as an OpenAPI tool server in Open WebUI
"""

import argparse
import sys

from fastmcp import FastMCP
from modal import App, Image, Sandbox

# Packages your agent's scripts commonly need go here:
sandbox_image = Image.debian_slim().pip_install("requests")

mcp = FastMCP("sandbox-mcp")

MAX_OUTPUT = 100_000  # chars; keeps tool responses context-friendly

_app = None


def _get_app():
    """Lazily create/reuse a Modal App reference (required by SDK >= 1.5)."""
    global _app
    if _app is None:
        _app = App.lookup("sandbox-mcp", create_if_missing=True)
    return _app


@mcp.tool()
def run_script(
    code: str,
    cpu: float = 2.0,
    memory_mb: int = 2048,
    gpu: str = "",
    timeout: int = 600,
) -> str:
    """Run a Python script on Modal hardware and return its stdout/stderr.

    Args:
        code: Python source code to execute.
        cpu: Number of CPU cores (e.g. 1.0, 2.0, 8.0).
        memory_mb: Memory in MB (e.g. 2048, 8192, 65536).
        gpu: GPU type for the sandbox, e.g. "T4", "A10G", "A100". Leave empty for CPU-only.
        timeout: Max seconds the script may run before it is force-killed (default 600).
    """
    gpu = gpu.strip() or None

    sb = Sandbox.create(
        "python3",
        image=sandbox_image,
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        timeout=timeout,
        app=_get_app(),
    )
    try:
        proc = sb.exec("python3", "-")
        proc.stdin.write(code)
        proc.stdin.write_eof()
        proc.stdin.drain()
        proc.wait()

        out = "".join(proc.stdout)
        err = "".join(proc.stderr)
        rc = proc.poll()

        result = f"exit code: {rc}\n\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        if len(result) > MAX_OUTPUT:
            result = result[:MAX_OUTPUT] + "\n...[truncated]"
        return result
    finally:
        try:
            sb.terminate()
        except Exception:
            pass  # sandbox already exited


def main():
    parser = argparse.ArgumentParser(description="Modal-backed MCP server (local glue)")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8020, help="HTTP port")
    args = parser.parse_args()

    # Resolve the Modal App reference up front so startup fails fast if creds are bad.
    # Keep ALL logs on stderr -- stdout is the MCP stdio transport when running under mcpo.
    _get_app()
    print(f"Modal app ready: sandbox-mcp ({_app.app_id})", file=sys.stderr, flush=True)

    if args.http:
        import uvicorn

        uvicorn.run(mcp.http_app(transport="streamable-http"), host=args.host, port=args.port)
    else:
        # stdio transport, for mcpo. Banner suppressed: stdout must stay clean JSON-RPC.
        mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
