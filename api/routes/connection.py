from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from mimir.config import get_settings
from mimir.setup_profile import (
    ALLOWED_AUTH_METHODS,
    ALLOWED_USE_CASES,
    build_mcp_config,
    build_config_variants,
    effective_public_url,
    load_setup_profile,
    normalize_setup_profile,
    profile_warnings,
    recommended_auth,
    save_setup_profile,
)
from storage.database import get_session
from storage.models import User

router = APIRouter(tags=["connection"])


class ConnectionProfileIn(BaseModel):
    use_case: Literal[
        "local_browser",
        "lan_browser",
        "ssh_remote",
        "headless",
        "remote_dev",
        "rpi5",
        "hosted_https",
    ] = "local_browser"
    preferred_auth: Literal["api_key", "oauth", "device_code"] = "oauth"
    public_url: str = ""
    ssh_host: str = ""
    remote_mimir_path: str = ""
    cursor_mcp_path: str = ""
    remote_python_path: str = ""
    notes: str = ""


def _request_base(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _settings_payload(request: Request) -> dict:
    settings = get_settings()
    request_base = _request_base(request)
    profile = normalize_setup_profile(load_setup_profile())
    return {
        "auth_mode": settings._effective_auth_mode,
        "oauth_enabled": settings.oauth_enabled,
        "device_code_supported": False,
        "allowed_use_cases": list(ALLOWED_USE_CASES),
        "allowed_auth_methods": list(ALLOWED_AUTH_METHODS),
        "profile": profile,
        "recommended_auth": {
            "current": profile["preferred_auth"],
            "recommended": "api_key" if profile["use_case"] in {"ssh_remote", "headless", "remote_dev", "rpi5"} else "oauth",
        },
        "warnings": profile_warnings(profile, request_base=request_base),
        "generated_configs": build_config_variants(profile, request_base=effective_public_url(request_base)),
        "public_url_sources": {
            "request_base": request_base,
            "env_public_url": settings.public_url.rstrip("/") if settings.public_url else "",
            "saved_public_url": profile.get("public_url", ""),
        },
    }


async def _owner_exists(session: AsyncSession) -> bool:
    result = await session.execute(select(User.id).where(User.role == "owner").limit(1))
    return result.scalar_one_or_none() is not None


@router.get("/api/connection/onboarding")
async def get_connection_onboarding(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    request_base = _request_base(request)
    profile = normalize_setup_profile(load_setup_profile())
    public_base = effective_public_url(request_base)
    settings = get_settings()
    return {
        "auth_mode": settings._effective_auth_mode,
        "oauth_enabled": settings.oauth_enabled,
        "owner_exists": await _owner_exists(session),
        "profile": profile,
        "recommended_auth": recommended_auth(profile["use_case"]),
        "warnings": profile_warnings(profile, request_base=request_base),
        "urls": {
            "dashboard": f"{public_base}/",
            "connection_settings": f"{public_base}/settings/connection",
            "first_run_setup": f"{public_base}/setup",
            "oauth_authorize": f"{public_base}/oauth/authorize",
            "mcp_url": f"{public_base}/mcp",
        },
        "generated": {
            "oauth_local": build_mcp_config(
                profile,
                request_base=public_base,
                use_case="local_browser",
                auth_method="oauth",
            ),
            "api_key_remote": build_mcp_config(
                profile,
                request_base=public_base,
                use_case="ssh_remote",
                auth_method="api_key",
            ),
        },
    }


@router.get("/api/connection/settings")
async def get_connection_settings(
    request: Request,
    current_user: UserContext = Depends(get_current_user),
):
    _ = current_user
    return _settings_payload(request)


@router.put("/api/connection/settings")
async def update_connection_settings(
    body: ConnectionProfileIn,
    request: Request,
    current_user: UserContext = Depends(get_current_user),
):
    _ = current_user
    save_setup_profile(body.model_dump())
    return _settings_payload(request)


def _connection_page_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mimir Connection Settings</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #07111f;
    --panel: #0f1a2b;
    --panel-soft: #122036;
    --line: #23334e;
    --text: #e5eefb;
    --muted: #91a3c0;
    --accent: #f97316;
    --accent-soft: rgba(249, 115, 22, 0.15);
    --warn: #f59e0b;
    --ok: #10b981;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background:
    radial-gradient(circle at top left, rgba(249,115,22,0.16), transparent 26%),
    linear-gradient(180deg, #07111f, #091524 55%, #07111f);
    color: var(--text); }
  .shell { max-width: 1200px; margin: 0 auto; padding: 32px 20px 56px; }
  .hero, .panel { background: rgba(15,26,43,0.92); border: 1px solid var(--line); border-radius: 18px; }
  .hero { padding: 24px; margin-bottom: 20px; }
  h1, h2, h3 { margin: 0; }
  h1 { font-size: 2rem; }
  p { color: var(--muted); line-height: 1.45; }
  .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; }
  .stack { display: grid; gap: 20px; }
  .panel { padding: 20px; }
  .meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
  .chip { border: 1px solid var(--line); background: var(--panel-soft); color: var(--text); border-radius: 999px; padding: 6px 10px; font-size: 12px; }
  .chip.reco { border-color: rgba(16,185,129,0.4); color: #b7f7dc; }
  .callout { border-left: 3px solid var(--accent); background: var(--accent-soft); padding: 12px 14px; border-radius: 12px; color: #fbd5bf; font-size: 14px; }
  .warn { border-left-color: var(--warn); }
  label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 8px; }
  input, select, textarea, button {
    width: 100%; border-radius: 12px; border: 1px solid var(--line); background: #091321; color: var(--text);
    padding: 12px 14px; font: inherit;
  }
  textarea { min-height: 110px; resize: vertical; }
  button { background: linear-gradient(135deg, #ea580c, #f97316); border: 0; cursor: pointer; font-weight: 600; }
  button.secondary { background: #13253d; border: 1px solid var(--line); }
  button.ghost { background: transparent; border: 1px solid rgba(239,68,68,0.35); color: #fecaca; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .row3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .actions { display: flex; gap: 12px; }
  .actions > * { flex: 1; }
  .hidden { display: none; }
  pre { margin: 0; padding: 14px; overflow: auto; border-radius: 14px; background: #08101b; border: 1px solid #18263c; font-size: 12px; }
  .config-grid { display: grid; gap: 14px; }
  .config-card { border: 1px solid #1e314f; border-radius: 16px; overflow: hidden; background: #0b1524; }
  .config-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; border-bottom: 1px solid #18263c; }
  .small { font-size: 12px; color: var(--muted); }
  .keys { display: grid; gap: 10px; }
  .key-item { border: 1px solid #18263c; border-radius: 14px; padding: 12px; background: #0a1320; }
  .key-top { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
  .status { font-size: 13px; color: #bfdbfe; min-height: 18px; }
  .status.error { color: #fecaca; }
  .status.ok { color: #bbf7d0; }
  @media (max-width: 960px) {
    .grid, .row2, .row3 { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>Connection Setup</h1>
      <p>Pick the way you use Mimir, save the matching connection details, and copy the generated Cursor MCP config without guessing.</p>
      <div class="meta">
        <span class="chip" id="auth-mode-chip">Mode: loading</span>
        <span class="chip reco">API key is recommended for SSH and remote Cursor</span>
        <a class="chip" href="/setup" style="text-decoration:none;color:inherit;">First-run setup</a>
      </div>
    </section>

    <div class="grid">
      <div class="stack">
        <section class="panel">
          <h2>Authenticate</h2>
          <p>Paste an active Mimir API key to load and edit settings. Existing keys are never shown again.</p>
          <div class="row2">
            <div>
              <label for="api-key-input">API key</label>
              <input id="api-key-input" type="password" placeholder="paste active API key">
            </div>
            <div>
              <label for="key-name-input">New API key name</label>
              <input id="key-name-input" type="text" value="cursor-connection">
            </div>
          </div>
          <div class="actions" style="margin-top:14px;">
            <button id="load-button" type="button">Load Settings</button>
            <button id="create-key-button" class="secondary" type="button">Create New API Key</button>
          </div>
          <p id="auth-status" class="status"></p>
          <div id="new-key-panel" class="callout hidden" style="margin-top:14px;">
            <strong>New API key</strong>
            <p id="new-key-note">Store this now. It will not be shown again.</p>
            <pre id="new-key-value"></pre>
          </div>
        </section>

        <section class="panel">
          <h2>Connection Profile</h2>
          <p>These settings feed Mimir’s browser connect hints and MCP config generation.</p>
          <div class="row3">
            <div>
              <label for="use-case">Connection type</label>
              <select id="use-case"></select>
            </div>
            <div>
              <label for="preferred-auth">Preferred auth</label>
              <select id="preferred-auth">
                <option value="api_key">API key</option>
                <option value="oauth">OAuth</option>
                <option value="device_code">device-code later</option>
              </select>
            </div>
            <div>
              <label for="auth-mode-display">Auth mode</label>
              <input id="auth-mode-display" type="text" readonly>
            </div>
          </div>
          <div class="row2" style="margin-top:14px;">
            <div>
              <label for="public-url">MIMIR_PUBLIC_URL</label>
              <input id="public-url" type="text" placeholder="http://127.0.0.1:8787">
            </div>
            <div>
              <label for="ssh-host">SSH host alias</label>
              <input id="ssh-host" type="text" placeholder="atlas">
            </div>
          </div>
          <div class="row2" style="margin-top:14px;">
            <div>
              <label for="remote-mimir-path">Remote Mimir path</label>
              <input id="remote-mimir-path" type="text" placeholder="/home/sketch/Projects/mimir">
            </div>
            <div>
              <label for="cursor-mcp-path">Cursor MCP config path</label>
              <input id="cursor-mcp-path" type="text" placeholder="~/.cursor/mcp.json">
            </div>
          </div>
          <div class="row2" style="margin-top:14px;">
            <div>
              <label for="remote-python-path">Remote Python path</label>
              <input id="remote-python-path" type="text" placeholder="/home/sketch/Projects/mimir/.venv/bin/python">
            </div>
            <div>
              <label for="notes">Notes</label>
              <textarea id="notes" placeholder="Anything specific about this machine, SSH hop, or hosted setup."></textarea>
            </div>
          </div>
          <div class="actions" style="margin-top:14px;">
            <button id="save-button" type="button">Save Connection Profile</button>
          </div>
          <p id="save-status" class="status"></p>
          <div id="warnings"></div>
        </section>
      </div>

      <div class="stack">
        <section class="panel">
          <h2>Generated MCP Config</h2>
          <p>Each block is generated from the saved profile and the selected use case.</p>
          <div id="configs" class="config-grid"></div>
        </section>
        <section class="panel">
          <h2>Existing API Keys</h2>
          <p>Raw key values are never shown here again. Revoke keys you no longer use.</p>
          <div id="key-list" class="keys"></div>
        </section>
      </div>
    </div>
  </div>
<script>
const state = { apiKey: "", settings: null };

const els = {
  authModeChip: document.getElementById("auth-mode-chip"),
  apiKeyInput: document.getElementById("api-key-input"),
  keyNameInput: document.getElementById("key-name-input"),
  loadButton: document.getElementById("load-button"),
  createKeyButton: document.getElementById("create-key-button"),
  authStatus: document.getElementById("auth-status"),
  newKeyPanel: document.getElementById("new-key-panel"),
  newKeyValue: document.getElementById("new-key-value"),
  useCase: document.getElementById("use-case"),
  preferredAuth: document.getElementById("preferred-auth"),
  authModeDisplay: document.getElementById("auth-mode-display"),
  publicUrl: document.getElementById("public-url"),
  sshHost: document.getElementById("ssh-host"),
  remoteMimirPath: document.getElementById("remote-mimir-path"),
  cursorMcpPath: document.getElementById("cursor-mcp-path"),
  remotePythonPath: document.getElementById("remote-python-path"),
  notes: document.getElementById("notes"),
  saveButton: document.getElementById("save-button"),
  saveStatus: document.getElementById("save-status"),
  warnings: document.getElementById("warnings"),
  configs: document.getElementById("configs"),
  keyList: document.getElementById("key-list"),
};

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
  }[char]));
}

function setStatus(el, message, kind) {
  el.textContent = message || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  return headers;
}

function renderWarnings(warnings) {
  els.warnings.innerHTML = "";
  if (!warnings || warnings.length === 0) return;
  warnings.forEach((warning) => {
    const div = document.createElement("div");
    div.className = "callout warn";
    div.style.marginTop = "12px";
    div.innerHTML = "<strong>" + escapeHtml(warning.code) + "</strong><p>" + escapeHtml(warning.message) + "</p>";
    els.warnings.appendChild(div);
  });
}

function renderConfigs(configs) {
  const entries = Object.entries(configs || {});
  els.configs.innerHTML = entries.map(([key, cfg]) => `
    <div class="config-card">
      <div class="config-head">
        <div>
          <h3>${escapeHtml(cfg.label)}</h3>
          <div class="small">${escapeHtml(cfg.use_case)} · ${escapeHtml(cfg.auth_method)}</div>
        </div>
        <span class="chip ${cfg.recommended ? "reco" : ""}">${cfg.recommended ? "Recommended" : "Alternate"}</span>
      </div>
      <pre>${escapeHtml(cfg.json)}</pre>
    </div>
  `).join("");
}

async function loadKeys() {
  const response = await fetch("/api/auth/keys", { headers: state.apiKey ? { "X-API-Key": state.apiKey } : {} });
  if (!response.ok) throw new Error("Unable to load API keys");
  const data = await response.json();
  const keys = data.keys || [];
  els.keyList.innerHTML = keys.map((key) => `
    <div class="key-item">
      <div class="key-top">
        <div>
          <strong>${escapeHtml(key.name || "unnamed")}</strong>
          <div class="small">Created ${escapeHtml(key.created_at || "unknown")}</div>
          <div class="small">Last used ${escapeHtml(key.last_used_at || "never")}</div>
        </div>
        <button class="ghost" type="button" data-key-id="${escapeHtml(key.id)}">Revoke</button>
      </div>
    </div>
  `).join("") || "<div class='small'>No active API keys found.</div>";
  Array.from(els.keyList.querySelectorAll("button[data-key-id]")).forEach((button) => {
    button.addEventListener("click", async () => {
      const keyId = button.getAttribute("data-key-id");
      const response = await fetch("/api/auth/keys/" + keyId, {
        method: "DELETE",
        headers: state.apiKey ? { "X-API-Key": state.apiKey } : {},
      });
      if (!response.ok) {
        setStatus(els.saveStatus, "Could not revoke API key.", "error");
        return;
      }
      setStatus(els.saveStatus, "API key revoked.", "ok");
      await loadKeys();
    });
  });
}

function fillProfile(settings) {
  state.settings = settings;
  els.authModeChip.textContent = "Mode: " + settings.auth_mode;
  els.authModeDisplay.value = settings.auth_mode;
  els.useCase.innerHTML = (settings.allowed_use_cases || []).map((value) => `
    <option value="${escapeHtml(value)}">${escapeHtml(value)}</option>
  `).join("");
  const profile = settings.profile || {};
  els.useCase.value = profile.use_case || "local_browser";
  els.preferredAuth.value = profile.preferred_auth || "oauth";
  els.publicUrl.value = profile.public_url || "";
  els.sshHost.value = profile.ssh_host || "";
  els.remoteMimirPath.value = profile.remote_mimir_path || "";
  els.cursorMcpPath.value = profile.cursor_mcp_path || "";
  els.remotePythonPath.value = profile.remote_python_path || "";
  els.notes.value = profile.notes || "";
  renderWarnings(settings.warnings || []);
  renderConfigs(settings.generated_configs || {});
}

async function loadSettings() {
  state.apiKey = els.apiKeyInput.value.trim();
  setStatus(els.authStatus, "Loading settings…");
  try {
    const response = await fetch("/api/connection/settings", { headers: state.apiKey ? { "X-API-Key": state.apiKey } : {} });
    if (!response.ok) throw new Error("auth");
    const data = await response.json();
    fillProfile(data);
    await loadKeys();
    setStatus(els.authStatus, "Connection settings loaded.", "ok");
  } catch {
    setStatus(els.authStatus, "Could not load settings. Check the API key.", "error");
  }
}

async function saveSettings() {
  if (!state.apiKey) {
    setStatus(els.saveStatus, "Load settings with an API key first.", "error");
    return;
  }
  const payload = {
    use_case: els.useCase.value,
    preferred_auth: els.preferredAuth.value,
    public_url: els.publicUrl.value.trim(),
    ssh_host: els.sshHost.value.trim(),
    remote_mimir_path: els.remoteMimirPath.value.trim(),
    cursor_mcp_path: els.cursorMcpPath.value.trim(),
    remote_python_path: els.remotePythonPath.value.trim(),
    notes: els.notes.value.trim(),
  };
  setStatus(els.saveStatus, "Saving profile…");
  const response = await fetch("/api/connection/settings", {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    setStatus(els.saveStatus, "Could not save connection profile.", "error");
    return;
  }
  const data = await response.json();
  fillProfile(data);
  setStatus(els.saveStatus, "Connection profile saved.", "ok");
}

async function createKey() {
  if (!state.apiKey && !els.apiKeyInput.value.trim()) {
    setStatus(els.authStatus, "Paste an active API key first.", "error");
    return;
  }
  state.apiKey = els.apiKeyInput.value.trim() || state.apiKey;
  const name = encodeURIComponent(els.keyNameInput.value.trim() || "cursor-connection");
  const response = await fetch("/api/auth/keys?name=" + name, {
    method: "POST",
    headers: state.apiKey ? { "X-API-Key": state.apiKey } : {},
  });
  if (!response.ok) {
    setStatus(els.authStatus, "Could not create API key.", "error");
    return;
  }
  const data = await response.json();
  els.newKeyPanel.classList.remove("hidden");
  els.newKeyValue.textContent = data.api_key || "";
  setStatus(els.authStatus, "New API key created. It is shown once below.", "ok");
  await loadKeys();
}

els.loadButton.addEventListener("click", loadSettings);
els.saveButton.addEventListener("click", saveSettings);
els.createKeyButton.addEventListener("click", createKey);
window.addEventListener("load", async () => {
  try {
    const response = await fetch("/api/connection/settings");
    if (!response.ok) return;
    const data = await response.json();
    fillProfile(data);
    await loadKeys();
    setStatus(els.authStatus, "Connection settings loaded.", "ok");
  } catch {
  }
});
</script>
</body>
</html>"""


@router.get("/settings/connection", response_class=HTMLResponse)
async def connection_settings_page() -> HTMLResponse:
    return HTMLResponse(_connection_page_html())


@router.get("/admin/connection", response_class=HTMLResponse)
async def admin_connection_page() -> HTMLResponse:
    return HTMLResponse(_connection_page_html())
