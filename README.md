# Modal Sandbox MCP

Give your AI agent **on-demand cloud compute** — CPU, RAM, and even GPU — through a
[Model Context Protocol](https://modelcontextprotocol.io) (MCP) server backed by
[Modal](https://modal.com) sandboxes.

`run_script` executes arbitrary Python on Modal's serverless infrastructure with
per-second billing, so your agent can crunch through heavy jobs that would crawl
(or OOM) on a small VPS — without you renting an always-on GPU box.

Works with **Open WebUI** (native MCP or via the mcpo OpenAPI bridge), Claude, and
any other MCP-capable client.

---

## Why

Your Open WebUI / agent host (e.g. a cheap VPS) usually has:

- limited CPU and RAM
- no GPU
- no burst capacity

Modal provides:

- containers with **up to 64+ cores, 100+ GB RAM**
- **GPUs** (T4, A10G, A100, ...) on demand
- scale-to-zero: **you pay nothing while idle**, only per-second while a script runs
- a free monthly credit (~$30/mo) — plenty for light/medium usage

This server is the glue: a thin MCP wrapper that turns "run this script" into a
Modal sandbox with whatever specs the agent asks for.

---

## Architecture

```
┌────────────┐    MCP (stdio or HTTP)    ┌──────────────────┐
│  Client    │ ────────────────────────▶ │  FastMCP server  │
│ (Open WebUI│                           │  (this repo)     │
│  / Claude) │ ◀──────────────────────── │                  │
└────────────┘                           └────────┬─────────┘
                                                  │ modal.Sandbox.create(
                                                  │   cpu=..., memory=...,
                                                  │   gpu=..., timeout=...)
                                                  ▼
                                        ┌──────────────────┐
                                        │  Modal cloud     │
                                        │  (ephemeral      │
                                        │   sandbox)       │
                                        └──────────────────┘
```

Two deployment flavors are included:

| File | Where it runs | Best for |
|---|---|---|
| `sandbox_mcp.py` | **On Modal** (`modal deploy`) | No server to babysit; public URL |
| `sandbox_mcp_local.py` | **On your own host** (systemd / mcpo) | Private (bind to Docker bridge), matches mcpo-style Open WebUI setups |

The heavy lifting always happens on Modal either way — the wrapper is just glue.

---

## Setup

### 1. Prerequisites

- A [Modal](https://modal.com) account (free tier: no payment method required)
- Python 3.10+ (for the local flavor)

### 2. Install & authenticate

```bash
pip install modal fastmcp uvicorn   # or: uv pip install ...
modal token new                     # opens browser; stores creds in ~/.modal.toml
```

### 3a. Deploy on Modal (hosted flavor)

```bash
modal deploy sandbox_mcp.py
```

Note the printed URL — it serves MCP over streamable HTTP at `<url>/mcp`.

### 3b. Run locally (glue flavor)

```bash
python sandbox_mcp_local.py --http --host 127.0.0.1 --port 8020
# stdio mode (for mcpo / MCP stdio clients):
python sandbox_mcp_local.py
```

Recommended for Open WebUI: wrap it with **mcpo** so it appears as an OpenAPI
tool server, exactly like the official Open WebUI MCP servers:

```bash
uvx mcpo --port 8021 --name sandbox-mcp \
  --description "Run Python scripts on Modal hardware via Modal Sandboxes." \
  -- /path/to/venv/bin/python /path/to/sandbox_mcp_local.py
```

A ready-made systemd user unit is in [`deploy/sandbox-mcpo.service`](deploy/sandbox-mcpo.service).

---

## Connecting Open WebUI

**Via OpenAPI tool server (mcpo):**
Admin Panel → Settings → Connections → **OpenAPI Tool Servers** → add:

```
http://<host>:8021/openapi.json
```

Then in a chat: **+ → Tools** → enable `sandbox-mcp`.

**Via native MCP:**
Admin Panel → Settings → Connections → **MCP Servers** → add:

```
http://<host>:8020/mcp     (streamable HTTP)
```

> 💡 Open WebUI's MCP connection test runs **from your browser** — a server bound to
> a private address (e.g. Docker bridge `172.17.0.1`) will fail the browser-side
> check even though the backend can reach it. The **OpenAPI/mcpo route** is fetched
> server-side and is the reliable choice for containerized Open WebUI.

---

## The tool: `run_script`

| Param | Type | Default | Meaning |
|---|---|---|---|
| `code` | string | *required* | Python source to execute (use `print()` for output) |
| `cpu` | number | 2.0 | CPU cores (e.g. 1.0, 4.0, 8.0) |
| `memory_mb` | integer | 2048 | RAM in MB (e.g. 8192, 65536) |
| `gpu` | string | `""` | GPU type: `T4`, `A10G`, `A100`; empty = CPU-only |
| `timeout` | integer | 600 | Max seconds before the sandbox is force-killed |

Returns `exit code`, stdout, and stderr (truncated at 100 KB). Each run is a fresh,
isolated, ephemeral sandbox — no persistent state, no access to your host's files.

Sandboxes have network access and come with Python + `requests`; scripts can
`pip install` extra packages at runtime (adds a little time).

---

## Tuning timeouts (Open WebUI gotcha)

Open WebUI caps tool-server calls with the aiohttp client timeout. If your scripts
run longer than 5 minutes, set this env var **on the open-webui container**
(default fallback is only **300s**):

```
AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER=900
```

(Requires recreating the container; keep it above your sandbox `timeout`.)

Full stack for reference: Open WebUI `900s` → mcpo `900s` → sandbox `timeout` (the
real backstop).

---

## Cost notes

- CPU sandbox runs cost **fractions of a cent** — effectively free inside Modal's
  monthly credit.
- GPUs are the expensive option (~$0.60+/hr). The agent should default to CPU and
  only request a GPU when the workload genuinely needs one (see the example system
  prompt).
- Scale-to-zero means an idle server costs **nothing**.

---

## Security notes

- The `run_script` tool is **arbitrary code execution** by design. It's for *your*
  agent, not strangers.
- **Hosted flavor:** the Modal URL is unauthenticated — anyone with it can run code
  on your Modal account. Add auth (e.g. a bearer-token middleware) before exposing
  it publicly.
- **Local flavor:** bind to `127.0.0.1` or the Docker bridge gateway
  (`172.17.0.1`) so only your container/host can reach it.
- Modal tokens stay in `~/.modal.toml` (or `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`
  env vars) — never commit them.

---

## Example agent system prompt

See [`examples/system-prompt.md`](examples/system-prompt.md) — copy it into your
model's system prompt (Open WebUI: Workspace → Models → edit → System Prompt) to
teach the agent *when* and *how* to use the tool.

---

## Files

```
sandbox_mcp.py          # Modal-hosted flavor (modal deploy)
sandbox_mcp_local.py    # local glue flavor (HTTP or stdio/mcpo)
deploy/
  sandbox-mcpo.service  # systemd user unit for mcpo wrapping
examples/
  system-prompt.md      # ready-to-paste agent instructions
```

## License

MIT — see [LICENSE](LICENSE).
