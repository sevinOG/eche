# games/simon.py

import discord
import random
from datetime import timedelta
from discord.ui import View, Select, Button
from cogs.games._core import register_game

COLORS = [
    ("red", "🔴", discord.ButtonStyle.red),
    ("orange", "🟠", discord.ButtonStyle.blurple),
    ("yellow", "🟡", discord.ButtonStyle.gray),
    ("green", "🟢", discord.ButtonStyle.green),
    ("blue", "🔵", discord.ButtonStyle.blurple),
    ("indigo", "🟣", discord.ButtonStyle.blurple),
    ("violet", "🟪", discord.ButtonStyle.gray),
]

DIFFICULTY_SETTINGS = {
    1: {"flash": 1.0, "delay": 0.6, "timeout": 30, "mult": 1.15},
    2: {"flash": 0.7, "delay": 0.4, "timeout": 20, "mult": 1.20},
    3: {"flash": 0.45, "delay": 0.25, "timeout": 12, "mult": 1.30},
}

# ---------------------------------------------------------
# ⭐ ODDS OPTIONS FOR BET GUI (Speed 1–3)
# ---------------------------------------------------------
ODDS_OPTIONS = [
    ("Speed 1", 1),
    ("Speed 2", 2),
    ("Speed 3", 3),
]


class SevinSaysGame:
    description = "A push‑your‑luck memory game where you repeat a growing color sequence."
    supports_odds = True
    ODDS_OPTIONS = ODDS_OPTIONS

    @staticmethod
    async def usage(ctx):
        embed = discord.Embed(
            title="🧠 Sevin says",
            description=(
                "Repeat the growing color sequence.\n\n"
                "**Betting Rules:**\n"
                "• Lose only your initial bet.\n"
                "• Pot grows each round.\n"
                "• Choose Continue or Cash Out."
            ),
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance, load_callback, save_callback, message):
        odds = max(1, min(3, int(odds)))
        settings = DIFFICULTY_SETTINGS[odds]

        pot = float(betvalue)
        sequence = [random.choice(COLORS)[0]]

        view = SevinSaysButtons(
            ctx=ctx,
            sequence=sequence,
            pot=pot,
            odds=odds,
            settings=settings,
            initial_bet=betvalue,
            save_callback=save_callback,
            starting_balance=starting_balance,
        )

        embed = discord.Embed(
            title="🧠 Sevin says",
            description="Watch the sequence...",
            color=discord.Color.purple()
        )

        await message.edit(embed=embed, view=None)
        view.message = message

        await view.show_sequence()
        await view.message.edit(embed=view.make_prompt_embed(), view=view)


class SevinSaysButtons(View):
    def __init__(self, ctx, sequence, pot, odds, settings, initial_bet, save_callback, starting_balance):
        super().__init__(timeout=settings["timeout"])
        self.ctx = ctx
        self.sequence = sequence
        self.user_progress = []
        self.message = None
        self.pot = pot
        self.odds = odds
        self.settings = settings
        self.initial_bet = initial_bet
        self.save_callback = save_callback
        self.starting_balance = starting_balance
        self.balance = starting_balance

        for name, emoji, style in COLORS:
            self.add_item(ColorButton(name, emoji, style, self))

    async def interaction_check(self, interaction):
        # Reset timeout on interaction
        self.timeout = self.settings["timeout"]
        return interaction.user.id == self.ctx.author.id

    async def handle_press(self, interaction, color):
        self.user_progress.append(color)
        index = len(self.user_progress) - 1

        if self.user_progress[index] != self.sequence[index]:
            await self.fail(interaction)
            return

        if len(self.user_progress) == len(self.sequence):
            await interaction.response.defer()
            await self.round_complete()
            return

        await interaction.response.defer()

    async def round_complete(self):
        for child in self.children:
            child.disabled = True

        self.pot = round(self.pot * self.settings["mult"], 2)

        embed = discord.Embed(
            title="🧠 Round Complete!",
            description=f"Current pot: **{self.pot:.2f} coins**",
            color=discord.Color.gold()
        )

        await self.message.edit(embed=embed, view=CashOutView(self))

    async def next_round(self):
        self.user_progress = []
        self.sequence.append(random.choice(COLORS)[0])

        for child in self.children:
            child.disabled = False

        await self.message.edit(embed=self.make_wait_embed(), view=None)
        await self.show_sequence()
        await self.message.edit(embed=self.make_prompt_embed(), view=self)

    async def fail(self, interaction):
        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="💀 Game Over!",
            description=f"You lost your initial bet of **{self.initial_bet} coins**.",
            color=discord.Color.red()
        )

        new_balance = round(self.starting_balance - self.initial_bet, 2)
        await self.save_callback(self.ctx.author, new_balance)

        # Replace GUI with final embed — no new message
        await interaction.response.edit_message(embed=embed, view=None)

    async def show_sequence(self):
        for color in self.sequence:
            embed = discord.Embed(
                title="🧠 Sevin says",
                description=f"**{color.upper()}**",
                color=discord.Color.purple()
            )
            await self.message.edit(embed=embed, view=None)
            await discord.utils.sleep_until(
                discord.utils.utcnow() + timedelta(seconds=self.settings["flash"])
            )

        await discord.utils.sleep_until(
            discord.utils.utcnow() + timedelta(seconds=self.settings["delay"])
        )

    def make_prompt_embed(self):
        return discord.Embed(
            title="🧠 Sevin says",
            description="Repeat the sequence!",
            color=discord.Color.purple()
        )

    def make_wait_embed(self):
        return discord.Embed(
            title="🧠 Sevin says",
            description="Watch the sequence...",
            color=discord.Color.purple()
        )

    async def on_timeout(self):
        # Final embed on timeout
        embed = discord.Embed(
            title="🧠 Sevin Says — Concluded",
            description=(
                f"**Speed:** {self.odds}\n"
                f"**Final Pot:** {self.pot:.2f}\n"
                f"**Player:** {self.ctx.author.mention}"
            ),
            color=discord.Color.gold()
        )

        try:
            await self.message.edit(embed=embed, view=None)
        except:
            pass


class CashOutView(View):
    def __init__(self, controller):
        super().__init__(timeout=20)
        self.controller = controller

    async def interaction_check(self, interaction):
        # Reset timeout on interaction
        self.timeout = 20
        return interaction.user.id == self.controller.ctx.author.id

    @discord.ui.button(label="💰 TAKE WINNINGS", style=discord.ButtonStyle.green)
    async def take(self, interaction, button):
        winnings = round(self.controller.pot, 2)

        embed = discord.Embed(
            title="💰 CASH OUT!",
            description=f"You won **{winnings:.2f} coins**!",
            color=discord.Color.green()
        )

        new_balance = round(self.controller.starting_balance + winnings, 2)
        await self.controller.save_callback(self.controller.ctx.author, new_balance)

        for child in self.children:
            child.disabled = True

        # Replace GUI with final embed — no new message
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="🔁 CONTINUE", style=discord.ButtonStyle.blurple)
    async def cont(self, interaction, button):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(view=self)
        await self.controller.next_round()


class ColorButton(discord.ui.Button):
    def __init__(self, name, emoji, style, controller):
        super().__init__(label=emoji, style=style)
        self.name = name
        self.controller = controller

    async def callback(self, interaction):
        await self.controller.handle_press(interaction, self.name)


register_game("Sevin Says", SevinSaysGame)
