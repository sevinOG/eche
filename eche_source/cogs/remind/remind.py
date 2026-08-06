# reminders/remind.py

import discord
from discord.ext import commands
from cogs.remind.remind_handler import ReminderHandler


class Remind(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.handler = ReminderHandler(bot)

    @commands.Cog.listener()
    async def on_ready(self):
        # Rebuild persistent reminders when the bot starts
        await self.handler.load_persistent_reminders()

    @commands.command(name="remind")
    async def remind(self, ctx, target: str, *, time_and_message: str):
        """
        Natural language reminder command.

        Examples:
        !remind me in 10 minutes take out the trash
        !remind @user 1s ping
        !remind me tomorrow at 5 meeting
        !remind me next monday at 8 dentist
        """

        # Determine target user or group
        if target.lower() == "me":
            user = ctx.author

        elif target == "@everyone":
            user = "@everyone"

        elif target == "@here":
            user = "@here"

        elif ctx.message.mentions:
            user = ctx.message.mentions[0]

        elif ctx.message.role_mentions:
            role = ctx.message.role_mentions[0]
            user = f"role:{role.id}"

        else:
            await ctx.send("I couldn't identify the user or role you want to remind.")
            return

        # Delegate to handler
        success, response = await self.handler.create_reminder(
            ctx=ctx,
            user=user,
            time_and_message=time_and_message
        )

        await ctx.send(response)


# ---------------------------------------------------------
# REQUIRED EXTENSION ENTRY POINT
# ---------------------------------------------------------
async def setup(bot):
    await bot.add_cog(Remind(bot))
