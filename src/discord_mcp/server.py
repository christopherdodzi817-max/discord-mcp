from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .community import SELF_ASSIGNABLE_ROLES
from .discord_service import DiscordService
from .moderation import ActivityTracker, find_invite_signal, find_term_signals


def create_mcp(settings: Settings, discord_service: DiscordService) -> FastMCP:
    mcp = FastMCP(
        "Cloud Discord MCP",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "discord-mcp-ph17.onrender.com",
                "discord-mcp-ph17.onrender.com:*",
            ]
        ),
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
    async def get_server_stats(server_id: str) -> dict[str, Any]:
        """Read cached membership and channel statistics for an allowlisted server."""
        guild = discord_service.require_guild(int(server_id))
        members = guild.members
        return {
            "server_id": str(guild.id),
            "total_members": guild.member_count,
            "cached_humans": sum(not member.bot for member in members),
            "cached_bots": sum(member.bot for member in members),
            "exact": guild.chunked is True,
            "text_channels": len(guild.text_channels),
            "voice_channels": len(guild.voice_channels),
            "forum_channels": len(guild.forums),
            "categories": len(guild.categories),
            "roles": len(guild.roles),
            "boost_count": guild.premium_subscription_count,
            "bot_ready": discord_service.bot.is_ready(),
        }

    @mcp.tool()
    async def list_moderation_flags(server_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Read HeadMod incident embeds from the configured staff log."""
        guild = discord_service.require_guild(int(server_id))
        channel = discord_service.require_channel(settings.moderation_log_channel_id)
        if channel.guild.id != guild.id:
            raise PermissionError("Moderation log belongs to another server")
        if not hasattr(channel, "history"):
            raise ValueError("Moderation log does not support message history")
        flags = []
        bounded_limit = max(1, min(limit, 50))
        bot_user = discord_service.bot.user
        async for message in channel.history(limit=bounded_limit):
            if bot_user is None or message.author.id != bot_user.id:
                continue
            for embed in message.embeds:
                if embed.footer.text != "HeadMod incident v1 - Staff review required":
                    continue
                fields = {field.name: field.value for field in embed.fields}
                flags.append({
                    "message_id": str(message.id),
                    "created_at": message.created_at.isoformat(),
                    "title": embed.title,
                    "description": embed.description,
                    "message_url": fields.get("Message"),
                    "evidence_excerpt": fields.get("Evidence excerpt"),
                    "author_id": fields.get("Author ID"),
                })
                if len(flags) >= bounded_limit:
                    return flags
        return flags

    @mcp.tool()
    async def audit_recent_messages(channel_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Read recent messages and return deterministic moderation candidates."""
        channel = discord_service.require_channel(int(channel_id))
        if not hasattr(channel, "history"):
            raise ValueError("This channel does not support message history")
        messages = [message async for message in channel.history(limit=max(1, min(limit, 50)))]
        tracker = ActivityTracker(
            settings.spam_window_seconds, settings.spam_message_threshold, 3, settings.mention_threshold
        )
        findings = []
        for message in sorted(messages, key=lambda item: (item.created_at, item.id)):
            if message.author.bot or not (message.content or "").strip():
                continue
            permissions = message.author.guild_permissions
            if permissions.administrator or permissions.manage_messages or any(
                role.name in {"Moderator", "Developer", "Admin"} for role in message.author.roles
            ):
                continue
            signals = find_term_signals(message.content, settings.terms_by_rule or {}, settings.safe_phrases)
            invite = find_invite_signal(message.content, channel.name)
            if invite is not None:
                signals.append(invite)
            signals.extend(tracker.inspect(
                message.author.id, message.content, len(message.mentions), message.mention_everyone,
                message.created_at.timestamp(),
            ))
            for signal in signals:
                findings.append({
                    "message_id": str(message.id),
                    "author_id": str(message.author.id),
                    "created_at": message.created_at.isoformat(),
                    "message_url": message.jump_url,
                    "rule_id": signal.rule_id,
                    "label": signal.label,
                    "reason": signal.reason,
                })
        return findings

    @mcp.tool()
    async def audit_server(server_id: str) -> list[dict[str, Any]]:
        """Report permission concerns in an allowlisted server without changing them."""
        guild = discord_service.require_guild(int(server_id))
        findings = []
        for role in guild.roles:
            permissions = role.permissions
            for permission, severity in (("administrator", "critical"), ("manage_roles", "high")):
                if getattr(permissions, permission, False):
                    findings.append({
                        "severity": severity,
                        "evidence": f"Role {role.name} ({role.id}) has {permission} permission.",
                    })
            if guild.me is not None and role.name in {item[0] for item in SELF_ASSIGNABLE_ROLES}:
                if role.position >= guild.me.top_role.position:
                    findings.append({
                        "severity": "medium",
                        "evidence": f"Bot role must be above self-assignable role {role.name} ({role.id}).",
                    })
        for channel in guild.channels:
            public = channel.permissions_for(guild.default_role)
            for permission in ("manage_channels", "manage_messages"):
                if getattr(public, permission, False):
                    findings.append({
                        "severity": "high",
                        "evidence": f"Channel {channel.name} ({channel.id}) grants @everyone {permission}.",
                    })
        log_id = settings.moderation_log_channel_id
        if settings.allowed_channel_ids and log_id not in settings.allowed_channel_ids:
            findings.append({"severity": "high", "evidence": f"Moderation log channel {log_id} is outside ALLOWED_CHANNEL_IDS."})
        log_channel = discord_service.bot.get_channel(log_id)
        if log_channel is None:
            findings.append({"severity": "high", "evidence": f"Moderation log channel {log_id} is unavailable to the bot."})
        elif getattr(getattr(log_channel, "guild", None), "id", None) != guild.id:
            findings.append({"severity": "high", "evidence": f"Moderation log channel {log_id} belongs to another server."})
        elif not hasattr(log_channel, "history") or not hasattr(log_channel, "send"):
            findings.append({"severity": "high", "evidence": f"Moderation log channel {log_id} is unavailable for incident history or posting."})
        else:
            if log_channel.permissions_for(guild.default_role).view_channel:
                findings.append({
                    "severity": "high",
                    "evidence": f"Moderation log channel {log_channel.name} ({log_id}) is visible to @everyone.",
                })
            if guild.me is not None:
                log_permissions = log_channel.permissions_for(guild.me)
                for permission in ("view_channel", "read_message_history", "send_messages", "embed_links"):
                    if not getattr(log_permissions, permission, False):
                        findings.append({
                            "severity": "medium",
                            "evidence": f"Bot lacks {permission} in moderation log channel {log_id}.",
                        })
        if guild.me is not None:
            bot_permissions = guild.me.guild_permissions
            for permission in ("view_channel", "read_message_history", "send_messages"):
                if not getattr(bot_permissions, permission, False):
                    findings.append({
                        "severity": "medium",
                        "evidence": f"Bot lacks {permission} permission in server {guild.id}.",
                    })
        return findings

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
