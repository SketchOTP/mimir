# Cursor MCP Setup — Mimir Remote Server

Mimir exposes a Streamable HTTP MCP endpoint at `/mcp`. Cursor connects via OAuth 2.1 / PKCE — **no manual API key copy-paste required**.

---

## Prerequisites

- Mimir server running (see [SELF_HOSTING.md](SELF_HOSTING.md))
- Owner account created (`python -m mimir.auth.create_owner`)
- `MIMIR_AUTH_MODE=single_user` or `multi_user` set

---

## Add Mimir in Cursor (OAuth — recommended)

1. Open **Cursor Settings** → **MCP** → **Add MCP Server**
2. Choose **URL** as the transport type
3. Enter the URL only:

| Field | Value |
|-------|-------|
| **Name** | `Mimir` |
| **URL** | `http://127.0.0.1:8787/mcp` |

4. Click **Save**. Cursor will open your browser for OAuth authentication.
5. Enter your Mimir API key in the browser → click **Authorize Access**.
6. Cursor stores the token — future connections are automatic.

### LAN server

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://192.168.1.246:8787/mcp"
    }
  }
}
```

### Hosted server

```json
{
  "mcpServers": {
    "mimir": {
      "url": "https://mimir.example.com/mcp"
    }
  }
}
```

---

## Manual Bearer Fallback

If OAuth is unavailable, use a static API key directly:

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

---

## Add Mimir in Cursor (legacy header mode)

---

## Available Tools

| Tool | Description |
|------|-------------|
| `memory.remember` | Store an event or fact |
| `memory.recall` | Retrieve relevant memories for a query |
| `memory.search` | Semantic search across all memory layers |
| `memory.record_outcome` | Record the outcome of a task |
| `skill.list` | List available skills |
| `approval.request` | Create an approval request for an improvement |
| `approval.status` | List pending and recent approvals |
| `reflection.log` | Log observations and lessons |
| `improvement.propose` | Propose a system improvement |

---

## Example: Verify with curl

```bash
# List tools
curl -s -X POST http://192.168.1.246:8787/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-MIMIR_API_KEY>" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | jq .result.tools[].name

# Store a memory
curl -s -X POST http://192.168.1.246:8787/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-MIMIR_API_KEY>" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 2,
    "params": {
      "name": "memory.remember",
      "arguments": {
        "type": "fact",
        "content": "Cursor is configured with Mimir MCP",
        "project": "my-project"
      }
    }
  }' | jq .
```

---

## Auth Details

- The endpoint accepts `Authorization: Bearer <key>` (standard Bearer token)
- Also accepts `X-API-Key: <key>` for backward compatibility
- In dev mode (`MIMIR_ENV=development`), auth is bypassed automatically
- In prod mode (`MIMIR_AUTH_MODE=prod`), requests without a valid key return HTTP 401

---

## Troubleshooting

**401 Unauthorized** — Key doesn't match `MIMIR_API_KEY` on the server. Check the env var on the machine running Mimir.

**Connection refused** — Verify Mimir is running and the port is reachable (check Tailscale if remote).

**Tool call returns error JSON** — Check the `error` field in the JSON-RPC response for details.

---

## Protocol Notes

- Transport: MCP Streamable HTTP 2025-03-26 spec (JSON-RPC 2.0)
- **POST /mcp** — client→server messages; responds with `text/event-stream` SSE when `Accept` includes `text/event-stream` (required by Cursor)
- **GET /mcp** — server→client SSE keep-alive channel; Mimir holds it open with 15-second heartbeats
- **DELETE /mcp** — session cleanup; always 200 (stateless)
- Notifications (no `id`) return 202 per spec
- No `Mcp-Session-Id` session tracking (stateless)
- No Mimir package install needed on the Cursor machine
- No DB files are copied — all data stays on the Atlas Mimir host
