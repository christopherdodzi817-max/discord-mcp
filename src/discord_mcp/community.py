from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands

from .config import Settings
from .moderation import ActivityTracker, RuleSignal, find_invite_signal, find_term_signals


SELF_ASSIGNABLE_ROLES: tuple[tuple[str, str, str], ...] = (
    ("Creator Interest", "Tell the community you are interested in the Creator program.", "🎨"),
    ("Playtester Interest", "Show interest in playtests; staff approval is still required for access.", "🧪"),
    ("Early Supporter", "Support the community and unlock supporter perks.", "⭐"),
)

TICKET_TYPES: tuple[tuple[str, str, str, discord.ButtonStyle], ...] = (
    ("support", "Support", "📩", discord.ButtonStyle.primary),
    ("creator", "Creator Application", "🎥", discord.ButtonStyle.success),
    ("staff", "Staff Application", "🛡️", discord.ButtonStyle.secondary),
    ("report", "Report / Appeal", "⚠️", discord.ButtonStyle.danger),
)


class RoleMenuView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for name, description, emoji in SELF_ASSIGNABLE_ROLES:
            self.add_item(RoleToggleButton(name, description, emoji))


class RoleToggleButton(discord.ui.Button):
    def __init__(self, role_name: str, description: str, emoji: str) -> None:
        super().__init__(
            label=role_name,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"fishing:role:{role_name.lower().replace(' ', '-')}",
        )
        self.role_name = role_name
        self.description = description

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server, not a DM.", ephemeral=True)
            return
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if role is None:
            await interaction.response.send_message(
                f"The **{self.role_name}** role is not configured yet.", ephemeral=True
            )
            return
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Self-serve community role removal")
                message = f"Removed **{self.role_name}** from your roles."
            else:
                await interaction.user.add_roles(role, reason="Self-serve community role selection")
                message = f"Added **{self.role_name}**. Welcome to that part of the community!"
        except discord.Forbidden:
            message = "I cannot manage that role yet. Move my bot role above the community roles in Server Settings → Roles."
        await interaction.response.send_message(message, ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        for ticket_key, label, emoji, style in TICKET_TYPES:
            self.add_item(TicketButton(ticket_key, label, emoji, style, bot))


class TicketButton(discord.ui.Button):
    def __init__(
        self,
        ticket_key: str,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        bot: "CommunityBot",
    ) -> None:
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"fishing:ticket:{ticket_key}")
        self.ticket_key = ticket_key
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.open_ticket(interaction, self.ticket_key)


class CloseTicketView(discord.ui.View):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(CloseTicketButton(bot))


class CloseTicketButton(discord.ui.Button):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="fishing:ticket:close")
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.close_ticket(interaction)


class CommunityBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = settings.member_events
        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.log = logging.getLogger("discord-mcp.community")
        self._ticket_panels_ready = False
        self.activity_tracker = ActivityTracker(
            settings.spam_window_seconds, settings.spam_message_threshold, 3, settings.mention_threshold
        )

        @self.tree.command(name="roles", description="Choose optional community roles and perks.")
        async def roles(interaction: discord.Interaction) -> None:
            if interaction.guild is None or interaction.guild.id not in settings.allowed_guild_ids:
                await interaction.response.send_message("This command is not available here.", ephemeral=True)
                return
            embed = discord.Embed(
                title="Choose your community roles",
                description=(
                    "Tap a role to add or remove it. These roles unlock relevant spaces and help people find you.\n\n"
                    "Staff roles are assigned by the team and cannot be self-selected."
                ),
                color=discord.Color.blurple(),
            )
            for name, description, emoji in SELF_ASSIGNABLE_ROLES:
                embed.add_field(name=f"{emoji} {name}", value=description, inline=False)
            await interaction.response.send_message(embed=embed, view=RoleMenuView(), ephemeral=True)

    async def setup_hook(self) -> None:
        self.add_view(RoleMenuView())
        self.add_view(TicketPanelView(self))
        self.add_view(CloseTicketView(self))
        for guild_id in self.settings.allowed_guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

    def is_staff(self, member: discord.Member) -> bool:
        permissions = member.guild_permissions
        return bool(permissions.administrator or permissions.manage_messages) or any(
            role.name in {"Moderator", "Developer", "Admin"} for role in member.roles
        )

    def resolve_escalation_admin(self, guild: discord.Guild) -> discord.Member | None:
        member = guild.get_member(self.settings.escalation_admin_id)
        if member is None or member.id != self.settings.escalation_admin_id:
            return None
        return member if member.guild_permissions.administrator else None

    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or guild.id not in self.settings.allowed_guild_ids or message.author.bot:
            return
        content = message.content or ""
        if not content.strip():
            return
        if self.user is not None and any(user.id == self.user.id for user in message.mentions):
            await self._answer_ping(message)
        if self.is_staff(message.author):
            return
        signals = find_term_signals(content, self.settings.terms_by_rule or {}, self.settings.safe_phrases)
        invite = find_invite_signal(content, message.channel.name)
        if invite is not None:
            signals.append(invite)
        signals.extend(self.activity_tracker.inspect(
            message.author.id, content, len(message.mentions), message.mention_everyone, time.monotonic()
        ))
        for signal in signals:
            await self.post_incident(message, signal)

    async def _answer_ping(self, message: discord.Message) -> None:
        rules = f"<#{self.settings.rules_channel_id}>"
        response = f"I can point you to the published rules in {rules} and report possible concerns for staff review."
        allowed_mentions = discord.AllowedMentions.none()
        if re.search(r"\b(?:ban|kick|timeout|mute|delete|punish)\b", message.content, re.IGNORECASE):
            admin = self.resolve_escalation_admin(message.guild)
            if admin is not None:
                response += f" A server administrator can decide on that request: <@{admin.id}>."
                allowed_mentions = discord.AllowedMentions(
                    everyone=False, roles=False, users=[admin.id], replied_user=False
                )
            else:
                response += " Please ask a server administrator to review that request."
        try:
            await message.reply(response, allowed_mentions=allowed_mentions, mention_author=False)
        except discord.HTTPException:
            self.log.warning("Could not answer HeadMod ping in channel %s", message.channel.id)

    async def post_incident(self, message: discord.Message, signal: RuleSignal) -> bool:
        guild = message.guild
        if guild is None or guild.id not in self.settings.allowed_guild_ids:
            return False
        channel_id = self.settings.moderation_log_channel_id
        if self.settings.allowed_channel_ids and channel_id not in self.settings.allowed_channel_ids:
            self.log.warning("Moderation log channel %s is not allowlisted", channel_id)
            return False
        channel = self.get_channel(channel_id)
        if channel is None or channel.guild.id != guild.id or not hasattr(channel, "send"):
            self.log.warning("Moderation log channel %s is unavailable in guild %s", channel_id, guild.id)
            return False
        if channel.permissions_for(guild.default_role).view_channel:
            self.log.warning("Moderation log channel %s is public; refusing incident", channel_id)
            return False
        excerpt = discord.utils.escape_mentions(discord.utils.escape_markdown(message.content[:300]))
        embed = discord.Embed(
            title=f"Possible rule {signal.rule_id} concern",
            description=f"**{discord.utils.escape_markdown(signal.label)}**\n{discord.utils.escape_markdown(signal.reason)}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Message", value=message.jump_url, inline=False)
        embed.add_field(name="Evidence excerpt", value=excerpt or "[No text]", inline=False)
        embed.add_field(name="Author ID", value=str(message.author.id), inline=True)
        embed.set_footer(text="HeadMod incident v1 - Staff review required")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.log.warning("Could not send moderation incident to channel %s", channel_id)
            return False
        return True

    async def open_ticket(self, interaction: discord.Interaction, ticket_key: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server, not a DM.", ephemeral=True)
            return
        if interaction.guild.id not in self.settings.allowed_guild_ids:
            await interaction.response.send_message("Tickets are not available here.", ephemeral=True)
            return
        ticket_label = next((item[1] for item in TICKET_TYPES if item[0] == ticket_key), None)
        if ticket_label is None:
            await interaction.response.send_message("Unknown ticket type.", ephemeral=True)
            return

        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="TICKETS")
        if category is None:
            await interaction.response.send_message("The ticket category is not ready yet.", ephemeral=True)
            return
        owner_marker = f"ticket-owner:{interaction.user.id};"
        existing = discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel)
            and channel.category_id == category.id
            and (channel.topic or "").startswith(owner_marker),
            guild.channels,
        )
        if existing is not None:
            await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.display_name.lower().replace(" ", "-"))
        channel_name = f"ticket-{ticket_key}-{safe_name or interaction.user.id}"[:90]
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for role_name in ("Moderator", "Developer", "Admin"):
            role = discord.utils.get(guild.roles, name=role_name)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                topic=f"{owner_marker}type:{ticket_key}",
                overwrites=overwrites,
                reason=f"{ticket_label} ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message("I need Manage Channels permission to open tickets.", ephemeral=True)
            return

        prompts = {
            "support": "Describe what you need help with and include screenshots or error details if useful.",
            "creator": "Send your YouTube, TikTok, Twitch, Kick, or other public creator links, plus one recent example and what you want to create for the community.",
            "staff": "Tell us your timezone, moderation/community experience, availability, and why you want to help. Do not share private information.",
            "report": "Describe what happened, include relevant message links/screenshots, and avoid posting private information you do not own.",
        }
        embed = discord.Embed(
            title=f"{ticket_label} Ticket",
            description=prompts[ticket_key],
            color=discord.Color.blurple(),
        )
        await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView(self))
        await interaction.response.send_message(f"Your private ticket is ready: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if interaction.guild is None or not isinstance(channel, discord.TextChannel) or channel.category is None or channel.category.name != "TICKETS":
            await interaction.response.send_message("This button only works inside a ticket.", ephemeral=True)
            return
        owner_id = (channel.topic or "").split(";", 1)[0].removeprefix("ticket-owner:")
        if str(interaction.user.id) != owner_id and not (isinstance(interaction.user, discord.Member) and self.is_staff(interaction.user)):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket…", ephemeral=True)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")

    async def on_ready(self) -> None:
        if self._ticket_panels_ready:
            return
        self._ticket_panels_ready = True
        for guild in self.guilds:
            if guild.id not in self.settings.allowed_guild_ids:
                continue
            panel_channel = discord.utils.find(
                lambda channel: isinstance(channel, discord.TextChannel) and channel.name == "open-a-ticket",
                guild.text_channels,
            )
            if panel_channel is None:
                continue
            try:
                recent = [message async for message in panel_channel.history(limit=25)]
                has_panel = any(
                    message.author.id == self.user.id
                    and any(component.custom_id == "fishing:ticket:support" for row in message.components for component in row.children)
                    for message in recent
                )
                if not has_panel:
                    embed = discord.Embed(
                        title="Open a private ticket",
                        description="Choose what you need. Only you and the staff team can see the ticket.",
                        color=discord.Color.blurple(),
                    )
                    await panel_channel.send(embed=embed, view=TicketPanelView(self))
            except discord.Forbidden:
                self.log.warning("Could not create the ticket panel in #%s", panel_channel.name)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id not in self.settings.allowed_guild_ids:
            return
        member_role = discord.utils.get(member.guild.roles, name="Community Member")
        if member_role is not None:
            try:
                await member.add_roles(member_role, reason="Automatic community member role")
            except discord.Forbidden:
                self.log.warning("Could not assign Community Member role to %s", member.id)
        welcome = discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel) and channel.name == "welcome",
            member.guild.text_channels,
        )
        if welcome is None:
            return
        embed = discord.Embed(
            title="A new angler just joined! 🎣",
            description=(
                f"Welcome {member.mention}! Say hello in **#introductions**, then use **/roles** "
                "to choose the community spaces you want to see."
            ),
            color=discord.Color.teal(),
        )
        try:
            await welcome.send(embed=embed)
        except discord.Forbidden:
            self.log.warning("Could not send join message in #%s", welcome.name)

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id not in self.settings.allowed_guild_ids:
            return
        departures = discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel)
            and channel.name == "member-departures",
            member.guild.text_channels,
        )
        if departures is None:
            return
        name = discord.utils.escape_markdown(member.name)
        try:
            await departures.send(f"`{name}` (`{member.id}`) left the server.")
        except discord.Forbidden:
            self.log.warning("Could not log a member departure in #%s", departures.name)
