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


def _optional_id(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a numeric Discord ID") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive Discord ID")
    return value


def _positive_number(name: str, default: str, parser):
    try:
        value = parser(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _csv_text(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.getenv(name, "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    discord_token: str
    mcp_auth_token: str
    allowed_guild_ids: frozenset[int]
    allowed_channel_ids: frozenset[int]
    host: str
    port: int
    log_level: str
    member_events: bool = False
    moderation_log_channel_id: int = 1533923840726663421
    rules_channel_id: int = 1529680640872677491
    escalation_admin_id: int = 1388189183633526946
    terms_by_rule: dict[str, tuple[str, ...]] | None = None
    safe_phrases: tuple[str, ...] = ()
    spam_window_seconds: float = 10
    spam_message_threshold: int = 5
    mention_threshold: int = 8

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
            member_events=os.getenv("ENABLE_MEMBER_EVENTS", "true").strip().lower() in {"1", "true", "yes", "on"},
            moderation_log_channel_id=_optional_id("MODERATION_LOG_CHANNEL_ID", 1533923840726663421),
            rules_channel_id=_optional_id("RULES_CHANNEL_ID", 1529680640872677491),
            escalation_admin_id=_optional_id("ESCALATION_ADMIN_ID", 1388189183633526946),
            terms_by_rule={rule: _csv_text(f"MODERATION_TERMS_RULE_{rule}") for rule in ("1", "2", "4")},
            safe_phrases=_csv_text("MODERATION_SAFE_PHRASES"),
            spam_window_seconds=_positive_number("SPAM_WINDOW_SECONDS", "10", float),
            spam_message_threshold=_positive_number("SPAM_MESSAGE_THRESHOLD", "5", int),
            mention_threshold=_positive_number("MENTION_THRESHOLD", "8", int),
        )

