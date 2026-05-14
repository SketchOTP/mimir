# Cursor MCP Setup — Mimir Remote Server

Mimir exposes a Streamable HTTP MCP endpoint at `/mcp`. Use OAuth for normal local/browser-capable Cursor setups. Use API-key Bearer auth for Cursor over SSH, headless clients, remote development, and RPi5 workflows.

---

## Prerequisites

- Mimir server running (see [SELF_HOSTING.md](SELF_HOSTING.md))
- Owner account created (`python -m mimir.auth.create_owner`)
- `MIMIR_AUTH_MODE=single_user` or `multi_user` set

---

## Choose The Right Auth Path

| Setup | Recommended auth |
|-------|------------------|
| Cursor running locally with browser access | OAuth |
| Cursor over SSH | API key |
| Headless client | API key |
| Remote development box | API key |
| RPi5 workflow | API key |

OAuth is optional. MCP setup does not require OAuth.

---

## Add Mimir in Cursor (OAuth — local/browser setups)

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

If you use OAuth, `MIMIR_PUBLIC_URL` must be reachable from the machine running Cursor, not just from the server itself.

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

## Add Mimir in Cursor (API key — SSH/remote/headless)

Use this for Cursor over SSH, remote development, headless environments, and RPi5 workflows:

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

---

## Available Tools

| Tool | Description |
|------|-------------|
| `memory_remember` | Store an event or fact |
| `memory_recall` | Retrieve relevant memories for a query |
| `memory_search` | Semantic search across all memory layers |
| `memory_record_outcome` | Record the outcome of a task |
| `skill_list` | List available skills |
| `approval_request` | Create an approval request for an improvement |
| `approval_status` | List pending and recent approvals |
| `reflection_log` | Log observations and lessons |
| `improvement_propose` | Propose a system improvement |
| `project_bootstrap` | Seed Mimir with a repo's project capsule |

> **Note:** Legacy dotted names (`memory.remember`, etc.) are accepted as aliases for backward compatibility but are not advertised. Cursor requires `^[A-Za-z0-9_]+$` names.

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
      "name": "memory_remember",
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

- The endpoint accepts `Authorization: Bearer <key>` for both API keys and OAuth access tokens
- Also accepts `X-API-Key: <key>` for backward compatibility
- API-key Bearer auth is a first-class supported MCP path
- In dev mode (`MIMIR_ENV=development`), auth is bypassed automatically
- In prod mode (`MIMIR_AUTH_MODE=prod`), requests without a valid key return HTTP 401

---

## Troubleshooting

**OAuth browser window never completes** — Check that `MIMIR_PUBLIC_URL` is reachable from the machine running Cursor. For SSH/headless setups, use API-key Bearer auth instead.

**401 Unauthorized** — Key doesn't match a valid Mimir API key or OAuth token on the server. Check the credential on the machine running Cursor.

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
