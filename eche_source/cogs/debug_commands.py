# debug_commands.py — General Debug Commands (Owner Only)
# Owner = Discord application owner (no hardcoded user IDs).

import discord
from discord.ext import commands

from core.context_manager import get_home_guild


class DebugCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # OPT-IN ALL USERS WHO HAVE A CONTEXT CATEGORY
    # ---------------------------------------------------------
    @commands.command(name="context_optin_all")
    @commands.is_owner()
    async def context_optin_all(self, ctx):
        guild = get_home_guild(self.bot)
        count = 0

        for category in guild.categories:
            for channel in category.channels:
                # Look for channels named like: context-<snowflake>
                if channel.name.startswith("context-"):
                    try:
                        user_id = int(channel.name.replace("context-", ""))
                    except ValueError:
                        continue

                    member = guild.get_member(user_id)
                    if not member:
                        continue

                    # Skip other bots; allow this bot
                    if member.bot and member.id != self.bot.user.id:
                        continue

                    # Skip users already opted in
                    if user_id in self.bot.context_opted_in:
                        continue

                    await self.bot.force_opt_in(member)
                    count += 1

        await ctx.send(f"Opted in **{count}** users who had context categories.")

    # ---------------------------------------------------------
    # PING
    # ---------------------------------------------------------
    @commands.command(name="ping")
    @commands.is_owner()
    async def ping(self, ctx):
        await ctx.send("Pong.")

    # ---------------------------------------------------------
    # FLASHYTHING — delete last N messages
    # ---------------------------------------------------------
    @commands.command(name="flashything")
    @commands.is_owner()
    async def flashything(self, ctx, count: int = 1):
        if count < 1:
            return await ctx.send("Count must be at least 1.")

        deleted = 0
        async for msg in ctx.channel.history(limit=200):
            if deleted >= count:
                break

            if msg.author == ctx.author or msg.author == self.bot.user:
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass

        await ctx.send(
            f"Flashything complete. Deleted {deleted} messages.",
            delete_after=3,
        )

    # ---------------------------------------------------------
    # FLASHYTHING NUKE — delete EVERYTHING except pinned
    # ---------------------------------------------------------
    @commands.command(name="flashything_nuke")
    @commands.is_owner()
    async def flashything_nuke(self, ctx):
        channel = ctx.channel
        pinned_ids = {p.id for p in await channel.pins()}

        to_delete = []
        async for msg in channel.history(limit=None):
            if msg.id not in pinned_ids:
                to_delete.append(msg)

        if not to_delete:
            confirm = await ctx.send("Nothing to delete — only pinned messages remain.")
            return await confirm.delete(delay=3)

        try:
            await channel.delete_messages(to_delete)
        except Exception:
            for m in to_delete:
                try:
                    await m.delete()
                except Exception:
                    pass

        confirm = await ctx.send(f"Nuke complete. Deleted {len(to_delete)} messages.")
        await confirm.delete(delay=3)


async def setup(bot):
    await bot.add_cog(DebugCommands(bot))
