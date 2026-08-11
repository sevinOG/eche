import discord
from discord.ext import commands
from discord.ui import View, Select
import os

# New manager
try:
    from core.opt_in_manager import get_valid_members_for_guild
except ImportError:
    from opt_in_manager import get_valid_members_for_guild


class TargetSelect(Select):
    def __init__(self, ctx, amount, bot, parent_message, job_type, valid_members):
        self.ctx = ctx
        self.amount = amount
        self.bot = bot
        self.parent_message = parent_message
        self.job_type = job_type
        self.valid_members = valid_members

        if not valid_members:
            options = [
                discord.SelectOption(
                    label="No hirable users in this server",
                    value="none",
                    description="No one here is opted into memory"
                )
            ]
        else:
            options = []
            for m in valid_members[:25]:  # Discord limit
                # Use display_name but keep username in description
                options.append(
                    discord.SelectOption(
                        label=m.display_name[:100],
                        value=str(m.id),
                        description=f"@{m.name}"[:100]
                    )
                )

        super().__init__(
            placeholder="Choose a target..." if valid_members else "No targets available",
            min_values=1,
            max_values=1,
            options=options,
            disabled=len(valid_members) == 0
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This menu isn't for you.", ephemeral=True
            )

        if self.values[0] == "none":
            return await interaction.response.send_message(
                "No valid targets available in this server.", ephemeral=True
            )

        target_id = int(self.values[0])
        target = self.ctx.guild.get_member(target_id)
        if target is None:
            try:
                target = await self.ctx.guild.fetch_member(target_id)
            except Exception:
                return await interaction.response.send_message(
                    "Could not find that user anymore.", ephemeral=True
                )

        # Final message always same
        try:
            await self.parent_message.edit(
                content=f"**{self.ctx.author.display_name} hired a specialist...**",
                view=None
            )
        except Exception:
            pass

        # Invoke the correct job silently
        cmd = self.bot.get_command(self.job_type)
        if cmd:
            try:
                if self.job_type == "law":
                    await self.ctx.invoke(cmd, defendant=target, settlement_amount=str(self.amount))
                else:
                    await self.ctx.invoke(cmd, target=target, amount=self.amount)
            except Exception as e:
                print(f"[hire] invoke failed {self.job_type}: {e}")

        try:
            await interaction.response.defer()
        except Exception:
            pass


class HireJobView(View):
    def __init__(self, ctx, amount, bot):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.amount = amount
        self.bot = bot
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This menu isn't yours.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.edit(content="Hire menu timed out.", view=None)
        except Exception:
            pass

    async def _show_target_selector(self, interaction: discord.Interaction, job_type: str, prompt: str):
        await interaction.response.defer()

        # INTELLIGENT CHECK: opted_in ∩ members in invoker's guild
        valid_members = await get_valid_members_for_guild(self.bot, self.ctx.guild)

        dropdown = TargetSelect(
            self.ctx, self.amount, self.bot, self.message, job_type, valid_members
        )
        view = View(timeout=60)
        view.add_item(dropdown)

        await self.message.edit(
            content=prompt if valid_members else f"No hirable users in **{self.ctx.guild.name}**. Users must be opted into memory and be in this server.",
            view=view
        )

    @discord.ui.button(label="Mugger", style=discord.ButtonStyle.danger, emoji="🔪")
    async def mugger(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_target_selector(interaction, "rob", f"Choose a target to mug for **${self.amount}**:")

    @discord.ui.button(label="Heckler", style=discord.ButtonStyle.primary, emoji="📢")
    async def heckler(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_target_selector(interaction, "heckle", f"Choose a target to heckle:")

    @discord.ui.button(label="Lawyer", style=discord.ButtonStyle.secondary, emoji="⚖️")
    async def lawyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_target_selector(interaction, "law", f"Choose a target to sue for **${self.amount}**:")

    @discord.ui.button(label="Entertainers", style=discord.ButtonStyle.success, emoji="🎭")
    async def entertainers(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await self.message.edit(
                content=f"**{self.ctx.author.display_name} hired a specialist...**",
                view=None
            )
        except Exception:
            pass

        cmd = self.bot.get_command("entertainers")
        if cmd:
            await self.ctx.invoke(cmd, amount=str(self.amount))


class Hire(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="hire")
    async def hire(self, ctx, amount: int):
        if amount <= 0:
            return await ctx.send("Amount must be positive.")
        
        # Ensure guild has chunked members
        if not ctx.guild.chunked:
            async with ctx.typing():
                try:
                    await ctx.guild.chunk(cache=True)
                except Exception:
                    pass

        view = HireJobView(ctx, amount, self.bot)
        msg = await ctx.send(
            f"What kind of specialist do you want to hire for **${amount}**?",
            view=view
        )
        view.message = msg


async def setup(bot):
    await bot.add_cog(Hire(bot))
