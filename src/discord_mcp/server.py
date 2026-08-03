from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .discord_service import DiscordService


def create_mcp(settings: Settings, discord_service: DiscordService) -> FastMCP:
    mcp = FastMCP(
        "Cloud Discord MCP",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    async def list_servers() -> list[dict[str, Any]]:
        """List allowlisted Discord servers the bot can access."""
        return [
            {"id": str(guild.id), "name": guild.name, "member_count": guild.member_count}
            for guild in discord_service.bot.guilds
            if guild.id in settings.allowed_guild_ids
        ]

    @mcp.tool()
    async def get_channels(server_id: str) -> list[dict[str, str]]:
        """List channels in an allowlisted server."""
        guild = discord_service.require_guild(int(server_id))
        return [
            {"id": str(channel.id), "name": channel.name, "type": str(channel.type)}
            for channel in guild.channels
            if not settings.allowed_channel_ids or channel.id in settings.allowed_channel_ids
        ]

    @mcp.tool()
    async def read_messages(channel_id: str, limit: int = 20) -> list[dict[str, str]]:
        """Read recent messages from an allowlisted text channel."""
        limit = max(1, min(limit, 100))
        channel = discord_service.require_channel(int(channel_id))
        if not hasattr(channel, "history"):
            raise ValueError("This channel does not support message history")
        messages = []
        async for message in channel.history(limit=limit):
            messages.append(
                {
                    "id": str(message.id),
                    "author": str(message.author),
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
            )
        return messages

    @mcp.tool()
    async def send_message(channel_id: str, content: str, confirm: bool = False) -> dict[str, str]:
        """Send a Discord message. Requires explicit confirm=true."""
        if not confirm:
            raise PermissionError("Sending messages requires confirm=true")
        if not content.strip():
            raise ValueError("Message content cannot be empty")
        if len(content) > 2000:
            raise ValueError("Discord messages are limited to 2000 characters")
        channel = discord_service.require_channel(int(channel_id))
        if not hasattr(channel, "send"):
            raise ValueError("This channel cannot receive messages")
        message = await channel.send(content.strip())
        logging.getLogger("discord-mcp.audit").info(
            "send_message channel_id=%s message_id=%s", channel_id, message.id
        )
        return {"status": "sent", "message_id": str(message.id), "channel_id": channel_id}

    return mcp

