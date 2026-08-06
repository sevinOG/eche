import discord
from discord.ext import commands
import os
from datetime import datetime, timedelta
import uuid

from cogs.economy.bank import Bank
from .entertainer_manager import register_entertainer_batch


class Entertainers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank: Bank = bot.get_cog("Bank")

    @commands.command(name="entertainers")
    async def entertainers(self, ctx, amount: str = None):

        if amount is None:
            return await ctx.send("Usage: `?entertainers <amount>`")

        try:
            amount = float(amount)
        except:
            return await ctx.send("❌ Amount must be a number.")

        if amount < 10000:
            return await ctx.send("❌ Minimum purchase is 10,000 coins (1 entertainer).")

        units = int(amount // 10000)
        cost = units * 10000

        bal = await self.bank.load_bank(ctx.author)
        if bal < cost:
            return await ctx.send(
                f"❌ You need **{cost} coins** but only have **{bal}**."
            )

        bal -= cost
        await self.bank.save_bank(ctx.author, bal)

        # HOME SERVER ONLY
        home_id = int(os.getenv("HOME_SERVER_ID"))
        home_guild = self.bot.get_guild(home_id)
        if home_guild is None:
            return await ctx.send("❌ HOME_SERVER_ID invalid.")

        category = discord.utils.get(home_guild.categories, name=f"memory-{ctx.author.id}")
        if category is None:
            return await ctx.send(
                f"❌ You are not opted in on the home server.\n"
                f"Use `?forceoptin {ctx.author.mention}` there."
            )

        workers_channel = discord.utils.get(category.channels, name="workers")
        if workers_channel is None:
            workers_channel = await home_guild.create_text_channel(
                "workers", category=category
            )

        batch_id = str(uuid.uuid4())
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(hours=1)

        invoke_guild_id = ctx.guild.id
        invoke_channel_id = ctx.channel.id

        content = (
            f"Batch ID: {batch_id}\n"
            f"Units: {units}\n"
            f"Purchased by: <@{ctx.author.id}>\n"
            f"Server ID: {home_guild.id}\n"
            f"Channel ID: {workers_channel.id}\n"
            f"Invoke Server ID: {invoke_guild_id}\n"
            f"Invoke Channel ID: {invoke_channel_id}\n"
            f"Created at (UTC): {created_at.isoformat()}\n"
            f"Expires at (UTC): {expires_at.isoformat()}\n"
            f"Ticks remaining: 4\n"
        )

        msg = await workers_channel.send(content)
        await msg.pin()

        register_entertainer_batch(
            user_id=ctx.author.id,
            units=units,
            guild_id=home_guild.id,
            channel_id=workers_channel.id,
            batch_id=batch_id,
            created_at=created_at,
            expires_at=expires_at,
            invoke_guild_id=invoke_guild_id,
            invoke_channel_id=invoke_channel_id,
        )

        await ctx.send(
            f"🎉 <@{ctx.author.id}> hired **{units} entertainers!**\n"
            f"They will update you every 15 minutes.\n"
            f"Batch ID: `{batch_id}`"
        )


async def setup(bot):
    await bot.add_cog(Entertainers(bot))
