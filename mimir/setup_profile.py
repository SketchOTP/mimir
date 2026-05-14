from __future__ import annotations

import json
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

from mimir.config import get_settings

ALLOWED_USE_CASES = (
    "local_browser",
    "lan_browser",
    "ssh_remote",
    "headless",
    "remote_dev",
    "rpi5",
    "hosted_https",
)
ALLOWED_AUTH_METHODS = ("api_key", "oauth", "device_code")
REMOTE_USE_CASES = {"ssh_remote", "headless", "remote_dev", "rpi5"}


def _profile_path() -> Path:
    settings = get_settings()
    settings.ensure_dirs()
    return settings.data_dir / "setup_profile.json"


def load_setup_profile() -> dict[str, Any]:
    path = _profile_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_setup_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_setup_profile(profile)
    _profile_path().write_text(json.dumps(normalized, indent=2, sort_keys=True))
    return normalized


def normalize_setup_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    raw = profile or {}
    use_case = str(raw.get("use_case") or "local_browser").strip()
    if use_case not in ALLOWED_USE_CASES:
        use_case = "local_browser"
    preferred_auth = str(raw.get("preferred_auth") or recommended_auth(use_case)).strip()
    if preferred_auth not in ALLOWED_AUTH_METHODS:
        preferred_auth = recommended_auth(use_case)
    return {
        "use_case": use_case,
        "preferred_auth": preferred_auth,
        "public_url": str(raw.get("public_url") or "").strip().rstrip("/"),
        "ssh_host": str(raw.get("ssh_host") or "").strip(),
        "remote_mimir_path": str(raw.get("remote_mimir_path") or "").strip(),
        "cursor_mcp_path": str(raw.get("cursor_mcp_path") or "").strip(),
        "remote_python_path": str(raw.get("remote_python_path") or "").strip(),
        "notes": str(raw.get("notes") or "").strip(),
    }


def effective_public_url(request_base: str) -> str:
    settings = get_settings()
    profile = load_setup_profile()
    if profile.get("public_url"):
        return str(profile["public_url"]).rstrip("/")
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return request_base.rstrip("/")


def recommended_auth(use_case: str) -> str:
    if use_case in REMOTE_USE_CASES:
        return "api_key"
    return "oauth"


def profile_warnings(profile: dict[str, Any], request_base: str = "") -> list[dict[str, str]]:
    normalized = normalize_setup_profile(profile)
    public_url = normalized.get("public_url") or request_base
    use_case = normalized["use_case"]
    preferred_auth = normalized["preferred_auth"]
    warnings: list[dict[str, str]] = []
    parsed = urlparse(public_url or "")
    host = (parsed.hostname or "").lower()
    is_localhost = host in {"127.0.0.1", "localhost", "::1"}

    if use_case in REMOTE_USE_CASES and public_url and is_localhost:
        warnings.append({
            "code": "public_url_localhost_remote",
            "severity": "warning",
            "message": "PUBLIC_URL points at localhost, which remote or SSH Cursor clients cannot reach.",
        })
    if use_case in REMOTE_USE_CASES and preferred_auth == "oauth":
        warnings.append({
            "code": "remote_oauth_without_device_code",
            "severity": "warning",
            "message": "OAuth is not recommended for SSH/headless setups because device-code flow is not implemented yet.",
        })
    if preferred_auth == "device_code":
        warnings.append({
            "code": "device_code_not_supported",
            "severity": "warning",
            "message": "Device-code auth is not supported yet. Use an API key today.",
        })
    if use_case == "hosted_https" and public_url and parsed.scheme == "http":
        warnings.append({
            "code": "hosted_https_without_tls",
            "severity": "info",
            "message": "Hosted HTTPS mode usually wants an https:// public URL.",
        })
    if use_case in REMOTE_USE_CASES and preferred_auth != "api_key":
        warnings.append({
            "code": "api_key_recommended_remote",
            "severity": "info",
            "message": "API-key auth is the recommended Cursor path for SSH and remote development setups.",
        })
    return warnings


def build_mcp_config(
    profile: dict[str, Any],
    api_key: str = "YOUR_API_KEY",
    *,
    request_base: str = "",
    use_case: str | None = None,
    auth_method: str | None = None,
) -> str:
    normalized = normalize_setup_profile(profile)
    selected_use_case = use_case or normalized["use_case"]
    public_url = normalized.get("public_url") or request_base or "http://127.0.0.1:8787"
    base = public_url.rstrip("/")
    if not base.endswith("/mcp"):
        base = f"{base}/mcp"
    auth = auth_method or normalized.get("preferred_auth") or recommended_auth(selected_use_case)
    if auth not in ALLOWED_AUTH_METHODS:
        auth = recommended_auth(selected_use_case)
    if auth in {"oauth", "device_code"}:
        return (
            '{\n'
            '  "mcpServers": {\n'
            '    "mimir": {\n'
            f'      "url": "{base}"\n'
            "    }\n"
            "  }\n"
            "}"
        )
    return (
        '{\n'
        '  "mcpServers": {\n'
        '    "mimir": {\n'
        f'      "url": "{base}",\n'
        '      "headers": {\n'
        f'        "Authorization": "Bearer {api_key}"\n'
        "      }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def build_config_variants(profile: dict[str, Any], request_base: str = "") -> dict[str, dict[str, str | bool]]:
    normalized = normalize_setup_profile(profile)
    variants = [
        ("cursor_local", "Cursor Local", "local_browser", "oauth"),
        ("cursor_ssh", "Cursor Over SSH", "ssh_remote", "api_key"),
        ("lan_server", "LAN Server", "lan_browser", normalized["preferred_auth"]),
        ("hosted_https", "Hosted HTTPS", "hosted_https", normalized["preferred_auth"]),
    ]
    result: dict[str, dict[str, str | bool]] = {}
    for key, label, use_case, auth_method in variants:
        effective_auth = auth_method if auth_method in ALLOWED_AUTH_METHODS else recommended_auth(use_case)
        result[key] = {
            "label": label,
            "use_case": use_case,
            "auth_method": effective_auth,
            "recommended": effective_auth == recommended_auth(use_case),
            "json": build_mcp_config(
                normalized,
                request_base=request_base,
                use_case=use_case,
                auth_method=effective_auth,
            ),
        }
    return result
