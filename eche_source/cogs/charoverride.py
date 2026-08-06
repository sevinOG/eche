# charoverride.py — Owner-only next-reply override
# Owner = Discord application owner (no hardcoded user IDs).

import discord
from discord.ext import commands


class CharOverride(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.next_reply_override = False
        self.bot.override_waiting_for = None

    @commands.command(name="charoverride")
    @commands.is_owner()
    async def charoverride(self, ctx):
        msg = await ctx.send("What would you like me to say?")

        self.bot.next_reply_override = True
        self.bot.override_waiting_for = msg.id

    @commands.Cog.listener()
    async def on_message(self, message):
        # If override isn't active, ignore
        if not getattr(self.bot, "next_reply_override", False):
            return

        # Must be replying to the bot's question
        if not message.reference:
            return

        if message.reference.message_id != self.bot.override_waiting_for:
            return

        # Only the application owner may complete the override
        if not await self.bot.is_owner(message.author):
            return

        # --- VALID OVERRIDE TRIGGER ---
        user_text = message.content

        # Call your normal pipeline but with 2000-char limit
        reply = await self.bot.generate_reply(
            user_text,
            max_chars=2000,
        )

        await message.channel.send(reply)

        # Reset override
        self.bot.next_reply_override = False
        self.bot.override_waiting_for = None


async def setup(bot):
    await bot.add_cog(CharOverride(bot))
