"""CLI to create the first owner account.

Usage:
    python -m mimir.auth.create_owner --email user@example.com --display-name "User Name"

Creates a User with role=owner + an API key. Fails if an owner already exists.
Prints the raw API key (shown once — store it securely).
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sys
import uuid
import argparse
import os


async def _create_owner(email: str, display_name: str) -> None:
    # Set up data dir before importing anything that touches the DB
    os.environ.setdefault("MIMIR_DATA_DIR", "./data")
    os.environ.setdefault("MIMIR_VECTOR_DIR", "./data/vectors")

    from storage.database import init_db, get_session_factory
    from storage.models import User, APIKey
    from sqlalchemy import select

    await init_db()

    factory = get_session_factory()
    async with factory() as session:
        existing = await session.execute(select(User).where(User.role == "owner").limit(1))
        if existing.scalar_one_or_none():
            print("ERROR: An owner account already exists.", file=sys.stderr)
            print("       Use the web UI or API to manage additional users.", file=sys.stderr)
            sys.exit(1)

        # Create owner user
        user = User(
            id=uuid.uuid4().hex,
            email=email,
            display_name=display_name,
            role="owner",
        )
        session.add(user)

        # Create default API key
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = APIKey(
            id=uuid.uuid4().hex,
            user_id=user.id,
            key_hash=key_hash,
            name="default",
        )
        session.add(api_key)
        await session.commit()

    print(f"\n✓ Owner account created")
    print(f"  Email:        {email}")
    print(f"  Display name: {display_name}")
    print(f"  User ID:      {user.id}")
    print(f"\n  API Key: {raw_key}")
    print(f"\n  ⚠ Store this key securely — it will NOT be shown again.")
    print(f"\n  To use with Cursor, add to mcp.json:")
    print(f'    {{"mcpServers": {{"mimir": {{"url": "http://127.0.0.1:8787/mcp"}}}}}}')
    print(f"\n  Or use the OAuth flow (open browser to http://127.0.0.1:8787/oauth/authorize).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the Mimir owner account")
    parser.add_argument("--email", required=True, help="Owner email address")
    parser.add_argument("--display-name", required=True, help="Owner display name")
    args = parser.parse_args()

    asyncio.run(_create_owner(args.email, args.display_name))


if __name__ == "__main__":
    main()
