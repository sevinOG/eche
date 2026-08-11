import discord
from discord.ext import commands
import os

from cogs.games.registry import GAME_REGISTRY
from core.opt_in_manager import load_opted_in, opt_in

HOME_SERVER_ID = int(os.getenv("HOME_SERVER_ID", "0"))
ECONOMY_CHANNEL_NAME = "economy"

LOSS_FLOOR = -5000


# ---------------------------------------------------------
# GUI COMPONENTS
# ---------------------------------------------------------

class GameSelectButton(discord.ui.Button):
    def __init__(self, game_name, parent_view):
        super().__init__(label=game_name, style=discord.ButtonStyle.primary)
        self.game_name = game_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.parent_view.selected_game = self.game_name

        for child in self.parent_view.children:
            if isinstance(child, discord.ui.Button):
                child.style = (
                    discord.ButtonStyle.success
                    if child.label == self.game_name
                    else discord.ButtonStyle.primary
                )

        game_class = GAME_REGISTRY[self.game_name]
        odds_list = getattr(game_class, "ODDS_OPTIONS", [("Odds 1", 1)])

        self.parent_view.update_odds_dropdown(odds_list)
        self.parent_view.odds_dropdown.placeholder = self.game_name

        await self.parent_view.update_message()


class OddsDropdown(discord.ui.Select):
    def __init__(self, parent_view, odds_list):
        options = [
            discord.SelectOption(label=label, value=str(value))
            for label, value in odds_list
        ]
        super().__init__(placeholder="Game Type", options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected = self.values[0]
        self.parent_view.selected_odds = int(selected)
        selected_label = next(
            (opt.label for opt in self.options if opt.value == selected),
            f"Odds {selected}"
        )
        self.placeholder = selected_label
        await self.parent_view.update_message()


class StartGameButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="Start Game", style=discord.ButtonStyle.green, disabled=True)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        game_name = self.parent_view.selected_game
        game_class = GAME_REGISTRY[game_name]

        summary_embed = discord.Embed(
            title="🎮 Game Started",
            description=f"{interaction.user.mention} started **{game_name}**",
            color=discord.Color.gold()
        )
        self.parent_view.summary_embed = summary_embed
        await self.parent_view.message.edit(embed=summary_embed)
        self.parent_view.update_message = lambda *args, **kwargs: None

        try:
            await self.parent_view.message.edit(view=None)
        except:
            pass

        await game_class.start(
            ctx=self.parent_view.ctx,
            odds=self.parent_view.selected_odds,
            betvalue=self.parent_view.betvalue,
            starting_balance=self.parent_view.balance,
            load_callback=self.parent_view.load_callback,
            save_callback=self.parent_view.save_callback,
            message=self.parent_view.message
        )


class BetGUI(discord.ui.View):
    def __init__(self, ctx, betvalue, balance, save_callback):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.betvalue = betvalue
        self.balance = balance
        self.save_callback = save_callback
        self.selected_game = None
        self.selected_odds = 1
        self.message = None
        self.summary_embed = None
        self.load_callback = ctx.cog.load_balance

        self.odds_dropdown = OddsDropdown(self, [("Odds 1", 1)])
        self.add_item(self.odds_dropdown)

        for gname in GAME_REGISTRY.keys():
            self.add_item(GameSelectButton(gname, self))

        self.start_button = StartGameButton(self)
        self.add_item(self.start_button)

    def update_odds_dropdown(self, odds_list):
        self.remove_item(self.odds_dropdown)
        self.odds_dropdown = OddsDropdown(self, odds_list)
        self.add_item(self.odds_dropdown)

    async def interaction_check(self, interaction):
        self.timeout = 300
        return True

    async def update_message(self):
        if self.summary_embed:
            await self.message.edit(embed=self.summary_embed, view=self)
            return

        ready = self.selected_game is not None and self.selected_odds is not None
        self.start_button.disabled = not ready
        odds_display = self.odds_dropdown.placeholder

        embed = discord.Embed(
            title="🎲 Betting Menu",
            description=(
                f"**Balance:** {self.balance}\n"
                f"**Bet Amount:** {self.betvalue}\n"
                f"**Game Type:** {odds_display}\n\n"
                "Select a game to begin."
            ),
            color=discord.Color.gold()
        )
        await self.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        try:
            await self.message.edit(view=None)
        except:
            pass


# ---------------------------------------------------------
# BET COG - FIXED BALANCE LOGIC
# ---------------------------------------------------------

class Bet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def load_balance(self, member):
        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return 0, True

        category = discord.utils.get(guild.categories, name=f"memory-{member.id}")
        if category is None:
            category = await guild.create_category(f"memory-{member.id}")

        economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
        if economy_channel is None:
            economy_channel = await category.create_text_channel(ECONOMY_CHANNEL_NAME)

        # FIXED: dedupe and get newest BANK DATA pin
        try:
            pins = await economy_channel.pins()
        except:
            pins = []

        bank_messages = [m for m in pins if m.content.startswith("BANK DATA")]
        
        # Clean up duplicates - keep newest
        if len(bank_messages) > 1:
            bank_messages.sort(key=lambda m: m.created_at, reverse=True)
            for dup in bank_messages[1:]:
                try:
                    await dup.delete()
                except:
                    pass
            bank_messages = bank_messages[:1]

        if not bank_messages:
            new_msg = await economy_channel.send("BANK DATA\n0.00\nSTARTER:0")
            try:
                await new_msg.pin()
            except:
                pass
            return 0.00, True

        bank_message = bank_messages[0]
        lines = bank_message.content.splitlines()
        try:
            bal = round(float(lines[1].strip()), 2)
        except:
            bal = 0.0
        starter_flag = lines[2].strip() if len(lines) > 2 else "STARTER:0"
        first_time = ("STARTER:0" in starter_flag)
        return bal, first_time

    async def save_balance(self, member, new_value):
        # Enforce loss floor
        if new_value < LOSS_FLOOR:
            new_value = LOSS_FLOOR

        # FIXED: Remove the broken 0 -> -1 logic that caused cycling
        # Only apply negative mode conversion if explicitly at exactly 0 and previous wasn't already negative
        # Better: keep 0 as 0, let bet command handle -1 conversion
        rounded = round(float(new_value), 2)

        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return
        category = discord.utils.get(guild.categories, name=f"memory-{member.id}")
        if category is None:
            return
        economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
        if economy_channel is None:
            return

        try:
            pins = await economy_channel.pins()
        except:
            pins = []

        bank_messages = [m for m in pins if m.content.startswith("BANK DATA")]

        if len(bank_messages) > 1:
            bank_messages.sort(key=lambda m: m.created_at, reverse=True)
            for dup in bank_messages[1:]:
                try:
                    await dup.delete()
                except:
                    pass
            bank_messages = bank_messages[:1]

        if not bank_messages:
            new_msg = await economy_channel.send(f"BANK DATA\n{rounded:.2f}\nSTARTER:1")
            try:
                await new_msg.pin()
            except:
                pass
            return

        bank_message = bank_messages[0]
        lines = bank_message.content.splitlines()
        starter_flag = lines[2].strip() if len(lines) > 2 else "STARTER:1"

        try:
            await bank_message.edit(content=f"BANK DATA\n{rounded:.2f}\n{starter_flag}")
        except Exception as e:
            # If edit fails, create new one (pin may have been deleted)
            try:
                new_msg = await economy_channel.send(f"BANK DATA\n{rounded:.2f}\n{starter_flag}")
                await new_msg.pin()
            except:
                pass

    @commands.command(name="bet")
    async def bet(self, ctx, betvalue=None):
        newly_opted = await opt_in(self.bot, ctx.author)
        if newly_opted:
            await ctx.send("🎉 You've been automatically opted into the economy system.")

        balance, first_time = await self.load_balance(ctx.author)

        if first_time and balance == 0:
            balance = 500.00
            await self.save_balance(ctx.author, balance)

        if balance == 0:
            balance = -1.00
            await self.save_balance(ctx.author, balance)
            await ctx.send(
                "⚠ Your balance was 0, so you've been moved into negative mode.\n"
                "You may now bet up to 70 coins."
            )

        if betvalue is None:
            betvalue = 500
        else:
            if not betvalue.isdigit():
                return await ctx.send("❌ Bet amount must be a number.")
            betvalue = int(betvalue)

        if balance >= 0 and betvalue > balance:
            return await ctx.send("❌ You cannot bet more than your balance.")

        if balance < 0 and betvalue > 70:
            return await ctx.send("❌ When below 0, max bet is **70**.")

        view = BetGUI(ctx, betvalue, balance, self.save_balance)

        embed = discord.Embed(
            title="🎲 Betting Menu",
            description=(
                f"**Balance:** {balance}\n"
                f"**Bet Amount:** {betvalue}\n"
                f"**Game Type:** Game Type\n\n"
                "Select a game to begin."
            ),
            color=discord.Color.gold()
        )

        msg = await ctx.send(embed=embed, view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(Bet(bot))
