import discord
from discord.ext import commands

from .showdown import do_showdown
from cogs.games._core import release_view
from cogs.games.showdown import prepare_table, send_hole_cards, table_text


# ---------------------------------------------------------
# ⭐ ODDS OPTIONS FOR BET GUI
# ---------------------------------------------------------
# These appear in the dropdown when the user selects "poker"
# Format: (label, odds_value)
ODDS_OPTIONS = [
    ("Showdown", 1)
]


# ---------------------------------------------------------
# POKER LOBBY VIEW
# ---------------------------------------------------------

class PokerLobbyView(discord.ui.View):
    def __init__(self, ctx, betvalue, load_callback, save_callback, message):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.betvalue = betvalue
        self.load_callback = load_callback
        self.save_callback = save_callback
        self.message = message

        self.host = ctx.author
        self.players = {self.host.id: self.host}
        self.player_states = {}
        self.started = False

        # Buttons
        self.join_button = discord.ui.Button(label="Join Table", style=discord.ButtonStyle.primary)
        self.join_button.callback = self.join_table
        self.add_item(self.join_button)

        self.leave_button = discord.ui.Button(label="Leave Table", style=discord.ButtonStyle.secondary)
        self.leave_button.callback = self.leave_table
        self.add_item(self.leave_button)

        self.start_button = discord.ui.Button(label="Start Now (Host)", style=discord.ButtonStyle.success)
        self.start_button.callback = self.start_now
        self.add_item(self.start_button)

    async def interaction_check(self, interaction):
        self.timeout = 120
        return True

    async def join_table(self, interaction):
        await interaction.response.defer()
        user = interaction.user

        if user.id in self.players:
            return
        if len(self.players) >= 5:
            return

        self.players[user.id] = user
        await self.update_lobby()

    async def leave_table(self, interaction):
        await interaction.response.defer()
        user = interaction.user

        if user.id == self.host.id:
            return

        if user.id in self.players:
            del self.players[user.id]
            await self.update_lobby()

    async def start_now(self, interaction):
        await interaction.response.defer()
        if interaction.user.id != self.host.id:
            return
        await self.start_game()

    async def update_lobby(self):
        desc = (
            f"**Host:** {self.host.mention}\n"
            f"**Entry Bet:** {self.betvalue}\n"
            f"**Players Joined ({len(self.players)}/5):**\n"
        )
        for m in self.players.values():
            desc += f"- {m.mention}\n"

        embed = discord.Embed(
            title="🃏 Poker Lobby",
            description=desc,
            color=discord.Color.blurple()
        )
        await self.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        if not self.started:
            await self.start_game()

    async def start_game(self):
        if self.started:
            return
        self.started = True
        release_view(self)

        try:
            await self.message.edit(view=None)
        except Exception:
            pass

        try:
            community, pot, notes = await prepare_table(self)
        except Exception as exc:
            await self.message.edit(
                embed=discord.Embed(
                    title="🃏 Poker",
                    description=f"The deal failed: {exc}",
                    color=discord.Color.red(),
                )
            )
            return
        if community is None:
            await self.message.edit(
                embed=discord.Embed(
                    title="🃏 Poker",
                    description="\n".join(notes) or "The table could not start.",
                    color=discord.Color.orange(),
                )
            )
            return

        await send_hole_cards(self, notes)
        table_embed = discord.Embed(
            title="🃏 Poker Table",
            description=table_text(
                self,
                community,
                pot,
                notes,
                "Hole cards are in each player's DMs. Showdown is next.",
            ),
            color=discord.Color.blue(),
        )
        await self.message.edit(embed=table_embed)
        await do_showdown(self, community, pot)


# ---------------------------------------------------------
# PokerGame wrapper
# ---------------------------------------------------------

class PokerGame:
    description = "Multiplayer poker with a 2-minute lobby and simplified showdown."

    # ⭐ Odds options exposed to Bet GUI
    ODDS_OPTIONS = ODDS_OPTIONS

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance, load_callback, save_callback, message):
        desc = (
            f"**Host:** {ctx.author.mention}\n"
            f"**Entry Bet:** {betvalue}\n"
            f"**Players Joined (1/5):**\n- {ctx.author.mention}\n"
        )

        embed = discord.Embed(
            title="🃏 Poker Lobby",
            description=desc,
            color=discord.Color.blurple()
        )

        view = PokerLobbyView(ctx, betvalue, load_callback, save_callback, message)

        await message.edit(embed=embed, view=view)


# Register game
from cogs.games.registry import register_game
register_game("Poker", PokerGame)
