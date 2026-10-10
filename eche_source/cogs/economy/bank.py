import discord
from discord.ext import commands

# ⭐ NEW — allow bot economy participation
from core.bot_whitelist import is_allowed_bot
from core.home_id import home_server_id_from_env

HOME_SERVER_ID = home_server_id_from_env()


class Bank(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # INTERNAL: Ensure bank file exists
    # ---------------------------------------------------------
    async def ensure_bank_file(self, member):
        from core.discord_store import BANK_HEADER, THREAD_BANK, ensure_record, ensure_user_thread

        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None or member is None:
            return None

        thread = await ensure_user_thread(
            guild, member.id, THREAD_BANK, getattr(member, "name", None)
        )
        if thread is None:
            return None
        return await ensure_record(thread, BANK_HEADER, "BANK DATA\n500.00\nSTARTER:1")

    # ---------------------------------------------------------
    # LOAD BANK (rounded)
    # ---------------------------------------------------------
    async def load_bank(self, member):
        bank_message = await self.ensure_bank_file(member)
        if bank_message is None:
            return 500.00

        from core.discord_store import refresh_record

        bank_message = await refresh_record(bank_message)
        lines = (bank_message.content or "").splitlines()
        try:
            return round(float(lines[1].strip()), 2)
        except Exception:
            return 500.00

    # ---------------------------------------------------------
    # SAVE BANK (rounded)
    # ---------------------------------------------------------
    async def save_bank(self, member, new_value):
        bank_message = await self.ensure_bank_file(member)
        if bank_message is None:
            return

        from core.discord_store import edit_record, refresh_record

        bank_message = await refresh_record(bank_message)
        rounded = round(float(new_value), 2)

        lines = (bank_message.content or "").splitlines()
        starter_flag = lines[2].strip() if len(lines) >= 3 else "STARTER:1"

        new_content = f"BANK DATA\n{rounded}\n{starter_flag}"
        await edit_record(bank_message, new_content)

    # ---------------------------------------------------------
    # OWNER-ONLY: ?bankrebuild @user
    # ---------------------------------------------------------
    @commands.command(name="bankrebuild")
    @commands.is_owner()
    async def bank_rebuild(self, ctx, member: discord.Member = None):

        if member is None:
            return await ctx.send("Usage: `?bankrebuild @user`")

        from core.discord_store import (
            THREAD_BANK,
            ensure_user_thread,
            forget_parent,
            list_pins,
            remember_record,
        )

        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return await ctx.send("Home guild not found.")

        thread = await ensure_user_thread(
            guild, member.id, THREAD_BANK, getattr(member, "name", None)
        )
        if thread is None:
            return await ctx.send("Could not open that user's bank thread.")

        for msg in await list_pins(thread):
            if (msg.content or "").startswith("BANK DATA"):
                await msg.delete()
        forget_parent(thread.id)

        new_msg = await thread.send("BANK DATA\n500.00\nSTARTER:1")
        await new_msg.pin()
        remember_record(thread.id, new_msg)

        await ctx.send(
            f"✅ Rebuilt **{member.display_name}**'s bank file.\n"
            f"Balance reset to **500.00**.\n"
            f"Bank thread: {thread.mention}"
        )

    # ---------------------------------------------------------
    # ?bank (root)
    # ---------------------------------------------------------
    @commands.group(name="bank", invoke_without_command=True)
    async def bank(self, ctx):
        embed = discord.Embed(
            title="🏦 Bank Command Usage",
            description=(
                "**Available Commands:**\n"
                "• `?bank give @user amount`\n"
                "• `?bank value`\n"
                "• `?bank value @user`\n"
                "\n*(Owner-only commands are hidden)*"
            ),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------
    # ?bank value [@user]
    # ---------------------------------------------------------
    @bank.command(name="value")
    async def bank_value(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        bal = await self.load_bank(target)

        embed = discord.Embed(
            title=f"🏦 Bank Balance — {target.display_name}",
            description=f"**{bal:.2f} coins**",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------
    # ?bank give @user amount  (BOT-FRIENDLY)
    # ---------------------------------------------------------
    @bank.command(name="give")
    async def bank_give(self, ctx, member: discord.Member = None, amount: str = None):

        if member is None or amount is None:
            return await ctx.send("Usage: `?bank give @user amount`")

        # Validate amount
        try:
            amount = float(amount)
        except:
            return await ctx.send("❌ Amount must be a number.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than 0.")

        # ⭐ Allow bots IF they are whitelisted
        if member.bot and not is_allowed_bot(member.id):
            return await ctx.send("❌ That bot is not allowed to participate in the economy.")

        # ⭐ Also ensure the SENDER bot is allowed
        if ctx.author.bot and not is_allowed_bot(ctx.author.id):
            return await ctx.send("❌ You (bot) are not allowed to use the economy.")

        # Prevent giving to yourself
        if member.id == ctx.author.id:
            return await ctx.send("❌ You cannot give coins to yourself.")

        # Load balances
        sender_bal = await self.load_bank(ctx.author)
        receiver_bal = await self.load_bank(member)

        if sender_bal < amount:
            return await ctx.send("❌ You do not have enough coins to give that amount.")

        # Apply transfer
        sender_bal = round(sender_bal - amount, 2)
        receiver_bal = round(receiver_bal + amount, 2)

        await self.save_bank(ctx.author, sender_bal)
        await self.save_bank(member, receiver_bal)

        await ctx.send(
            f"💸 **{ctx.author.display_name}** gave **{amount:.2f} coins** to **{member.display_name}**!\n"
            f"Your new balance: **{sender_bal:.2f} coins**"
        )


async def setup(bot):
    await bot.add_cog(Bank(bot))
