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
