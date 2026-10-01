from __future__ import annotations

import logging
from typing import Any

import discord


def register_management_tools(mcp, settings, service) -> None:
    audit = logging.getLogger('discord-mcp.audit')

    def confirmed(confirm):
        if not confirm:
            raise PermissionError('Server editing requires confirm=true')

    def valid_name(name):
        if not name.strip() or len(name) > 100:
            raise ValueError('Name must contain 1 to 100 characters')
        return name.strip()

    def valid_color(color):
        if not 0 <= color <= 0xFFFFFF:
            raise ValueError('Color must be an RGB integer from 0 to 16777215')
        return discord.Color(color)

    def channel_data(channel):
        return {'id': str(channel.id), 'name': channel.name, 'position': channel.position,
                'category_id': str(channel.category_id) if channel.category_id else None}

    def role_data(role):
        return {'id': str(role.id), 'name': role.name, 'position': role.position,
                'color': role.color.value, 'hoist': role.hoist, 'permissions': str(role.permissions.value)}

    def parent(category_id, guild):
        category = service.require_channel(int(category_id))
        if category.guild.id != guild.id:
            raise ValueError('Destination category must be in the same server')
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError('Destination must be a category')
        return category

    @mcp.tool()
    async def edit_channel(channel_id: str, name: str | None = None, topic: str | None = None,
                           position: int | None = None, category_id: str | None = None,
                           confirm: bool = False) -> dict[str, Any]:
        """Rename, describe, reorder, or move an allowlisted channel. Preserves permissions. Requires confirm=true."""
        confirmed(confirm)
        channel = service.require_channel(int(channel_id))
        if not isinstance(channel, discord.abc.GuildChannel):
            raise ValueError('Only server channels and categories can be edited')
        changes = {}
        if name is not None:
            changes['name'] = valid_name(name)
        if topic is not None:
            if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                raise ValueError('Topics require a text or forum channel')
            if len(topic) > (4096 if isinstance(channel, discord.ForumChannel) else 1024):
                raise ValueError('Topic exceeds the channel limit')
            changes['topic'] = topic
        if position is not None:
            if position < 0:
                raise ValueError('Position must be nonnegative')
            changes['position'] = position
        if category_id is not None:
            if isinstance(channel, discord.CategoryChannel):
                raise ValueError('Categories cannot be nested')
            changes['category'] = parent(category_id, channel.guild) if category_id else None
            changes['sync_permissions'] = False
        if not changes:
            raise ValueError('Provide at least one channel change')
        updated = await channel.edit(**changes, reason='Authorized MCP server organization')
        audit.info('edit_channel channel_id=%s fields=%s', channel_id, sorted(changes))
        return channel_data(updated or channel)

    @mcp.tool()
    async def create_channel(server_id: str, name: str, channel_type: str = 'text',
                             category_id: str | None = None, topic: str | None = None,
                             confirm: bool = False) -> dict[str, Any]:
        """Create a text, voice, forum channel or category in an allowlisted server. Requires confirm=true."""
        confirmed(confirm)
        guild = service.require_guild(int(server_id))
        if settings.allowed_channel_ids:
            raise PermissionError('Channel creation is disabled when ALLOWED_CHANNEL_IDS is restricted')
        methods = {'text': guild.create_text_channel, 'voice': guild.create_voice_channel,
                   'forum': guild.create_forum, 'category': guild.create_category}
        if channel_type not in methods:
            raise ValueError('channel_type must be text, voice, forum, or category')
        kwargs = {'reason': 'Authorized MCP channel creation'}
        if category_id:
            if channel_type == 'category':
                raise ValueError('Categories cannot be nested')
            kwargs['category'] = parent(category_id, guild)
        if topic is not None:
            if channel_type not in {'text', 'forum'}:
                raise ValueError('Topics require a text or forum channel')
            if len(topic) > (4096 if channel_type == 'forum' else 1024):
                raise ValueError('Topic exceeds the channel limit')
            kwargs['topic'] = topic
        channel = await methods[channel_type](valid_name(name), **kwargs)
        audit.info('create_channel server_id=%s channel_id=%s', server_id, channel.id)
        return channel_data(channel)

    @mcp.tool()
    async def get_roles(server_id: str) -> list[dict[str, Any]]:
        """List server roles, colors, grouping and permissions."""
        guild = service.require_guild(int(server_id))
        return [role_data(role) for role in guild.roles]

    @mcp.tool()
    async def create_role(server_id: str, name: str, color: int = 0,
                          hoist: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Create a role label with no permissions. Does not assign it to members. Requires confirm=true."""
        confirmed(confirm)
        guild = service.require_guild(int(server_id))
        role = await guild.create_role(name=valid_name(name), color=valid_color(color), hoist=hoist,
                                       permissions=discord.Permissions.none(), mentionable=False,
                                       reason='Authorized MCP role label creation')
        audit.info('create_role server_id=%s role_id=%s', server_id, role.id)
        return role_data(role)

    @mcp.tool()
    async def style_role(server_id: str, role_id: str, color: int | None = None,
                         hoist: bool | None = None, position: int | None = None,
                         confirm: bool = False) -> dict[str, Any]:
        """Set a role's color, member-list grouping or order without changing its name or permissions. Requires confirm=true."""
        confirmed(confirm)
        guild = service.require_guild(int(server_id))
        role = guild.get_role(int(role_id))
        if role is None:
            raise LookupError('Role not found')
        if role.managed or role.id == guild.id:
            raise PermissionError('Cannot style managed roles or @everyone')
        if guild.me is None or role >= guild.me.top_role:
            raise PermissionError('Role must be below HeadMod in the role hierarchy')
        changes = {}
        if color is not None:
            changes['color'] = valid_color(color)
        if hoist is not None:
            changes['hoist'] = hoist
        if position is not None:
            if position <= 0 or position >= guild.me.top_role.position:
                raise ValueError('Role position must be below HeadMod and above @everyone')
            changes['position'] = position
        if not changes:
            raise ValueError('Provide at least one role style change')
        updated = await role.edit(**changes, reason='Authorized MCP role styling')
        audit.info('style_role server_id=%s role_id=%s fields=%s', server_id, role_id, sorted(changes))
        return role_data(updated)
