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

        # Build prompt + char limit
        prompt, max_chars = build_heckle_prompt(f"<@{target.id}>", amount)

        # Call Groq via SIMPLE endpoint
        try:
            heckle_text = await call_groq_simple(prompt, max_chars=max_chars)
        except Exception as e:
            return await ctx.send(f"❌ Groq error: {e}")

        # If the REST client returned an error tuple
        if isinstance(heckle_text, tuple):
            return await ctx.send(f"❌ {heckle_text[1]}")

        # Enforce char limit (hard cutoff)
        heckle_text = heckle_text[:max_chars]

        # Send the heckle
        await ctx.send(
            f"🤭 **Heckler says:**\n{heckle_text}"
        )


async def setup(bot):
    await bot.add_cog(Heckle(bot))
