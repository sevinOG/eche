import discord
import os
from discord.ext import commands

# ⭐ NEW — allow bot economy participation
from core.bot_whitelist import is_allowed_bot

ECONOMY_CHANNEL_NAME = "economy"
HOME_SERVER_ID = int(os.getenv("HOME_SERVER_ID", "0"))


class Bank(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # INTERNAL: Ensure bank file exists
    # ---------------------------------------------------------
    async def ensure_bank_file(self, member):
        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return None

        # Ensure category exists
        category = discord.utils.get(guild.categories, name=f"memory-{member.id}")
        if category is None:
            return None

        # Ensure economy channel exists
        economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
        if economy_channel is None:
            economy_channel = await category.create_text_channel(ECONOMY_CHANNEL_NAME)

        # Get pinned messages
        pins = await economy_channel.pins()
        bank_messages = [m for m in pins if m.content.startswith("BANK DATA")]

        # If missing → create new
        if not bank_messages:
            new_msg = await economy_channel.send("BANK DATA\n500.00\nSTARTER:1")
            await new_msg.pin()
            return new_msg

        return bank_messages[0]

    # ---------------------------------------------------------
    # LOAD BANK (rounded)
    # ---------------------------------------------------------
    async def load_bank(self, member):
        bank_message = await self.ensure_bank_file(member)
        if bank_message is None:
            return 500.00

        lines = bank_message.content.splitlines()
        try:
            return round(float(lines[1].strip()), 2)
        except:
            return 500.00

    # ---------------------------------------------------------
    # SAVE BANK (rounded)
    # ---------------------------------------------------------
    async def save_bank(self, member, new_value):
        bank_message = await self.ensure_bank_file(member)
        if bank_message is None:
            return

        rounded = round(float(new_value), 2)

        lines = bank_message.content.splitlines()
        starter_flag = lines[2].strip() if len(lines) >= 3 else "STARTER:1"

        new_content = f"BANK DATA\n{rounded}\n{starter_flag}"
        await bank_message.edit(content=new_content)

    # ---------------------------------------------------------
    # OWNER-ONLY: ?bankrebuild @user
    # ---------------------------------------------------------
    @commands.command(name="bankrebuild")
    @commands.is_owner()
    async def bank_rebuild(self, ctx, member: discord.Member = None):

        if member is None:
            return await ctx.send("Usage: `?bankrebuild @user`")

        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return await ctx.send("Home guild not found.")

        category = discord.utils.get(guild.categories, name=f"memory-{member.id}")
        if category is None:
            category = await guild.create_category(f"memory-{member.id}")

        economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
        if economy_channel is None:
            economy_channel = await category.create_text_channel(ECONOMY_CHANNEL_NAME)

        pins = await economy_channel.pins()
        for msg in pins:
            if msg.content.startswith("BANK DATA"):
                await msg.delete()

        new_msg = await economy_channel.send("BANK DATA\n500.00\nSTARTER:1")
        await new_msg.pin()

        await ctx.send(
            f"✅ Rebuilt **{member.display_name}**'s bank file.\n"
            f"Balance reset to **500.00**.\n"
            f"Here is their economy channel: {economy_channel.mention}"
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
