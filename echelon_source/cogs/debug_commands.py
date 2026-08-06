# debug_commands.py — General Debug Commands (Owner Only)

import discord
from discord.ext import commands

from core.context_manager import get_home_guild

OWNER_ID = 284007193181945857


class DebugCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # OPT-IN ALL USERS WHO HAVE A CONTEXT CATEGORY
    # ---------------------------------------------------------
    @commands.command(name="context_optin_all")
    async def context_optin_all(self, ctx):
        if ctx.author.id != OWNER_ID:
            return await ctx.send("Hey, @everyone, I just tried to do something very silly")

        guild = get_home_guild(self.bot)
        count = 0

        for category in guild.categories:
            for channel in category.channels:
                # Look for channels named like: context-123456789012345678
                if channel.name.startswith("context-"):
                    try:
                        user_id = int(channel.name.replace("context-", ""))
                    except ValueError:
                        continue

                    member = guild.get_member(user_id)
                    if not member:
                        continue

                    # Skip bots except Sevin (your bot)
                    if member.bot and member.id != self.bot.user.id:
                        continue

                    # Skip users already opted in
                    if user_id in self.bot.context_opted_in:
                        continue

                    # Run your existing opt-in logic
                    await self.bot.force_opt_in(member)
                    count += 1

        await ctx.send(f"Opted in **{count}** users who had context categories.")

    # ---------------------------------------------------------
    # PING
    # ---------------------------------------------------------
    @commands.command(name="ping")
    async def ping(self, ctx):
        if ctx.author.id != OWNER_ID:
            return await ctx.send("Hey, @everyone, I just tried to do something very silly")
        await ctx.send("Pong.")

    # ---------------------------------------------------------
    # FLASHYTHING — delete last N messages
    # ---------------------------------------------------------
    @commands.command(name="flashything")
    async def flashything(self, ctx, count: int = 1):
        if ctx.author.id != OWNER_ID:
            return await ctx.send("Hey, @everyone, I just tried to do something very silly")

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
                except:
                    pass

        await ctx.send(
            f"Flashything complete. Deleted {deleted} messages.",
            delete_after=3
        )

    # ---------------------------------------------------------
    # FLASHYTHING NUKE — delete EVERYTHING except pinned
    # ---------------------------------------------------------
    @commands.command(name="flashything_nuke")
    async def flashything_nuke(self, ctx):
        if ctx.author.id != OWNER_ID:
            return await ctx.send("Hey, @everyone, I just tried to do something very silly")

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
        except:
            # Fallback for older messages
            for m in to_delete:
                try:
                    await m.delete()
                except:
                    pass

        confirm = await ctx.send(f"Nuke complete. Deleted {len(to_delete)} messages.")
        await confirm.delete(delay=3)


async def setup(bot):
    await bot.add_cog(DebugCommands(bot))
