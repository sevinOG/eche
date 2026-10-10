# charoverride.py — Owner-only next-reply override
# Owner = Settings → Security Owner IDs (application owner when that field is blank).

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

        if not await self.bot.is_owner(message.author):
            return

        user_text = message.content
        from core.client import THREAD_COMPLETION_TOKENS, call_groq_turn, discord_chunks, settle_cut_reply

        turn = await call_groq_turn(
            user_text,
            user_id=message.author.id,
            max_completion_tokens=THREAD_COMPLETION_TOKENS,
            tools=None,
        )
        if turn.cut:
            turn = await settle_cut_reply(
                turn,
                user_text,
                message.author.id,
                max_completion_tokens=THREAD_COMPLETION_TOKENS,
            )
        for chunk in discord_chunks(turn.reply or ""):
            await message.channel.send(chunk)

        # Reset override
        self.bot.next_reply_override = False
        self.bot.override_waiting_for = None


async def setup(bot):
    await bot.add_cog(CharOverride(bot))
