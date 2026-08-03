from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _csv_ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                values.add(int(item))
            except ValueError as exc:
                raise ValueError(f"{name} contains a non-numeric Discord ID") from exc
    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    discord_token: str
    mcp_auth_token: str
    allowed_guild_ids: frozenset[int]
    allowed_channel_ids: frozenset[int]
    host: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        discord_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        mcp_auth_token = os.getenv("MCP_AUTH_TOKEN", "").strip()
        guild_ids = _csv_ids("ALLOWED_GUILD_IDS")
        if not discord_token:
            raise ValueError("DISCORD_BOT_TOKEN is required")
        if not mcp_auth_token:
            raise ValueError("MCP_AUTH_TOKEN is required")
        if not guild_ids:
            raise ValueError("ALLOWED_GUILD_IDS must contain at least one server ID")
        return cls(
            discord_token=discord_token,
            mcp_auth_token=mcp_auth_token,
            allowed_guild_ids=guild_ids,
            allowed_channel_ids=_csv_ids("ALLOWED_CHANNEL_IDS"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

