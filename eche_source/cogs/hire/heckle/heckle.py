# cogs/hire/heckle/heckle.py

import discord
from discord.ext import commands

from cogs.economy.bank import Bank
from core.client import call_groq_simple  # <-- IMPORTANT
from .hbuilder import build_heckle_prompt


class Heckle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank: Bank = bot.get_cog("Bank")

    @commands.command(name="heckle")
    async def heckle(self, ctx, target: discord.Member = None, amount: str = None):

        # Validate args
        if target is None or amount is None:
            return await ctx.send("Usage: `?heckle @user amount`")

        if target.id == ctx.author.id:
            return await ctx.send("❌ You can't heckle yourself.")

        if target.bot:
            return await ctx.send("❌ You can't heckle bots.")

        # Validate amount
        try:
            amount = float(amount)
        except:
            return await ctx.send("❌ Amount must be a number.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than 0.")

        # Load balances
        attacker_bal = await self.bank.load_bank(ctx.author)

        if attacker_bal < amount:
            return await ctx.send(
                f"❌ <@{ctx.author.id}> you need **{amount} coins** to hire a heckler, "
                f"but you only have **{attacker_bal}**."
            )

        # Deduct fee
        attacker_bal -= amount
        await self.bank.save_bank(ctx.author, attacker_bal)

        prompt, max_chars = build_heckle_prompt(f"<@{target.id}>", amount)
        from core.client import REPLY_MAX_CHARS, discord_chunks

        prefix = "🤭 **Heckler says:**\n"
        room = max(16, REPLY_MAX_CHARS - len(prefix))
        budget = min(max_chars, room)

        try:
            heckle_text = await call_groq_simple(prompt, max_chars=budget)
        except Exception as e:
            for chunk in discord_chunks(f"❌ Groq error: {e}") or ["❌ Groq error."]:
                await ctx.send(chunk)
            return

        if isinstance(heckle_text, tuple):
            detail = heckle_text[1] if len(heckle_text) > 1 else "Heckle failed."
            for chunk in discord_chunks(f"❌ {detail}") or ["❌ Heckle failed."]:
                await ctx.send(chunk)
            return

        for chunk in discord_chunks(prefix + (heckle_text or "").strip()):
            await ctx.send(chunk)


async def setup(bot):
    await bot.add_cog(Heckle(bot))
