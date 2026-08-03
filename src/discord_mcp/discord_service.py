from __future__ import annotations

import discord

from .config import Settings


class DiscordService:
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = discord.Client(intents=intents)
        self.settings = settings

    def require_guild(self, guild_id: int) -> discord.Guild:
        if guild_id not in self.settings.allowed_guild_ids:
            raise PermissionError("That server is not in ALLOWED_GUILD_IDS")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise LookupError("The bot is not connected to that server")
        return guild

    def require_channel(self, channel_id: int) -> discord.abc.Messageable:
        if self.settings.allowed_channel_ids and channel_id not in self.settings.allowed_channel_ids:
            raise PermissionError("That channel is not in ALLOWED_CHANNEL_IDS")
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            raise LookupError("Channel not found or not visible to the bot")
        return channel

    async def start(self) -> None:
        await self.bot.start(self.settings.discord_token)

    async def close(self) -> None:
        if not self.bot.is_closed():
            await self.bot.close()

