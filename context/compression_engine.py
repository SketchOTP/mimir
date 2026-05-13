"""Compress episodic memories into compact rolling summaries."""

from __future__ import annotations

from storage.models import Memory


def summarize_memories(memories: list[Memory], max_chars: int = 500) -> str:
    """Create a compact summary string from a list of memories."""
    if not memories:
        return ""

    lines = []
    for mem in memories:
        content = mem.content.strip()
        if len(content) > 200:
            content = content[:197] + "..."
        lines.append(f"- [{mem.layer}] {content}")

    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


def compress_session(session_memories: list[Memory]) -> str:
    """Produce a session-level summary suitable for injection into context."""
    episodic = [m for m in session_memories if m.layer == "episodic"]
    semantic = [m for m in session_memories if m.layer == "semantic"]
    procedural = [m for m in session_memories if m.layer == "procedural"]

    parts = []
    if semantic:
        parts.append("Key facts: " + " | ".join(m.content[:100] for m in semantic[:5]))
    if procedural:
        parts.append("Active rules: " + " | ".join(m.content[:100] for m in procedural[:3]))
    if episodic:
        parts.append("Recent events: " + " | ".join(m.content[:80] for m in episodic[:5]))

    return "\n".join(parts)
