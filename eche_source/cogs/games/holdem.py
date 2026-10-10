import discord
from discord.ext import commands

from cogs.games._core import release_view
from .showdown import do_holdem, prepare_table, send_hole_cards, table_text


class HoldemGame:
    description = "Texas Hold'em poker with 2-minute lobby and simplified showdown."
    
    # Odds options exposed to Bet GUI
    ODDS_OPTIONS = [
        ("Showdown", 1)
    ]

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance, load_callback, save_callback, message):
        desc = (
            f"**Host:** {ctx.author.mention}\n"
            f"**Entry Bet:** {betvalue}\n"
            f"**Players Joined (1/10):**\n- {ctx.author.mention}\n"
        )

        embed = discord.Embed(
            title="🃏 Hold'em Lobby",
            description=desc,
            color=discord.Color.purple()
        )

        view = HoldemLobbyView(ctx, betvalue, load_callback, save_callback, message)

        await message.edit(embed=embed, view=view)


# ---------------------------------------------------------
# HOLDEM LOBBY VIEW
# ---------------------------------------------------------

class HoldemLobbyView(discord.ui.View):
    def __init__(self, ctx, betvalue, load_callback, save_callback, message):
        super().__init__(timeout=180)
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
        self.timeout = 180
        return True

    async def join_table(self, interaction):
        await interaction.response.defer()
        user = interaction.user

        if user.id in self.players:
            return
        if len(self.players) >= 10:
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
            f"**Players Joined ({len(self.players)}/10):**\n"
        )
        for m in self.players.values():
            desc += f"- {m.mention}\n"

        embed = discord.Embed(
            title="🃏 Hold'em Lobby",
            description=desc,
            color=discord.Color.purple()
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
                    title="🃏 Hold'em",
                    description=f"The deal failed: {exc}",
                    color=discord.Color.red(),
                )
            )
            return
        if community is None:
            await self.message.edit(
                embed=discord.Embed(
                    title="🃏 Hold'em",
                    description="\n".join(notes) or "The table could not start.",
                    color=discord.Color.orange(),
                )
            )
            return

        await send_hole_cards(self, notes)
        table_embed = discord.Embed(
            title="🃏 Hold'em Table",
            description=table_text(
                self,
                community,
                pot,
                notes,
                "Hole cards are in each player's DMs. Showdown is next.",
            ),
            color=discord.Color.deep_red(),
        )
        await self.message.edit(embed=table_embed)
        await do_holdem(self, community, pot)


# Register game
from cogs.games.registry import register_game
register_game("Holdem", HoldemGame)