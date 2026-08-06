# cogs/hire/rob.py

import discord
from discord.ext import commands
import random

from cogs.economy.bank import Bank


class Rob(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank: Bank = bot.get_cog("Bank")

    @commands.command(name="rob")
    async def rob(self, ctx, target: discord.Member = None, amount: str = None):

        # Validate args
        if target is None or amount is None:
            return await ctx.send("Usage: `?rob @user amount`")

        # Prevent robbing self or bots
        if target.id == ctx.author.id:
            return await ctx.send("❌ You can't rob yourself.")

        if target.bot:
            return await ctx.send("❌ You can't rob bots.")

        # Validate amount
        try:
            amount = float(amount)
        except:
            return await ctx.send("❌ Amount must be a number.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than 0.")

        # Load balances
        attacker_bal = await self.bank.load_bank(ctx.author)
        target_bal = await self.bank.load_bank(target)

        # Check attacker has enough to attempt
        if attacker_bal < amount:
            return await ctx.send(
                f"❌ <@{ctx.author.id}> you need **{amount} coins** to hire a mugger, "
                f"but you only have **{attacker_bal}**."
            )

        # Deduct upfront fee
        attacker_bal -= amount
        await self.bank.save_bank(ctx.author, attacker_bal)

        roll = random.random()  # 0.0 - 1.0

        # 60% FAIL
        if roll < 0.60:
            return await ctx.send(
                f"💸 Mugger sent by <@{ctx.author.id}> "
                f"to rob <@{target.id}> **FAILED**.\n"
                f"You lose your **{amount} coins**."
            )

        # 30% SUCCESS
        if roll < 0.90:
            stolen = amount * 2
            stolen = min(stolen, target_bal)

            target_bal -= stolen
            attacker_bal += stolen

            await self.bank.save_bank(target, target_bal)
            await self.bank.save_bank(ctx.author, attacker_bal)

            return await ctx.send(
                f"🕶️ Mugger from <@{ctx.author.id}> "
                f"SUCCESSFULLY robbed <@{target.id}>!\n"
                f"You steal **{stolen} coins**."
            )

        # 10% WORSE FAIL
        extra_loss = int(amount * 1.5)
        attacker_bal -= extra_loss
        await self.bank.save_bank(ctx.author, attacker_bal)

        return await ctx.send(
            f"💀 Mugger sent by <@{ctx.author.id}> "
            f"to rob <@{target.id}> failed… **but way worse**.\n"
            f"You lose an additional **{extra_loss} coins**."
        )


async def setup(bot):
    await bot.add_cog(Rob(bot))
