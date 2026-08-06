import random
import discord
from discord.ui import View, Button, Select
from cogs.games._core import register_game


# ---------------------------------------------------------
# ⭐ ODDS OPTIONS FOR BET GUI
# ---------------------------------------------------------
ODDS_OPTIONS = [
    ("Classic Slots", 1),
    ("Advanced Slots", 2),
    ("High‑Roller Slots", 3)
]


class SlotsGame:
    description = "🎰 Spin the reels! Three slot machine modes based on odds."
    supports_odds = True

    ODDS_OPTIONS = ODDS_OPTIONS

    @staticmethod
    async def usage(ctx):
        embed = discord.Embed(
            title="🎰 Slots",
            description=(
                "**Three slot machine modes based on odds:**\n"
                "• Odds 1 → Classic Slots (low volatility)\n"
                "• Odds 2 → Advanced Slots (medium volatility)\n"
                "• Odds 3 → High‑Roller Slots (high volatility + jackpot)\n\n"
                "Use the GUI: `?bet <amount>`"
            ),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance, load_callback, save_callback, message):
        session = SlotSession(
            ctx=ctx,
            odds=int(odds),
            bet=betvalue,
            starting_balance=starting_balance,
            save_callback=save_callback,
            message=message
        )
        await session.start()


# ---------------------------------------------------------
# SESSION VIEW
# ---------------------------------------------------------

class SlotSessionView(View):
    def __init__(self, session):
        super().__init__(timeout=120)
        self.session = session

        self.add_item(OddsDropdown(session))
        self.add_item(PlayAgainButton(session))
        self.add_item(CashOutButton(session))

    async def interaction_check(self, interaction):
        # Reset timeout whenever user interacts
        self.timeout = 120
        return True

    async def on_timeout(self):
        # Build final embed
        embed = discord.Embed(
            title=f"{self.session.machine.title} — Concluded",
            description=(
                f"**Bet:** {self.session.bet}\n"
                f"**Final Balance:** {self.session.balance:.2f}\n"
                f"**Player:** {self.session.ctx.author.mention}"
            ),
            color=discord.Color.gold()
        )

        # Edit existing message — no new message
        try:
            await self.session.message.edit(embed=embed, view=None)
        except:
            pass


class OddsDropdown(Select):
    def __init__(self, session):
        self.session = session
        options = [
            discord.SelectOption(label="Classic Slots", value="1"),
            discord.SelectOption(label="Advanced Slots", value="2"),
            discord.SelectOption(label="High‑Roller Slots", value="3"),
        ]
        super().__init__(placeholder=f"Odds {session.odds}", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.session.odds = int(self.values[0])
        self.placeholder = f"Odds {self.values[0]}"
        await self.session.update_message()


class PlayAgainButton(Button):
    def __init__(self, session):
        super().__init__(label="Play Again", style=discord.ButtonStyle.green)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.session.spin()


class CashOutButton(Button):
    def __init__(self, session):
        super().__init__(label="Cash Out", style=discord.ButtonStyle.red)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.session.cash_out()


# ---------------------------------------------------------
# SLOT SESSION LOGIC
# ---------------------------------------------------------

class SlotSession:
    def __init__(self, ctx, odds, bet, starting_balance, save_callback, message):
        self.ctx = ctx
        self.odds = max(1, min(3, odds))
        self.bet = bet

        self.starting_balance = starting_balance
        self.balance = starting_balance

        self.save_callback = save_callback
        self.message = message

        if self.odds == 1:
            self.machine = ClassicSlots()
        elif self.odds == 2:
            self.machine = AdvancedSlots()
        else:
            self.machine = HighRollerSlots()

    async def start(self):
        await self.spin()

    async def spin(self):
        self.balance -= self.bet

        r1 = random.choice(self.machine.symbols)
        r2 = random.choice(self.machine.symbols)
        r3 = random.choice(self.machine.symbols)

        board = f"🎰 | {r1} | {r2} | {r3} |"

        win = 0
        reason = "No win."

        if self.machine.jackpot_chance > 0 and random.random() < self.machine.jackpot_chance:
            win = self.bet * self.machine.jackpot_mult
            reason = f"JACKPOT! ({self.machine.jackpot_mult}x)"
        else:
            if r1 == r2 == r3:
                mult = self.machine.payouts.get(r1, 3)
                win = self.bet * mult
                reason = f"3 in a row! ({mult}x)"
            elif r1 == r2 or r2 == r3 or r1 == r3:
                win = self.bet * 2
                reason = "2 matching symbols! (2x)"

        self.balance += win

        if win > 0:
            color = discord.Color.green()
            result_text = f"You won **{win:,}** coins!"
        else:
            color = discord.Color.red()
            result_text = f"You lost **{self.bet:,}** coins."

        embed = discord.Embed(
            title=self.machine.title,
            description=f"{board}\n\n**{reason}**\n{result_text}\n",
            color=color
        )
        embed.set_footer(text=f"Session balance: {self.balance:.2f} coins")

        view = SlotSessionView(self)
        await self.message.edit(embed=embed, view=view)

    async def update_message(self):
        embed = self.message.embeds[0]
        embed.set_footer(text=f"Session balance: {self.balance:.2f} coins")
        await self.message.edit(embed=embed, view=SlotSessionView(self))

    async def cash_out(self):
        await self.save_callback(self.ctx.author, self.balance)

        old = self.message.embeds[0]
        embed = discord.Embed.from_dict(old.to_dict())
        embed.set_footer(text=f"Final balance: {self.balance:.2f} coins")

        # Replace GUI with final embed — no new message
        await self.message.edit(embed=embed, view=None)


# ---------------------------------------------------------
# SLOT MACHINE VARIANTS
# ---------------------------------------------------------

class ClassicSlots:
    title = "🎰 Classic Slots"

    def __init__(self):
        self.symbols = ["🍒", "🍋", "🍇", "🍉"]
        self.payouts = {"🍒": 3, "🍋": 4, "🍇": 5, "🍉": 6}
        self.jackpot_chance = 0
        self.jackpot_mult = 0


class AdvancedSlots:
    title = "💎 Advanced Slots"

    def __init__(self):
        self.symbols = ["🍒", "🍋", "🍇", "🍉", "⭐", "💎"]
        self.payouts = {"🍒": 3, "🍋": 4, "🍇": 5, "🍉": 6, "⭐": 10, "💎": 15}
        self.jackpot_chance = 0
        self.jackpot_mult = 0


class HighRollerSlots:
    title = "🔥 High‑Roller Slots"

    def __init__(self):
        self.symbols = ["🍒", "🍋", "🍇", "🍉", "⭐", "💎", "👑"]
        self.payouts = {"🍒": 4, "🍋": 5, "🍇": 6, "🍉": 8, "⭐": 12, "💎": 18, "👑": 25}
        self.jackpot_chance = 0.03
        self.jackpot_mult = 50


# Register game
register_game("Slots", SlotsGame)
