import discord
from discord.ext import commands
import random

# Import showdown logic (no circular import now)
from .showdown import do_holdem


SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def generate_deck():
    return [f"{rank}{suit}" for suit in SUITS for rank in RANKS]


def card_value(rank):
    if rank.isdigit():
        return int(rank)
    return {"J": 11, "Q": 12, "K": 13, "A": 14}[rank]


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

        try:
            await self.message.edit(view=None)
        except:
            pass

        deck = generate_deck()
        random.shuffle(deck)

        self.player_states = {}
        pot = 0
        dealer_added = False

        # Dealer fallback
        if len(self.players) < 2:
            dealer_member = self.ctx.guild.me
            self.players[dealer_member.id] = dealer_member
            dealer_added = True

            await self.message.edit(
                embed=discord.Embed(
                    title="🃏 Hold'em",
                    description="Not enough players joined. Starting heads-up vs dealer.",
                    color=discord.Color.orange()
                )
            )

        # Assign cards + balances
        for pid, member in self.players.items():
            if pid == self.ctx.guild.me.id and dealer_added:
                bal = 0
                current = 0
                self.player_states[pid] = {
                    "member": member,
                    "starting_balance": bal,
                    "current_balance": current,
                    "cards": [],
                    "active": True,
                    "is_dealer": pid == self.ctx.guild.me.id and dealer_added
                }
                continue

            bal, _ = await self.load_callback(member)
            current = bal - self.betvalue
            pot += self.betvalue

            # Deal two unique hole cards per player
            hole1 = deck.pop()
            hole2 = deck.pop()
            self.player_states[pid] = {
                "member": member,
                "starting_balance": bal,
                "current_balance": current,
                "cards": [hole1, hole2],
                "active": True,
                "is_dealer": pid == self.ctx.guild.me.id and dealer_added
            }

        # DM player cards
        for state in self.player_states.values():
            if state.get("is_dealer"):
                continue
            try:
                dm = await state["member"].create_dm()
                await dm.send(f"🃏 Your hole cards:\n{state['cards'][0]}  {state['cards'][1]}")
            except:
                pass

        # Community cards
        community = [deck.pop(), deck.pop(), deck.pop(), deck.pop(), deck.pop()]

        desc = (
            f"Players:\n" +
            "\n".join(
                f"- {s['member'].mention}{' (Dealer)' if s.get('is_dealer') else ''}"
                for s in self.player_states.values()
            ) +
            "\n\n" +
            f"Community Cards: {' '.join(community)}\n"
            f"Pot: {pot}\n\n"
            "Hold'em gameplay begins with simplified betting rounds.\n"
            "This version will auto-evaluate community cards at end.\n"
            "Hole cards shown to host only as simplified version."
        )

        table_embed = discord.Embed(
            title="🃏 Hold'em Table",
            description=desc,
            color=discord.Color.deep_red()
        )

        await self.message.edit(embed=table_embed)

        # Run showdown
        await do_holdem(self, community, pot)


# Register game
from cogs.games.registry import register_game
register_game("Holdem", HoldemGame)