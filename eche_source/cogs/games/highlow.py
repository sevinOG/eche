import discord
import random
import asyncio
from discord.ui import View
from cogs.games._core import register_game

ODDS_TABLE = {
    1: {"win": 1.5, "lose": 1},
    2: {"win": 2.0, "lose": 1},
    3: {"win": 3.0, "lose": 2},
}

ODDS_OPTIONS = [
    ("Odds 1", 1),
    ("Odds 2", 2),
    ("Odds 3", 3),
]


class HighLowGame:
    description = "Guess HIGHER or LOWER with odds‑based payouts."
    supports_odds = True
    ODDS_OPTIONS = ODDS_OPTIONS

    @staticmethod
    async def usage(ctx):
        embed = discord.Embed(
            title="🎲 High-Low (Odds Mode)",
            description=(
                "**Guess HIGHER or LOWER.**\n\n"
                "**Odds:**\n"
                "• **1** → Win **1.5×**, Lose **1×**\n"
                "• **2** → Win **2×**, Lose **1×**\n"
                "• **3** → Win **3×**, Lose **2×**\n"
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance, load_callback, save_callback, message):
        if odds not in ODDS_TABLE:
            return await ctx.send("❌ Odds must be **1**, **2**, or **3**.")

        first_number = random.randint(1, 100)

        embed = discord.Embed(
            title="🎲 High-Low",
            description=(
                f"**Odds:** {odds}\n"
                f"**Bet:** {betvalue}\n"
                f"**Session Balance:** {starting_balance}\n"
                f"**Bank Start:** {starting_balance}\n\n"
                f"Your first number is:\n"
                f"**{first_number}**\n\n"
                "Will the next number be **higher** or **lower**?"
            ),
            color=discord.Color.gold()
        )

        view = HighLowButtons(
            ctx=ctx,
            odds=odds,
            betvalue=betvalue,
            session_balance=starting_balance,  # running total = current bank value
            starting_balance=starting_balance,  # remember where we started
            first_number=first_number,
            save_callback=save_callback
        )

        await message.edit(embed=embed, view=view)
        view.message = message


class HighLowButtons(View):
    def __init__(self, ctx, odds, betvalue, session_balance, starting_balance, first_number, save_callback):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.odds = odds
        self.betvalue = betvalue
        self.session_balance = session_balance
        self.starting_balance = starting_balance
        self.first_number = first_number
        self.save_callback = save_callback
        self.message = None
        self._finalized = False
        self._lock = asyncio.Lock()

    async def _finalize_balance(self, final_balance: float):
        """Ensures bank is only saved ONCE - prevents cycling bug."""
        async with self._lock:
            if self._finalized:
                return
            self._finalized = True
            try:
                await self.save_callback(self.ctx.author, final_balance)
            except Exception as e:
                print(f"[highlow] save failed: {e}")

    async def interaction_check(self, interaction):
        self.timeout = 30
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="HIGHER", style=discord.ButtonStyle.green)
    async def higher(self, interaction, button):
        await self.resolve(interaction, "higher")

    @discord.ui.button(label="LOWER", style=discord.ButtonStyle.red)
    async def lower(self, interaction, button):
        await self.resolve(interaction, "lower")

    async def resolve(self, interaction, guess):
        await interaction.response.defer()

        for child in self.children:
            child.disabled = True

        second_number = random.randint(1, 100)
        odds_data = ODDS_TABLE[self.odds]

        win = (
            (guess == "higher" and second_number > self.first_number) or
            (guess == "lower" and second_number < self.first_number)
        )

        if win:
            delta = int(self.betvalue * odds_data["win"])
            self.session_balance += delta
            embed = discord.Embed(
                title="🎉 You Won!",
                description=(
                    f"First: **{self.first_number}** → Second: **{second_number}**\n"
                    f"**Odds:** {self.odds} | Won **{delta}**\n"
                    f"Session: **{self.session_balance}** (Started: {self.starting_balance})\n"
                    f"Net: **{self.session_balance - self.starting_balance:+}**"
                ),
                color=discord.Color.green()
            )
        else:
            delta = int(self.betvalue * odds_data["lose"])
            self.session_balance -= delta
            embed = discord.Embed(
                title="💀 You Lost!",
                description=(
                    f"First: **{self.first_number}** → Second: **{second_number}**\n"
                    f"**Odds:** {self.odds} | Lost **{delta}**\n"
                    f"Session: **{self.session_balance}** (Started: {self.starting_balance})\n"
                    f"Net: **{self.session_balance - self.starting_balance:+}**"
                ),
                color=discord.Color.red()
            )

        end_view = HighLowEndButtons(
            ctx=self.ctx,
            odds=self.odds,
            betvalue=self.betvalue,
            session_balance=self.session_balance,
            starting_balance=self.starting_balance,
            save_callback=self.save_callback,
            message=self.message
        )

        await self.message.edit(embed=embed, view=end_view)

    async def on_timeout(self):
        # FIXED: Now saves on timeout, and only once
        await self._finalize_balance(self.session_balance)
        embed = discord.Embed(
            title="🎲 High-Low — Concluded (Timeout)",
            description=(
                f"**Odds:** {self.odds}\n"
                f"**Final Session Balance:** {self.session_balance}\n"
                f"**Net Change:** {self.session_balance - self.starting_balance:+}\n"
                f"**Player:** {self.ctx.author.mention}\n\n"
                "*Saved to bank due to inactivity.*"
            ),
            color=discord.Color.gold()
        )
        try:
            await self.message.edit(embed=embed, view=None)
        except:
            pass


class HighLowEndButtons(View):
    def __init__(self, ctx, odds, betvalue, session_balance, starting_balance, save_callback, message):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.odds = odds
        self.betvalue = betvalue
        self.session_balance = session_balance
        self.starting_balance = starting_balance
        self.save_callback = save_callback
        self.message = message
        self._finalized = False
        self._lock = asyncio.Lock()

    async def _finalize_balance(self, final_balance: float):
        async with self._lock:
            if self._finalized:
                return
            self._finalized = True
            try:
                await self.save_callback(self.ctx.author, final_balance)
            except Exception as e:
                print(f"[highlow] save failed: {e}")

    async def interaction_check(self, interaction):
        self.timeout = 60
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.green)
    async def play_again(self, interaction, button):
        await interaction.response.defer()
        # No save yet - keep running total in memory
        await HighLowGame.start(
            ctx=self.ctx,
            odds=self.odds,
            betvalue=self.betvalue,
            starting_balance=self.session_balance,
            load_callback=None,
            save_callback=self.save_callback,
            message=self.message
        )

    @discord.ui.button(label="Take Winnings", style=discord.ButtonStyle.gray, emoji="💰")
    async def take_winnings(self, interaction, button):
        await interaction.response.defer()

        # FIXED: Guard against double-save
        await self._finalize_balance(self.session_balance)

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="🎲 High-Low — Concluded",
            description=(
                f"**Odds:** {self.odds}\n"
                f"**Final Session Balance:** {self.session_balance}\n"
                f"**Net Change:** {self.session_balance - self.starting_balance:+}\n"
                f"**Player:** {self.ctx.author.mention}\n\n"
                "*Bank updated.*"
            ),
            color=discord.Color.gold()
        )
        await self.message.edit(embed=embed, view=None)

    async def on_timeout(self):
        # FIXED: Was missing - now saves if user AFKs on this screen
        await self._finalize_balance(self.session_balance)
        embed = discord.Embed(
            title="🎲 High-Low — Concluded (Timeout)",
            description=(
                f"**Odds:** {self.odds}\n"
                f"**Final Session Balance:** {self.session_balance}\n"
                f"**Net Change:** {self.session_balance - self.starting_balance:+}\n"
                f"**Player:** {self.ctx.author.mention}\n\n"
                "*Saved to bank due to inactivity.*"
            ),
            color=discord.Color.gold()
        )
        try:
            await self.message.edit(embed=embed, view=None)
        except:
            pass


register_game("High/Low", HighLowGame)
