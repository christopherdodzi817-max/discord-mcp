import asyncio
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from test_server_tools import settings, setup_server


def async_test(fn):
    @wraps(fn)
    def run():
        asyncio.run(fn())
    return run


def fixture(channels=frozenset()):
    guild = Mock(id=1)
    channel = Mock(spec=discord.TextChannel, id=2, guild=guild, position=0, category_id=3)
    channel.name = 'general'
    channel.edit = AsyncMock(return_value=channel)
    category = Mock(spec=discord.CategoryChannel, id=3, guild=guild)
    category.name = 'COMMUNITY'
    foreign = Mock(spec=discord.CategoryChannel, id=4, guild=Mock(id=9))
    foreign.name = 'OTHER'
    mcp, service = setup_server(settings(channels=channels, guilds=frozenset({1, 9})), guild, [channel, category, foreign])
    return mcp, guild, channel, service


@async_test
async def test_management_tools_are_registered():
    mcp, *_ = fixture()
    names = {t.name for t in await mcp.list_tools()}
    assert {'edit_channel', 'create_channel', 'get_roles', 'create_role', 'style_role'} <= names


@async_test
async def test_edit_requires_confirmation_and_preserves_permissions():
    mcp, _, channel, _ = fixture()
    with pytest.raises(ToolError, match='confirm=true'):
        await mcp._tool_manager.call_tool('edit_channel', {'channel_id': '2', 'name': '💬・general'})
    channel.edit.assert_not_awaited()
    result = await mcp._tool_manager.call_tool('edit_channel', {'channel_id': '2', 'name': '💬・general', 'confirm': True})
    assert result['id'] == '2'
    assert 'overwrites' not in channel.edit.call_args.kwargs


@async_test
async def test_edit_rejects_cross_guild_parent_and_nonallowlisted_channel():
    mcp, _, channel, _ = fixture()
    with pytest.raises(ToolError, match='same server'):
        await mcp._tool_manager.call_tool('edit_channel', {'channel_id': '2', 'category_id': '4', 'confirm': True})
    channel.edit.assert_not_awaited()
    mcp, _, channel, _ = fixture(frozenset({3}))
    with pytest.raises(ToolError, match='ALLOWED_CHANNEL_IDS'):
        await mcp._tool_manager.call_tool('edit_channel', {'channel_id': '2', 'name': 'test', 'confirm': True})
    channel.edit.assert_not_awaited()


@async_test
async def test_creation_rejects_invalid_type_and_channel_limited_config():
    mcp, guild, *_ = fixture()
    with pytest.raises(ToolError, match='channel_type'):
        await mcp._tool_manager.call_tool('create_channel', {'server_id': '1', 'name': 'test', 'channel_type': 'invalid', 'confirm': True})
    guild.create_text_channel.assert_not_awaited()
    mcp, guild, *_ = fixture(frozenset({2}))
    with pytest.raises(ToolError, match='ALLOWED_CHANNEL_IDS'):
        await mcp._tool_manager.call_tool('create_channel', {'server_id': '1', 'name': 'test', 'confirm': True})
    guild.create_text_channel.assert_not_awaited()


@async_test
async def test_create_role_has_no_privileges():
    mcp, guild, *_ = fixture()
    guild.create_role = AsyncMock(return_value=SimpleNamespace(id=8, name='Owner', position=1, color=discord.Color.gold(), hoist=True, permissions=discord.Permissions.none()))
    result = await mcp._tool_manager.call_tool('create_role', {'server_id': '1', 'name': 'Owner', 'color': 0xFEE75C, 'hoist': True, 'confirm': True})
    assert result['id'] == '8'
    assert guild.create_role.call_args.kwargs['permissions'].value == 0


@async_test
async def test_create_channel_inherits_selected_category():
    mcp, guild, channel, _ = fixture()
    guild.create_text_channel.return_value = channel
    result = await mcp._tool_manager.call_tool('create_channel', {'server_id': '1', 'name': '🏷️・roles', 'category_id': '3', 'confirm': True})
    assert result['id'] == '2'
    assert guild.create_text_channel.call_args.kwargs['category'].id == 3
    assert 'overwrites' not in guild.create_text_channel.call_args.kwargs


@async_test
async def test_move_preserves_existing_overwrites():
    mcp, _, channel, _ = fixture()
    await mcp._tool_manager.call_tool('edit_channel', {'channel_id': '2', 'category_id': '3', 'confirm': True})
    assert channel.edit.call_args.kwargs['sync_permissions'] is False
    assert 'overwrites' not in channel.edit.call_args.kwargs


@async_test
async def test_style_role_cannot_change_managed_or_higher_role():
    mcp, guild, *_ = fixture()
    role = Mock(id=8, managed=True)
    guild.get_role.return_value = role
    with pytest.raises(ToolError, match='managed'):
        await mcp._tool_manager.call_tool('style_role', {'server_id': '1', 'role_id': '8', 'color': 0xFF0000, 'confirm': True})
    role.edit.assert_not_called()

