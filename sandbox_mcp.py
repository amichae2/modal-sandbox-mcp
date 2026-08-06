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


@mcp.tool()
def list_hardware() -> str:
    """List available Modal hardware options: CPU/RAM tiers and GPU types with approximate pricing and guidance.

    Call this BEFORE run_script whenever you need to choose cpu, memory_mb, or gpu
    values. Returns a static catalog (prices approximate — verify at modal.com/pricing).
    """
    return """MODAL HARDWARE CATALOG (approximate prices; verify at https://modal.com/pricing)

CPU & MEMORY (serverless sandbox, per-second billing):
- cpu: any value, e.g. 1.0, 2.0, 4.0, 8.0, 16.0+ (vCPU cores)
- memory_mb: any value, e.g. 2048, 8192, 65536 (MB)
- Cost: fractions of a cent per typical run (~$0.14 per vCPU-hour).
  Within Modal's monthly credit (~$30/mo), light-medium use is effectively free.

GPU OPTIONS (per-second billing, approx per hour, cheapest first):
- T4 (16GB): ~$0.59/hr — entry GPU, light inference
- L4 (24GB): ~$0.80/hr — efficient inference, small models
- A10G (24GB): ~$1.10/hr — general-purpose training/inference
- L40S (48GB): ~$1.95/hr — heavier training, more VRAM
- A100 40GB: ~$2.10/hr — serious training
- A100 80GB: ~$2.50/hr — large models
- RTX PRO 6000 (48GB): ~$3.03/hr — pro workstation class
- H100 80GB: ~$3.95/hr — flagship Hopper, large LLM training
- H200 141GB: ~$4.54/hr — Hopper successor, huge VRAM
- B200 192GB: ~$6.25/hr — Blackwell datacenter GPU
- B300 288GB: ~$7.10/hr — Blackwell Ultra, top tier

GUIDANCE:
- Default to CPU-only (gpu="") unless the task is ML inference/training,
  heavy numerics, or image/video processing.
- Small models / inference -> T4 or L4.
- Training / finetuning -> A10G or L40S.
- Large models (7B+ params, big LoRA bases) -> A100 80GB or H100.
- Very large models / frontier-scale jobs -> H200 / B200 / B300.
- GPU is the only expensive option — never request it casually."""


# --- Serve over HTTP -----------------------------------------------------

@app.function(image=server_image, allow_concurrent_inputs=100)
@asgi_app()
def fastapi_app():
    # Streamable HTTP (preferred by Open WebUI). SSE fallback: http_app(transport="sse")
    return mcp.http_app(transport="streamable-http")
