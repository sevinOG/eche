import discord
from discord.ext import commands
from discord.ui import View, Button, Select
import json
import os

OPT_IN_FILE = "opted_in.json"  # corrected filename


def load_opted_in_ids():
    if not os.path.exists(OPT_IN_FILE):
        return set()

    try:
        with open(OPT_IN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(int(uid) for uid in data)
    except:
        return set()


class TargetSelect(Select):
    def __init__(self, ctx, amount, bot, parent_message, job_type):
        self.ctx = ctx
        self.amount = amount  # For lawyer: settlement amount
        self.bot = bot
        self.parent_message = parent_message
        self.job_type = job_type  # "rob", "heckle", "lawyer"

        opted_in_ids = load_opted_in_ids()

        options = []
        for member in ctx.guild.members:
            if member.id in opted_in_ids and not member.bot:
                options.append(discord.SelectOption(
                    label=member.display_name,
                    value=str(member.id)
                ))

        options = options[:25]

        if not options:
            options = [
                discord.SelectOption(
                    label="No opted-in users found",
                    value="none"
                )
            ]

        super().__init__(
            placeholder="Choose a target...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This menu isn't for you.", ephemeral=True
            )

        if self.values[0] == "none":
            return await interaction.response.send_message(
                "No valid targets available.", ephemeral=True
            )

        target_id = int(self.values[0])
        target = self.ctx.guild.get_member(target_id)

        # FINAL MESSAGE ALWAYS THE SAME
        await self.parent_message.edit(
            content=f"**{self.ctx.author.display_name} hired a specialist...**",
            view=None
        )

        # Invoke the correct job silently
        cmd = self.bot.get_command(self.job_type)
        if cmd:
            # Lawyer uses: ?lawyer @target <settlement_amount>
            if self.job_type == "law":
                await self.ctx.invoke(cmd, defendant=target, settlement_amount=str(self.amount))
            else:
                await self.ctx.invoke(cmd, target=target, amount=self.amount)

        await interaction.response.defer()


class HireJobView(View):
    def __init__(self, ctx, amount, bot):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.amount = amount  # For lawyer: settlement amount
        self.bot = bot
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self):
        pass

    @discord.ui.button(label="Mugger", style=discord.ButtonStyle.danger)
    async def mugger(self, interaction: discord.Interaction, button: Button):
        dropdown = TargetSelect(self.ctx, self.amount, self.bot, self.message, "rob")
        view = View(timeout=30)
        view.add_item(dropdown)

        await interaction.response.edit_message(
            content="Choose a target:",
            view=view
        )

    @discord.ui.button(label="Heckler", style=discord.ButtonStyle.primary)
    async def heckler(self, interaction: discord.Interaction, button: Button):
        dropdown = TargetSelect(self.ctx, self.amount, self.bot, self.message, "heckle")
        view = View(timeout=30)
        view.add_item(dropdown)

        await interaction.response.edit_message(
            content="Choose a target:",
            view=view
        )

    @discord.ui.button(label="Lawyer", style=discord.ButtonStyle.secondary)
    async def lawyer(self, interaction: discord.Interaction, button: Button):
        dropdown = TargetSelect(self.ctx, self.amount, self.bot, self.message, "law")
        view = View(timeout=30)
        view.add_item(dropdown)

        await interaction.response.edit_message(
            content="Choose a target to sue:",
            view=view
        )

    @discord.ui.button(label="Entertainers", style=discord.ButtonStyle.success)
    async def entertainers(self, interaction: discord.Interaction, button: Button):

        # FINAL MESSAGE ALWAYS THE SAME
        await self.message.edit(
            content=f"**{self.ctx.author.display_name} hired a specialist...**",
            view=None
        )

        cmd = self.bot.get_command("entertainers")
        if cmd:
            await self.ctx.invoke(cmd, amount=str(self.amount))

        await interaction.response.defer()


class Hire(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="hire")
    async def hire(self, ctx, amount: int):
        view = HireJobView(ctx, amount, self.bot)
        msg = await ctx.send(
            f"What kind of specialist do you want to hire for **${amount}**?",
            view=view
        )
        view.message = msg


async def setup(bot):
    await bot.add_cog(Hire(bot))
