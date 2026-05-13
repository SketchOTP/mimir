"""Detect adversarial or poisoned memory content before storage.

Seven trigger categories (per P5 directive):
  1. prompt_injection          — attempts to override agent instructions
  2. security_policy_overwrite — disabling safety/filtering rules
  3. approval_spoofing         — falsely claiming prior authorization
  4. tailscale_manipulation    — network infrastructure control instructions
  5. dangerous_procedure       — shell/command execution without approval
  6. credential_exposure       — storing secrets, API keys, passwords
  7. high_trust_contradiction  — reserved for future upstream callers

Rules intentionally avoid using forbidden operational command strings.
Pattern comments describe what human text they catch, not the commands themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QuarantineResult:
    quarantined: bool
    flags: list[str] = field(default_factory=list)
    reason: str | None = None


def apply_quarantine_overrides(
    *,
    verification_status: str,
    trust_score: float,
    confidence: float,
    result: QuarantineResult,
) -> tuple[str, float, float]:
    """Clamp trust fields for quarantined content."""
    if not result.quarantined:
        return verification_status, trust_score, confidence
    return "quarantined", min(trust_score, 0.2), min(confidence, 0.2)


def merge_quarantine_meta(meta: dict | None, result: QuarantineResult) -> dict | None:
    """Attach operator-visible quarantine details without mutating input."""
    if not result.quarantined:
        return meta
    return {
        **(meta or {}),
        "quarantine_reason": result.reason,
        "poisoning_flags": list(result.flags),
    }


# ─── Rule registry ────────────────────────────────────────────────────────────
# Each rule: (compiled_pattern, flag_name, human_readable_reason)

_RULES: list[tuple[re.Pattern[str], str, str]] = []


def _rule(pattern: str, flag: str, reason: str) -> None:
    _RULES.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), flag, reason))


# ── 1. Prompt injection ───────────────────────────────────────────────────────
_rule(
    r"ignore\s+(previous|prior|above|all)\s+(instructions?|rules?|context|prompt|directives?)",
    "prompt_injection",
    "Attempts to override prior instructions",
)
_rule(
    r"(disregard|forget|override|bypass).{0,40}(instructions?|rules?|constraints?|restrictions?|guidelines?|directives?)",
    "prompt_injection",
    "Attempts to bypass agent constraints",
)
_rule(
    r"you\s+(must|should|will|are\s+to)\s+(now\s+)?(ignore|forget|disregard|override)\s+(your|all|previous|these)",
    "prompt_injection",
    "Direct instruction override attempt",
)
_rule(
    r"pretend\s+(you\s+have\s+no|there\s+are\s+no|you\s+don.t\s+have)\s+(rule|restriction|instruction|filter|limit)",
    "prompt_injection",
    "Constraint nullification via role-play framing",
)
_rule(
    r"(act\s+as\s+if|behave\s+as\s+if)\s+(you\s+(have\s+no|are\s+not\s+bound|lack)\s+(restrict|rule|guideline|filter))",
    "prompt_injection",
    "Persona-based constraint bypass",
)

# ── 2. Security policy overwrite ──────────────────────────────────────────────
_rule(
    r"(disable|bypass|ignore|skip|remove|circumvent).{0,20}(safety|security|filter|guardrail|restriction|policy|check)",
    "security_policy_overwrite",
    "Attempts to disable a safety mechanism",
)
_rule(
    r"(no\s+more|without\s+any?)\s+(safety|security|filter|restriction|guardrail)",
    "security_policy_overwrite",
    "Instructs removal of safety constraints",
)
_rule(
    r"this\s+(rule|policy|restriction|constraint|filter)\s+(is|was|should\s+be)\s+(wrong|outdated|lifted|removed|disabled|overridden)",
    "security_policy_overwrite",
    "Policy invalidation instruction",
)

# ── 3. Approval spoofing ──────────────────────────────────────────────────────
_rule(
    r"(user|owner|admin|operator).{0,30}(already\s+approved|approved\s+this|gave\s+(permission|approval)|granted\s+(access|permission))",
    "approval_spoofing",
    "Falsely claims prior user authorization",
)
_rule(
    r"(no\s+need|don.t\s+need|doesn.t\s+need|not\s+required).{0,20}(approval|permission|confirmation|review|ask)",
    "approval_spoofing",
    "Instructs skipping approval step",
)
_rule(
    r"(skip|bypass|omit|avoid|remove)\s+(the\s+)?(approval|confirmation|review|authorization)\s+(step|process|check|gate|queue|flow)",
    "approval_spoofing",
    "Explicit approval gate bypass instruction",
)
_rule(
    r"(this\s+(change|action|update|modification)\s+)?(has\s+been|is\s+already|was)\s+(pre.?approved|auto.?approved|authorized\s+already)",
    "approval_spoofing",
    "False pre-approval claim",
)

# ── 4. Tailscale / network manipulation ──────────────────────────────────────
# Note: patterns here detect natural-language instructions about Tailscale
# network control, not the actual CLI commands (which are forbidden in source).
_rule(
    r"tailscale.{0,50}(restart|reconnect|enable|disable|anytime|freely|be\s+restarted|take\s+down|bring\s+up)",
    "tailscale_manipulation",
    "Tailscale network control instruction",
)
_rule(
    r"(restart|stop|start|bring\s+down|take\s+down|reconnect).{0,30}tailscale",
    "tailscale_manipulation",
    "Tailscale state manipulation instruction",
)
_rule(
    r"(change|modify|alter|update)\s+(the\s+)?(tailscale|vpn|tailnet)\s+(config|configuration|routing|acl|policy|hostname|route)",
    "tailscale_manipulation",
    "Tailscale configuration modification instruction",
)
_rule(
    r"tailscale.{0,50}(without\s+(approval|permission|asking)|automatically|whenever|freely)",
    "tailscale_manipulation",
    "Unauthorized Tailscale operation instruction",
)

# ── 5. Dangerous procedural commands ─────────────────────────────────────────
_rule(
    r"(always|automatically|unconditionally)\s+(run|execute|perform|invoke)\s+(shell|bash|command|script|subprocess)",
    "dangerous_procedure",
    "Instruction to execute shell commands automatically without oversight",
)
_rule(
    r"(run|execute|perform|invoke)\s+(commands?|scripts?|shell).{0,30}(without|with\s+no)\s+(approval|confirmation|review|asking|checking)",
    "dangerous_procedure",
    "Instruction to execute commands without approval",
)
_rule(
    r"(auto.?approve|self.?approve|skip\s+approval\s+for)\s+(all|any|every)\s+(command|action|change|operation|modification)",
    "dangerous_procedure",
    "Auto-approval bypass for all actions",
)
_rule(
    r"(rm\s+-rf|format\s+(c:|the\s+drive)|drop\s+(database|table)|delete\s+all\s+data).{0,30}(automatically|without|always)",
    "dangerous_procedure",
    "Destructive operation combined with automatic execution",
)

# ── 6. Credential / secret exposure ──────────────────────────────────────────
_rule(
    r"(store|save|remember|memorize|record|keep|note\s+down)\s+(my\s+)?(api[\s_-]?key|secret[\s_-]?key|password|token|credential|passphrase|private[\s_-]?key|access[\s_-]?key)",
    "credential_exposure",
    "Instruction to store a credential or secret in memory",
)
_rule(
    r"(api[\s_-]?key|secret[\s_-]?key|password|auth[\s_-]?token|bearer[\s_-]?token|access[\s_-]?token)\s*[:=]\s*[A-Za-z0-9_\-\.\/+]{8,}",
    "credential_exposure",
    "Credential value detected in memory content",
)
_rule(
    r"(my|the)\s+(password|passphrase|pin|secret)\s+(is|was|=)\s+\S{4,}",
    "credential_exposure",
    "Password/secret value detected in memory content",
)


# ─── Public API ───────────────────────────────────────────────────────────────

def check(content: str) -> QuarantineResult:
    """Check content for adversarial patterns.

    Returns QuarantineResult with quarantined=True and populated flags/reason
    if any trigger rule matches.  All rules are evaluated; all matching flags
    are collected.
    """
    if not content or not content.strip():
        return QuarantineResult(quarantined=False)

    matched_flags: list[str] = []
    first_reason: str | None = None

    for pattern, flag, reason in _RULES:
        if pattern.search(content):
            if flag not in matched_flags:
                matched_flags.append(flag)
            if first_reason is None:
                first_reason = reason

    if matched_flags:
        return QuarantineResult(
            quarantined=True,
            flags=matched_flags,
            reason=first_reason,
        )
    return QuarantineResult(quarantined=False)
