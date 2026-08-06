# cogs/forceoptin.py

import discord
from discord.ext import commands
from core.opt_in_manager import opt_in, ensure_user_category, load_opted_in, save_opted_in
import os


class ForceOptIn(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="forceoptin")
    @commands.is_owner()
    async def forceoptin(self, ctx, member: discord.Member = None):
        """
        Force a user into the opt‑in system.
        If no member is provided, bulk‑opt‑in all users who already have memory categories.
        """

        # ---------------------------------------------------------
        # BULK MODE — no member provided
        # ---------------------------------------------------------
        if member is None:
            guild = self.bot.get_guild(int(os.getenv("HOME_SERVER_ID")))
            if guild is None:
                return await ctx.send("❌ HOME_SERVER_ID is invalid or the bot is not in that server.")

            opted_in = load_opted_in()
            added = 0

            for category in guild.categories:
                if not category.name.startswith("memory-"):
                    continue

                # Extract user ID from category name
                try:
                    user_id = int(category.name.replace("memory-", ""))
                except ValueError:
                    continue

                # Skip bots or invalid members
                member_obj = guild.get_member(user_id)
                if member_obj is None or member_obj.bot:
                    continue

                # Add to opted_in.json if not already present
                if user_id not in opted_in:
                    opted_in.add(user_id)
                    added += 1

            save_opted_in(opted_in)

            return await ctx.send(f"✅ Bulk opt‑in complete. Added **{added}** users to opted_in.json.")

        # ---------------------------------------------------------
        # SINGLE USER MODE — member provided
        # ---------------------------------------------------------
        if member.bot:
            return await ctx.send("Bots cannot be opted in.")

        # Perform opt‑in using the centralized manager
        await opt_in(self.bot, member)

        # Ensure their memory/category channels exist
        await ensure_user_category(self.bot, member)

        await ctx.send(f"✅ Forced opt‑in completed for **{member}**.")


async def setup(bot):
    await bot.add_cog(ForceOptIn(bot))
