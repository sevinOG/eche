# debug_commands.py — General Debug Commands (Owner Only)
# Owner = Settings → Security Owner IDs (application owner when that field is blank).

import discord
from discord.ext import commands

from core.context_manager import get_home_guild


class DebugCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # OPT-IN MEMBERS WHO ALREADY HAVE A USER CHANNEL
    # ---------------------------------------------------------
    @commands.command(name="context_optin_all")
    @commands.is_owner()
    async def context_optin_all(self, ctx):
        from core.discord_store import list_user_ids
        from core.opt_in_manager import ensure_user_category, save_opted_in

        guild = get_home_guild(self.bot)
        if guild is None:
            return await ctx.send("Home server not found.")
        try:
            await guild.fetch_channels()
        except Exception:
            pass

        count = 0
        opted = self.bot.context_opted_in
        for user_id in list_user_ids(guild):
            member = guild.get_member(user_id)
            if member is None or member.bot:
                continue
            if user_id in opted:
                continue
            await ensure_user_category(self.bot, member)
            opted.add(user_id)
            count += 1

        save_opted_in(set(opted))
        await ctx.send(f"Opted in **{count}** users who have a channel in bot memory.")

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
