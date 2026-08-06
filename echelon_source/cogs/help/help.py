# cogs/help/help.py

import discord
from discord.ext import commands
from discord.ui import View, Button

# ---------------------------------------------------------
# COGS TO HIDE FROM HELP
# ---------------------------------------------------------
HIDDEN_COGS = {
    "ContextDebug",
    "DebugCommands",
    "ForceOptIn",
    "HelpCog",  # hide the help command itself
}


# ---------------------------------------------------------
# PAGINATION VIEW
# ---------------------------------------------------------
class HelpView(View):
    def __init__(self, pages, ctx):
        super().__init__(timeout=30)
        self.pages = pages
        self.index = 0
        self.ctx = ctx
        self.message = None

    async def on_timeout(self):
        try:
            await self.message.edit(
                content=f"{self.ctx.author.display_name} used ?help",
                embed=None,
                view=None
            )
        except:
            pass

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your help menu.", ephemeral=True)

        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your help menu.", ephemeral=True)

        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


# ---------------------------------------------------------
# HELP COG — RUNTIME INTROSPECTION
# ---------------------------------------------------------
class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def help_command(self, ctx):
        """Displays commands grouped by cog, one cog per page."""

        pages = []

        # Alphabetical cog order, minus hidden ones
        for cog_name in sorted(self.bot.cogs.keys()):

            if cog_name in HIDDEN_COGS:
                continue

            cog = self.bot.get_cog(cog_name)
            if not cog:
                continue

            # Filter commands
            cmds = [
                cmd for cmd in cog.get_commands()
                if not cmd.hidden
                and cmd.name not in ("help", "on_ready", "on_message")
                and not cmd.name.startswith("on_")
            ]

            if not cmds:
                continue

            embed = discord.Embed(
                title=f"📘 {cog_name}",
                description="Commands in this category:",
                color=discord.Color.blurple()
            )

            for cmd in cmds:
                # Add the main command
                embed.add_field(
                    name=f"**?{cmd.name}**",
                    value="\u200b",
                    inline=False
                )

                # If it's a group, add subcommands
                if isinstance(cmd, commands.Group):
                    for sub in cmd.commands:
                        embed.add_field(
                            name=f"  ↳ **?{cmd.name} {sub.name}**",
                            value="\u200b",
                            inline=False
                        )

            pages.append(embed)

        if not pages:
            return await ctx.send("No commands found.")

        view = HelpView(pages, ctx)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
