"""
Modal-hosted MCP server: lets an Open WebUI agent run Python scripts
on on-demand Modal hardware (CPU / RAM / GPU) via Modal Sandboxes.

Deploy:
    pip install modal
    modal token new        # or set MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
    modal deploy sandbox_mcp.py

Connect in Open WebUI:
    Admin Panel -> Settings -> Connections -> MCP Servers -> add
    URL: https://<your-user>--sandbox-mcp-server.modal.run/mcp
    Transport: streamable HTTP (if your Open WebUI only offers SSE,
    swap streamable_http_app() for sse_app() below).
"""

from fastmcp import FastMCP
from modal import App, Image, Sandbox, asgi_app

# --- Modal app + images ------------------------------------------------

app = App("sandbox-mcp-server")

server_image = Image.debian_slim().pip_install("fastmcp")
# Add any packages your agent's scripts commonly need here:
sandbox_image = Image.debian_slim().pip_install("requests")

mcp = FastMCP("sandbox-mcp")

MAX_OUTPUT = 100_000  # chars; keeps tool responses context-friendly


# --- Tools ---------------------------------------------------------------

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
    )
    try:
        proc = sb.exec("python3", "-")
        proc.stdin.write(code)
        proc.stdin.write_eof()
        proc.stdin.drain()
        proc.wait()

        out = b"".join(proc.stdout).decode("utf-8", errors="replace")
        err = b"".join(proc.stderr).decode("utf-8", errors="replace")
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


# --- Serve over HTTP -----------------------------------------------------

@app.function(image=server_image, allow_concurrent_inputs=100)
@asgi_app()
def fastapi_app():
    # Streamable HTTP (preferred by Open WebUI). SSE fallback: http_app(transport="sse")
    return mcp.http_app(transport="streamable-http")
