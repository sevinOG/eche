import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import json
import os
import time

from cogs.economy.bank import Bank

LAWYER_COST = 50000
CASE_FILE = "lawsuits.json"


# -----------------------------
# CASE STORAGE HELPERS
# -----------------------------
def load_cases():
    if not os.path.exists(CASE_FILE):
        return {}
    try:
        with open(CASE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_cases(cases):
    try:
        with open(CASE_FILE, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=2)
    except:
        pass


# -----------------------------
# MODALS
# -----------------------------
class AccusationModal(Modal, title="Lawsuit Accusation"):
    def __init__(self, ctx, plaintiff, defendant, settlement_amount, bank, parent_view):
        super().__init__()
        self.ctx = ctx
        self.plaintiff = plaintiff
        self.defendant = defendant
        self.settlement_amount = settlement_amount
        self.bank = bank
        self.parent_view = parent_view

        self.accusation = TextInput(
            label="Accusation",
            placeholder="Describe the alleged wrongdoing in detail.",
            style=discord.TextStyle.long,
            required=True,
            max_length=2000,
        )

        self.add_item(self.accusation)

    async def on_submit(self, interaction: discord.Interaction):
        accusation_text = str(self.accusation.value)

        # Charge plaintiff when accusations are filed
        bal = await self.bank.load_bank(self.plaintiff)
        await self.bank.save_bank(self.plaintiff, bal - LAWYER_COST)

        # Create case entry
        cases = load_cases()
        msg_id = self.parent_view.message.id
        cases[str(msg_id)] = {
            "guild_id": self.ctx.guild.id,
            "channel_id": self.ctx.channel.id,
            "message_id": msg_id,
            "plaintiff_id": self.plaintiff.id,
            "defendant_id": self.defendant.id,
            "settlement_amount": self.settlement_amount,
            "accusation": accusation_text,
            "defense": None,
            "status": "awaiting_defense",
            "accusation_at": time.time(),
            "judge_text": None,
            "winner": None,
        }
        save_cases(cases)

        # Clean readable accusation block
        content = (
            f"**{self.plaintiff.display_name} has filed a lawsuit against {self.defendant.display_name}!**\n\n"
            f"⚖️ **PLAINTIFF:** {self.plaintiff.mention}\n"
            f"⚖️ **DEFENDANT:** {self.defendant.mention}\n\n"
            f"**ACCUSATION:**\n"
            f"{accusation_text}\n\n"
            f"🕒 24 hours remaining before default judgment.\n"
            f"Click below to submit your defense."
        )

        defense_view = DefenseView(
            ctx=self.ctx,
            plaintiff=self.plaintiff,
            defendant=self.defendant,
            settlement_amount=self.settlement_amount,
            bank=self.bank,
            message_id=msg_id,
        )

        await self.parent_view.message.edit(
            content=content,
            view=defense_view,
        )

        await interaction.response.send_message(
            "Accusations submitted. The defendant has been notified.",
            ephemeral=True,
        )


class DefenseModal(Modal, title="Submit Your Defense"):
    def __init__(self, ctx, plaintiff, defendant, settlement_amount, bank, message_id):
        super().__init__()
        self.ctx = ctx
        self.plaintiff = plaintiff
        self.defendant = defendant
        self.settlement_amount = settlement_amount
        self.bank = bank
        self.message_id = message_id

        self.defense = TextInput(
            label="Your Defense",
            placeholder="Explain your side of the story...",
            style=discord.TextStyle.long,
            required=True,
            max_length=2000,
        )

        self.add_item(self.defense)

    async def on_submit(self, interaction: discord.Interaction):
        defense_text = str(self.defense.value)

        cases = load_cases()
        case = cases.get(str(self.message_id))
        if not case or case.get("status") != "awaiting_defense":
            return await interaction.response.send_message(
                "This case is no longer accepting defenses.",
                ephemeral=True,
            )

        case["defense"] = defense_text
        case["status"] = "awaiting_judgment"
        save_cases(cases)

        # Clean readable block
        channel = self.ctx.channel
        msg = await channel.fetch_message(self.message_id)

        content = (
            f"⚖️ **PLAINTIFF:** {self.plaintiff.mention}\n"
            f"⚖️ **DEFENDANT:** {self.defendant.mention}\n\n"
            f"**ACCUSATION:**\n"
            f"{case['accusation']}\n\n"
            f"🛡️ **DEFENSE SUBMITTED:**\n"
            f"{defense_text}\n\n"
            f"🏛️ Awaiting judge's decision..."
        )

        await msg.edit(content=content, view=None)

        await interaction.response.send_message(
            "Defense submitted. The judge will now review the case.",
            ephemeral=True,
        )

        # ⭐ Automatically trigger the judge
        await self.ctx.bot.law_manager.run_case(self.message_id)


# -----------------------------
# VIEWS
# -----------------------------
class LawyerStartView(View):
    def __init__(self, ctx, plaintiff, defendant, settlement_amount, bank):
        super().__init__(timeout=None)

        self.ctx = ctx
        self.plaintiff = plaintiff
        self.defendant = defendant
        self.settlement_amount = settlement_amount
        self.bank = bank
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction):
        return True

    @discord.ui.button(label="Enter accusations", style=discord.ButtonStyle.success)
    async def enter_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.plaintiff.id:
            return

        modal = AccusationModal(
            ctx=self.ctx,
            plaintiff=self.plaintiff,
            defendant=self.defendant,
            settlement_amount=self.settlement_amount,
            bank=self.bank,
            parent_view=self,
        )
        await interaction.response.send_modal(modal)


class DefenseView(View):
    def __init__(self, ctx, plaintiff, defendant, settlement_amount, bank, message_id):
        super().__init__(timeout=86400)

        self.ctx = ctx
        self.plaintiff = plaintiff
        self.defendant = defendant
        self.settlement_amount = settlement_amount
        self.bank = bank
        self.message_id = message_id

    async def interaction_check(self, interaction: discord.Interaction):
        return True

    async def on_timeout(self):
        cases = load_cases()
        case = cases.get(str(self.message_id))
        if not case or case.get("status") != "awaiting_defense":
            return

        case["status"] = "default_judgment"
        save_cases(cases)

        channel = self.ctx.channel
        msg = await channel.fetch_message(self.message_id)

        content = (
            f"⚖️ **PLAINTIFF:** {self.plaintiff.mention}\n"
            f"⚖️ **DEFENDANT:** {self.defendant.mention}\n\n"
            f"**ACCUSATION:**\n"
            f"{case['accusation']}\n\n"
            f"⏳ Defendant failed to respond in time.\n"
            f"⚖️ DEFAULT JUDGMENT ENTERED in favor of {self.plaintiff.mention}."
        )

        await msg.edit(content=content, view=None)

    @discord.ui.button(label="Proceed with defense", style=discord.ButtonStyle.primary)
    async def defense_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.defendant.id:
            return

        cases = load_cases()
        case = cases.get(str(self.message_id))
        if not case or case.get("status") != "awaiting_defense":
            return

        modal = DefenseModal(
            ctx=self.ctx,
            plaintiff=self.plaintiff,
            defendant=self.defendant,
            settlement_amount=self.settlement_amount,
            bank=self.bank,
            message_id=self.message_id,
        )
        await interaction.response.send_modal(modal)


# -----------------------------
# MAIN COG
# -----------------------------
class Lawyer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="law")
    async def lawyer(self, ctx, defendant: discord.Member = None, settlement_amount: str = None):
        if defendant is None or settlement_amount is None:
            return await ctx.send("Usage: `?law @user <settlement amount>`")

        try:
            settlement_amount = int(settlement_amount)
        except:
            return await ctx.send("❌ Settlement amount must be a number.")

        bank: Bank = self.bot.get_cog("Bank")
        if bank is None:
            return await ctx.send("❌ Bank system not loaded.")

        bal = await bank.load_bank(ctx.author)
        if bal < LAWYER_COST:
            return await ctx.send(
                f"❌ You need **{LAWYER_COST:,} coins** to hire a lawyer."
            )

        view = LawyerStartView(
            ctx=ctx,
            plaintiff=ctx.author,
            defendant=defendant,
            settlement_amount=settlement_amount,
            bank=bank,
        )

        msg = await ctx.send(
            f"⚖️ **PLAINTIFF:** {ctx.author.mention}\n"
            f"⚖️ **DEFENDANT:** {defendant.mention} is accused of...\n\n"
            f"Click **Enter accusations** to continue.",
            view=view,
        )
        view.message = msg

    # -------------------------
    # AGENT CALLBACK
    # -------------------------
    async def complete_case(self, message_id: int, winner: str, judge_text: str):
        cases = load_cases()
        case = cases.get(str(message_id))
        if not case:
            return

        # Normalize winner
        w = winner.strip().lower()
        if w == "plaintiff":
            winner_side = "plaintiff"
        elif w == "defendant":
            winner_side = "defendant"
        else:
            winner_side = None

        case["winner"] = winner_side
        case["judge_text"] = judge_text
        case["status"] = "complete"
        save_cases(cases)

        guild = self.bot.get_guild(case["guild_id"])
        if not guild:
            return

        channel = guild.get_channel(case["channel_id"])
        if not channel:
            return

        msg = await channel.fetch_message(message_id)

        plaintiff = guild.get_member(case["plaintiff_id"])
        defendant = guild.get_member(case["defendant_id"])

        bank: Bank = self.bot.get_cog("Bank")

        # Settlement transfer
        if winner_side == "plaintiff":
            def_bal = await bank.load_bank(defendant)
            await bank.save_bank(defendant, def_bal - case["settlement_amount"])

            pl_bal = await bank.load_bank(plaintiff)
            await bank.save_bank(plaintiff, pl_bal + case["settlement_amount"])

            settlement_line = (
                f"💰 **Settlement Awarded:** {case['settlement_amount']:,} coins transferred "
                f"from {defendant.mention} to {plaintiff.mention}.\n\n"
            )
        else:
            settlement_line = "💰 **No settlement awarded.**\n\n"

        # Winner mention
        if winner_side == "plaintiff":
            winner_mention = plaintiff.mention
        elif winner_side == "defendant":
            winner_mention = defendant.mention
        else:
            winner_mention = "Judge undecided"

        accusation_text = case.get("accusation", "No accusation recorded.")
        defense_text = case.get("defense", "No defense recorded.")

        # Clean readable final message
        content = (
            f"⚖️ **Lawsuit complete — {winner_mention} wins.**\n\n"
            f"{settlement_line}"
            f"**ACCUSATION:**\n"
            f"{accusation_text}\n\n"
            f"**DEFENSE:**\n"
            f"{defense_text}\n\n"
            f"**JUDGE'S RESPONSE:**\n"
            f"{judge_text}"
        )

        await msg.edit(content=content, view=None)


async def setup(bot):
    await bot.add_cog(Lawyer(bot))
